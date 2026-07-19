import os, sys, asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Header ,Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))
sys.path.insert(0, _here)

from config import load_config, ConfigError
from core import DeviceCore
from broker import EventBroker
from auth import authenticate, require, SessionStore, AuthError, PermissionDenied




logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("device")

broker = EventBroker()
core: DeviceCore | None = None
_idempotency: dict[str, int] = {}      # key -> runId
sessions = SessionStore()
_bearer = HTTPBearer(auto_error=False)


def current_session(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """Resolve the bearer token to a live session, or 401."""
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    session = sessions.get(creds.credentials)
    if session is None:
        raise HTTPException(401, "invalid or expired session")
    return session


def requires(perm: str):
    """Dependency factory — the @PreAuthorize equivalent."""
    def _dep(session = Depends(current_session)):
        try:
            return require(session, perm)
        except AuthError:
            raise HTTPException(401, "authentication required")
        except PermissionDenied as e:
            raise HTTPException(403, str(e))
    return _dep

@asynccontextmanager
async def lifespan(app: FastAPI):
    global core
    broker.bind_loop(asyncio.get_running_loop())

    config = load_config(os.path.join(_here, "..", "config.json"))
    log.info(f"config loaded for device '{config['device_id']}'")

    core = DeviceCore(
        config,
        on_reading=lambda d: broker.publish_threadsafe({"type": "reading", **d}),
        on_state=lambda s: broker.publish_threadsafe({"type": "state", "state": s}),
        on_progress=lambda p: broker.publish_threadsafe({"type": "progress", "percent": p}),
    )
    log.info("device service ready")
    yield
    core.shutdown()


app = FastAPI(title="Edge Device Control API", lifespan=lifespan)


# ---------- commands ----------
@app.post("/login")
def login(body: dict):
    username = body.get("username", "")
    password = body.get("password", "")
    try:
        session = authenticate(core.conn, sessions, username, password)
    except AuthError:
        from store import audit
        audit(core.conn, {"action": "login_failed", "username": username})
        raise HTTPException(401, "invalid credentials")

    from store import audit
    audit(core.conn, {"action": "login", "username": session.username,
                      "role": session.role})
    return {"token": session.token, "role": session.role,
            "expiresIn": session.ttl}

@app.post("/runs")
def start_run(session = Depends(requires("run:start")),
              idempotency_key: str | None = Header(default=None)):
    if idempotency_key and idempotency_key in _idempotency:
        return {"runId": _idempotency[idempotency_key], "replayed": True}

    state = core.state()
    if state == "running":
        raise HTTPException(409, "a run is already in progress")
    if state in ("completed", "failed"):
        raise HTTPException(409, f"device is '{state}' — POST /reset before starting a new run")

    run_id = core.start()
    if run_id is None:
        raise HTTPException(500, "start failed unexpectedly")

    from store import audit
    audit(core.conn, {"action": "run_started", "run_id": run_id,
                      "by": session.username})            # ← who

    if idempotency_key:
        _idempotency[idempotency_key] = run_id
    return {"runId": run_id, "replayed": False}


@app.post("/runs/{run_id}/abort")
def abort_run(run_id: int, session = Depends(requires("run:abort"))):
    if core.run_id != run_id:
        raise HTTPException(404, f"run {run_id} is not the active run")
    aborted = core.abort()
    from store import audit
    audit(core.conn, {"action": "run_aborted", "run_id": run_id,
                      "by": session.username})
    return {"ok": True, "aborted": aborted}

@app.post("/reset")
def reset(session = Depends(requires("run:reset"))):
    ok = core.reset()
    if ok:
        from store import audit
        audit(core.conn, {"action": "run_reset", "by": session.username})
    return {"ok": ok, "state": core.state()}

# ---------- queries ----------

@app.get("/state")
def state():
    return {"state": core.state(), "activeRunId": core.run_id}


@app.get("/health")
def health():
    h = core.health()
    return JSONResponse(h, status_code=200 if h["ok"] else 503)


@app.get("/runs/{run_id}/status")
def run_status(run_id: int):
    row = core.conn.execute(
        "SELECT id, started, ended, status FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"no run {run_id}")
    return {"runId": row[0], "started": row[1], "ended": row[2], "status": row[3]}


@app.get("/runs/{run_id}/results")
def run_results(run_id: int):
    run = core.conn.execute(
        "SELECT id, started, ended, status FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, f"no run {run_id}")

    counts = dict(core.conn.execute(
        "SELECT status, COUNT(*) FROM readings WHERE run_id=? GROUP BY status",
        (run_id,)).fetchall())
    total = sum(counts.values())

    return {"runId": run[0], "status": run[3], "readings": total,
            "breakdown": counts}


# ---------- events ----------

@app.websocket("/events")
async def events(ws: WebSocket):
    await ws.accept()
    q = broker.subscribe()
    log.info("event subscriber connected")
    try:
        await ws.send_json({"type": "state", "state": core.state()})   # sync on connect
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        log.info("event subscriber disconnected")
    finally:
        broker.unsubscribe(q)
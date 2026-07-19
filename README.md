# Edge Device — A Simulated Linux Instrument

A Linux edge-device application: a sensor streams readings over a serial port, the device service
interprets them against configured thresholds, persists results with a tamper-evident audit trail,
authenticates operators, and serves a Qt/QML touchscreen an operator controls. It runs under systemd
and recovers from power loss.

**The hardware is simulated.** The sensor writes to a virtual serial port (a `pty` pair), so the OS
presents it as a real character device and the application's serial code is identical to what it
would be against physical hardware. Everything above that line — the application, the decision
logic, the storage, the UI, the recovery, the auth — is real.

This document covers the architecture, the reasoning behind each design decision, and what each
choice costs.

---

## Status

| Component | Status |
|---|---|
| Serial input over a virtual port | ✅ |
| Device loop, structured logging, clean shutdown | ✅ |
| Decision engine (pure, config-driven) | ✅ |
| Local database + hash-chained audit log | ✅ |
| Configuration validation (fail-closed) | ✅ |
| Qt/QML touchscreen with worker-thread isolation | ✅ |
| Run state machine + operator controls | ✅ |
| Crash recovery, health checks, systemd supervision | ✅ |
| Control API (REST + WebSocket), service/client split | ✅ |
| Local authentication & role-based access | ✅ |
| Signed updates & removable-media security | 🚧 In progress |
| Cross-layer debugging tooling | 🚧 In progress |

**58 tests passing. No hardware required.**

---

## Architecture

The device runs as a headless service that owns the hardware and the database. Clients — the
touchscreen, a test harness, `curl` — authenticate, then drive it over REST and receive live events
over a WebSocket.

```mermaid
flowchart TD
    subgraph SVC[device-service — systemd-supervised, owns hardware + DB]
      SIM[sensor simulator] -->|virtual serial / pty| RD[reader — defensive parse]
      subgraph W[worker thread]
        RD --> DEC[decision engine<br/>pure, config-driven]
        DEC --> DB[(SQLite — WAL<br/>runs · readings · audit · users)]
      end
      DEC -->|callbacks| BRK[event broker]
      BRK -->|call_soon_threadsafe| API[FastAPI<br/>REST + WebSocket + auth]
      FSM[run state machine] -.lock-guarded.- DEC
      CFG[config.json<br/>validated, fail-closed] --> DEC
      CK[atomic checkpoint] -.crash recovery.-> DB
    end

    subgraph UIP[UI process — a client]
      WS[WS listener thread] -->|Qt signals| BR[DeviceBridge]
      BR --> QML[dashboard.qml<br/>login + 800x480 touchscreen]
      QML -->|REST + bearer token| HTTP[DeviceClient<br/>timeout · backoff]
    end

    API -->|events| WS
    HTTP -->|login / start / abort / reset| API
    CLI[curl · test harness] --> API
```

### Device stack

| Layer | Responsibility | Implementation |
|---|---|---|
| Cloud connection | Telemetry out, commands in | Out of scope — this device is offline by design |
| Local UI | Touchscreen, operator workflow, login | `ui/` — a client |
| Control API | Commands, queries, live events, auth | `service/` |
| Application | Reads inputs, decides, drives outputs | `device_app/core.py` |
| Data | Local storage, audit trail, users | `device_app/store.py` |
| OS | Service supervision, recovery | `scripts/edge-device-service.service` |
| Firmware & drivers | Bus-level hardware access | Simulated via pty |
| Physical hardware | Sensors, screen | Simulated |

### Components

| Component | File | Responsibility |
|---|---|---|
| Sensor simulator | `sensor_sim/sensor.py` | Emits readings over a pty as real hardware would |
| Reader | `device_app/reader.py` | Parses the byte stream defensively |
| Decision engine | `device_app/decision.py` | Interprets readings → status + reasons |
| Run state machine | `device_app/run_state.py` | Run lifecycle; illegal transitions impossible |
| Store | `device_app/store.py` | SQLite persistence + hash-chained audit log + users |
| Config | `device_app/config.py` | Validates configuration; refuses to start if invalid |
| Recovery | `device_app/recovery.py` | Atomic checkpoints, crash recovery, health checks |
| Auth | `device_app/auth.py` | bcrypt hashing, sessions, RBAC |
| Device core | `device_app/core.py` | All device logic, no UI dependency; reports via callbacks |
| Control API | `service/control_api.py` | FastAPI: REST commands, WebSocket events, auth |
| Event broker | `service/broker.py` | Bridges worker-thread callbacks into asyncio queues |
| Client | `service/client.py` | Timeouts, backoff, error classification, token auth |
| UI bridge | `ui/bridge.py` | Adapter: API client + WS listener → Qt signals |
| Dashboard | `ui/dashboard.qml` | Login screen + 800×480 touchscreen |
| User provisioning | `tools/useradd.py` | Creates device users; no default password |
| Service unit | `scripts/edge-device-service.service` | systemd supervision |

### Stack selection

| Component | Chosen | Rationale | Alternative |
|---|---|---|---|
| Language | Python | Fast iteration; strong libraries for serial, UI, and web | C++ where timing or performance is critical |
| Serial | pyserial + pty | Real serial API without hardware | Physical UART device |
| UI | Qt/QML (PySide6) | Native, low footprint, touch-first | Web UI — requires a bundled browser engine |
| Database | SQLite (WAL) | Serverless, single-file, offline-native | PostgreSQL — needs a server process |
| API | FastAPI (REST + WS) | Async-native, generated docs, WebSocket support | gRPC, raw sockets |
| Auth | bcrypt + RBAC | Offline, standard, tunable work factor | Cloud identity provider |
| Supervision | systemd | Restart-on-crash and start-on-boot, declaratively | Custom init script |
| Configuration | JSON + validation | Behaviour changes without a redeploy | Hard-coded constants |

---

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Provision a user (first run only):

```bash
python3 tools/useradd.py alice operator
```

The device service:

```bash
uvicorn service.control_api:app --host 127.0.0.1 --port 8000
```

The touchscreen, in another terminal — it opens to a login screen:

```bash
python3 ui/main.py
```

Qt requires a display. On WSL2, WSLg provides one.

```bash
pytest -v              # 58 tests
```

Interactive API docs at `http://localhost:8000/docs`.

### As a service

```bash
sudo cp scripts/edge-device-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edge-device-service
journalctl -u edge-device-service -n 20 --no-pager
```

The UI is launched separately and connects to the supervised device.

---

# Design

---

## Serial input

Serial (UART) is a stream of bytes over a wire at an agreed baud rate — no request/response, no
framing beyond what the device protocol defines. With no physical hardware available, the sensor
writes into a **pseudo-terminal (pty)**: two linked endpoints where bytes written to one emerge from
the other, and the OS exposes each end as a character device.

The consequence is that `serial.Serial(port, 9600)` is the same call it would be against
`/dev/ttyUSB0`. The application cannot distinguish the simulated sensor from a real one, and
swapping in hardware means changing one function — `_open_port()`.

```mermaid
flowchart LR
    SIM[sensor.py] -->|writes bytes| P((pty pair<br/>virtual serial))
    P -->|pyserial reads| RD[reader.py]
    RD -->|parsed dict| APP[application]
```

| Approach | Advantage | Cost |
|---|---|---|
| Virtual serial (pty) | Zero hardware, fast iteration, unmodified serial API | No electrical or timing realism; no bus-level debugging |
| Physical hardware | Authentic timing, real fault modes | Cost, slower iteration cycle |

**Parsing is defensive by design:**

```python
try:
    out[key] = float(val)
except ValueError:
    pass          # real serial links drop and corrupt bytes
```

Server-side code can assume well-formed input from a well-behaved client. A device receiving bytes
off a wire cannot. A device that crashes on one malformed line is useless in the field.

---

## The device loop

The device is a continuous loop rather than a request handler: it reads from its inputs
indefinitely and stops only when instructed. Three properties follow from that:

- **Bad input is skipped, not fatal.** A read timeout returns empty; a corrupt line parses to
  nothing. Neither warrants a crash.
- **Output goes through `logging`, not `print`.** Levels and timestamps, and `StandardOutput=journal`
  means it lands in `journalctl` with no extra work.
- **Termination is handled.** Signals set a flag; the loop finishes its current iteration and exits
  on its own terms rather than dying mid-write.

```mermaid
flowchart TD
    START([start]) --> PORT[open serial port]
    PORT --> READ[read line]
    READ --> EMPTY{empty?<br/>timeout}
    EMPTY -- yes --> RUN{still running?}
    EMPTY -- no --> PARSE{parsed ok?}
    PARSE -- no --> RUN
    PARSE -- yes --> HANDLE[decide · store · publish]
    HANDLE --> RUN
    RUN -- yes --> READ
    RUN -- no --> CLOSE[close port, exit cleanly]
```

| Choice | Rationale | Cost |
|---|---|---|
| Single loop | Predictable, one thing at a time | Slow work blocks everything — resolved with worker threads |
| Skip malformed lines | Field resilience | Silent discards can mask real problems |
| Signal-driven shutdown | Resources released, state flushed | Loop must poll the flag |

Graceful shutdown, observed:

```
INFO shutdown signal received
INFO T=25.3C P=1016.8hPa H=39.9%     ← in-flight read completes
INFO stopped cleanly
```

The loop was blocked in `readline()` when the signal arrived. The handler set the flag, the read
returned, the iteration completed, and only then did the loop exit — rather than being terminated
mid-write.

---

## Decision engine

Measurement and interpretation are separated. `decide(reading, rules)` is a pure function: no I/O,
no clock, no randomness. The same reading and rules always produce the same `Result` — a status
(`NORMAL` / `WARNING` / `CRITICAL` / `INVALID`) plus the reasons behind it.

Purity makes the entire decision layer testable without a sensor, a database, or a running device.
Thresholds live in `config.json`, so behaviour changes without a code change. Missing input returns
an explicit `INVALID` rather than a default — a device that reports "normal" when a sensor is
disconnected is worse than one that reports nothing.

```mermaid
flowchart TD
    IN[reading + rules] --> CHK{all required<br/>values present?}
    CHK -- no --> INV[status = INVALID<br/>reason: which are missing]
    CHK -- yes --> LOOP[for each value:<br/>check crit band, then warn band]
    LOOP --> WORST[keep worst status<br/>collect all reasons]
    WORST --> OUT[Result: status + reasons + values]
```

| Choice | Rationale | Cost |
|---|---|---|
| Pure function | Deterministic; testable without hardware | State passed explicitly |
| Config-driven thresholds | Behaviour changes without redeploy | Configuration must be validated |
| Explicit `INVALID` | No confident wrong answers | Additional state for the UI to handle |
| Reasons returned with status | Operator sees why; supports debugging | Marginally more code |
| Worst-status-wins | One unambiguous verdict | All reasons retained to preserve detail |

The critical band is evaluated before the warning band. A value outside critical is also outside
warning, so the order prevents a critical reading being reported as a warning.

Tightening `"T": {"warn": [15, 25]}` in `config.json` flips the same ~25.7 °C readings from
`[normal]` to `[warning]` with the reason attached, with no code modified.

---

## Local storage and the audit chain

The device operates offline, so the local database is the system of record. There is no upstream to
re-sync from; if it is wrong, the data is lost. The implementation reflects that:

- **WAL mode** — writes append to a log before folding into the main file. Readers don't block the
  writer, and a crash mid-write leaves a recoverable log rather than a corrupted database.
- **Transactions** — `with conn:` commits on success and rolls back on exception, so multi-step
  writes are atomic. No half-written runs.
- **Thread discipline** — one connection, owned by the worker thread.
- **Integrity check on startup** — `PRAGMA integrity_check` detects a damaged file before the device
  serves data from it.

```mermaid
flowchart TD
    R[reading + decision] --> TX[BEGIN transaction]
    TX --> INS[insert reading + result]
    INS --> OK{success?}
    OK -- yes --> CM[COMMIT — now durable]
    OK -- no --> RB[ROLLBACK — no partial write]
    CM --> AUD{status changed<br/>or notable event?}
    AUD -- yes --> HASH[hash prev entry + new event]
    HASH --> APP[append to audit chain]
```

### Hash-chained audit log

The audit table is append-only, and each entry stores the SHA-256 of the previous entry's payload.
Editing or deleting any past row breaks every subsequent hash, so `verify_audit()` detects tampering
rather than merely discouraging it.

`sort_keys=True` when serializing the payload is load-bearing: dictionary ordering must be
deterministic or the same event hashes differently on re-verification and every check fails.

The audit log records **events** — startup, state changes, configuration changes, logins, crash
recovery — not readings. A representative session produced 22 readings and 3 audit entries. Recording
data in the audit log produces a log nobody reads.

| | SQLite (chosen) | PostgreSQL / MySQL |
|---|---|---|
| Setup | Single file, no configuration | Server to install, run, and secure |
| Fit for a single device | Native | Overkill; another process to fail |
| Concurrency | One writer | Many concurrent writers |
| Operations | Application owns backup/recovery | DBA or managed service |

| Choice | Rationale | Cost |
|---|---|---|
| WAL mode | Crash resilience; non-blocking reads | Extra `-wal` / `-shm` files |
| Hash-chained audit | Tampering is detectable | History is genuinely immutable |
| Persist every reading | Full traceability | Storage growth requires pruning |

Verified: editing an audit row directly in SQLite causes startup to report
`audit chain valid: False`.

---

## Configuration

A missing or malformed config file fails loudly and gets fixed. The real hazard is a **silently
wrong** configuration: remove the `"T"` rule and the device runs normally, never evaluates
temperature again, and reports `[normal]` indefinitely.

Validation converts silent wrongness into loud wrongness. `load_config()` checks structure, enforces
that each band is two ordered numbers, and — the non-obvious constraint — that the critical band
*contains* the warning band. Inverted bands produce meaningless output that no runtime check would
catch. Optional keys receive validated defaults.

Invalid configuration stops the device from starting.

```mermaid
flowchart TD
    START([startup]) --> READ[read config.json]
    READ --> EXIST{file exists?}
    EXIST -- no --> FAIL[ConfigError — refuse to start]
    EXIST -- yes --> JSON{valid JSON?}
    JSON -- no --> FAIL
    JSON -- yes --> REQ{required keys<br/>present?}
    REQ -- no --> FAIL
    REQ -- yes --> BANDS{bands valid?<br/>lo<hi, crit contains warn}
    BANDS -- no --> FAIL
    BANDS -- yes --> DEF[apply defaults for<br/>optional keys]
    DEF --> OK([start device])
```

| | Fail closed (chosen) | Fail open (defaults) |
|---|---|---|
| Invalid config | Device refuses to start | Device starts with substituted values |
| Safety | Cannot operate on wrong thresholds | May operate wrongly, silently |
| Availability | Down until corrected | Stays up |
| Appropriate for | Instruments that report verdicts | Best-effort telemetry |

For a device that interprets measurements and reports a result, fail-closed is the correct side of
the trade. A device that is down is obviously broken; a device reporting confident nonsense is not.

Observed:

```
ERROR invalid configuration: rules.P: crit band [1000, 1025] must contain warn band [990, 1035]
ERROR refusing to start — fix config.json and restart
```

The error names the exact key and the exact constraint violated.

---

## Touchscreen UI

The UI runs on the device, fullscreen, with no browser. Qt is the standard framework for embedded
device interfaces; QML describes the screen declaratively and property bindings keep it synchronized
with the model — no manual redraws.

### Thread architecture

The device loop blocks indefinitely. Qt's event loop (`app.exec()`) also blocks indefinitely. They
cannot share a thread. And no GUI toolkit permits a background thread to touch the UI.

The resolution:

- **Main thread** runs Qt's event loop. It renders and handles touch input. Nothing slow executes here.
- **Worker thread** runs the device loop — serial reads, decisions, database writes.
- **Qt signals** carry data between them. Emitting a signal is the only thread-safe path from the
  worker to the UI.

```mermaid
flowchart LR
    subgraph W[Worker thread]
      SER[read serial] --> DEC[decide] --> DB[(store)]
    end
    DEC -->|emit signal| BR[DeviceBridge<br/>QObject]
    subgraph M[Main thread — Qt event loop]
      BR -->|property binding| QML[dashboard.qml]
      QML -->|renders| SCR[800x480 screen]
    end
```

The touchscreen stays responsive regardless of what the device is doing — the worker performs
continuous serial I/O and database writes while the UI renders at full rate.

| | Qt/QML (chosen) | Web UI (Chromium) |
|---|---|---|
| Footprint | Native rendering, low memory | Requires a full browser engine |
| Touch | First-class | Supported, additional layer |
| Startup | Fast | Browser boot time |
| Offline | Native | Works, more moving parts |

| Choice | Rationale | Cost |
|---|---|---|
| Worker thread + signals | UI never blocks; thread-safe by construction | Cross-thread reasoning; timing-dependent bugs |
| Declarative QML | View stays synchronized automatically | Framework-specific syntax |
| 800×480 | Common embedded panel resolution | — |

**Path resolution.** `db_path` resolves relative to the config file, not the working directory.
Launching from `ui/` originally created a second database there; systemd starts services from `/`,
which would have caused the same fault in production. Two tests pin the behaviour:

```python
def test_db_path_resolves_next_to_config(tmp_path): ...
def test_absolute_db_path_is_left_alone(tmp_path): ...
```

---

## Run state machine

The device has modes — idle, running, completed, failed — and illegal actions follow from that: a
run that hasn't started cannot be aborted.

Boolean flags (`is_running`, `is_finished`, `has_failed`) admit eight combinations of which four are
legal, and every call site has to defend itself. A finite state machine declares the states and the
legal transitions in one place; everything else is impossible by construction. One `state` variable,
always valid, and illegal transitions raise rather than corrupt.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: operator taps Start
    Running --> Completed: duration reached
    Running --> Failed: abort or error
    Completed --> Idle: reset
    Failed --> Idle: reset
```

### Concurrency

Two threads transition the machine: the caller on Start/Abort, the worker on completion. An abort
landing at the same moment a run completes would race, so transitions are lock-guarded. The abort
flag is a `threading.Event` rather than a plain boolean.

The UI binds directly to the state, which means the controls cannot offer an illegal action:

```qml
TouchButton { label: "START"; active: runState === "idle"; onTapped: device.startRun() }
```

| | FSM (chosen) | Boolean flags |
|---|---|---|
| Illegal states | Impossible by construction | Possible; detected by inspection |
| Adding a state | One entry in the transition map | Audit every conditional |
| UI rendering | Switch on one value | Combinatorial conditionals |

| Choice | Rationale | Cost |
|---|---|---|
| Lock-guarded transitions | Prevents caller/worker races | Must not be held during slow work |
| Abort → FAILED, not COMPLETED | An aborted run has incomplete data | Two paths reach FAILED |
| Cooperative abort | Worker exits at a safe point | Not instantaneous |
| `try/except` around the worker loop | An unhandled exception on a thread dies silently | — |

That last point is load-bearing: without it the thread disappears, the UI reports "running"
indefinitely, and nothing indicates why. Catching it and transitioning to FAILED is the difference
between a device that reports its own failure and one that simply stops.

Cooperative abort, measured:

```
15:09:57,372 INFO abort requested
15:09:57,622 INFO run 9 aborted      ← 250ms
```

250 ms is one sample interval at 4 Hz. The worker was blocked in `readline()`, the event fired, it
completed the iteration, closed the port, wrote the outcome, and exited — not killed mid-write.

---

## Reliability and recovery

Everything above assumes an orderly exit. Devices don't get that guarantee: power cuts, OOM kills,
and unhandled faults happen with nobody present.

**The orphaned run.** If the device dies mid-run, the database still holds:

```
13|1784265618.56957||running
```

That row claims `running` indefinitely. The service restarts, opens run 14, and run 13 misreports
forever.

### Checkpointing

An in-progress run writes a checkpoint every two seconds, atomically. `open(path, "w")` truncates
immediately, so a crash mid-write leaves a corrupt file — worse than none. Instead: write to a temp
file, `fsync` it, then `os.replace()`. Rename is atomic, so the checkpoint is either entirely the
previous version or entirely the new one.

`fsync` matters: `flush()` only moves bytes into the OS page cache. Without it, atomic rename
survives a crashed process but not a power loss.

### Startup recovery

A surviving checkpoint or a run still marked `running` means the previous session died mid-flight.
Those runs are marked `failed` and the recovery is written to the audit log.

```mermaid
flowchart TD
    BOOT([systemd starts service]) --> HC{health ok?<br/>db + disk}
    HC -- no --> REFUSE[log + refuse to start]
    HC -- yes --> CKPT{checkpoint<br/>exists?}
    CKPT -- yes --> ORPH[mark orphaned run failed<br/>audit the recovery]
    CKPT -- no --> READY
    ORPH --> READY([ready — idle])
    READY --> RUN[run: checkpoint periodically]
    RUN --> DONE{finished?}
    DONE -- yes --> CLR[clear checkpoint]
    DONE -- crash --> BOOT
```

### Boot sequence

```mermaid
flowchart TD
    PWR([power on]) --> BL[bootloader loads kernel]
    BL --> K[kernel boots, mounts filesystems]
    K --> SD[systemd starts as PID 1]
    SD --> READ[reads unit files in<br/>/etc/systemd/system/]
    READ --> WANT{enabled for<br/>multi-user.target?}
    WANT -- yes --> DEP[wait for After= dependencies]
    DEP --> EXEC[ExecStart — service runs]
    EXEC --> WATCH[systemd supervises the process]
    WATCH --> DIED{exited or crashed?}
    DIED -- yes --> WAIT[wait RestartSec]
    WAIT --> EXEC
    DIED -- no --> WATCH
```

`enable` writes a symlink into `multi-user.target.wants/` — that is what starts the service at boot.
`Restart=always` is what restarts it when it dies. The two are independent.

| Choice | Rationale | Cost |
|---|---|---|
| Atomic write (temp + fsync + rename) | Never a partial checkpoint | Two syscalls instead of one |
| Checkpoint on interval, not per reading | Bounded write churn | Up to N seconds of state loss |
| Mark orphaned runs failed, don't resume | The data has an unknown gap | Discards a run that may have been sound |
| `Restart=always` | Self-healing with no application code | Masks crash loops without a start limit |
| Health check before start | Refuses to operate degraded | Another failure path at startup |

**Resume versus fail.** An orphaned run could be resumed. It isn't. The device was off for an
unknown interval, so the readings have a hole. A measurement with a silent gap is worse than an
honest failure.

### Verified behaviour

Crash recovery, after `kill -9` mid-run:

```
INFO health ok — 973639MB free
WARNING run 13 was interrupted — marking failed
```

systemd self-healing, after `kill -9` on the service:

```
00:44:06  Main process exited, code=killed, status=9/KILL
00:44:09  Scheduled restart job, restart counter is at 2.
00:44:09  Started edge-device.service
00:44:10  INFO health ok
```

Killed at :06, running at :09 with a new PID — `RestartSec=3`.

Full operator cycle under systemd supervision:

```
00:34:12  Started edge-device.service
00:34:12  INFO health ok — 973642MB free
00:34:23  INFO run 18 started
00:34:23  INFO run 18 reading on /dev/pts/9
00:34:38  INFO run 18 completed
00:35:19  INFO stopped cleanly
00:35:19  edge-device.service: Deactivated successfully.
00:35:19  Consumed 22.039s CPU time, 2.4M memory peak
```

Peak memory 2.4 MB — an embedded-appropriate footprint.

### Crash evidence in the audit trail

Every clean session terminates with a `shutdown` audit entry. The crashed session reads:

```
{"action": "startup", "device": "edge-01"}
{"action": "state_change", "to": "running"}
{"action": "status_change", ... "to": "normal"}
                                              ← no shutdown entry
{"action": "crash_recovery", "run_id": 13, "resolution": "marked failed"}
```

The absent shutdown entry is itself evidence of the failure, and the following session's
`crash_recovery` records the resolution. Both sit inside the hash chain, so neither can be edited
away afterwards.

---

## Control API

### Why the split

A single process owning both the device and the UI has a hard failure: the systemd service and a
manually launched UI both open `device.db`, and SQLite permits one writer. The workaround was to run
only one at a time, which is not a design.

One process owns the hardware and the database; everything else is a client.

| | Single process | Service + clients |
|---|---|---|
| Database writers | Contention if anything else runs | One owner |
| UI crash | Device stops | Device keeps running |
| Remote access | None | Any client can connect |
| Entry points | Device loop duplicated per entry point | One core, thin adapters |
| Complexity | Lower | An API boundary to design and defend |

```mermaid
flowchart TD
    subgraph SVC[device-service — owns hardware + DB]
      CORE[DeviceCore<br/>sensor · decision · FSM · store]
      API[FastAPI<br/>REST + WebSocket]
      CORE --- API
    end

    subgraph CLIENTS[clients]
      UI[Qt/QML touchscreen]
      TEST[test harness / curl]
    end

    UI -->|REST: login/start/abort/reset| API
    UI -->|WS: live events| API
    TEST --> API
```

### DeviceCore

`device_app/core.py` holds the device logic — sensor, decision engine, state machine, store,
recovery — with no UI dependency. It reports through plain callbacks (`on_reading`, `on_state`,
`on_progress`).

Each adapter supplies its own callbacks: the Qt bridge turns them into signals, the service turns
them into WebSocket events. The logic is written and tested once and cannot drift between entry
points. `ui/bridge.py` dropped from ~130 lines of mixed concerns to ~35 lines of adapter, and
`dashboard.qml` did not change at all when the UI became a network client — the seam held.

### Threading boundaries

The same problem appears three times with two different answers:

| Boundary | Rule | Bridge |
|---|---|---|
| worker thread → Qt UI | Only the UI thread touches the UI | `Signal.emit()` |
| worker thread → asyncio | Only the event loop touches asyncio objects | `loop.call_soon_threadsafe()` |
| asyncio WS → Qt UI | Both rules at once | `Signal.emit()` from the listener thread |

The event broker holds a bounded `asyncio.Queue` per subscriber and drops events when a queue fills:

```python
try:
    q.put_nowait(event)
except asyncio.QueueFull:
    log.warning("slow subscriber — dropping event")
```

Backpressure flows to the client, never into the device loop. A wedged laptop must not stall a
sensor.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/login` | Authenticate; returns a session token |
| POST | `/runs` | Start a run. **Requires `run:start`.** Honours `Idempotency-Key`. |
| POST | `/runs/{id}/abort` | Abort the active run. **Requires `run:abort`.** Idempotent. |
| POST | `/reset` | Return to idle. **Requires `run:reset`.** |
| GET | `/state` | Current FSM state + active run id. |
| GET | `/health` | Device health. **503** when unhealthy. |
| GET | `/runs/{id}/status` | Run row. |
| GET | `/runs/{id}/results` | Reading count + status breakdown. |
| WS | `/events` | Live readings, progress, state changes. |

`/health` returns a status code rather than 200-with-a-flag: monitors, load balancers, and
`curl --fail` all understand 503 without parsing a body. Commands require authentication; queries do
not — auth gates actions, not observations.

### Idempotency

A client sends `POST /runs`, the response is lost in transit, the client retries — and starts a
second run. That is a real fault, not a hypothetical.

Clients supply an `Idempotency-Key`; a repeat with the same key returns the original result instead
of acting again.

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Service
    C->>S: POST /runs (Idempotency-Key: abc)
    S->>S: start run 25
    S--xC: 200 {runId: 25}   ✗ response lost
    C->>S: POST /runs (Idempotency-Key: abc)   retry
    S->>S: key seen → do not start again
    S-->>C: 200 {runId: 25}   same answer
```

`DeviceClient.start()` generates a key by default — the safe path is the default path, not something
the caller has to remember.

Abort returns 200 even when nothing was aborted. Aborting a finished run is not an error: the caller
wanted it stopped and it is stopped. Idempotency is about the outcome, not about whether this
particular call did the work.

### Error mapping

Errors name the next action rather than describing the failure:

```
POST /runs while running    → 409  "a run is already in progress"
POST /runs while completed  → 409  "device is 'completed' — POST /reset before starting a new run"
```

An earlier version returned "already in progress" for both, because `core.start()` returns `None`
for both failure modes. A client reading that would wait indefinitely for a run that had already
finished. The API now checks actual state before mapping the error.

### Client resilience

`service/client.py` classifies failures rather than retrying blindly:

```mermaid
flowchart TD
    CALL[call] --> TRY{attempt}
    TRY -- 2xx --> OK([return])
    TRY -- 4xx --> FAIL[ClientError — do NOT retry]
    TRY -- 5xx / timeout / conn refused --> RETRY{attempts left?}
    RETRY -- no --> RAISE([ServiceUnavailable])
    RETRY -- yes --> WAIT[sleep 0.5 · 2^n]
    WAIT --> TRY
```

A 4xx means the request was invalid — retrying changes nothing and wastes the device's time. A 5xx
or a timeout means the service is struggling, which is worth retrying with backoff at 0.5s, 1s, 2s.
Every call carries a timeout; a client without one hangs forever on a wedged peer, and the hang is
the client's fault.

Observed with the service down:

```
WARNING GET /state failed (ConnectionError) — retry 1/2 in 0.5s
WARNING GET /state failed (ConnectionError) — retry 2/2 in 1.0s
handled cleanly: GET /state failed after 3 attempts
```

No hang, no traceback — a typed error the caller can act on.

### The UI as a client

`ui/bridge.py` holds a `DeviceClient` and a WebSocket listener thread. The listener reconnects on
its own:

```python
while self._running:
    try:
        async with websockets.connect(self.ws_url) as ws:
            ...
    except Exception:
        await asyncio.sleep(2)      # service restarted — reconnect
```

The status banner carries a connection indicator. An operator needs to distinguish "the device
reports normal" from "the device isn't talking to me" — without it, a dead connection is
indistinguishable from a healthy idle device.

### State across a process boundary

Splitting the UI out broke a guarantee from the state machine. QML initialized `runState` to its
default `"idle"`, which enabled START on a device that was actually `completed`:

```
13:55:02 INFO connected to device service
13:55:06 WARNING start rejected: device is 'completed' — POST /reset before starting a new run
```

The service does send current state on WebSocket connect, but QML loads a few milliseconds after the
listener connects, so it missed the emit. The FSM guarantee — that the UI cannot offer an illegal
action — held in-process and broke across the network.

**Push for changes, pull for initial state.** `Component.onCompleted` queries the bridge for current
state and connection status; signals keep them updated afterwards. Any late-joining consumer of an
async stream needs both.

### Service unit

```ini
[Unit]
Description=Edge Device Service
StartLimitBurst=5              # after 5 failures...
StartLimitIntervalSec=60       # ...in 60s, stop retrying and surface the fault
After=network.target

[Service]
Type=simple
User=chetan                    # least privilege
WorkingDirectory=/home/chetan/edge-device
ExecStart=/home/chetan/edge-device/.venv/bin/uvicorn service.control_api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3                   # delay prevents a crash loop saturating a core
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

No `DISPLAY`, no `WAYLAND_DISPLAY`, no `QT_QPA_PLATFORM` — a headless service needs no screen, which
is what made the earlier GUI unit awkward. `ExecStart` uses an absolute path to the virtualenv's
`uvicorn`; systemd provides no `PATH`, no shell, and no virtualenv activation.

On a production instrument the UI would also be a unit, rendering to the framebuffer via `eglfs`,
ordered `After=edge-device-service.service`.

---

## Authentication and roles

The device operates offline, so there is no identity provider to call. The device is its own: it
stores credentials, issues sessions, and enforces roles itself. Sensitive actions require a valid
session and an adequate role, and every one is recorded against the user who performed it.

```mermaid
flowchart TD
    LOGIN[POST /login: user + pw] --> LOOK{user exists?}
    LOOK -- no --> DUMMY[verify against dummy hash<br/>same CPU time] --> DENY401
    LOOK -- yes --> HASH{bcrypt verify}
    HASH -- fail --> DENY401[401 + audit: login_failed]
    HASH -- ok --> SESS[issue session token<br/>+ audit: login]
    SESS --> ACT[request carries bearer token]
    ACT --> VALID{session valid<br/>and unexpired?}
    VALID -- no --> DENY401
    VALID -- yes --> PERM{role has permission?}
    PERM -- no --> DENY403[403 + audit: denied]
    PERM -- yes --> DO[perform + audit: who did what]
```

### Password storage

Passwords are hashed with bcrypt and a per-user salt, never stored in plaintext. bcrypt is
deliberately slow — a tunable work factor (2^12 rounds here) makes one verification cost ~100ms,
invisible to a user and ruinous to an attacker running billions of guesses against a stolen hash
file. The salt, generated and embedded by bcrypt, means two users with the same password produce
different hashes, so one cracked hash does not compromise another and precomputed tables are useless.

A fast hash such as SHA-256 is the wrong tool here: fast hashes are for integrity, slow hashes are
for passwords.

### 401 versus 403

The two failures are distinct and map to distinct status codes:

| Code | Meaning | Cause |
|---|---|---|
| 401 Unauthorized | The device does not know who you are | Missing, invalid, or expired session |
| 403 Forbidden | The device knows who you are, and the answer is no | Valid session, insufficient role |

Conflating them produces a UI that shows a login prompt to someone already logged in who simply lacks
permission. In code the distinction is two exceptions — `AuthError` and `PermissionDenied` — mapped
to 401 and 403 respectively.

### Roles

| Role | Permissions |
|---|---|
| operator | run:start, run:abort, run:reset, results:view |
| supervisor | operator + config:edit |
| admin | all (wildcard) |

Roles map to permission sets; sensitive endpoints require a specific permission rather than a
specific user. Adding a capability means adding a permission to a role, not editing call sites.

### Sessions in memory

Sessions live in memory, not on disk. A reboot forces re-login, and no bearer token is ever written
to storage. For an instrument this is the correct default — a restart is exactly when
re-authentication should be required, and a token on disk is a credential on disk. Sessions expire
after inactivity, with a sliding window that activity extends.

| Choice | Rationale | Cost |
|---|---|---|
| bcrypt + per-user salt | Slow by design; rainbow tables useless | ~100ms per login |
| Local credentials | Works with no network | The device owns credential security |
| RBAC over per-user grants | Simple, auditable, scales to a team | Role→permission map must stay current |
| Sessions in memory | Reboot forces re-login; no token on disk | Sessions lost on restart (intended) |
| Vague "invalid credentials" | Does not reveal whether a username exists | Slightly worse UX |
| Dummy-hash on unknown user | Blocks timing-based username enumeration | One wasted bcrypt round |

### Provisioning

The first admin cannot come from a default password — hardcoded defaults are how devices are
compromised at scale. `tools/useradd.py` provisions users interactively: the password is read with
`getpass` (never echoed, never in the process argument list) and the creation is audited. On a real
instrument this happens at manufacture.

### Attribution

Each command records the acting user in the audit log:

```json
{"action": "login", "username": "chetan", "role": "admin"}
{"action": "run_started", "run_id": 54, "by": "chetan"}
```

Both entries sit inside the hash chain, so the record of who did what cannot be altered afterwards.
Failed logins are recorded too, giving a trail of unsuccessful attempts.

### FastAPI dependencies as declarative guards

Authorization is enforced with a dependency, not inline checks:

```python
@app.post("/runs")
def start_run(session = Depends(requires("run:start")), ...):
    ...
```

`requires("run:start")` resolves the bearer token to a session, checks the permission, and raises
401 or 403 before the endpoint body runs — the same pattern as a `@PreAuthorize` annotation, placed
declaratively in the signature. Read-only endpoints omit the dependency.

---

# Planned work

## Signed updates and removable-media security

Removable media is treated as untrusted input.

```mermaid
flowchart TD
    USB[update bundle from USB] --> VER{signature valid?}
    VER -- no --> REJECT[reject - do not install]
    VER -- yes --> STAGE[install to inactive B slot]
    STAGE --> SWITCH[switch active slot]
    SWITCH --> HC{health check ok?}
    HC -- yes --> DONE[update complete]
    HC -- no --> ROLL[rollback to A slot]
```

| | Checksum (SHA-256) | Digital signature |
|---|---|---|
| Detects accidental corruption | Yes | Yes |
| Proves origin | No — anyone can recompute it | Yes — requires the private key |
| Sufficient for updates | No | Required |

| | In-place update | A/B slots + rollback |
|---|---|---|
| Complexity | Lower | Two slots to manage |
| Failure mode | Can brick the device | Automatic rollback |

Signature verification precedes installation, never follows it. Nothing on the media is executed.

## Cross-layer debugging

The stack runs UI → control API → device service → firmware → hardware. Faults are localized by
bisecting at the boundaries rather than assuming.

```mermaid
flowchart TD
    BUG[symptom] --> UI2{UI showing<br/>good data wrong?}
    UI2 -- yes --> FIXUI[UI layer]
    UI2 -- no --> API2{API returns<br/>error?}
    API2 -- yes --> APP2{app logic or<br/>service?}
    API2 -- no --> SVC{service<br/>failing?}
    SVC -- yes --> FIXSVC[service/firmware]
    SVC -- no --> HW[hardware / bus]
```

Probes: `journalctl` and structured logs for the service, `curl` at the API boundary, `strace` /
`gdb` for a stuck process, bus inspection and `dmesg` at the hardware edge.

The instrumentation already exists — structured logging, the audit trail, health checks, a pure
testable decision function, and a `/health` endpoint.

---

# Tests

58 tests, no hardware required. The decision engine, state machine, config validator, audit chain,
recovery, and auth logic are pure enough to test directly.

```
tests/test_decision.py     thresholds, boundaries, worst-status-wins, invalid input
tests/test_config.py       validation, defaults, fail-closed, path resolution
tests/test_store.py        persistence, audit chain verification, tamper detection
tests/test_run_state.py    legal/illegal transitions, state preserved after rejection
tests/test_recovery.py     atomic writes, corrupt checkpoints, health checks
tests/test_auth.py         password hashing, salt, sessions, RBAC, 401/403 distinction
```

Notable:

- `test_tampering_is_detected` — asserts a security property directly.
- `test_failed_state_survives_illegal_attempt` — a rejected transition leaves state untouched rather
  than half-applied.
- `test_db_path_resolves_next_to_config` — pins the working-directory fault described above.
- `test_same_password_different_hashes` — proves the per-user salt works.
- `test_no_session_is_401_not_403` — pins the unauthenticated-versus-unauthorized distinction.

The domain logic is unit-tested. The integration layer — FastAPI endpoints, the WebSocket broker,
the Qt bridge — is verified manually (curl, the UI, killing the service); API-level tests with
FastAPI's `TestClient` against a mock core are the natural next step.

---

# Faults found during development

| Fault | Symptom | Root cause | Resolution |
|---|---|---|---|
| Duplicate database | Run counter reset to 1 | `open_db("device.db")` resolved against the working directory | Resolve paths relative to the config file |
| Inverted log levels | Nominal readings logged as ERROR | `!=` where `==` was intended; execution fell to `else` | Corrected branch order |
| Duplicate startup log | `config loaded` emitted twice | Duplicate call site | Removed |
| systemd crash loop | 122 restarts, each reported as "Started" | `QT_QPA_PLATFORM=xcb` forced a platform that cannot initialize in this session | Use `wayland`; add `StartLimitBurst` to surface the fault |
| Ignored unit keys | No effect, no error | `StartLimit*` moved to `[Unit]` in systemd 229 | Relocated; validated with `systemd-analyze verify` |
| Two writers | Service and UI both opened `device.db` | Both processes owned a `DeviceCore` | Split: the service owns the device, the UI is a client |
| Misleading 409 | "a run is already in progress" on a completed device | `core.start()` returns `None` for two different failure modes | API checks actual state before mapping the error |
| UI state race | Connection dot red; START offered on a `completed` device | QML loads after the WS connects and misses the initial state emit | Push for changes, pull for initial state |
| Import above path insert | `ModuleNotFoundError: auth` on service start | `from auth import` sat above `sys.path.insert` | Local imports must follow the path insert (same as an earlier Part) |

**Resolved.** `systemctl stop` previously orphaned in-flight runs: Qt's C++ event loop never returns
control to the Python interpreter, so the `SIGTERM` handler never ran. Splitting the device into a
headless service removed the problem — uvicorn handles the signal, the lifespan teardown calls
`core.shutdown()`, and an in-flight run is aborted and recorded before exit:

```
^C INFO: Shutting down
   INFO run 29 aborted
   INFO stopped cleanly
```

---

# Reference

## Commands

```bash
# environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# users
python3 tools/useradd.py alice operator

# run
uvicorn service.control_api:app --port 8000    # device service
python3 ui/main.py                             # touchscreen client (login screen)
pytest -v                                      # 58 tests

# systemd
sudo cp scripts/edge-device-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edge-device-service
systemctl status edge-device-service
systemd-analyze verify /etc/systemd/system/edge-device-service.service
journalctl -u edge-device-service -n 20 --no-pager

# API
curl -s -X POST localhost:8000/login -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"..."}'
curl -s -X POST localhost:8000/runs -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/health

# database
sqlite3 device.db "SELECT * FROM runs;"
sqlite3 device.db "SELECT id, event, substr(hash,1,12) FROM audit;"
sqlite3 device.db "PRAGMA integrity_check;"
```

## Reproducing the safety behaviours

```bash
# Fail-closed config — invert a band in config.json, then start the service:
uvicorn service.control_api:app --port 8000    # refuses to start, names the offending key

# Tamper detection:
sqlite3 device.db "UPDATE audit SET event='{\"action\":\"nothing\"}' WHERE id=1;"
# restart the service — reports: audit chain valid: False

# Crash recovery — start a run, then from another terminal:
pkill -9 -f uvicorn               # SIGKILL: no cleanup, simulates power loss
# restart the service — "run N was interrupted — marking failed"

# Auth — a command without a token is rejected:
curl -s -X POST localhost:8000/runs      # {"detail":"missing bearer token"}
```

## Design decisions, summarized

- **Python over C++** — iteration speed for application logic; C++ where timing or performance is
  critical.
- **Virtual over physical serial** — identical API, no electrical or timing realism.
- **SQLite over PostgreSQL** — serverless and single-file suits one offline device.
- **Qt over a web UI** — native rendering; a browser engine is heavy on a device.
- **FSM over flags** — illegal states become impossible; the UI gets one source of truth.
- **Service over monolith** — one owner of the hardware and the database; clients can crash, restart,
  and multiply without touching the device.
- **REST + WebSocket** — commands and queries over REST; live streams over WebSocket.
- **Idempotency keys on commands** — a lost response cannot start a second run.
- **Classify errors, don't blanket-retry** — 4xx surfaces immediately; 5xx and timeouts back off.
- **Bounded queues, drop on full** — backpressure reaches the client, never the sensor loop.
- **Push for changes, pull for initial state** — a late-joining consumer needs both.
- **The device is its own identity provider** — offline, credentials and sessions are local.
- **bcrypt over a fast hash** — deliberate slowness resists brute force; SHA-256 in a password field
  is a vulnerability.
- **401 vs 403 kept distinct** — unauthenticated and unauthorized are different failures.
- **Sessions in memory** — a reboot forces re-login; no bearer token touches disk.
- **Gate actions, not observations** — commands require auth; status and health reads stay open.
- **Worker threads over a single loop** — long work offloaded so the UI never blocks.
- **Fail closed over fail open** — a device that is down is obviously broken; one reporting confident
  nonsense is not.
- **Fail an interrupted run rather than resume it** — an honest failure beats a silent gap.

---

# Scope

Covers the embedded Linux application layer end to end: device-side architecture, serial I/O,
offline data integrity, touchscreen UI, thread isolation, service/client separation, authentication,
lifecycle management, and crash recovery.

Does not cover hardware bring-up, firmware, or bus-level debugging with a scope or logic analyzer —
those require physical hardware.

On running a GUI under systemd: a production instrument has no X server or Wayland compositor. Qt
renders directly to the framebuffer via `eglfs` or `linuxfb`. WSLg provides a desktop-style session,
which is precisely what a device lacks.

## Stack

Python · pyserial · Qt/QML (PySide6) · FastAPI · SQLite · bcrypt · systemd · pytest
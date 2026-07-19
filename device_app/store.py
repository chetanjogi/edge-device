import sqlite3, hashlib, json, time

GENESIS = "GENESIS"


def open_db(path="device.db"):
    """Open (or create) the device database with device-appropriate settings."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")     # crash-resilient
    conn.execute("PRAGMA foreign_keys=ON;")      # enforce relationships
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username  TEXT PRIMARY KEY,
            pw_hash   BLOB NOT NULL,
            role      TEXT NOT NULL,
            created   REAL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id      INTEGER PRIMARY KEY,
            started REAL,
            ended   REAL,
            status  TEXT
        );
        CREATE TABLE IF NOT EXISTS readings (
            id      INTEGER PRIMARY KEY,
            run_id  INTEGER,
            ts      REAL,
            t       REAL,
            p       REAL,
            h       REAL,
            status  TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );
        CREATE TABLE IF NOT EXISTS audit (
            id        INTEGER PRIMARY KEY,
            ts        REAL,
            event     TEXT,
            prev_hash TEXT,
            hash      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_readings_run ON readings(run_id);
    """)
    return conn


def integrity_ok(conn) -> bool:
    """Detect a corrupted database file — call this on startup."""
    return conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"


def start_run(conn) -> int:
    with conn:                                   # transaction
        cur = conn.execute(
            "INSERT INTO runs (started, status) VALUES (?, ?)",
            (time.time(), "running"))
        return cur.lastrowid

def create_user(conn, username, pw_hash, role):
    with conn:
        conn.execute("INSERT INTO users (username, pw_hash, role, created) "
                     "VALUES (?,?,?,?)", (username, pw_hash, role, time.time()))


def get_user(conn, username):
    return conn.execute(
        "SELECT username, pw_hash, role FROM users WHERE username=?",
        (username,)).fetchone()


def list_users(conn):
    return conn.execute("SELECT username, role, created FROM users").fetchall()

def end_run(conn, run_id, status):
    with conn:
        conn.execute("UPDATE runs SET ended=?, status=? WHERE id=?",
                     (time.time(), status, run_id))


def save_reading(conn, run_id, reading, status):
    """Persist one reading + its decision. Atomic."""
    with conn:                                   # BEGIN ... COMMIT (or ROLLBACK)
        conn.execute(
            "INSERT INTO readings (run_id, ts, t, p, h, status) VALUES (?,?,?,?,?,?)",
            (run_id, time.time(), reading.get("T"), reading.get("P"),
             reading.get("H"), status))


# ---------- tamper-evident audit log ----------

def _last_hash(conn) -> str:
    row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else GENESIS


def audit(conn, event: dict):
    """Append an event, chaining its hash to the previous entry."""
    prev = _last_hash(conn)
    ts = time.time()
    event_json = json.dumps(event, sort_keys=True)
    payload = json.dumps({"ts": ts, "event": event_json, "prev": prev},
                         sort_keys=True)
    h = hashlib.sha256(payload.encode()).hexdigest()
    with conn:
        conn.execute(
            "INSERT INTO audit (ts, event, prev_hash, hash) VALUES (?,?,?,?)",
            (ts, event_json, prev, h))


def verify_audit(conn) -> bool:
    """Recompute the whole chain. False means someone edited history."""
    prev = GENESIS
    rows = conn.execute(
        "SELECT ts, event, prev_hash, hash FROM audit ORDER BY id").fetchall()
    for ts, event_json, prev_hash, h in rows:
        if prev_hash != prev:
            return False                         # chain link broken
        payload = json.dumps({"ts": ts, "event": event_json, "prev": prev},
                             sort_keys=True)
        if hashlib.sha256(payload.encode()).hexdigest() != h:
            return False                         # entry itself altered
        prev = h
    return True

def orphaned_runs(conn):
    """Runs the DB still thinks are in progress — i.e. we crashed."""
    return conn.execute(
        "SELECT id, started FROM runs WHERE status='running'").fetchall()
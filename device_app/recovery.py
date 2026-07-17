import os, json, shutil, logging, time

log = logging.getLogger("device")


def checkpoint_path(db_path):
    """Live next to the database, not the working directory."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        "run_checkpoint.json")


def write_checkpoint(path, state: dict):
    """
    Atomic write: temp file + rename.

    A plain open(path,"w") truncates immediately — a crash mid-write leaves
    a corrupt file. os.replace() is atomic: the file is either entirely the
    old version or entirely the new one.
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())      # force to disk, not just the OS cache
    os.replace(tmp, path)         # atomic


def read_checkpoint(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"unreadable checkpoint, ignoring: {e}")
        return None


def clear_checkpoint(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def health(conn, db_path, min_free_mb=50):
    """Ask the device whether it is actually fit to operate."""
    db_ok = True
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as e:
        log.error(f"database unhealthy: {e}")
        db_ok = False

    free_mb = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free // (1024 * 1024)
    disk_ok = free_mb >= min_free_mb
    if not disk_ok:
        log.error(f"low disk: {free_mb}MB free, need {min_free_mb}MB")

    return {"db": db_ok, "disk_free_mb": free_mb, "disk": disk_ok,
            "ok": db_ok and disk_ok}
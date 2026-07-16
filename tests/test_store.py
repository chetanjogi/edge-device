import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from store import open_db, audit, verify_audit, save_reading, start_run


def fresh_db(tmp_path):
    return open_db(str(tmp_path / "test.db"))


def test_audit_chain_verifies(tmp_path):
    conn = fresh_db(tmp_path)
    audit(conn, {"action": "startup"})
    audit(conn, {"action": "run_started"})
    audit(conn, {"action": "config_changed", "key": "T.warn"})
    assert verify_audit(conn) is True


def test_tampering_is_detected(tmp_path):
    conn = fresh_db(tmp_path)
    audit(conn, {"action": "startup"})
    audit(conn, {"action": "run_started"})
    # Someone edits history directly in the DB:
    conn.execute("UPDATE audit SET event=? WHERE id=1",
                 ('{"action": "nothing_happened"}',))
    conn.commit()
    assert verify_audit(conn) is False          # chain catches it


def test_deletion_is_detected(tmp_path):
    conn = fresh_db(tmp_path)
    audit(conn, {"action": "a"})
    audit(conn, {"action": "b"})
    audit(conn, {"action": "c"})
    conn.execute("DELETE FROM audit WHERE id=2")
    conn.commit()
    assert verify_audit(conn) is False


def test_readings_persist(tmp_path):
    conn = fresh_db(tmp_path)
    run_id = start_run(conn)
    save_reading(conn, run_id, {"T": 24.5, "P": 1013.0, "H": 45.0}, "normal")
    count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    assert count == 1
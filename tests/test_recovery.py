import sys, os, json, sqlite3, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from recovery import (write_checkpoint, read_checkpoint, clear_checkpoint,
                      checkpoint_path, health)
from store import open_db


def test_write_then_read(tmp_path):
    p = str(tmp_path / "ckpt.json")
    write_checkpoint(p, {"run_id": 7, "started": 123.0})
    assert read_checkpoint(p) == {"run_id": 7, "started": 123.0}


def test_missing_checkpoint_returns_none(tmp_path):
    assert read_checkpoint(str(tmp_path / "nope.json")) is None


def test_corrupt_checkpoint_returns_none(tmp_path):
    p = tmp_path / "ckpt.json"
    p.write_text("{ half written")
    assert read_checkpoint(str(p)) is None      # ignored, not crashed


def test_no_temp_file_left_behind(tmp_path):
    p = str(tmp_path / "ckpt.json")
    write_checkpoint(p, {"run_id": 1})
    assert not os.path.exists(p + ".tmp")


def test_clear_is_idempotent(tmp_path):
    p = str(tmp_path / "ckpt.json")
    write_checkpoint(p, {"run_id": 1})
    clear_checkpoint(p)
    clear_checkpoint(p)                          # safe to call twice
    assert read_checkpoint(p) is None


def test_checkpoint_lives_next_to_db(tmp_path):
    db = str(tmp_path / "device.db")
    assert checkpoint_path(db) == str(tmp_path / "run_checkpoint.json")


def test_health_ok_on_fresh_db(tmp_path):
    db = str(tmp_path / "device.db")
    conn = open_db(db)
    h = health(conn, db)
    assert h["ok"] is True
    assert h["db"] is True


def test_health_fails_on_closed_db(tmp_path):
    db = str(tmp_path / "device.db")
    conn = open_db(db)
    conn.close()
    h = health(conn, db)
    assert h["db"] is False
    assert h["ok"] is False


def test_health_fails_on_low_disk(tmp_path):
    db = str(tmp_path / "device.db")
    conn = open_db(db)
    h = health(conn, db, min_free_mb=10**9)      # absurd requirement
    assert h["disk"] is False
    assert h["ok"] is False
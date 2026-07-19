import sys, os, time, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device_app"))

from auth import (hash_password, verify_password, authenticate, require,
                  Session, SessionStore, AuthError, PermissionDenied)
from store import open_db, create_user


def db(tmp_path):
    return open_db(str(tmp_path / "test.db"))


# ---------- hashing ----------

def test_password_verifies():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)


def test_wrong_password_fails():
    h = hash_password("secret123")
    assert not verify_password("secret124", h)


def test_hash_is_not_the_password():
    h = hash_password("secret123")
    assert b"secret123" not in h


def test_same_password_different_hashes():
    """Per-user salt — identical passwords must not produce identical hashes."""
    assert hash_password("same") != hash_password("same")


# ---------- authentication ----------

def test_authenticate_issues_session(tmp_path):
    conn = db(tmp_path)
    create_user(conn, "alice", hash_password("pw12345678"), "operator")
    s = authenticate(conn, SessionStore(), "alice", "pw12345678")
    assert s.username == "alice"
    assert s.role == "operator"
    assert len(s.token) > 20


def test_wrong_password_raises(tmp_path):
    conn = db(tmp_path)
    create_user(conn, "alice", hash_password("pw12345678"), "operator")
    with pytest.raises(AuthError):
        authenticate(conn, SessionStore(), "alice", "wrong")


def test_unknown_user_raises_same_error(tmp_path):
    """Must not reveal whether the username exists."""
    conn = db(tmp_path)
    with pytest.raises(AuthError, match="invalid credentials"):
        authenticate(conn, SessionStore(), "nobody", "whatever")


# ---------- sessions ----------

def test_session_expires():
    s = Session("alice", "operator", ttl=0)
    time.sleep(0.01)
    assert not s.valid()


def test_touch_extends_session():
    s = Session("alice", "operator", ttl=1)
    time.sleep(0.5)
    s.touch()
    time.sleep(0.6)
    assert s.valid()          # would have expired without the touch


def test_store_drops_expired_sessions():
    store = SessionStore()
    s = store.create("alice", "operator", ttl=0)
    time.sleep(0.01)
    assert store.get(s.token) is None


def test_revoke_kills_session():
    store = SessionStore()
    s = store.create("alice", "operator")
    store.revoke(s.token)
    assert store.get(s.token) is None


def test_unknown_token_returns_none():
    assert SessionStore().get("not-a-real-token") is None


# ---------- authorization ----------

def test_operator_can_start_run():
    require(Session("alice", "operator"), "run:start")     # no raise


def test_operator_cannot_edit_config():
    with pytest.raises(PermissionDenied, match="operator"):
        require(Session("alice", "operator"), "config:edit")


def test_supervisor_can_edit_config():
    require(Session("bob", "supervisor"), "config:edit")


def test_admin_wildcard_allows_anything():
    require(Session("root", "admin"), "config:edit")
    require(Session("root", "admin"), "some:future:permission")


def test_no_session_is_401_not_403():
    """Unauthenticated and unauthorized are different failures."""
    with pytest.raises(AuthError):
        require(None, "run:start")


def test_expired_session_is_401():
    with pytest.raises(AuthError):
        require(Session("alice", "admin", ttl=-1), "run:start")


def test_unknown_role_has_no_permissions():
    with pytest.raises(PermissionDenied):
        require(Session("mallory", "wizard"), "run:start")
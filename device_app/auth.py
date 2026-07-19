import time, secrets, logging
import bcrypt

log = logging.getLogger("device")


PERMISSIONS = {
    "operator":   {"run:start", "run:abort", "run:reset", "results:view"},
    "supervisor": {"run:start", "run:abort", "run:reset", "results:view",
                   "config:edit"},
    "admin":      {"*"},
}

ROLES = set(PERMISSIONS)

# A real bcrypt hash of random bytes. When a username doesn't exist we verify
# against this so the request burns the same CPU as a real check — otherwise
# response timing reveals which usernames are valid.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt())


class AuthError(Exception):
    """Authentication failed. Deliberately vague — never leak which part."""


class PermissionDenied(Exception):
    """Authenticated, but the role doesn't permit this action."""


def hash_password(pw: str) -> bytes:
    """Salted and slow. bcrypt generates and embeds the salt."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt())


def verify_password(pw: str, hashed: bytes) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed)


class Session:
    def __init__(self, username: str, role: str, ttl: int = 900):
        self.username = username
        self.role = role
        self.ttl = ttl
        self.token = secrets.token_urlsafe(32)     # CSPRNG, not uuid4
        self.expires = time.time() + ttl

    def valid(self) -> bool:
        return time.time() < self.expires

    def touch(self):
        """Sliding expiry — activity keeps the session alive."""
        self.expires = time.time() + self.ttl

    def can(self, perm: str) -> bool:
        perms = PERMISSIONS.get(self.role, set())
        return "*" in perms or perm in perms


class SessionStore:
    """
    In memory on purpose: a reboot forces re-login, and no bearer token is
    ever written to disk.
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, username, role, ttl=900) -> Session:
        s = Session(username, role, ttl)
        self._sessions[s.token] = s
        return s

    def get(self, token) -> Session | None:
        s = self._sessions.get(token)
        if s is None:
            return None
        if not s.valid():
            del self._sessions[token]      # expired — clean it up
            return None
        return s

    def revoke(self, token):
        self._sessions.pop(token, None)

    def count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.valid())


def authenticate(conn, sessions: SessionStore, username: str,
                 password: str) -> Session:
    """Verify credentials and issue a session. Raises AuthError on failure."""
    from store import get_user

    row = get_user(conn, username)
    if row is None:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)   # constant-ish time
        raise AuthError("invalid credentials")

    uname, pw_hash, role = row
    if not verify_password(password, pw_hash):
        raise AuthError("invalid credentials")

    return sessions.create(uname, role)


def require(session: Session | None, perm: str) -> Session:
    """Guard a sensitive action. Raises AuthError (401) or PermissionDenied (403)."""
    if session is None or not session.valid():
        raise AuthError("no valid session")
    if not session.can(perm):
        raise PermissionDenied(f"role '{session.role}' lacks '{perm}'")
    session.touch()
    return session
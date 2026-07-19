import time, uuid, logging, requests

log = logging.getLogger("device")


class ServiceUnavailable(Exception):
    """The device service could not be reached after retries."""


class DeviceClient:
    def __init__(self, base="http://localhost:8000", timeout=2.0, retries=3):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.token = None

    def _call(self, method, path, retry_safe=True, **kw):
        url = f"{self.base}{path}"
        last = None

        for attempt in range(self.retries if retry_safe else 1):
            try:
                r = requests.request(method, url, timeout=self.timeout, **kw)

                if 400 <= r.status_code < 500:
                    # Our fault. Retrying won't help — surface it immediately.
                    detail = r.json().get("detail", r.text)
                    raise ClientError(r.status_code, detail)

                r.raise_for_status()          # 5xx falls through to retry
                return r.json()

            except ClientError:
                raise                          # never retry a 4xx
            except requests.RequestException as e:
                last = e
                if attempt < self.retries - 1:
                    delay = 0.5 * (2 ** attempt)      # 0.5s, 1s, 2s
                    log.warning(f"{method} {path} failed ({e.__class__.__name__}) "
                                f"— retry {attempt + 1}/{self.retries - 1} in {delay}s")
                    time.sleep(delay)

        raise ServiceUnavailable(f"{method} {path} failed after "
                                 f"{self.retries} attempts: {last}")

    # ---------- safe to retry ----------

    def health(self):
        return self._call("GET", "/health")

    def state(self):
        return self._call("GET", "/state")

    def run_status(self, run_id):
        return self._call("GET", f"/runs/{run_id}/status")

    def run_results(self, run_id):
        return self._call("GET", f"/runs/{run_id}/results")

    def is_alive(self) -> bool:
        try:
            return self.health().get("ok", False)
        except Exception:
            return False

    # ---------- commands ----------

    def start(self, key=None):
        """Idempotency key makes this safe to retry — without one, it is not."""
        key = key or str(uuid.uuid4())
        return self._call("POST", "/runs", retry_safe=True,
                          headers={"Idempotency-Key": key, **self._auth_header()})

    def abort(self, run_id):
        return self._call("POST", f"/runs/{run_id}/abort", headers=self._auth_header())   # idempotent by design

    def reset(self):
        return self._call("POST", "/reset", headers=self._auth_header())   # idempotent by design

    def login(self, username, password):
        r = self._call("POST", "/login", retry_safe=False,
                       json={"username": username, "password": password})
        self.token = r["token"]
        return r

    def _auth_header(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

class ClientError(Exception):
    """4xx — the request was invalid. Retrying will not help."""
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(f"{status}: {detail}")
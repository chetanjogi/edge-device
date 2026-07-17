import os, sys, json, asyncio, threading, logging
from PySide6.QtCore import QObject, Signal, Slot

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "service"))

import websockets
from client import DeviceClient, ClientError, ServiceUnavailable

log = logging.getLogger("device")


class DeviceBridge(QObject):
    """Adapter: the UI is now an API client, not the device."""

    reading = Signal(float, float, float, str, str)
    stateChanged = Signal(str)
    progress = Signal(int)
    connectionChanged = Signal(bool)          # NEW: the service can be gone
    errorOccurred = Signal(str)               # NEW: surface 4xx to the operator

    def __init__(self, base="http://localhost:8000"):
        super().__init__()
        self.client = DeviceClient(base)
        self.ws_url = base.replace("http://", "ws://") + "/events"
        self._running = True
        self._connected = False
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    # ---------- operator actions (UI thread → REST) ----------

    @Slot()
    def startRun(self):
        try:
            r = self.client.start()
            log.info(f"started run {r['runId']}")
        except ClientError as e:
            log.warning(f"start rejected: {e.detail}")
            self.errorOccurred.emit(e.detail)
        except ServiceUnavailable as e:
            log.error(f"service unreachable: {e}")
            self.errorOccurred.emit("device service unreachable")

    @Slot()
    def abortRun(self):
        try:
            st = self.client.state()
            if st["activeRunId"]:
                self.client.abort(st["activeRunId"])
        except (ClientError, ServiceUnavailable) as e:
            self.errorOccurred.emit(str(e))

    @Slot()
    def resetRun(self):
        try:
            self.client.reset()
        except (ClientError, ServiceUnavailable) as e:
            self.errorOccurred.emit(str(e))

    @Slot(result=bool)
    def isConnected(self):
        return self._connected
    
    
    @Slot(result=str)
    def currentState(self):
        try:
            return self.client.state()["state"]
        except Exception:
            return "idle"
        
        
        
    # ---------- event listener (asyncio thread → Qt signals) ----------

    def _listen(self):
        asyncio.run(self._listen_async())

    async def _listen_async(self):
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._set_connected(True)
                    log.info("connected to device service")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._dispatch(json.loads(raw))
            except Exception as e:
                if self._running:
                    self._set_connected(False)
                    log.warning(f"event stream lost ({e.__class__.__name__}) — "
                                f"reconnecting in 2s")
                    await asyncio.sleep(2)

    def _set_connected(self, ok: bool):
        if ok != self._connected:
            self._connected = ok
            self.connectionChanged.emit(ok)

    def _dispatch(self, e: dict):
        t = e.get("type")
        if t == "reading":
            self.reading.emit(e.get("T") or 0.0, e.get("P") or 0.0, e.get("H") or 0.0,
                              e["status"], "; ".join(e["reasons"]))
        elif t == "state":
            self.stateChanged.emit(e["state"])
        elif t == "progress":
            self.progress.emit(e["percent"])

    def stop(self):
        self._running = False
        log.info("ui stopped")
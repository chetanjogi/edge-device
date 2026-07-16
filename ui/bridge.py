import os, sys, pty, threading, time, logging, serial
from PySide6.QtCore import QObject, Signal, Slot

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "sensor_sim"))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))

import sensor
from reader import parse_line
from decision import decide
from run_state import RunStateMachine, RunState, IllegalTransition
from store import open_db, integrity_ok, start_run, end_run, save_reading, audit

log = logging.getLogger("device")


class DeviceBridge(QObject):
    """The model QML binds to. Owns the FSM and the per-run worker thread."""

    reading = Signal(float, float, float, str, str)   # T, P, H, status, reasons
    stateChanged = Signal(str)
    progress = Signal(int)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.rules = config["rules"]
        self.duration = config["run_duration_s"]

        self.conn = open_db(config["db_path"])
        if not integrity_ok(self.conn):
            raise RuntimeError("database integrity check failed")

        self.fsm = RunStateMachine()
        self._lock = threading.Lock()      # guards FSM transitions
        self._abort = threading.Event()    # cross-thread abort flag
        self._thread = None
        self.run_id = None

        audit(self.conn, {"action": "startup", "device": config["device_id"]})

    # ---------- state ----------

    def _transition(self, new: RunState) -> bool:
        """Thread-safe transition. Returns False if it was illegal."""
        with self._lock:
            try:
                self.fsm.to(new)
            except IllegalTransition as e:
                log.warning(str(e))
                return False
        audit(self.conn, {"action": "state_change", "to": new.value})
        self.stateChanged.emit(new.value)
        return True

    @Slot(result=str)
    def currentState(self):
        return self.fsm.state.value

    # ---------- operator actions (called from QML, on the UI thread) ----------

    @Slot()
    def startRun(self):
        if not self._transition(RunState.RUNNING):
            return                                   # already running
        self._abort.clear()
        self.run_id = start_run(self.conn)
        log.info(f"run {self.run_id} started")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @Slot()
    def abortRun(self):
        with self._lock:
            if self.fsm.state != RunState.RUNNING:
                log.warning("abort ignored — no run in progress")
                return
        log.info("abort requested")
        self._abort.set()          # worker notices and exits cleanly

    @Slot()
    def resetRun(self):
        if self._transition(RunState.IDLE):
            self.progress.emit(0)

    # ---------- worker thread ----------

    def _open_port(self):
        primary, secondary = pty.openpty()
        threading.Thread(target=sensor.run, args=(primary,),
                         kwargs={"hz": self.config["sample_hz"]},
                         daemon=True).start()
        return serial.Serial(os.ttyname(secondary), 9600, timeout=1)

    def _loop(self):
        """Runs on the WORKER thread. Reaches the UI only via signals."""
        ser = self._open_port()
        log.info(f"run {self.run_id} reading on {ser.port}")
        started = time.time()
        last = None

        try:
            while not self._abort.is_set():
                elapsed = time.time() - started
                if elapsed >= self.duration:
                    break
                self.progress.emit(int(elapsed / self.duration * 100))

                raw = ser.readline().decode(errors="ignore")
                if not raw:
                    continue
                r = parse_line(raw)
                if not r:
                    continue

                result = decide(r, self.rules)
                save_reading(self.conn, self.run_id, r, result.status.value)

                if result.status != last:
                    audit(self.conn, {"action": "status_change",
                                      "from": last.value if last else None,
                                      "to": result.status.value,
                                      "reasons": result.reasons})
                    last = result.status

                self.reading.emit(r.get("T", 0.0), r.get("P", 0.0), r.get("H", 0.0),
                                  result.status.value, "; ".join(result.reasons))

        except Exception:
            log.exception("device loop failed")
            ser.close()
            end_run(self.conn, self.run_id, "failed")
            self._transition(RunState.FAILED)
            return

        ser.close()

        if self._abort.is_set():
            end_run(self.conn, self.run_id, "aborted")
            self._transition(RunState.FAILED)
            log.info(f"run {self.run_id} aborted")
        else:
            self.progress.emit(100)
            end_run(self.conn, self.run_id, "completed")
            self._transition(RunState.COMPLETED)
            log.info(f"run {self.run_id} completed")

    # ---------- shutdown ----------

    def stop(self):
        self._abort.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        audit(self.conn, {"action": "shutdown"})
        self.conn.close()
        log.info("stopped cleanly")
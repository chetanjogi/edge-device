import os, sys, pty, threading, logging, serial
from PySide6.QtCore import QObject, Signal

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "sensor_sim"))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))

import sensor
from reader import parse_line
from decision import decide
from store import open_db, integrity_ok, start_run, end_run, save_reading, audit

log = logging.getLogger("device")


class DeviceBridge(QObject):
    """The model QML binds to. Owns the device loop on a worker thread."""

    # Signals are the ONLY safe path from worker thread -> UI thread.
    reading = Signal(float, float, float, str, str)   # T, P, H, status, reasons

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.rules = config["rules"]
        self.conn = open_db(config["db_path"])
        if not integrity_ok(self.conn):
            raise RuntimeError("database integrity check failed")
        self.run_id = None
        self._running = False
        self._thread = None

    def start(self):
        audit(self.conn, {"action": "startup", "device": self.config["device_id"]})
        self.run_id = start_run(self.conn)
        log.info(f"run {self.run_id} started")
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        log.info("stopping device loop")
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)          # let it finish cleanly
        end_run(self.conn, self.run_id, "completed")
        audit(self.conn, {"action": "shutdown"})
        self.conn.close()
        log.info("stopped cleanly")

    def _open_port(self):
        primary, secondary = pty.openpty()
        threading.Thread(target=sensor.run, args=(primary,),
                         kwargs={"hz": self.config["sample_hz"]},
                         daemon=True).start()
        return serial.Serial(os.ttyname(secondary), 9600, timeout=1)

    def _loop(self):
        """Runs on the WORKER thread. Never touches the UI directly."""
        ser = self._open_port()
        log.info(f"device loop running on {ser.port}")
        last = None

        while self._running:
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

            # Hand off to the UI thread. Qt queues this safely.
            self.reading.emit(r.get("T", 0.0), r.get("P", 0.0), r.get("H", 0.0),
                              result.status.value, "; ".join(result.reasons))

        ser.close()
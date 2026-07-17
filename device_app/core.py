import os, sys, pty, threading, time, logging, serial

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "sensor_sim"))

import sensor
from reader import parse_line
from decision import decide
from run_state import RunStateMachine, RunState, IllegalTransition
from store import (open_db, integrity_ok, start_run, end_run, save_reading,
                   audit, orphaned_runs)
from recovery import (checkpoint_path, write_checkpoint, read_checkpoint,
                      clear_checkpoint, health)

log = logging.getLogger("device")


class DeviceCore:
    """
    Device logic with no UI dependency.

    Callbacks (all optional, called from the worker thread):
      on_reading(dict)        — T/P/H + status + reasons
      on_state(str)           — run state changed
      on_progress(int)        — 0..100
    """

    def __init__(self, config, on_reading=None, on_state=None, on_progress=None):
        self.config = config
        self.rules = config["rules"]
        self.duration = config["run_duration_s"]

        self.on_reading = on_reading or (lambda d: None)
        self.on_state = on_state or (lambda s: None)
        self.on_progress = on_progress or (lambda p: None)

        self.conn = open_db(config["db_path"])
        if not integrity_ok(self.conn):
            raise RuntimeError("database integrity check failed")

        self.fsm = RunStateMachine()
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._thread = None
        self.run_id = None
        self.ckpt = checkpoint_path(config["db_path"])

        audit(self.conn, {"action": "startup", "device": config["device_id"]})
        self._recover()

    # ---------- lifecycle ----------

    def _recover(self):
        h = health(self.conn, self.config["db_path"])
        if not h["ok"]:
            raise RuntimeError(f"health check failed: {h}")
        log.info(f"health ok — {h['disk_free_mb']}MB free")

        ckpt = read_checkpoint(self.ckpt)
        orphans = orphaned_runs(self.conn)
        if not ckpt and not orphans:
            return

        for run_id, started in orphans:
            log.warning(f"run {run_id} was interrupted — marking failed")
            end_run(self.conn, run_id, "failed")
            audit(self.conn, {"action": "crash_recovery", "run_id": run_id,
                              "resolution": "marked failed",
                              "reason": "device restarted mid-run"})
        clear_checkpoint(self.ckpt)

    def health(self):
        return health(self.conn, self.config["db_path"])

    def state(self):
        return self.fsm.state.value

    # ---------- commands ----------

    def _transition(self, new: RunState) -> bool:
        with self._lock:
            try:
                self.fsm.to(new)
            except IllegalTransition as e:
                log.warning(str(e))
                return False
        audit(self.conn, {"action": "state_change", "to": new.value})
        self.on_state(new.value)
        return True

    def start(self):
        if not self._transition(RunState.RUNNING):
            return None                        # already running
        self._abort.clear()
        self.run_id = start_run(self.conn)
        write_checkpoint(self.ckpt, {"run_id": self.run_id, "started": time.time()})
        log.info(f"run {self.run_id} started")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self.run_id

    def abort(self) -> bool:
        with self._lock:
            if self.fsm.state != RunState.RUNNING:
                log.warning("abort ignored — no run in progress")
                return False
        log.info("abort requested")
        self._abort.set()
        return True

    def reset(self) -> bool:
        if self._transition(RunState.IDLE):
            self.on_progress(0)
            return True
        return False

    # ---------- worker ----------

    def _open_port(self):
        primary, secondary = pty.openpty()
        threading.Thread(target=sensor.run, args=(primary,),
                         kwargs={"hz": self.config["sample_hz"]},
                         daemon=True).start()
        return serial.Serial(os.ttyname(secondary), 9600, timeout=1)

    def _loop(self):
        ser = self._open_port()
        log.info(f"run {self.run_id} reading on {ser.port}")
        started, last, last_ckpt = time.time(), None, 0.0

        try:
            while not self._abort.is_set():
                elapsed = time.time() - started
                if elapsed >= self.duration:
                    break
                self.on_progress(int(elapsed / self.duration * 100))

                now = time.time()
                if now - last_ckpt > 2.0:
                    write_checkpoint(self.ckpt, {"run_id": self.run_id,
                                                 "started": started,
                                                 "elapsed": elapsed})
                    last_ckpt = now

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

                self.on_reading({"T": r.get("T"), "P": r.get("P"), "H": r.get("H"),
                                 "status": result.status.value,
                                 "reasons": result.reasons})

        except Exception:
            log.exception("device loop failed")
            ser.close()
            end_run(self.conn, self.run_id, "failed")
            clear_checkpoint(self.ckpt)
            self._transition(RunState.FAILED)
            return

        ser.close()
        clear_checkpoint(self.ckpt)

        if self._abort.is_set():
            end_run(self.conn, self.run_id, "aborted")
            self._transition(RunState.FAILED)
            log.info(f"run {self.run_id} aborted")
        else:
            self.on_progress(100)
            end_run(self.conn, self.run_id, "completed")
            self._transition(RunState.COMPLETED)
            log.info(f"run {self.run_id} completed")

    def shutdown(self):
        self._abort.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        audit(self.conn, {"action": "shutdown"})
        self.conn.close()
        log.info("stopped cleanly")
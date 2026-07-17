import os, sys, logging
from PySide6.QtCore import QObject, Signal, Slot

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))

from core import DeviceCore

log = logging.getLogger("device")


class DeviceBridge(QObject):
    """Adapter: turns DeviceCore callbacks into Qt signals."""

    reading = Signal(float, float, float, str, str)
    stateChanged = Signal(str)
    progress = Signal(int)

    def __init__(self, config):
        super().__init__()
        self.core = DeviceCore(
            config,
            on_reading=lambda d: self.reading.emit(
                d.get("T") or 0.0, d.get("P") or 0.0, d.get("H") or 0.0,
                d["status"], "; ".join(d["reasons"])),
            on_state=lambda s: self.stateChanged.emit(s),
            on_progress=lambda p: self.progress.emit(p),
        )

    @Slot(result=str)
    def currentState(self):
        return self.core.state()

    @Slot()
    def startRun(self):
        self.core.start()

    @Slot()
    def abortRun(self):
        self.core.abort()

    @Slot()
    def resetRun(self):
        self.core.reset()

    def stop(self):
        self.core.shutdown()
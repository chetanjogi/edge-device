import sys, os, logging
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)

from bridge import DeviceBridge

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("device")


def main():
    app = QGuiApplication(sys.argv)
    bridge = DeviceBridge(os.environ.get("DEVICE_URL", "http://localhost:8000"))

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("device", bridge)
    engine.load(os.path.join(_here, "dashboard.qml"))
    if not engine.rootObjects():
        log.error("failed to load QML")
        return 1

    app.aboutToQuit.connect(bridge.stop)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
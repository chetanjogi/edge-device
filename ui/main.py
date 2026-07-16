import sys, os, logging
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))

from config import load_config, ConfigError
from bridge import DeviceBridge

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("device")


def main():
    try:
        config = load_config(os.path.join(_here, "..", "config.json"))
    except ConfigError as e:
        log.error(f"invalid configuration: {e}")
        log.error("refusing to start")
        return 1

    log.info(f"config loaded for device '{config['device_id']}'")

    app = QGuiApplication(sys.argv)
    bridge = DeviceBridge(config)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("device", bridge)   # expose to QML
    engine.load(os.path.join(_here, "dashboard.qml"))
    if not engine.rootObjects():
        log.error("failed to load QML")
        return 1

    app.aboutToQuit.connect(bridge.stop)  # clean shutdown on window close
    return app.exec()                     # Qt event loop owns the main thread


if __name__ == "__main__":
    sys.exit(main())
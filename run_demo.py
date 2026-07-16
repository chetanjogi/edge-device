import os, pty, sys, threading, serial, logging, signal , json


sys.path.insert(0, "sensor_sim")
sys.path.insert(0, "device_app")
import sensor
from reader import parse_line
from decision import decide, Status

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("device")

running = True

def shutdown(signum, frame):
    global running
    log.info("shutdown signal received")
    running = False

def open_sensor_port():
    """Create the virtual serial pair and start the simulated sensor."""
    primary, secondary = pty.openpty()
    threading.Thread(target=sensor.run, args=(primary,), daemon=True).start()
    return serial.Serial(os.ttyname(secondary), 9600, timeout=1)

def handle(reading,rules):
    """The seam — everything downstream hooks in here."""
    result = decide(reading, rules)
    msg = f"[{result.status.value}] T={reading['T']:.1f} P={reading['P']:.1f} H={reading['H']:.1f}"
    if result.status == Status.NORMAL:
        log.info(msg)
    elif result.status == Status.WARNING:
        log.warning(f"{msg} — {', '.join(result.reasons)}")
    else:
        log.error(f"{msg} — {', '.join(result.reasons)}")
        
def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    config = json.load(open("config.json"))
    rules = config["rules"]

    ser = open_sensor_port()
    log.info(f"device started on {ser.port}")

    while running:
        raw = ser.readline().decode(errors="ignore")
        if not raw:
            continue                    # timeout, no data — keep going
        reading = parse_line(raw)
        if not reading:
            continue                    # unparseable — skip, don't crash
        handle(reading,rules)

    ser.close()
    log.info("stopped cleanly")

if __name__ == "__main__":
    main()
import os, pty, sys, threading, serial, logging, signal , json


sys.path.insert(0, "sensor_sim")
sys.path.insert(0, "device_app")
import sensor
from reader import parse_line
from decision import decide, Status
from store import open_db, integrity_ok, start_run, end_run, save_reading, audit, verify_audit
from config import load_config, ConfigError

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

def handle(reading, rules, conn, run_id, last_status):
    """The seam — decide, log, persist."""
    result = decide(reading, rules)
    msg = f"[{result.status.value}] T={reading['T']:.1f} P={reading['P']:.1f} H={reading['H']:.1f}"

    if result.status == Status.NORMAL:
        log.info(msg)
    elif result.status == Status.WARNING:
        log.warning(f"{msg} — {', '.join(result.reasons)}")
    else:
        log.error(f"{msg} — {', '.join(result.reasons)}")

    save_reading(conn, run_id, reading, result.status.value)

    # Only audit *changes* in status, not every reading — audit is for events.
    if result.status != last_status:
        audit(conn, {"action": "status_change",
                     "from": last_status.value if last_status else None,
                     "to": result.status.value,
                     "reasons": result.reasons})
    return result.status
        
def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        config = load_config()
    except ConfigError as e:
        log.error(f"invalid configuration: {e}")
        log.error("refusing to start — fix config.json and restart")
        return                      # fail closed
    rules = config["rules"]
    log.info(f"config loaded for device '{config['device_id']}'")

    conn = open_db(config["db_path"])
    if not integrity_ok(conn):
        log.error("database integrity check FAILED — restore from backup")
        return
    log.info(f"database ok, audit chain valid: {verify_audit(conn)}")

    audit(conn, {"action": "startup", "device": config["device_id"]})
    run_id = start_run(conn)
    log.info(f"run {run_id} started")

    ser = open_sensor_port()
    log.info(f"device started on {ser.port}")

    last_status = None
    while running:
        raw = ser.readline().decode(errors="ignore")
        if not raw:
            continue
        reading = parse_line(raw)
        if not reading:
            continue
        last_status = handle(reading, rules, conn, run_id, last_status)

    end_run(conn, run_id, "completed")
    audit(conn, {"action": "shutdown"})
    ser.close()
    conn.close()
    log.info("stopped cleanly")

if __name__ == "__main__":
    main()
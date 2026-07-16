import os, pty, sys, threading, serial

sys.path.insert(0, "sensor_sim")
sys.path.insert(0, "device_app")
import sensor
from reader import parse_line

def main(limit=8):
    primary, secondary = pty.openpty()          # 1. create the virtual serial pair

    threading.Thread(target=sensor.run,          # 2. sensor writes into one end
                     args=(primary,), daemon=True).start()

    port = os.ttyname(secondary)                 # 3. the other end is a device path
    ser = serial.Serial(port, 9600, timeout=1)   #    open it like real hardware
    print(f"[setup] opened {port}\n")

    count = 0
    while count < limit:                         # 4. read through the real serial path
        raw = ser.readline().decode(errors="ignore")
        data = parse_line(raw)
        if data:
            print(f"[device] T={data['T']:.1f}C  P={data['P']:.1f}hPa  H={data['H']:.1f}%")
            count += 1
    ser.close()

if __name__ == "__main__":
    main()
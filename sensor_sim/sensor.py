import os, time, random,  math

def reading(t):
    temp = 24.0 + 2.0 * math.sin(t / 10.0) + random.uniform(-0.3, 0.3)
    pressure = 1013.0 + 5.0 * math.sin(t / 25.0) + random.uniform(-0.5, 0.5)
    humidity = 40.0 + 5.0 * math.cos(t / 15.0) + random.uniform(-0.4, 0.4)
    return f"T={temp:.1f},P={pressure:.1f},H={humidity:.1f}\n"

def run(fd,hz=4):
    t = 0
    while True:
        os.write(fd,reading(t).encode())
        t += 1
        time.sleep(1/hz)
#!/usr/bin/env python3
"""Upload with SaveAIO BEFORE the OLED toggle, so firmware reads GIF settings on init."""
import os
import sys
import time
import glob
import io
from PIL import Image

REPORT_SIZE = 65


def find_device():
    for f in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        if '00001887' in open(f).read():
            return '/dev/' + f.split('/')[4]
    return None


def send(fd, data):
    buf = bytearray(REPORT_SIZE)
    buf[0] = 0xEC
    for i, b in enumerate(data[:64]):
        buf[1 + i] = b
    os.write(fd, bytes(buf))


def recv(fd, timeout=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return os.read(fd, REPORT_SIZE)
        except BlockingIOError:
            time.sleep(0.01)
    return None


def drain(fd):
    while True:
        try:
            os.read(fd, REPORT_SIZE)
        except BlockingIOError:
            break


dev = find_device()
if not dev:
    print("Device not found!")
    sys.exit(1)

fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
drain(fd)

# Create solid CYAN GIF (very different from default red)
print("Creating solid CYAN GIF...")
img = Image.new('RGB', (160, 128), (0, 255, 255))
buf = io.BytesIO()
img.save(buf, format='GIF')
gif_data = buf.getvalue()
print(f"  {len(gif_data)} bytes")

# === PHASE 1: Upload file (while display is showing default) ===
print("\n--- Upload to slot 2 ---")
send(fd, [0x51, 0xA0])
time.sleep(0.05)
send(fd, [0x6B, 0x01, 0x00, 0x02])
time.sleep(0.05)
send(fd, [0x6C, 0x01])
time.sleep(0.05)
send(fd, [0x6C, 0x03])
time.sleep(0.05)
send(fd, [0x6C, 0x04])
time.sleep(0.05)
offset = 0
while offset < len(gif_data):
    chunk = gif_data[offset:offset + 62]
    send(fd, [0x6E, len(chunk)] + list(chunk))
    offset += len(chunk)
send(fd, [0x6C, 0x05])
time.sleep(0.1)
send(fd, [0x6C, 0xFF])
time.sleep(0.1)
send(fd, [0x51, 0x10, 0x01, 0x02])
time.sleep(0.2)
print("  Upload done")

# === PHASE 2: SaveAIO settings FIRST (before toggle) ===
print("\n--- SaveAIO (before toggle) ---")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    reg = list(r[2:])
    wb = [1] + reg[:62]
    wb[6] = 0x10  # mode=GIF
    wb[7] = 1     # source=custom
    wb[8] = 2     # index=slot 2
    send(fd, [0x5C] + wb[:63])
    time.sleep(0.3)
    print("  Settings written")

# Verify settings are in register
drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    print(f"  Reg 0x5C: {' '.join(f'{b:02x}' for b in r[2:14])}")

# === PHASE 3: OLED toggle (firmware should read GIF settings on init) ===
print("\n--- OLED OFF ---")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 1  # OFF
send(fd, [0x5C] + wb[:63])
time.sleep(2)

print("--- OLED ON ---")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 0  # ON
send(fd, [0x5C] + wb[:63])
time.sleep(0.3)
send(fd, [0x60, 0x80])
time.sleep(1)

# === PHASE 4: Set slot and start ===
print("--- Start animation ---")
send(fd, [0x6B, 0x01, 0x00, 0x02])
time.sleep(0.1)
send(fd, [0x6E, 0x00])
time.sleep(2)

drain(fd)
send(fd, [0xEB])
r = recv(fd)
path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
print(f"File: {path!r}")

drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    print(f"Reg 0x5C: {' '.join(f'{b:02x}' for b in r[2:14])}")

os.close(fd)
print("\n>>> CHECK OLED - CYAN background? Or still default red ROG? <<<")

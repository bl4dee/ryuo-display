#!/usr/bin/env python3
"""
Two-phase test:
Phase A: Restore default display using known working sequence
Phase B: Upload cyan and try to switch WITHOUT OLED toggle
"""
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


def read_reg(fd, reg):
    drain(fd)
    send(fd, [0x80 | reg])
    return recv(fd)


dev = find_device()
if not dev:
    print("Device not found!")
    sys.exit(1)

fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
drain(fd)

print("=" * 50)
print("PHASE A: Restore default display")
print("=" * 50)

# OLED OFF
drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 1
send(fd, [0x5C] + wb[:63])
time.sleep(1.5)

# OLED ON + refresh
drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 0
send(fd, [0x5C] + wb[:63])
time.sleep(0.2)
send(fd, [0x60, 0x80])
time.sleep(0.5)

# Set default slot and start
send(fd, [0x6B, 0x00, 0x00, 0x00])
time.sleep(0.1)
send(fd, [0x6E, 0x00])
time.sleep(1)

# Stop/start cycle
send(fd, [0x6C, 0x01])
time.sleep(0.2)
send(fd, [0x6C, 0x03])
time.sleep(0.5)
send(fd, [0x6B, 0x00, 0x00, 0x00])
time.sleep(0.1)
send(fd, [0x6E, 0x00])
time.sleep(2)

drain(fd)
send(fd, [0xEB])
r = recv(fd)
path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
print(f"  File: {path!r}")
print("  >>> Can you see the ROG display? (wait 5 sec) <<<")
time.sleep(5)

print()
print("=" * 50)
print("PHASE B: Upload CYAN and switch (no OLED toggle)")
print("=" * 50)

# Create cyan GIF
img = Image.new('RGB', (160, 128), (0, 255, 255))
buf = io.BytesIO()
img.save(buf, format='GIF')
gif_data = buf.getvalue()

# Upload to slot 2
print("Uploading CYAN to slot 2...")
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

# SaveAIO
print("Setting SaveAIO...")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    reg = list(r[2:])
    wb = [1] + reg[:62]
    wb[6] = 0x10
    wb[7] = 1
    wb[8] = 2
    send(fd, [0x5C] + wb[:63])
    time.sleep(0.3)

# Just start animation - no toggle
print("Starting animation...")
send(fd, [0x6B, 0x01, 0x00, 0x02])
time.sleep(0.1)
send(fd, [0x6E, 0x00])
time.sleep(3)

drain(fd)
send(fd, [0xEB])
r = recv(fd)
path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
print(f"  File: {path!r}")

os.close(fd)
print("\n>>> CHECK OLED - is it CYAN, default ROG, or BLACK? <<<")

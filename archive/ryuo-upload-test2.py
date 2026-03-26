#!/usr/bin/env python3
"""Upload test with full protocol sequence and delays."""
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
print(f"Device: {dev}")

fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
drain(fd)

# Create solid blue GIF (different color to confirm it's our upload)
img = Image.new('RGB', (160, 128), (0, 0, 255))
buf = io.BytesIO()
img.save(buf, format='GIF')
gif_data = buf.getvalue()
print(f"Solid BLUE GIF: {len(gif_data)} bytes")

slot = 2

# === FULL UPLOAD SEQUENCE (exactly matching LiveDash DLL) ===
print("\n--- Step 1: Init transfer ---")
send(fd, [0x51, 0xA0])
time.sleep(0.05)

print("--- Step 2: Set file slot ---")
send(fd, [0x6B, 0x01, 0x00, slot])
time.sleep(0.05)

print("--- Step 3: Stop animation ---")
send(fd, [0x6C, 0x01])
time.sleep(0.05)

print("--- Step 4: Force stop ---")
send(fd, [0x6C, 0x03])
time.sleep(0.05)

print("--- Step 5: Prepare transfer ---")
send(fd, [0x6C, 0x04])
time.sleep(0.05)

print("--- Step 6: Send data chunks ---")
offset = 0
chunks = 0
while offset < len(gif_data):
    chunk = gif_data[offset:offset + 62]
    send(fd, [0x6E, len(chunk)] + list(chunk))
    offset += len(chunk)
    chunks += 1
    time.sleep(0.002)  # small delay between chunks
print(f"  Sent {chunks} chunks ({len(gif_data)} bytes)")

print("--- Step 7: Transfer complete ---")
send(fd, [0x6C, 0x05])
time.sleep(0.1)

print("--- Step 8: Finalize ---")
send(fd, [0x6C, 0xFF])
time.sleep(0.1)

print("--- Step 9: Commit ---")
send(fd, [0x51, 0x10, 0x01, slot])
time.sleep(0.3)

print("--- Step 10: SaveAIO ---")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    reg = list(r[2:])
    wb = [1] + reg[:62]  # flag=1
    wb[6] = 0x10   # mode=GIF
    wb[7] = 1      # source=custom
    wb[8] = slot    # index=slot
    send(fd, [0x5C] + wb[:63])
    time.sleep(0.2)
    print("  SaveAIO written")

print("--- Step 11: Start animation ---")
send(fd, [0x6E, 0x00])
time.sleep(2)

# Check state
drain(fd)
send(fd, [0xEB])
r = recv(fd)
path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
print(f"\nFile: {path!r}")

drain(fd)
send(fd, [0xEC])
r = recv(fd)
state = r[2] if r else 0
print(f"Anim state: 0x{state:02x}")

drain(fd)
send(fd, [0xDC])
r = recv(fd)
if r:
    print(f"Reg 0x5C: {' '.join(f'{b:02x}' for b in r[2:14])}")

os.close(fd)
print("\n>>> CHECK OLED - should be solid BLUE <<<")

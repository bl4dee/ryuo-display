#!/usr/bin/env python3
"""Restore OLED, then test different file sizes to find what works."""
import os, sys, time, glob, io
from PIL import Image, ImageDraw

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

def upload_and_show(fd, gif_data, slot=2):
    """Upload and display. Returns number of chunks sent."""
    send(fd, [0x51, 0xA0])
    time.sleep(0.05)
    send(fd, [0x6B, 0x01, 0x00, slot])
    time.sleep(0.05)
    send(fd, [0x6C, 0x01])
    time.sleep(0.05)
    send(fd, [0x6C, 0x03])
    time.sleep(0.05)
    send(fd, [0x6C, 0x04])
    time.sleep(0.05)

    offset = 0
    chunks = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 62]
        send(fd, [0x6E, len(chunk)] + list(chunk))
        offset += len(chunk)
        chunks += 1

    send(fd, [0x6C, 0x05])
    time.sleep(0.1)
    send(fd, [0x6C, 0xFF])
    time.sleep(0.1)
    send(fd, [0x51, 0x10, 0x01, slot])
    time.sleep(0.2)

    # SaveAIO
    drain(fd)
    send(fd, [0xDC])
    r = recv(fd)
    if r:
        reg = list(r[2:])
        wb = [1] + reg[:62]
        wb[6] = 0x10
        wb[7] = 1
        wb[8] = slot
        send(fd, [0x5C] + wb[:63])
        time.sleep(0.3)

    # Set slot and start
    send(fd, [0x6B, 0x01, 0x00, slot])
    time.sleep(0.1)
    send(fd, [0x6E, 0x00])
    return chunks

def make_gif(r, g, b, text=None, quality=1):
    """Make a GIF. quality=1 is small solid, quality=2 adds text, quality=3 adds gradient."""
    img = Image.new('RGB', (160, 128), (r, g, b))
    if quality >= 3:
        draw = ImageDraw.Draw(img)
        for y in range(128):
            c = int(255 * y / 128)
            draw.line([(0, y), (159, y)], fill=(c, g, 128))
    if quality >= 2 and text:
        draw = ImageDraw.Draw(img)
        draw.text((30, 50), text, fill='white')
    buf = io.BytesIO()
    img.save(buf, format='GIF')
    return buf.getvalue()


dev = find_device()
if not dev:
    print("Device not found!")
    sys.exit(1)

fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
drain(fd)

# === RESTORE: OLED toggle + small cyan + stop/start ===
print("=== RESTORING OLED ===")

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

# Small cyan upload
cyan_gif = make_gif(0, 255, 255)
n = upload_and_show(fd, cyan_gif)
print(f"  Uploaded cyan: {len(cyan_gif)} bytes, {n} chunks")
time.sleep(1)

# Stop/start cycle
send(fd, [0x6C, 0x01])
time.sleep(0.2)
send(fd, [0x6C, 0x03])
time.sleep(0.5)
send(fd, [0x6B, 0x01, 0x00, 0x02])
time.sleep(0.1)
send(fd, [0x6E, 0x00])
time.sleep(3)

print("  OLED should be showing (cyan or default ROG)")
print()

# === TEST: Try increasingly complex images ===
# Only proceed if OLED is alive - each test builds on the previous
tests = [
    ("Yellow solid", make_gif(255, 255, 0)),
    ("Orange solid", make_gif(255, 128, 0)),
    ("Purple with text", make_gif(128, 0, 128, text="HELLO", quality=2)),
    ("Gradient", make_gif(255, 0, 0, text="TEST", quality=3)),
]

for name, gif_data in tests:
    print(f"--- {name}: {len(gif_data)} bytes ---")
    n = upload_and_show(fd, gif_data)
    time.sleep(3)
    drain(fd)
    send(fd, [0xEB])
    r = recv(fd)
    path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
    print(f"  {n} chunks, file={path!r}")

os.close(fd)
print("\n>>> What color/image do you see? <<<")

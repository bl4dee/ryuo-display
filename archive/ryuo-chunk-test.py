#!/usr/bin/env python3
"""Test: restore with small file, then try large file with chunk delays."""
import os, sys, time, glob, io
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

def upload(fd, gif_data, slot=2, chunk_delay=0):
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
        if chunk_delay > 0:
            time.sleep(chunk_delay)
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
    # Start
    send(fd, [0x6B, 0x01, 0x00, slot])
    time.sleep(0.1)
    send(fd, [0x6E, 0x00])
    return chunks

dev = find_device()
fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
drain(fd)

# Phase 1: OLED toggle + small magenta
print("Phase 1: Restore with OLED toggle + magenta")
drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 1  # OFF
send(fd, [0x5C] + wb[:63])
time.sleep(1.5)

drain(fd)
send(fd, [0xDC])
r = recv(fd)
reg = list(r[2:])
wb = [0] + reg[:62]
wb[3] = 0  # ON
send(fd, [0x5C] + wb[:63])
time.sleep(0.2)
send(fd, [0x60, 0x80])
time.sleep(0.5)

img = Image.new('RGB', (160, 128), (255, 0, 255))
buf = io.BytesIO()
img.save(buf, format='GIF')
small_gif = buf.getvalue()
n = upload(fd, small_gif)
print(f"  Uploaded magenta: {len(small_gif)} bytes, {n} chunks")
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

print("  >>> Phase 1 done - is it showing MAGENTA or default ROG? <<<")

# Phase 2: Large file with 10ms chunk delay (no OLED toggle)
print("\nPhase 2: Upload test_oled2.gif (10ms chunk delay)")
big_gif = open('/home/blink/test_oled2.gif', 'rb').read()
n = upload(fd, big_gif, chunk_delay=0.01)
print(f"  Uploaded: {len(big_gif)} bytes, {n} chunks")
time.sleep(3)

drain(fd)
send(fd, [0xEB])
r = recv(fd)
path = bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace') if r else '?'
print(f"  File: {path!r}")
print("  >>> Phase 2 - gradient image, or black? <<<")

os.close(fd)

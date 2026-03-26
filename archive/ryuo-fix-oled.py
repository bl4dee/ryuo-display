#!/usr/bin/env python3
"""Fix the black OLED: toggle off/on, upload to slot 2 (LiveDash style)."""
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


def hex_str(data, n=12):
    return ' '.join(f'{b:02x}' for b in data[:n])


def read_reg(fd, reg):
    drain(fd)
    send(fd, [0x80 | reg])
    return recv(fd)


def get_file(fd):
    drain(fd)
    send(fd, [0xEB])
    r = recv(fd)
    if r:
        return bytes(r[3:]).split(b'\x00')[0].decode('ascii', errors='replace')
    return '?'


def get_anim(fd):
    drain(fd)
    send(fd, [0xEC])
    r = recv(fd)
    return f'0x{r[2]:02x}' if r else '?'


def main():
    dev = find_device()
    if not dev:
        print("Device not found!")
        sys.exit(1)
    print(f"Device: {dev}")

    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    drain(fd)

    # Firmware check
    send(fd, [0x82])
    r = recv(fd)
    if r:
        fw = bytes(r[2:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"Firmware: {fw}")

    # Current state
    print(f"\nFile: {get_file(fd)}")
    print(f"Anim: {get_anim(fd)}")
    r = read_reg(fd, 0x5C)
    if r:
        print(f"Reg 0x5C: {hex_str(r[2:])}")

    # === TEST A: Toggle OLED OFF then ON ===
    print("\n=== TEST A: Toggle OLED OFF/ON ===")

    # OFF
    r = read_reg(fd, 0x5C)
    if r:
        reg = list(r[2:])
        wb = [0] + reg[:62]
        wb[3] = 1  # OFF
        send(fd, [0x5C] + wb[:63])
        time.sleep(1.5)
        print("  OLED OFF for 1.5s")

    # ON
    r = read_reg(fd, 0x5C)
    if r:
        reg = list(r[2:])
        wb = [0] + reg[:62]
        wb[3] = 0  # ON
        send(fd, [0x5C] + wb[:63])
        time.sleep(0.2)
        send(fd, [0x60, 0x80])
        time.sleep(0.5)
        print("  OLED ON + refresh")

    print(f"  File: {get_file(fd)}")
    print("  >>> Check OLED (TEST A) <<<")
    input("  Press Enter to continue...")

    # === TEST B: Upload to SLOT 2 (LiveDash style) ===
    print("\n=== TEST B: Upload solid RED to slot 2 ===")

    # Create GIF
    img = Image.new('RGB', (160, 128), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='GIF')
    gif_data = buf.getvalue()
    print(f"  GIF: {len(gif_data)} bytes")

    # Upload to slot 2
    send(fd, [0x51, 0xA0])
    time.sleep(0.05)
    send(fd, [0x6B, 0x01, 0x00, 0x02])  # slot 2
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
    print(f"  Sent {chunks} chunks to slot 2")

    send(fd, [0x6C, 0x05])
    time.sleep(0.1)
    send(fd, [0x6C, 0xFF])
    time.sleep(0.1)
    send(fd, [0x51, 0x10, 0x01, 0x02])  # commit slot 2
    time.sleep(0.2)

    # SaveAIO: mode=0x10, source=1, index=2 (exactly like LiveDash)
    r = read_reg(fd, 0x5C)
    if r:
        reg = list(r[2:])
        wb = [1] + reg[:62]
        wb[6] = 0x10  # mode=GIF
        wb[7] = 1     # source=custom
        wb[8] = 2     # index=slot 2
        print(f"  SaveAIO: {hex_str(wb)}")
        send(fd, [0x5C] + wb[:63])
        time.sleep(0.2)

    # Start animation
    send(fd, [0x6E, 0x00])
    time.sleep(1)

    print(f"  File: {get_file(fd)}")
    print(f"  Anim: {get_anim(fd)}")
    r = read_reg(fd, 0x5C)
    if r:
        print(f"  Reg 0x5C: {hex_str(r[2:])}")
    print("  >>> Check OLED (TEST B) <<<")

    os.close(fd)


if __name__ == "__main__":
    main()

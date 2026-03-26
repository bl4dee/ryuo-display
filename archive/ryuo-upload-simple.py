#!/usr/bin/env python3
"""
Upload a simple solid-color image to the Ryuo OLED.
Uses the corrected protocol with proper flag-prefix for register 0x5C writes.
"""
import os
import sys
import time
import glob
import struct

REPORT_ID = 0xEC
REPORT_SIZE = 65


def find_device():
    for f in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        content = open(f).read()
        if '00001887' in content:
            return '/dev/' + f.split('/')[4]
    return None


def send(fd, data):
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
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


def make_solid_gif(r, g, b, width=160, height=128):
    """Create a valid GIF with a solid color using Pillow."""
    from PIL import Image
    import io
    img = Image.new('RGB', (width, height), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format='GIF')
    return buf.getvalue()


def upload_file(fd, gif_data, slot=1):
    """Upload GIF data using the LiveDash protocol."""
    size = len(gif_data)
    print(f"  Uploading {size} bytes to slot {slot}...")

    # Step 1: Init
    send(fd, [0x51, 0xA0])
    time.sleep(0.02)

    # Step 2: Set file slot
    send(fd, [0x6B, 0x01, 0x00, slot])
    time.sleep(0.02)

    # Step 3-5: Stop and prepare
    send(fd, [0x6C, 0x01])
    time.sleep(0.02)
    send(fd, [0x6C, 0x03])
    time.sleep(0.02)
    send(fd, [0x6C, 0x04])
    time.sleep(0.02)

    # Step 6: Data chunks (62 bytes each)
    offset = 0
    chunks = 0
    while offset < size:
        chunk = gif_data[offset:offset + 62]
        send(fd, [0x6E, len(chunk)] + list(chunk))
        offset += len(chunk)
        chunks += 1
    print(f"  Sent {chunks} chunks")

    # Step 7-9: Complete, finalize, commit
    send(fd, [0x6C, 0x05])
    time.sleep(0.05)
    send(fd, [0x6C, 0xFF])
    time.sleep(0.05)
    send(fd, [0x51, 0x10, 0x01, slot])
    time.sleep(0.1)
    print("  Upload done")


def save_aio_settings(fd, mode=0x10, source=1, index=1):
    """
    SaveAIO_Settings_toFW with CORRECT flag-prefix format.

    Write format: [0x5C, FLAG, reg[0], reg[1], ..., reg[62]]
    FLAG = iData[0] is consumed by device as a command flag.
    iData[N] for N>=1 is stored as reg[N-1].

    DLL uses: array[0]=flag, array[6]=mode, array[7]=source, array[8]=index
    Which maps to: reg[5]=mode, reg[6]=source, reg[7]=index
    """
    print(f"  SaveAIO: mode=0x{mode:02x} source={source} index={index}")

    drain(fd)
    send(fd, [0xDC])  # read register 0x5C
    resp = recv(fd)
    if not resp:
        print("  ERROR: no response from 0x5C read")
        return

    reg_data = list(resp[2:])  # reg[0], reg[1], ...
    print(f"  Read: {' '.join(f'{b:02x}' for b in reg_data[:12])}")

    # Build write buffer: [flag, reg[0], reg[1], ...]
    # Prepend flag, keep reg data at correct positions
    write_buf = [1] + reg_data[:62]  # flag=1, then original reg data
    write_buf[6] = mode    # array[6] → reg[5]
    write_buf[7] = source  # array[7] → reg[6]
    write_buf[8] = index   # array[8] → reg[7]

    print(f"  Write: {' '.join(f'{b:02x}' for b in write_buf[:12])}")
    send(fd, [0x5C] + write_buf[:63])
    time.sleep(0.1)

    # Verify
    drain(fd)
    send(fd, [0xDC])
    resp = recv(fd)
    if resp:
        print(f"  After: {' '.join(f'{b:02x}' for b in resp[2:14])}")


def main():
    dev = find_device()
    if not dev:
        print("Device not found!")
        sys.exit(1)
    print(f"Device: {dev}")

    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    drain(fd)

    # Verify connection
    send(fd, [0x82])
    resp = recv(fd)
    if resp:
        fw = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"Firmware: {fw}")

    # Create a simple solid RED GIF (no Pillow needed)
    print("\n=== Creating solid red GIF ===")
    gif_data = make_solid_gif(255, 0, 0)
    print(f"GIF size: {len(gif_data)} bytes")

    # Verify it's a valid GIF
    print(f"GIF header: {gif_data[:6]}")
    assert gif_data[:4] == b'GIF8', "Invalid GIF!"

    # Upload to slot 1
    print("\n=== Upload to slot 1 ===")
    upload_file(fd, gif_data, slot=1)

    # Set display to show uploaded file
    print("\n=== SaveAIO settings ===")
    save_aio_settings(fd, mode=0x10, source=1, index=1)

    # Start animation
    print("\n=== Starting animation ===")
    send(fd, [0x6E, 0x00])
    time.sleep(1)

    # Check state
    drain(fd)
    send(fd, [0xEC])
    resp = recv(fd)
    print(f"Anim state: 0x{resp[2]:02x}" if resp else "No response")

    drain(fd)
    send(fd, [0xEB])
    resp = recv(fd)
    if resp:
        path = bytes(resp[3:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"File path: {path!r}")

    os.close(fd)
    print("\n>>> CHECK OLED - should be solid RED <<<")


if __name__ == "__main__":
    main()

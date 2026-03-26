#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe - Phase 2
Targeted probing of display and file transfer commands.
"""

import os
import sys
import time

DEVICE = "/dev/hidraw12"
REPORT_ID = 0xEC
REPORT_SIZE = 65


def send_cmd(fd, data):
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    for i, b in enumerate(data[:64]):
        buf[1 + i] = b
    os.write(fd, bytes(buf))


def read_resp(fd, timeout=0.3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = os.read(fd, REPORT_SIZE)
            return data
        except BlockingIOError:
            time.sleep(0.005)
    return None


def drain(fd):
    while True:
        try:
            os.read(fd, REPORT_SIZE)
        except BlockingIOError:
            break


def hex_dump(data, max_bytes=64):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:max_bytes])


def read_register(fd, reg):
    """Read a register (send 0x80+reg, get response)."""
    drain(fd)
    send_cmd(fd, [0x80 + reg])
    return read_resp(fd)


def decode_ascii(data, start=2):
    """Decode ASCII string from response starting at offset."""
    if data is None:
        return ""
    s = ""
    for b in data[start:]:
        if b == 0:
            break
        if 0x20 <= b < 0x7F:
            s += chr(b)
        else:
            s += f"\\x{b:02x}"
    return s


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Read current state
    print("\n=== Current device state ===")
    for reg, name in [(0x30, "config"), (0x31, "config2"), (0x50, "display"),
                       (0x5C, "mode"), (0x5D, "filepath1"), (0x6A, "fileinfo"),
                       (0x6B, "filepath2"), (0x6C, "reg6C"), (0x6D, "reg6D")]:
        resp = read_register(fd, reg)
        ascii_str = decode_ascii(resp)
        print(f"  reg 0x{reg:02x}: {hex_dump(resp)}")
        if ascii_str:
            print(f"           ASCII: {ascii_str}")

    # Phase 1: Try write commands that might affect the display
    print("\n=== Phase 1: Write command probing ===")
    print("Trying write commands with various sub-args...")
    print("(Watch the OLED for any changes!)\n")

    # Try writing to register 0x5C (display mode) with different values
    print("--- Testing display mode writes (reg 0x5C) ---")
    for mode in range(0, 8):
        drain(fd)
        send_cmd(fd, [0x5C, mode])
        resp = read_resp(fd, timeout=0.2)
        if resp is not None:
            print(f"  write 5C {mode:02x}: {hex_dump(resp)}")
        time.sleep(0.1)
        # Read back
        state = read_register(fd, 0x5C)
        print(f"  readback 5C: {hex_dump(state)}")

    # Try writing to register 0x5D (file path)
    print("\n--- Testing file path writes (reg 0x5D) ---")
    test_path = b"0:/Animat00.gif"
    drain(fd)
    cmd = [0x5D] + list(test_path) + [0x00]
    send_cmd(fd, cmd)
    resp = read_resp(fd, timeout=0.3)
    if resp is not None:
        print(f"  write 5D (same path): {hex_dump(resp)}")
    state = read_register(fd, 0x5D)
    print(f"  readback 5D: {hex_dump(state)}")

    # Phase 2: Test commands specifically in the 0x50-0x6F write range
    print("\n=== Phase 2: Systematic write command test 0x50-0x6F ===")
    for cmd_byte in range(0x50, 0x70):
        drain(fd)
        send_cmd(fd, [cmd_byte, 0x01])
        resp = read_resp(fd, timeout=0.15)
        if resp is not None:
            print(f"  write {cmd_byte:02x} 01: {hex_dump(resp)}")

    # Phase 3: Test commands in 0x30-0x4F range (AURA/display control area)
    print("\n=== Phase 3: Write commands 0x30-0x4F ===")
    for cmd_byte in range(0x30, 0x50):
        if cmd_byte == 0x3B:  # Skip AURA mode set to avoid changing LEDs
            continue
        if cmd_byte == 0x40:  # Skip direct LED control
            continue
        drain(fd)
        send_cmd(fd, [cmd_byte, 0x00])
        resp = read_resp(fd, timeout=0.15)
        if resp is not None:
            print(f"  write {cmd_byte:02x} 00: {hex_dump(resp)}")

    # Phase 4: Try "file transfer" patterns
    print("\n=== Phase 4: File transfer protocol candidates ===")

    # Pattern A: Command with path and size info
    file_path = b"0:/test.gif"
    file_size = 1024  # dummy size

    patterns = [
        # (description, command bytes)
        ("open-file 0x60", [0x60, 0x01] + list(file_path) + [0x00]),
        ("open-file 0x61", [0x61, 0x01] + list(file_path) + [0x00]),
        ("open-file 0x62", [0x62, 0x01] + list(file_path) + [0x00]),
        ("file-begin 0x50 w/size", [0x50, 0x01, (file_size >> 8) & 0xFF, file_size & 0xFF]),
        ("file-begin 0x50 w/LE-size", [0x50, 0x01, file_size & 0xFF, (file_size >> 8) & 0xFF]),
        ("file-xfer 0x5A", [0x5A, 0x01]),
        ("file-xfer 0x5B", [0x5B, 0x01]),
        ("file-xfer 0x5E", [0x5E, 0x01]),
        ("file-xfer 0x5F", [0x5F, 0x01]),
        # Try with path in different format
        ("write-file 0x6B path", [0x6B] + list(file_path) + [0x00]),
    ]

    for desc, data in patterns:
        drain(fd)
        send_cmd(fd, data)
        resp = read_resp(fd, timeout=0.2)
        if resp is not None:
            print(f"  {desc}: {hex_dump(resp)}")
        else:
            print(f"  {desc}: no response (write accepted?)")

    # Phase 5: Check if device has a bulk/streaming mode
    print("\n=== Phase 5: Streaming pixel data test ===")
    print("Sending small blocks of test pixel data...")

    # Try sending raw pixel data after various "begin" commands
    # 160x128 RGB565 = 40960 bytes
    # A single red pixel in RGB565 = 0xF800
    test_pixels = [0xF8, 0x00] * 30  # 30 red pixels = 60 bytes, fits in one packet

    for begin_cmd in [0x50, 0x58, 0x5A, 0x60, 0x36]:
        drain(fd)
        # Send begin command
        send_cmd(fd, [begin_cmd, 0x01, 0x00, 0x00, 0xA0, 0x00, 0x80, 0x00])
        resp = read_resp(fd, timeout=0.15)
        if resp is not None:
            print(f"  begin {begin_cmd:02x}: {hex_dump(resp)}")

        # Send pixel data
        send_cmd(fd, test_pixels)
        resp = read_resp(fd, timeout=0.15)
        if resp is not None:
            print(f"  data after {begin_cmd:02x}: {hex_dump(resp)}")

    # Phase 6: Interesting - check if temperature/sensor data is in 0xEA
    print("\n=== Phase 6: Read dynamic registers ===")
    for i in range(3):
        resp = read_register(fd, 0x6A)
        print(f"  reg 0x6A read {i}: {hex_dump(resp)}")
        time.sleep(0.5)

    # Read the full 0x50 register repeatedly to see if it changes
    for i in range(3):
        resp = read_register(fd, 0x50)
        print(f"  reg 0x50 read {i}: {hex_dump(resp)}")
        time.sleep(0.5)

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

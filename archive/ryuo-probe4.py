#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe - Phase 4
Deep testing of the 0x50/0x51/0x52 file transfer protocol.
"""

import os
import sys
import time
import struct

DEVICE = "/dev/hidraw12"
REPORT_ID = 0xEC
REPORT_SIZE = 65
CHUNK_DATA_SIZE = 63  # 64 - 1 for command byte


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
            return os.read(fd, REPORT_SIZE)
        except BlockingIOError:
            time.sleep(0.005)
    return None


def drain(fd):
    while True:
        try:
            os.read(fd, REPORT_SIZE)
        except BlockingIOError:
            break


def hex_dump(data, max_bytes=32):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:max_bytes])


def read_register(fd, reg):
    drain(fd)
    send_cmd(fd, [0x80 + reg])
    return read_resp(fd)


def read_key_regs(fd, label=""):
    """Read and print key registers."""
    if label:
        print(f"  [{label}]")
    for reg in [0x50, 0x5C, 0x5D, 0x6B, 0x6C]:
        resp = read_register(fd, reg)
        print(f"    0x{reg:02x}: {hex_dump(resp)}")


def create_minimal_gif(width, height, r, g, b):
    """Create a minimal single-color GIF."""
    # GIF89a header
    gif = bytearray()
    gif += b'GIF89a'
    # Logical screen descriptor
    gif += struct.pack('<HH', width, height)
    gif += bytes([0x80, 0x00, 0x00])  # GCT flag, bg, aspect
    # Global color table (2 entries)
    gif += bytes([r, g, b])  # color 0
    gif += bytes([0, 0, 0])  # color 1
    # Image descriptor
    gif += bytes([0x2C])  # Image separator
    gif += struct.pack('<HH', 0, 0)  # left, top
    gif += struct.pack('<HH', width, height)  # width, height
    gif += bytes([0x00])  # no local color table
    # Image data
    gif += bytes([0x02])  # LZW minimum code size
    # LZW compressed data for a solid color image
    # For a small image filled with color index 0
    pixel_count = width * height
    # Simple LZW: clear code, then all 0s, then end code
    # For code size 2: clear=4, end=5
    # Just output clear code + stream of 0s + end code
    # Packed into sub-blocks
    compressed = bytearray()
    compressed.append(0x04)  # clear code (bit pattern: 100)
    # For each pixel, code 0 (bit pattern: 00)
    # Then end code 5 (bit pattern: 101)
    # This is getting complex - let me just make a truly minimal GIF

    # Actually, let's use the simplest possible approach
    # Use minimum code size 2, output clear + all data + end
    import io

    # Simplest approach: use PIL to create the GIF in memory
    # But since we might not have PIL, let's create a hardcoded minimal GIF

    # Minimal 1x1 red GIF89a
    return None  # will use hardcoded below


def create_test_gif_1x1():
    """Hardcoded minimal 1x1 pixel red GIF."""
    return bytes([
        0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
        0x01, 0x00, 0x01, 0x00,  # 1x1
        0x80, 0x00, 0x00,  # GCT flag, bg=0, aspect=0
        0xFF, 0x00, 0x00,  # color 0: red
        0x00, 0x00, 0x00,  # color 1: black
        0x2C,  # image separator
        0x00, 0x00, 0x00, 0x00,  # left, top
        0x01, 0x00, 0x01, 0x00,  # 1x1
        0x00,  # no local CT
        0x02,  # LZW min code size
        0x02, 0x4C, 0x01,  # sub-block: 2 bytes of LZW data
        0x00,  # block terminator
        0x3B  # GIF trailer
    ])


def rgb565(r, g, b):
    """Convert RGB888 to RGB565 big-endian bytes."""
    val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return [(val >> 8) & 0xFF, val & 0xFF]


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    print("\n=== Baseline state ===")
    read_key_regs(fd, "baseline")

    # Test 1: Isolate which command in seq B caused the change
    print("\n=== Test 1: Isolate 0x50 effect ===")
    drain(fd)
    send_cmd(fd, [0x50, 0x01, 0x00, 0x00, 0x00, 0x06])
    read_resp(fd, timeout=0.1)
    time.sleep(0.1)
    read_key_regs(fd, "after 0x50 alone")

    # Test 2: Send 0x51 with known data, check register
    print("\n=== Test 2: 0x51 data correlation ===")
    for test_byte in [0xAA, 0x55, 0xDE, 0xAD]:
        drain(fd)
        send_cmd(fd, [0x50, 0x01, 0x00, 0x00, 0x00, 0x01])  # begin, size=1
        read_resp(fd, timeout=0.1)
        drain(fd)
        send_cmd(fd, [0x51, test_byte])  # data with test byte
        read_resp(fd, timeout=0.1)
        time.sleep(0.05)
        resp = read_register(fd, 0x50)
        byte3 = resp[3] if resp and len(resp) > 3 else None
        print(f"  sent 0x51 0x{test_byte:02x} -> reg 0x50 byte3 = 0x{byte3:02x}" +
              (" MATCH!" if byte3 == test_byte else ""))

    # Test 3: Try different begin (0x50) parameter formats
    print("\n=== Test 3: 0x50 parameter exploration ===")
    # The GIF file is small - try with actual file size
    test_gif = create_test_gif_1x1()
    print(f"  Test GIF size: {len(test_gif)} bytes")
    print(f"  Test GIF hex: {hex_dump(test_gif, 40)}")

    # Try: [0x50, mode, size_h, size_l]
    for mode in [0x00, 0x01, 0x02, 0x03]:
        drain(fd)
        size = len(test_gif)
        cmd = [0x50, mode, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
               (size >> 8) & 0xFF, size & 0xFF]
        send_cmd(fd, cmd)
        resp = read_resp(fd, timeout=0.15)
        if resp:
            print(f"  0x50 mode={mode} size={size}: RESPONSE {hex_dump(resp, 16)}")
        else:
            print(f"  0x50 mode={mode} size={size}: (no response)")

    # Test 4: Full transfer of minimal GIF with 0x50/0x51/0x52
    print("\n=== Test 4: Full GIF transfer via 0x50/0x51/0x52 ===")
    read_key_regs(fd, "before transfer")

    # Begin transfer
    size = len(test_gif)
    drain(fd)
    send_cmd(fd, [0x50, 0x00, 0x00, 0x00, (size >> 8) & 0xFF, size & 0xFF])
    resp = read_resp(fd, timeout=0.15)
    print(f"  begin (0x50): {hex_dump(resp, 16) if resp else 'no response'}")

    # Send data in chunks
    offset = 0
    chunk_num = 0
    while offset < len(test_gif):
        chunk = test_gif[offset:offset + CHUNK_DATA_SIZE]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        resp = read_resp(fd, timeout=0.1)
        if resp:
            print(f"  chunk {chunk_num}: RESPONSE {hex_dump(resp, 16)}")
        offset += CHUNK_DATA_SIZE
        chunk_num += 1
    print(f"  sent {chunk_num} chunks, {len(test_gif)} bytes total")

    # End transfer
    drain(fd)
    send_cmd(fd, [0x52])
    resp = read_resp(fd, timeout=0.15)
    print(f"  end (0x52): {hex_dump(resp, 16) if resp else 'no response'}")

    time.sleep(0.2)
    read_key_regs(fd, "after transfer")

    # Test 5: Try the same but with 0x50 having a file path
    print("\n=== Test 5: Transfer with file path ===")
    path = b"0:/test.gif"
    drain(fd)
    # Maybe: [0x50, mode, path..., 0x00]
    cmd = [0x50, 0x01] + list(path) + [0x00]
    send_cmd(fd, cmd)
    resp = read_resp(fd, timeout=0.15)
    print(f"  begin with path: {hex_dump(resp, 16) if resp else 'no response'}")

    # Send GIF data
    offset = 0
    while offset < len(test_gif):
        chunk = test_gif[offset:offset + CHUNK_DATA_SIZE]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        read_resp(fd, timeout=0.05)
        offset += CHUNK_DATA_SIZE

    drain(fd)
    send_cmd(fd, [0x52])
    read_resp(fd, timeout=0.15)
    time.sleep(0.2)
    read_key_regs(fd, "after path transfer")

    # Test 6: Try setting file path via 0x6B then committing
    print("\n=== Test 6: Set path + commit ===")
    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    read_resp(fd, timeout=0.1)
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])  # AURA save/commit
    read_resp(fd, timeout=0.1)
    time.sleep(0.2)
    read_key_regs(fd, "after path set + commit")

    # Test 7: Try raw RGB565 pixel transfer
    print("\n=== Test 7: Raw RGB565 pixel transfer ===")
    # Create a small block of red pixels (RGB565 = 0xF800)
    red_pixels = bytes(rgb565(255, 0, 0) * 31)  # 31 pixels = 62 bytes

    # Begin with dimensions?
    drain(fd)
    width, height = 160, 128
    total_size = width * height * 2  # RGB565
    send_cmd(fd, [0x50, 0x02,
                  (total_size >> 24) & 0xFF, (total_size >> 16) & 0xFF,
                  (total_size >> 8) & 0xFF, total_size & 0xFF,
                  (width >> 8) & 0xFF, width & 0xFF,
                  (height >> 8) & 0xFF, height & 0xFF])
    read_resp(fd, timeout=0.1)

    # Send a few chunks of red pixels
    for i in range(5):
        drain(fd)
        send_cmd(fd, [0x51] + list(red_pixels))
        read_resp(fd, timeout=0.05)

    drain(fd)
    send_cmd(fd, [0x52])
    read_resp(fd, timeout=0.1)
    time.sleep(0.2)
    read_key_regs(fd, "after RGB565 transfer")

    # Test 8: Try alternate data command bytes
    print("\n=== Test 8: Alternate data commands ===")
    for data_cmd in [0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59]:
        drain(fd)
        send_cmd(fd, [data_cmd, 0xBE, 0xEF])
        resp = read_resp(fd, timeout=0.1)
        if resp:
            print(f"  cmd 0x{data_cmd:02x}: RESPONSE {hex_dump(resp, 16)}")
        # Check what register 0x50 shows
        r = read_register(fd, 0x50)
        b3 = r[3] if r and len(r) > 3 else None
        print(f"  cmd 0x{data_cmd:02x}: reg50[3] = 0x{b3:02x}" if b3 is not None else f"  cmd 0x{data_cmd:02x}: no reg50")

    # Test 9: Try the 0x6C/0x6E control commands more
    print("\n=== Test 9: Animation control commands ===")
    # 0x6C changed reg 0x6C from 09 to 00 (maybe "stop animation"?)
    # 0x6E changed it back to 09 (maybe "start animation"?)
    for cmd_byte, desc in [(0x6C, "stop?"), (0x6E, "start?"), (0x6C, "stop again?")]:
        drain(fd)
        send_cmd(fd, [cmd_byte, 0x00])
        read_resp(fd, timeout=0.1)
        time.sleep(0.3)
        resp = read_register(fd, 0x6C)
        val = resp[2] if resp and len(resp) > 2 else None
        resp5c = read_register(fd, 0x5C)
        val5c_11 = resp5c[11] if resp5c and len(resp5c) > 11 else None
        val5c_12 = resp5c[12] if resp5c and len(resp5c) > 12 else None
        print(f"  {desc} (0x{cmd_byte:02x}): reg6C[2]={val}, reg5C[11]={val5c_11}, reg5C[12]={val5c_12}")
        print(f"    (CHECK OLED NOW - did animation stop/start?)")
        time.sleep(1)

    os.close(fd)
    print("\nDone. Report what happened on the OLED!")


if __name__ == "__main__":
    main()

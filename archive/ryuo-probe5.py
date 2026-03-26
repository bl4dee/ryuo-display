#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe - Phase 5
Full-frame image transfer attempts with various init/commit sequences.
Also tries direct framebuffer streaming without file system.
"""

import os
import time

DEVICE = "/dev/hidraw12"
REPORT_ID = 0xEC
REPORT_SIZE = 65
CHUNK_DATA = 63  # 64 - 1 for cmd byte

WIDTH = 160
HEIGHT = 128
FRAME_SIZE = WIDTH * HEIGHT * 2  # RGB565 = 40960 bytes
TOTAL_CHUNKS = (FRAME_SIZE + CHUNK_DATA - 1) // CHUNK_DATA  # ~651


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


def hex_dump(data, n=20):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:n])


def read_register(fd, reg):
    drain(fd)
    send_cmd(fd, [0x80 + reg])
    return read_resp(fd)


def rgb565_be(r, g, b):
    """RGB888 to RGB565 big-endian (2 bytes)."""
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return bytes([(v >> 8) & 0xFF, v & 0xFF])


def make_solid_frame(r, g, b):
    """Create full 160x128 solid color frame in RGB565."""
    pixel = rgb565_be(r, g, b)
    return pixel * (WIDTH * HEIGHT)


def send_frame_data(fd, frame_data, data_cmd=0x51):
    """Send frame data in chunks using the given data command byte."""
    offset = 0
    chunks = 0
    while offset < len(frame_data):
        chunk = frame_data[offset:offset + CHUNK_DATA]
        drain(fd)
        send_cmd(fd, [data_cmd] + list(chunk))
        # Don't wait for response - just blast data
        offset += CHUNK_DATA
        chunks += 1
    return chunks


def attempt(fd, name, setup_cmds, data, teardown_cmds, data_cmd=0x51, delay=0.5):
    """Try a transfer sequence and report results."""
    print(f"\n--- {name} ---")

    # Read before state
    r50_before = read_register(fd, 0x50)
    r5c_before = read_register(fd, 0x5C)

    # Setup
    for desc, cmd in setup_cmds:
        drain(fd)
        send_cmd(fd, cmd)
        resp = read_resp(fd, timeout=0.15)
        status = hex_dump(resp, 10) if resp else "ok (no resp)"
        print(f"  setup [{desc}]: {status}")

    # Data
    if data:
        t0 = time.monotonic()
        chunks = send_frame_data(fd, data, data_cmd)
        dt = time.monotonic() - t0
        print(f"  data: {len(data)} bytes, {chunks} chunks, {dt:.2f}s")

    # Teardown
    for desc, cmd in teardown_cmds:
        drain(fd)
        send_cmd(fd, cmd)
        resp = read_resp(fd, timeout=0.15)
        status = hex_dump(resp, 10) if resp else "ok (no resp)"
        print(f"  teardown [{desc}]: {status}")

    time.sleep(delay)

    # Read after state
    r50_after = read_register(fd, 0x50)
    r5c_after = read_register(fd, 0x5C)
    if r50_before != r50_after:
        print(f"  reg50 changed: {hex_dump(r50_after)}")
    if r5c_before != r5c_after:
        print(f"  reg5C changed: {hex_dump(r5c_after)}")


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Prepare test frames
    red_frame = make_solid_frame(255, 0, 0)
    green_frame = make_solid_frame(0, 255, 0)
    blue_frame = make_solid_frame(0, 0, 255)
    white_frame = make_solid_frame(255, 255, 255)

    print(f"Frame size: {FRAME_SIZE} bytes ({TOTAL_CHUNKS} chunks of {CHUNK_DATA})")
    print(f"Red pixel: {hex_dump(red_frame[:4])}")

    # Read all registers 0x00-0x7F to find any we missed
    print("\n=== Quick scan: all non-zero registers ===")
    for reg in range(0x00, 0x80):
        resp = read_register(fd, reg)
        if resp and not all(b == 0 for b in resp[2:]):
            # Has non-zero data beyond the echo byte
            print(f"  reg 0x{reg:02x}: {hex_dump(resp)}")

    # Attempt 1: Just blast a full frame via 0x51 (no setup/teardown)
    attempt(fd, "A1: Raw 0x51 blast (red)",
            setup_cmds=[],
            data=red_frame,
            teardown_cmds=[])

    # Attempt 2: 0x50 begin + 0x51 data + 0x52 end
    attempt(fd, "A2: 0x50/0x51/0x52 (green)",
            setup_cmds=[("begin", [0x50, 0x00, (FRAME_SIZE >> 24) & 0xFF,
                                    (FRAME_SIZE >> 16) & 0xFF,
                                    (FRAME_SIZE >> 8) & 0xFF,
                                    FRAME_SIZE & 0xFF])],
            data=green_frame,
            teardown_cmds=[("end", [0x52]),
                           ("save", [0x3F, 0x55])])

    # Attempt 3: Stop animation + blast + restart
    attempt(fd, "A3: Stop + blast + start (blue)",
            setup_cmds=[("stop anim", [0x6C, 0x01])],
            data=blue_frame,
            teardown_cmds=[("start anim", [0x6E, 0x00])])

    # Attempt 4: Set mode + begin + data + end + commit
    attempt(fd, "A4: Full sequence mode=0 (white)",
            setup_cmds=[("stop anim", [0x6C, 0x01]),
                        ("set mode", [0x5C, 0x00]),
                        ("begin", [0x50, 0x00, 0x00, 0x00,
                                   (FRAME_SIZE >> 8) & 0xFF, FRAME_SIZE & 0xFF])],
            data=white_frame,
            teardown_cmds=[("end", [0x52]),
                           ("commit", [0x3F, 0x55]),
                           ("start", [0x6E, 0x00])])

    # Attempt 5: Try with GIF file data instead of raw pixels
    print("\n--- A5: Try actual GIF file transfer ---")
    # Create a minimal but valid 160x128 GIF using PIL if available
    gif_data = None
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
        print(f"  Created 160x128 red GIF: {len(gif_data)} bytes")
    except ImportError:
        print("  PIL not available, creating minimal GIF manually")
        # Minimal GIF89a with solid color
        import struct
        gif = bytearray()
        gif += b'GIF89a'
        gif += struct.pack('<HH', 160, 128)
        gif += bytes([0xF0, 0x00, 0x00])  # 16-color GCT, bg=0, aspect=0
        # Color table (16 entries)
        for i in range(16):
            gif += bytes([255, 0, 0])  # all red
        gif += bytes([0x2C])  # image separator
        gif += struct.pack('<HH', 0, 0)
        gif += struct.pack('<HH', 160, 128)
        gif += bytes([0x00])  # no local CT
        gif += bytes([0x04])  # LZW min code size
        # LZW data: just clear + all index 0 + end
        # For 160x128=20480 pixels, each coded as index 0
        # This is complex, so use a simple approach:
        # Output sub-blocks of compressed data
        # Clear code = 16, End code = 17
        # Code for pixel 0 = 0
        # Simple: output clear, then 0 repeated, then end
        # Actually, just fill with a simple pattern
        # Use min code size 4 (codes 0-15 for colors, 16=clear, 17=end)
        compressed = bytearray()
        # First output clear code
        # Bit packing: codes are 5 bits initially
        # This is getting complex. Let's just use a pre-computed block
        # or skip this if PIL isn't available
        gif_data = bytes(gif)  # incomplete but worth trying
        print(f"  Manual GIF (incomplete): {len(gif_data)} bytes")

    if gif_data:
        attempt(fd, "A5: GIF file to 0:/Animat00.gif",
                setup_cmds=[("stop anim", [0x6C, 0x01]),
                            ("set path", [0x6B] + list(b"0:/Animat00.gif\x00")),
                            ("begin", [0x50, 0x01, (len(gif_data) >> 24) & 0xFF,
                                       (len(gif_data) >> 16) & 0xFF,
                                       (len(gif_data) >> 8) & 0xFF,
                                       len(gif_data) & 0xFF])],
                data=gif_data,
                teardown_cmds=[("end", [0x52]),
                               ("commit", [0x3F, 0x55]),
                               ("start", [0x6E, 0x00])])

    # Attempt 6: Try using DIFFERENT data command bytes
    # Maybe 0x51 writes to a different buffer, and another cmd writes to framebuffer
    for data_cmd in [0x50, 0x52, 0x53, 0x54, 0x60, 0x61]:
        # Just send a small amount to test
        small_red = red_frame[:CHUNK_DATA * 3]
        attempt(fd, f"A6: data_cmd=0x{data_cmd:02x} (red partial)",
                setup_cmds=[],
                data=small_red,
                teardown_cmds=[],
                data_cmd=data_cmd,
                delay=0.2)

    # Final state check
    print("\n=== Final state ===")
    for reg in [0x30, 0x50, 0x5C, 0x5D, 0x6A, 0x6B, 0x6C, 0x6D]:
        resp = read_register(fd, reg)
        print(f"  reg 0x{reg:02x}: {hex_dump(resp)}")

    os.close(fd)
    print("\nDone! Check the OLED display for any changes.")
    print("If nothing changed, we may need a USB capture from LiveDash on Windows.")


if __name__ == "__main__":
    main()

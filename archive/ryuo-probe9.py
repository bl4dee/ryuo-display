#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 9
Deep dive on command 0x5D which changed display registers.
Also try the 0x5C/0x5D/0x50 combination more carefully.
Hypothesis: 0x5D sets up a file write context, then 0x50/0x51/0x52 does the transfer.
"""

import os
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


def hex_dump(data, n=32):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:n])


def read_register(fd, reg):
    drain(fd)
    send_cmd(fd, [0x80 + reg])
    return read_resp(fd)


def dump_state(fd, label=""):
    if label:
        print(f"  [{label}]")
    for reg in [0x50, 0x5C, 0x5D, 0x6A, 0x6B, 0x6C]:
        resp = read_register(fd, reg)
        # Show meaningful bytes
        if resp:
            hex_part = " ".join(f"{b:02x}" for b in resp[:20])
            ascii_part = ""
            for b in resp[2:20]:
                ascii_part += chr(b) if 0x20 <= b < 0x7F else "."
            print(f"    0x{reg:02x}: {hex_part}  [{ascii_part}]")
        else:
            print(f"    0x{reg:02x}: TIMEOUT")


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Verify
    send_cmd(fd, [0x82])
    resp = read_resp(fd)
    fw = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace') if resp else "?"
    print(f"Firmware: {fw}\n")

    # Baseline
    dump_state(fd, "baseline")

    # === Test 1: 0x5D parameter exploration ===
    print("\n=== Test 1: 0x5D command with various parameters ===")
    print("  Each test: write 0x5D with params, then read 0x5C and 0x5D back\n")

    test_params = [
        ("mode 0x00", [0x5D, 0x00]),
        ("mode 0x01", [0x5D, 0x01]),
        ("mode 0x02", [0x5D, 0x02]),
        ("mode 0x03", [0x5D, 0x03]),
        ("mode 0x04", [0x5D, 0x04]),
        ("mode 0x00 + path", [0x5D, 0x00] + list(b"0:/Animat00.gif\x00")),
        ("mode 0x01 + path", [0x5D, 0x01] + list(b"0:/Animat00.gif\x00")),
        ("mode 0x02 + path", [0x5D, 0x02] + list(b"0:/Animat00.gif\x00")),
        ("mode 0x03 + path", [0x5D, 0x03] + list(b"0:/Animat00.gif\x00")),
        # Try with size + path
        ("sz+path", [0x5D, 0x01, 0x00, 0x01, 0x0D] + list(b"0:/Animat00.gif\x00")),
    ]

    for desc, cmd in test_params:
        drain(fd)
        send_cmd(fd, cmd)
        time.sleep(0.05)
        r5c = read_register(fd, 0x5C)
        r5d = read_register(fd, 0x5D)
        b5c_11 = r5c[11] if r5c and len(r5c) > 11 else "?"
        b5d_3 = r5d[3] if r5d and len(r5d) > 3 else "?"
        print(f"  {desc:20s} -> 5C[11]={b5c_11:#04x} 5D[3]={b5d_3:#04x}  5D: {hex_dump(r5d, 16)}"
              if isinstance(b5c_11, int) else f"  {desc:20s} -> error")

    # === Test 2: 0x5C write parameter exploration ===
    print("\n=== Test 2: 0x5C with extended parameters ===")
    for params in [
        [0x5C, 0x00, 0x00, 0x00],
        [0x5C, 0x01, 0x00, 0x00],
        [0x5C, 0x02, 0x00, 0x00],
        [0x5C, 0x03, 0x00, 0x00],
        [0x5C, 0x00, 0x01, 0x00],
        [0x5C, 0x00, 0x00, 0x01],
        [0x5C, 0x00, 0x00, 0x00, 0x01],
        [0x5C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03],
    ]:
        drain(fd)
        send_cmd(fd, params)
        time.sleep(0.05)
        resp = read_register(fd, 0x5C)
        hex_params = " ".join(f"{b:02x}" for b in params)
        print(f"  [{hex_params}] -> 5C: {hex_dump(resp, 16)}")

    # === Test 3: Try proper file write with 0x5D setup ===
    print("\n=== Test 3: File write with 0x5D setup ===")

    # Create minimal GIF
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (0, 255, 0))  # GREEN to distinguish
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
        print(f"  Green GIF: {len(gif_data)} bytes")
    except ImportError:
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0x00, 0xFF, 0x00, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])
        print(f"  Minimal GIF: {len(gif_data)} bytes")

    size = len(gif_data)

    # Sequence A: 0x6C stop -> 0x5D setup -> 0x50 begin -> 0x51 data -> 0x52 end -> 0x6E start
    print("\n  Sequence A: stop -> 5D setup -> 50/51/52 -> start")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])  # stop animation
    time.sleep(0.2)

    # Set up 0x5D with path
    drain(fd)
    send_cmd(fd, [0x5D, 0x00] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.05)

    # Begin with size
    drain(fd)
    send_cmd(fd, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)

    # Data chunks
    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    # End
    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)

    # Commit
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.2)

    # Restart
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after seq A")

    # Sequence B: Try with 0x5D mode=0x01 (which triggered the state change)
    print("\n  Sequence B: 5D mode=0x01 -> 50/51/52")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])  # stop
    time.sleep(0.2)

    drain(fd)
    send_cmd(fd, [0x5D, 0x01])  # mode 1 (this changed registers before)
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x50, 0x01, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)

    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.2)

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after seq B")

    # Sequence C: Try 0x5D with the file size encoded in it
    print("\n  Sequence C: 5D with size + path -> 50/51/52")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.2)

    # 0x5D: mode, size (32-bit BE), path
    drain(fd)
    send_cmd(fd, [0x5D, 0x01,
                  (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF] +
                 list(b"0:/Animat00.gif\x00"))
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x50, 0x01])  # begin (size already in 0x5D?)
    time.sleep(0.05)

    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.2)

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after seq C")

    # === Test 4: Try WITHOUT the file path, just raw 0x50/0x51/0x52 ===
    # Maybe 0x50 itself takes the path as parameter
    print("\n=== Test 4: 0x50 with embedded path ===")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.2)

    # 0x50: mode, path, null, size
    path_bytes = list(b"0:/Animat00.gif\x00")
    drain(fd)
    send_cmd(fd, [0x50] + path_bytes +
             [(size >> 24) & 0xFF, (size >> 16) & 0xFF,
              (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)

    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after embedded path")

    # === Test 5: Try command 0x6D ===
    # We know 0x6C and 0x6E but what about 0x6D?
    print("\n=== Test 5: 0x6D command ===")
    resp = read_register(fd, 0x6D)
    print(f"  reg 0x6D: {hex_dump(resp, 20)}")
    for param in [0x00, 0x01, 0x02, 0x03, 0xFF]:
        drain(fd)
        send_cmd(fd, [0x6D, param])
        time.sleep(0.1)
        resp = read_register(fd, 0x6D)
        r6c = read_register(fd, 0x6C)
        r5c = read_register(fd, 0x5C)
        print(f"  0x6D {param:02x}: 6D={hex_dump(resp, 8)} 6C={hex_dump(r6c, 8)} 5C={hex_dump(r5c, 14)}")

    # === Test 6: Read ALL 64 bytes of registers 0x50, 0x5D ===
    print("\n=== Test 6: Full register dumps ===")
    for reg in [0x50, 0x5C, 0x5D]:
        resp = read_register(fd, reg)
        if resp:
            print(f"  Register 0x{reg:02x} ({len(resp)} bytes):")
            for i in range(0, len(resp), 16):
                hex_part = " ".join(f"{b:02x}" for b in resp[i:i+16])
                ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in resp[i:i+16])
                print(f"    {i:3d}: {hex_part}  {ascii_part}")

    # === Test 7: Try alternating command approaches ===
    # What if the transfer uses 0x6B for path and 0x50 with mode=0x03 (matching display mode)?
    print("\n=== Test 7: Transfer with mode matching display mode (0x03) ===")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.2)

    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x50, 0x03, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)

    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after mode=3 transfer")

    # === Test 8: Try with SET_REPORT via hidraw ioctl ===
    # Previous probes used os.write (interrupt OUT), but maybe the device
    # needs SET_REPORT (control pipe) for the data transfer part
    print("\n=== Test 8: SET_REPORT for data transfer ===")
    import fcntl

    def hidioc_sfeature(size):
        IOC_WRITE = 1
        IOC_READ = 2
        return (IOC_WRITE | IOC_READ) << 30 | size << 16 | ord('H') << 8 | 0x06

    # Stop animation
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.2)

    # Set path via normal write
    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.05)

    # Begin via normal write
    drain(fd)
    send_cmd(fd, [0x50, 0x01, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)

    # Send data via SET_FEATURE report
    print("  Sending GIF data via SET_FEATURE...")
    offset = 0
    chunk_num = 0
    errors = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        feature_buf = bytearray(REPORT_SIZE)
        feature_buf[0] = REPORT_ID
        feature_buf[1] = 0x51  # data command
        for i, b in enumerate(chunk):
            feature_buf[2 + i] = b
        try:
            ioctl_num = hidioc_sfeature(REPORT_SIZE)
            fcntl.ioctl(fd, ioctl_num, bytes(feature_buf))
        except Exception as e:
            if errors == 0:
                print(f"    Feature report error: {e}")
            errors += 1
        offset += 63
        chunk_num += 1
    print(f"    Sent {chunk_num} chunks, {errors} errors")

    # End
    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)

    # Commit + restart
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    dump_state(fd, "after SET_FEATURE transfer")

    # === Test 9: What does the reg 0x50 look like with different 0x50 modes? ===
    print("\n=== Test 9: 0x50 mode effects ===")
    for mode in range(0x00, 0x10):
        drain(fd)
        send_cmd(fd, [0x50, mode, 0x00, 0x00, 0x01, 0x00])  # size=256
        time.sleep(0.02)
        resp = read_register(fd, 0x50)
        print(f"  mode 0x{mode:02x}: {hex_dump(resp, 16)}")

    # Clean up - restart animation
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    os.close(fd)
    print("\nDone. CHECK THE OLED FOR ANY CHANGES!")


if __name__ == "__main__":
    main()

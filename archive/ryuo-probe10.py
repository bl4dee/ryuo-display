#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 10
1. Dump and parse HID report descriptor
2. Try CRC/checksum in the end command
3. Try with a full-size animated GIF (multiple frames)
4. Try reading back the file to verify if it was written
5. Try a completely different protocol: bulk data via consecutive 0x51 without 0x50/0x52
6. Try slower transfer with per-packet delays
"""

import os
import time
import struct
import fcntl
import ctypes

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


def crc8(data):
    """Simple CRC8."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


def crc16(data):
    """CRC16-CCITT."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def simple_checksum(data):
    """Simple additive checksum."""
    return sum(data) & 0xFFFF


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # === Get HID Report Descriptor ===
    print("=== HID Report Descriptor ===")
    # HIDIOCGRDESCSIZE = _IOR('H', 0x01, int)
    IOC_READ = 2
    HIDIOCGRDESCSIZE = (IOC_READ << 30) | (4 << 16) | (ord('H') << 8) | 0x01

    size_buf = ctypes.c_int(0)
    try:
        fcntl.ioctl(fd, HIDIOCGRDESCSIZE, size_buf)
        desc_size = size_buf.value
        print(f"  Report descriptor size: {desc_size} bytes")

        # HIDIOCGRDESC = _IOR('H', 0x02, struct hidraw_report_descriptor)
        # struct hidraw_report_descriptor { __u32 size; __u8 value[HID_MAX_DESCRIPTOR_SIZE]; }
        HID_MAX_DESCRIPTOR_SIZE = 4096
        desc_buf = bytearray(4 + HID_MAX_DESCRIPTOR_SIZE)
        struct.pack_into('<I', desc_buf, 0, desc_size)
        HIDIOCGRDESC = (IOC_READ << 30) | ((4 + HID_MAX_DESCRIPTOR_SIZE) << 16) | (ord('H') << 8) | 0x02
        fcntl.ioctl(fd, HIDIOCGRDESC, desc_buf)
        desc = bytes(desc_buf[4:4 + desc_size])
        print(f"  Raw: {hex_dump(desc, desc_size)}")

        # Parse the descriptor
        print("  Parsed:")
        i = 0
        indent = 0
        report_ids = set()
        while i < len(desc):
            prefix = desc[i]
            bSize = prefix & 0x03
            if bSize == 3:
                bSize = 4
            bType = (prefix >> 2) & 0x03
            bTag = (prefix >> 4) & 0x0F

            if bSize == 0:
                value = 0
            elif bSize == 1:
                value = desc[i + 1] if i + 1 < len(desc) else 0
            elif bSize == 2:
                value = struct.unpack_from('<H', desc, i + 1)[0] if i + 2 < len(desc) else 0
            elif bSize == 4:
                value = struct.unpack_from('<I', desc, i + 1)[0] if i + 4 < len(desc) else 0

            # Type names
            type_names = {0: "Main", 1: "Global", 2: "Local"}
            # Tag names
            main_tags = {0x8: "Input", 0x9: "Output", 0xA: "Feature", 0xB: "Collection", 0xC: "End Collection"}
            global_tags = {0x0: "Usage Page", 0x1: "Logical Min", 0x2: "Logical Max",
                          0x3: "Physical Min", 0x4: "Physical Max", 0x5: "Unit Exponent",
                          0x6: "Unit", 0x7: "Report Size", 0x8: "Report ID",
                          0x9: "Report Count", 0xA: "Push", 0xB: "Pop"}
            local_tags = {0x0: "Usage", 0x1: "Usage Min", 0x2: "Usage Max"}

            if bType == 0:
                tag_name = main_tags.get(bTag, f"Main({bTag})")
            elif bType == 1:
                tag_name = global_tags.get(bTag, f"Global({bTag})")
            else:
                tag_name = local_tags.get(bTag, f"Local({bTag})")

            if tag_name == "End Collection":
                indent = max(0, indent - 2)
            elif tag_name == "Report ID":
                report_ids.add(value)

            print(f"    {'  ' * indent}{tag_name}: 0x{value:04X} ({value})")

            if tag_name == "Collection":
                indent += 2

            i += 1 + bSize

        print(f"\n  Report IDs found: {[f'0x{rid:02X}' for rid in sorted(report_ids)]}")

    except Exception as e:
        print(f"  Error reading descriptor: {e}")

    # === Create test GIF ===
    try:
        from PIL import Image
        import io

        # Create a full 160x128 animated GIF with 2 frames
        frames = []
        # Frame 1: solid blue
        frames.append(Image.new('RGB', (160, 128), (0, 0, 255)))
        # Frame 2: solid yellow
        frames.append(Image.new('RGB', (160, 128), (255, 255, 0)))

        buf = io.BytesIO()
        frames[0].save(buf, format='GIF', save_all=True,
                       append_images=frames[1:], duration=[500, 500], loop=0)
        gif_data = buf.getvalue()
        print(f"\nAnimated GIF: {len(gif_data)} bytes, 2 frames")

        # Also create single-frame GIF
        buf2 = io.BytesIO()
        Image.new('RGB', (160, 128), (255, 0, 0)).save(buf2, format='GIF')
        gif_single = buf2.getvalue()
        print(f"Single-frame GIF: {len(gif_single)} bytes")

    except ImportError:
        print("PIL not available, using minimal GIF")
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0xFF, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])
        gif_single = gif_data

    # === Test 1: Transfer with CRC/checksum in end command ===
    print("\n=== Test 1: Transfer with checksum ===")
    size = len(gif_data)

    for checksum_name, checksum_fn in [("XOR", lambda d: (0, sum(d) & 0xFF)),
                                        ("CRC8", lambda d: (0, crc8(d))),
                                        ("CRC16", lambda d: (crc16(d) >> 8, crc16(d) & 0xFF)),
                                        ("Sum16", lambda d: (simple_checksum(d) >> 8, simple_checksum(d) & 0xFF))]:
        chk_hi, chk_lo = checksum_fn(gif_data)
        drain(fd)
        send_cmd(fd, [0x6C, 0x01])  # stop
        time.sleep(0.1)

        drain(fd)
        send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
        time.sleep(0.02)

        drain(fd)
        send_cmd(fd, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                      (size >> 8) & 0xFF, size & 0xFF])
        time.sleep(0.02)

        offset = 0
        while offset < len(gif_data):
            chunk = gif_data[offset:offset + 63]
            drain(fd)
            send_cmd(fd, [0x51] + list(chunk))
            offset += 63

        # End with checksum
        drain(fd)
        send_cmd(fd, [0x52, chk_hi, chk_lo])
        time.sleep(0.05)

        drain(fd)
        send_cmd(fd, [0x3F, 0x55])
        time.sleep(0.05)

        drain(fd)
        send_cmd(fd, [0x6E, 0x00])
        time.sleep(0.3)

        print(f"  {checksum_name}: chk=0x{chk_hi:02x}{chk_lo:02x} - check OLED")

    # === Test 2: Transfer with sequence numbers ===
    print("\n=== Test 2: Data with sequence numbers ===")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.02)

    drain(fd)
    send_cmd(fd, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.02)

    # Send with sequence number in data
    offset = 0
    seq = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 62]  # 62 bytes of data + 1 seq byte
        drain(fd)
        send_cmd(fd, [0x51, seq & 0xFF] + list(chunk))
        offset += 62
        seq += 1

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.05)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)
    print(f"  Sent with seq numbers - check OLED")

    # === Test 3: Try without 0x50/0x52 framing ===
    # Just blast 0x51 packets
    print("\n=== Test 3: Raw 0x51 blast without framing ===")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.1)

    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    time.sleep(0.05)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)
    print(f"  Sent raw blast - check OLED")

    # === Test 4: Try with slow transfer (per-packet ack) ===
    print("\n=== Test 4: Slow transfer with per-packet read ===")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    resp = read_resp(fd, timeout=0.2)
    if resp:
        print(f"  Begin response: {hex_dump(resp, 16)}")

    offset = 0
    chunk_num = 0
    acks = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        # Wait for ack
        resp = read_resp(fd, timeout=0.05)
        if resp:
            acks += 1
            if chunk_num < 3:
                print(f"    chunk {chunk_num} ack: {hex_dump(resp, 8)}")
        offset += 63
        chunk_num += 1
        time.sleep(0.01)  # 10ms delay between packets

    print(f"  Sent {chunk_num} chunks, got {acks} acks")

    drain(fd)
    send_cmd(fd, [0x52])
    resp = read_resp(fd, timeout=0.2)
    if resp:
        print(f"  End response: {hex_dump(resp, 16)}")

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    # === Test 5: Try reading back file data ===
    print("\n=== Test 5: Try reading file data via 0x50+0x53 ===")
    # Maybe 0x50 for begin, 0x53 for read data (like 0x51 is write data)?
    drain(fd)
    send_cmd(fd, [0x50, 0x00, 0x00, 0x00, 0x01, 0x00])  # begin, size=256
    time.sleep(0.05)

    for read_cmd in [0xD0, 0xD1, 0xD2, 0xD3, 0x53, 0x54, 0x55]:
        drain(fd)
        send_cmd(fd, [read_cmd])
        resp = read_resp(fd, timeout=0.1)
        if resp and not all(b == 0 for b in resp[2:]):
            print(f"  cmd 0x{read_cmd:02x}: {hex_dump(resp, 20)}")

    # === Test 6: Try reading file content via GET_FEATURE ===
    print("\n=== Test 6: GET_FEATURE to read file ===")
    IOC_WRITE = 1
    IOC_READ = 2

    for report_id in [0xEC, 0x50, 0x51, 0x5D, 0x00]:
        gbuf = bytearray(REPORT_SIZE)
        gbuf[0] = report_id
        try:
            gfeat_ioctl = (IOC_WRITE | IOC_READ) << 30 | REPORT_SIZE << 16 | ord('H') << 8 | 0x07
            result = fcntl.ioctl(fd, gfeat_ioctl, gbuf)
            result_bytes = bytes(result) if isinstance(result, (bytes, bytearray)) else bytes(gbuf)
            print(f"  GET_FEATURE id=0x{report_id:02x}: {hex_dump(result_bytes, 20)}")
        except Exception as e:
            print(f"  GET_FEATURE id=0x{report_id:02x}: {e}")

    # === Test 7: Try 0x50 with mode=0xFF and other special modes ===
    print("\n=== Test 7: Special 0x50 modes ===")
    for mode in [0x10, 0x20, 0x40, 0x80, 0xFE, 0xFF]:
        drain(fd)
        send_cmd(fd, [0x50, mode, 0x00, 0x00, 0x01, 0x0D])
        time.sleep(0.02)
        resp = read_register(fd, 0x50)
        print(f"  mode 0x{mode:02x}: reg50 = {hex_dump(resp, 12)}")

    # === Test 8: Try using command 0x60-0x6F more systematically ===
    print("\n=== Test 8: 0x60-0x6F systematic ===")
    for cmd in range(0x60, 0x70):
        if cmd in (0x6C, 0x6E):
            continue  # skip known animation controls
        drain(fd)
        send_cmd(fd, [cmd, 0x00])
        time.sleep(0.02)
        # Read several registers
        r5c = read_register(fd, 0x5C)
        b5c_12 = r5c[12] if r5c and len(r5c) > 12 else "?"
        b5c_11 = r5c[11] if r5c and len(r5c) > 11 else "?"
        r6c = read_register(fd, 0x6C)
        b6c_2 = r6c[2] if r6c and len(r6c) > 2 else "?"
        print(f"  cmd 0x{cmd:02x}: 5C[11]={b5c_11} 5C[12]={b5c_12} 6C[2]={b6c_2}")

    # === Test 9: Can we write to reg 0x5C byte 12 (display mode)? ===
    print("\n=== Test 9: Direct display mode change ===")
    # 0x5C with 10 bytes to reach byte 12
    for val in [0, 1, 2, 4, 5]:
        drain(fd)
        # Write: cmd=0x5C, then 10 bytes padding, then the mode value
        send_cmd(fd, [0x5C] + [0x00]*10 + [val])
        time.sleep(0.3)
        resp = read_register(fd, 0x5C)
        mode_val = resp[12] if resp and len(resp) > 12 else None
        print(f"  Set mode={val}: reg5C[12]={mode_val}")
        print(f"    (CHECK OLED - did display change?)")
        time.sleep(0.5)

    # Restore mode 3
    drain(fd)
    send_cmd(fd, [0x5C] + [0x00]*10 + [0x03])
    time.sleep(0.1)

    # Restart animation
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

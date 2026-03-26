#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 8
Based on research findings:
- Try Ryujin II commands (0x99, 0x9A, 0xA0, 0xA1)
- Try MSI-style start/data pattern (0xC0/0xC1)
- Try untested high command bytes (0xA0-0xFF write range)
- Try 0x35 with file path + data transfer
- Try 0x6B path + 0x50/0x51/0x52 with proper sequencing
- Attempt to find the "upload GIF file" command
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


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Verify basic comms
    drain(fd)
    send_cmd(fd, [0x82])
    resp = read_resp(fd)
    if resp:
        fw = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"Firmware: {fw}")
    else:
        print("ERROR: No response to firmware query!")
        os.close(fd)
        return

    # === Test 1: Ryujin II commands ===
    print("\n=== Test 1: Ryujin II commands on Ryuo I ===")
    ryujin_cmds = [
        (0x99, "cooler status"),
        (0x9A, "cooler duty"),
        (0xA0, "controller fans"),
        (0xA1, "controller duty"),
    ]
    for cmd, desc in ryujin_cmds:
        drain(fd)
        # Read command (0x80+)
        send_cmd(fd, [cmd])
        resp = read_resp(fd, timeout=0.3)
        if resp:
            print(f"  cmd 0x{cmd:02x} ({desc}): {hex_dump(resp, 16)}")
        else:
            print(f"  cmd 0x{cmd:02x} ({desc}): no response")

    # === Test 2: Full read register scan 0x80-0xFF ===
    # We previously only scanned 0x80-0xFF (regs 0x00-0x7F)
    # But what about sending raw bytes 0x80-0xFF as write commands?
    print("\n=== Test 2: High-range read commands (regs 0x00-0x7F already known) ===")
    print("  Scanning for any responses to commands 0x90-0xFF (non-read range)...")
    for cmd in range(0x00, 0x80):
        # These are WRITE commands - send with no args to see if they respond
        drain(fd)
        send_cmd(fd, [cmd])
        resp = read_resp(fd, timeout=0.08)
        if resp and resp[1] != 0x00:
            # Got an unexpected response to a write command
            print(f"  write cmd 0x{cmd:02x} responded: {hex_dump(resp, 16)}")

    # === Test 3: MSI-style protocol ===
    print("\n=== Test 3: MSI-style OLED commands ===")
    # MSI uses 0xC0 for start, 0xC1 for data
    # But these are in the "read" range (0x80+) on our device
    # So 0xC0 would be "read register 0x40" and 0xC1 = "read register 0x41"
    # Let's try them as write commands: 0x40 and 0x41
    for cmd in [0x40, 0x41, 0x42, 0x43, 0x44, 0x45]:
        drain(fd)
        # Try as a "start transfer" with file size
        size = 1000  # arbitrary
        send_cmd(fd, [cmd, (size >> 8) & 0xFF, size & 0xFF, 0x00, 0x00])
        resp = read_resp(fd, timeout=0.15)
        if resp:
            print(f"  cmd 0x{cmd:02x} with size: {hex_dump(resp, 16)}")
        else:
            print(f"  cmd 0x{cmd:02x} with size: (no response, write accepted)")

    # Check if any register changed
    r50 = read_register(fd, 0x50)
    print(f"  reg 0x50 after: {hex_dump(r50, 16)}")

    # === Test 4: Proper file upload sequence ===
    # Theory: 0x6B sets path, 0x50 begins transfer, 0x51 sends data, 0x52 ends
    # But maybe we need the EXACT right parameters and ordering
    print("\n=== Test 4: Careful file upload attempt ===")

    # Create a minimal valid GIF
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
        print(f"  Created 160x128 red GIF: {len(gif_data)} bytes")
    except ImportError:
        # Hardcode a minimal 1x1 GIF
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0xFF, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])
        print(f"  Using minimal 1x1 GIF: {len(gif_data)} bytes")

    # Step 1: Read current state
    print("\n  Step 1: Current state")
    for reg in [0x50, 0x5C, 0x5D, 0x6B, 0x6C]:
        resp = read_register(fd, reg)
        print(f"    reg 0x{reg:02x}: {hex_dump(resp, 20)}")

    # Step 2: Stop animation
    print("\n  Step 2: Stop animation")
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.2)
    resp = read_register(fd, 0x6C)
    print(f"    reg 0x6C after stop: {hex_dump(resp, 10)}")

    # Step 3: Set file path FIRST
    print("\n  Step 3: Set file path")
    path = b"0:/Animat00.gif"
    drain(fd)
    send_cmd(fd, [0x6B] + list(path) + [0x00])
    time.sleep(0.05)
    resp = read_register(fd, 0x6B)
    print(f"    reg 0x6B after set: {hex_dump(resp, 20)}")

    # Step 4: Begin transfer with size in LITTLE-ENDIAN (maybe device expects LE?)
    print("\n  Step 4: Begin transfer (trying various size encodings)")
    size = len(gif_data)

    # Try 4a: size as big-endian 32-bit
    drain(fd)
    send_cmd(fd, [0x50, 0x00,
                  (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.05)
    resp = read_register(fd, 0x50)
    print(f"    reg 0x50 after begin(BE): {hex_dump(resp, 16)}")

    # Step 5: Send data
    print("\n  Step 5: Send data chunks")
    offset = 0
    chunk_num = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63
        chunk_num += 1
    print(f"    Sent {chunk_num} chunks, {size} bytes")

    # Step 6: End transfer
    print("\n  Step 6: End transfer")
    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)
    resp = read_register(fd, 0x50)
    print(f"    reg 0x50 after end: {hex_dump(resp, 16)}")

    # Step 7: Save/commit
    print("\n  Step 7: Save/commit")
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)

    # Step 8: Restart animation
    print("\n  Step 8: Restart animation")
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.5)

    # Read final state
    print("\n  Final state:")
    for reg in [0x50, 0x5C, 0x5D, 0x6B, 0x6C]:
        resp = read_register(fd, reg)
        print(f"    reg 0x{reg:02x}: {hex_dump(resp, 20)}")

    # === Test 5: Try 0x35 carefully ===
    # 0x35 caused pipe errors before but the research suggests it might be
    # a "set mode" command for AURA/display
    print("\n=== Test 5: 0x35 command (careful) ===")
    # In OpenRGB: 0x35 = set mode for AURA
    # byte2 might be: 0=static, 1=breathing, etc.
    # byte3-5: parameters
    for mode in range(0, 8):
        drain(fd)
        try:
            send_cmd(fd, [0x35, mode, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
            resp = read_resp(fd, timeout=0.1)
            if resp:
                print(f"  0x35 mode={mode}: {hex_dump(resp, 16)}")
            else:
                print(f"  0x35 mode={mode}: ok (no response)")
        except Exception as e:
            print(f"  0x35 mode={mode}: ERROR {e}")

    # === Test 6: Comprehensive write command scan ===
    # Send each command 0x00-0x7F and immediately read back ALL known registers
    # to detect ANY state change
    print("\n=== Test 6: Detect state changes from untested write commands ===")

    # Skip known commands to avoid side effects
    skip = {0x2A, 0x3B, 0x3F, 0x40, 0x50, 0x51, 0x52, 0x6B, 0x6C, 0x6E,
            0x30, 0x31, 0x35}

    # Read baseline for comparison
    baseline = {}
    for reg in [0x50, 0x5C, 0x5D, 0x6B, 0x6C, 0x6D, 0x6A]:
        baseline[reg] = read_register(fd, reg)

    for cmd in range(0x00, 0x80):
        if cmd in skip:
            continue
        drain(fd)
        try:
            send_cmd(fd, [cmd, 0x01, 0x00, 0x00])
            time.sleep(0.02)
        except:
            continue

        # Check if any register changed
        changed = False
        for reg in [0x50, 0x5C, 0x5D, 0x6B, 0x6C, 0x6D, 0x6A]:
            resp = read_register(fd, reg)
            if resp != baseline[reg]:
                if not changed:
                    print(f"  cmd 0x{cmd:02x} changed something!")
                    changed = True
                print(f"    reg 0x{reg:02x}: {hex_dump(baseline[reg], 12)} -> {hex_dump(resp, 12)}")
                baseline[reg] = resp  # update baseline

    # === Test 7: Try reading MORE registers we haven't seen ===
    # Previous scans used 0x80-0xFF for registers 0x00-0x7F
    # But maybe there's a way to read higher registers?
    print("\n=== Test 7: Extended register reads ===")
    # What if we use 0x80+reg and reg > 0x7F wraps around?
    # The read response byte 1 = reg, so let's look at all response patterns
    print("  Already covered 0x80-0xFF (regs 0x00-0x7F)")
    print("  Trying multi-byte register addressing...")
    for b2 in [0x00, 0x01, 0x80, 0xFF]:
        for b3 in [0x00, 0x01]:
            drain(fd)
            send_cmd(fd, [0x82, b2, b3])  # firmware query with extra bytes
            resp = read_resp(fd, timeout=0.15)
            if resp and resp != read_register(fd, 0x02):
                print(f"  0x82 {b2:02x} {b3:02x}: {hex_dump(resp, 16)}")

    # === Test 8: Try commands with 0xEC prefix in the data ===
    # Some devices expect the report ID inside the data too
    print("\n=== Test 8: Double report ID prefix ===")
    drain(fd)
    # Send [0xEC, 0x82, ...] as the data (so the full report is [0xEC, 0xEC, 0x82, ...])
    send_cmd(fd, [0xEC, 0x82])
    resp = read_resp(fd, timeout=0.3)
    if resp:
        print(f"  Double 0xEC prefix: {hex_dump(resp, 16)}")
    else:
        print(f"  Double 0xEC prefix: no response")

    # === Test 9: Try the 0xB0 command (AURA mode?) ===
    print("\n=== Test 9: 0xB0 and 0x60 commands ===")
    for cmd in [0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
                0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F]:
        if cmd in skip:
            continue
        drain(fd)
        send_cmd(fd, [cmd, 0x00, 0x00, 0x00])
        resp = read_resp(fd, timeout=0.08)
        if resp:
            print(f"  cmd 0x{cmd:02x}: {hex_dump(resp, 16)}")

    # === Test 10: Check if 0x5C controls display mode ===
    print("\n=== Test 10: Display mode via 0x5C ===")
    resp = read_register(fd, 0x5C)
    print(f"  Current 0x5C: {hex_dump(resp, 20)}")

    # The display mode value was 3. What if we change it?
    for mode in [0x00, 0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A]:
        drain(fd)
        send_cmd(fd, [0x5C, mode])
        time.sleep(0.2)
        resp = read_register(fd, 0x5C)
        # Check byte 12 which was the "display mode" value
        mode_val = resp[12] if resp and len(resp) > 12 else None
        print(f"  After write 0x5C {mode:02x}: mode byte = {mode_val}")
        # Also check if the OLED display changed!

    # Restore
    drain(fd)
    send_cmd(fd, [0x5C, 0x03])
    time.sleep(0.1)

    # === Test 11: Look at ALL 64 bytes of register 0x5C deeply ===
    print("\n=== Test 11: Full register 0x5C dump ===")
    resp = read_register(fd, 0x5C)
    if resp:
        for i in range(0, len(resp), 16):
            hex_part = " ".join(f"{b:02x}" for b in resp[i:i+16])
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in resp[i:i+16])
            print(f"  {i:3d}: {hex_part}  {ascii_part}")

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

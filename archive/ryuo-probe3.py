#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe - Phase 3
Systematic write command testing with state change detection.
Also tries LCD controller pass-through patterns.
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


def hex_dump(data, max_bytes=32):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:max_bytes])


def read_register(fd, reg):
    drain(fd)
    send_cmd(fd, [0x80 + reg])
    return read_resp(fd)


def read_state(fd):
    """Read key registers and return as dict."""
    state = {}
    for reg in [0x30, 0x50, 0x5C, 0x5D, 0x6B, 0x6C]:
        state[reg] = read_register(fd, reg)
    return state


def compare_states(before, after):
    """Print any registers that changed."""
    changed = False
    for reg in before:
        if before[reg] != after[reg]:
            print(f"    CHANGED reg 0x{reg:02x}:")
            print(f"      before: {hex_dump(before[reg])}")
            print(f"      after:  {hex_dump(after[reg])}")
            changed = True
    return changed


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Get baseline state
    print("Reading baseline state...")
    baseline = read_state(fd)
    for reg, data in baseline.items():
        print(f"  reg 0x{reg:02x}: {hex_dump(data)}")

    # Phase 1: Test each write command 0x00-0x7F individually
    # and check for state changes
    print("\n=== Phase 1: Individual write commands with state check ===")
    print("Testing commands 0x00-0x7F with arg 0x01...")

    skip = {0x2A, 0x3B, 0x40}  # Skip fan speed, LED mode, direct LED
    found_changes = []

    for cmd in range(0x00, 0x80):
        if cmd in skip:
            continue
        before = read_state(fd)
        drain(fd)
        send_cmd(fd, [cmd, 0x01])
        read_resp(fd, timeout=0.1)  # consume any response
        time.sleep(0.05)
        after = read_state(fd)
        if compare_states(before, after):
            found_changes.append(cmd)
            print(f"  ** Command 0x{cmd:02x} caused a state change! **")

    if found_changes:
        print(f"\nCommands that changed state: {[f'0x{c:02x}' for c in found_changes]}")
    else:
        print("\nNo state changes detected from individual write commands.")

    # Phase 2: Try multi-step file transfer sequences
    print("\n=== Phase 2: Multi-step file transfer attempts ===")

    test_gif_data = bytes([0x47, 0x49, 0x46, 0x38, 0x39, 0x61])  # "GIF89a" header

    sequences = [
        ("Seq A: 0x60 open + 0x61 data + 0x62 close", [
            [0x60, 0x01, 0x00],  # open
            [0x61] + list(test_gif_data),  # data
            [0x62],  # close
        ]),
        ("Seq B: 0x50 begin + 0x51 data + 0x52 end", [
            [0x50, 0x01, 0x00, 0x00, 0x00, 0x06],  # begin with size
            [0x51] + list(test_gif_data),  # data
            [0x52],  # end
        ]),
        ("Seq C: 0x5A open + 0x5B data + 0x5C close", [
            [0x5A, 0x01],  # open
            [0x5B] + list(test_gif_data),  # data
            [0x5C, 0x01],  # close
        ]),
        ("Seq D: 0x58 begin + 0x59 data + 0x5A commit", [
            [0x58, 0x01, 0x00, 0x00, 0x00, 0x06],
            [0x59] + list(test_gif_data),
            [0x5A, 0x00],
        ]),
        ("Seq E: 0x6B path + 0x60 begin + 0x61 data + 0x3F commit", [
            [0x6B] + list(b"0:/test.gif\x00"),
            [0x60, 0x01, 0x00, 0x00, 0x00, 0x06],
            [0x61] + list(test_gif_data),
            [0x3F, 0x55],
        ]),
    ]

    for desc, cmds in sequences:
        print(f"\n  {desc}")
        before = read_state(fd)
        for i, cmd in enumerate(cmds):
            drain(fd)
            send_cmd(fd, cmd)
            resp = read_resp(fd, timeout=0.15)
            if resp is not None:
                print(f"    step {i}: response {hex_dump(resp, 16)}")
            time.sleep(0.05)
        after = read_state(fd)
        if compare_states(before, after):
            print(f"    ** Sequence caused state change! **")
        else:
            print(f"    (no state change)")

    # Phase 3: Try LCD controller pass-through
    # Common 1.77" TFT controllers: ST7735, ILI9163
    print("\n=== Phase 3: LCD controller pass-through attempts ===")

    lcd_patterns = [
        # Try wrapping ST7735 commands in HID protocol
        # Format: [HID_CMD, LCD_CMD, data...]
        ("ST7735 via 0x58: CASET", [0x58, 0x2A, 0x00, 0x00, 0x00, 0x9F]),  # col 0-159
        ("ST7735 via 0x58: RASET", [0x58, 0x2B, 0x00, 0x00, 0x00, 0x7F]),  # row 0-127
        ("ST7735 via 0x58: RAMWR", [0x58, 0x2C] + [0xF8, 0x00] * 10),     # write red pixels

        # Try using the 0x60 command as pass-through
        ("LCD via 0x60: CASET", [0x60, 0x2A, 0x00, 0x00, 0x00, 0x9F]),
        ("LCD via 0x60: RASET", [0x60, 0x2B, 0x00, 0x00, 0x00, 0x7F]),
        ("LCD via 0x60: RAMWR", [0x60, 0x2C] + [0xF8, 0x00] * 10),

        # Maybe the HID command IS the LCD command
        # 0x2C is "RAMWR" in ST7735 and also a valid HID write command
        ("Direct LCD RAMWR 0x2C", [0x2C] + [0xF8, 0x00] * 31),

        # Try all data as pixel write after RAMWR setup
        ("Setup+pixel: CASET", [0x2A, 0x00, 0x00, 0x00, 0x9F]),
        ("Setup+pixel: RASET", [0x2B, 0x00, 0x00, 0x00, 0x7F]),
        ("Setup+pixel: RAMWR", [0x2C] + [0x00, 0x1F] * 31),  # blue pixels
    ]

    before = read_state(fd)
    for desc, data in lcd_patterns:
        drain(fd)
        send_cmd(fd, data)
        resp = read_resp(fd, timeout=0.1)
        if resp is not None:
            print(f"  {desc}: {hex_dump(resp, 16)}")
        else:
            print(f"  {desc}: (no response)")
        time.sleep(0.05)
    after = read_state(fd)
    if compare_states(before, after):
        print("  ** LCD pass-through caused state change! **")

    # Phase 4: Interesting - try write with register-matching format
    print("\n=== Phase 4: Write with register-matching offsets ===")

    # Register 0x5C read: ec 5c [00 00 00 00 00 00 00 00 00 00] 03
    # The value 03 is at data offset 10 (position 12 in full packet)
    # Try writing with value at same offset
    print("  Writing 0x5C with mode at offset 10:")
    for mode in [0, 1, 2, 4, 5]:
        drain(fd)
        data = [0x5C] + [0x00]*10 + [mode]
        send_cmd(fd, data)
        read_resp(fd, timeout=0.1)
        time.sleep(0.1)
        resp = read_register(fd, 0x5C)
        mode_val = resp[12] if resp and len(resp) > 12 else None
        print(f"    write mode={mode}: readback mode={mode_val}")

    # Register 0x5D read: ec 5d [00 00 00 00 00] 30:/ path
    # Path starts at data offset 5 (position 7 in full packet)
    # Try writing path at same offset
    print("\n  Writing 0x5D with path at offset 5:")
    drain(fd)
    path = b"0:/Animat00.gif"
    data = [0x5D] + [0x00]*5 + list(path) + [0x00]
    send_cmd(fd, data)
    read_resp(fd, timeout=0.1)
    time.sleep(0.1)
    resp = read_register(fd, 0x5D)
    print(f"    readback: {hex_dump(resp)}")

    # Phase 5: Brute force - try EVERY write command with sub-byte 0x00
    # to find ones that produce responses (some writes might ACK)
    print("\n=== Phase 5: Write commands that produce responses ===")
    for cmd in range(0x00, 0x80):
        if cmd in skip:
            continue
        drain(fd)
        send_cmd(fd, [cmd, 0x00, 0x00, 0x00])
        resp = read_resp(fd, timeout=0.1)
        if resp is not None:
            print(f"  0x{cmd:02x}: {hex_dump(resp, 16)}")

    os.close(fd)
    print("\nDone. Check the OLED - did anything change?")


if __name__ == "__main__":
    main()

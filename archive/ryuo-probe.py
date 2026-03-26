#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe
Report ID is 0xEC. Commands are: [0xEC, cmd_byte, ...]
"""

import os
import sys
import time

DEVICE = "/dev/hidraw12"
REPORT_ID = 0xEC
REPORT_SIZE = 65  # 1 byte report ID (0xEC) + 64 bytes data


def send_cmd(fd, data):
    """Send HID report: 0xEC + 64 bytes (data padded with zeros)."""
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    for i, b in enumerate(data[:64]):
        buf[1 + i] = b
    os.write(fd, bytes(buf))


def read_resp(fd, timeout=0.3):
    """Read response with timeout."""
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


def is_all_zero(data):
    if data is None:
        return False
    return all(b == 0 for b in data)


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE} (report ID = 0x{REPORT_ID:02x})")
    drain(fd)

    # Phase 1: Firmware version (known command)
    print("\n=== Phase 1: Known commands ===")
    drain(fd)
    send_cmd(fd, [0x82])  # EC is report ID, 0x82 is the command
    resp = read_resp(fd)
    print(f"  cmd 82 (firmware): {hex_dump(resp)}")

    # Phase 2: Full scan
    print("\n=== Phase 2: Full scan cmd 0x00..0xFF ===")

    results = {}
    for cmd in range(0x00, 0x100):
        drain(fd)
        try:
            send_cmd(fd, [cmd])
            resp = read_resp(fd, timeout=0.15)
        except (BrokenPipeError, OSError) as e:
            print(f"  0x{cmd:02x}: ERROR {e}")
            # Reopen device
            try:
                os.close(fd)
            except:
                pass
            time.sleep(0.1)
            fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
            drain(fd)
            continue

        if resp is not None and not is_all_zero(resp):
            results[cmd] = resp
            print(f"  0x{cmd:02x}: {hex_dump(resp)}")
        elif cmd % 0x40 == 0:
            status = "timeout" if resp is None else "all-zero"
            print(f"  ... 0x{cmd:02x} ({status}), scanning...")

    print(f"\n=== Summary: {len(results)} commands with non-zero responses ===")
    for cmd, resp in sorted(results.items()):
        print(f"  {cmd:02x}: {hex_dump(resp)}")

    # Phase 3: Sub-commands on interesting results
    print("\n=== Phase 3: Sub-commands ===")
    for cmd in sorted(results.keys()):
        for sub in range(0x00, 0x10):
            drain(fd)
            try:
                send_cmd(fd, [cmd, sub])
                resp = read_resp(fd, timeout=0.15)
            except (BrokenPipeError, OSError):
                try:
                    os.close(fd)
                except:
                    pass
                time.sleep(0.1)
                fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
                drain(fd)
                continue

            if resp is not None and not is_all_zero(resp):
                resp_hex = hex_dump(resp)
                base_hex = hex_dump(results.get(cmd))
                if resp_hex != base_hex:
                    print(f"  {cmd:02x} {sub:02x}: {resp_hex}")

    # Phase 4: Display protocol candidates
    print("\n=== Phase 4: ASUS display protocol patterns ===")
    candidates = [
        ([0x35, 0x01], "set-mode img"),
        ([0x35, 0x02], "set-mode gif"),
        ([0x35, 0x03], "set-mode banner"),
        ([0x35, 0x00, 0x64], "brightness 100"),
        ([0x36, 0x00], "img-begin"),
        ([0x36, 0x01], "img-begin alt"),
        ([0x38, 0x01], "LCD ctrl 38"),
        ([0x39, 0x01], "LCD ctrl 39"),
        ([0x3A, 0x01], "LCD ctrl 3A"),
        ([0xB0], "AURA init"),
        ([0x30, 0x01], "mode 1"),
        ([0x30, 0x02], "mode 2"),
        ([0x30, 0x03], "mode 3"),
        ([0x40, 0x00, 0x00, 0xA0, 0x00, 0x80, 0x00], "160x128 transfer"),
        # LiveDash-style commands
        ([0x20], "status 20"),
        ([0x21], "status 21"),
        ([0x10], "ctrl 10"),
        ([0x11], "ctrl 11"),
        ([0x12], "ctrl 12"),
        ([0x60], "query 60"),
        ([0x70], "query 70"),
    ]

    for data, desc in candidates:
        drain(fd)
        try:
            send_cmd(fd, data)
            resp = read_resp(fd)
        except (BrokenPipeError, OSError):
            try:
                os.close(fd)
            except:
                pass
            time.sleep(0.1)
            fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
            drain(fd)
            resp = None

        if resp is not None:
            z = "ALL-ZERO" if is_all_zero(resp) else hex_dump(resp)
            print(f"  {desc}: {z}")
        else:
            print(f"  {desc}: TIMEOUT")

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

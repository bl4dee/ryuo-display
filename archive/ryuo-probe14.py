#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 14
Focus: Display mode switching and sensor value injection.
Theory 1: The device has built-in modes (temp, clock, anim, custom)
          activated via a command we haven't found.
Theory 2: LiveDash sends CPU temp values to the device for display.
Theory 3: The HID commands map to ST7735/SSD1351 display controller regs.

ST7735 key commands: 0x2A=CASET, 0x2B=RASET, 0x2C=RAMWR, 0x36=MADCTL
"""

import os
import time
import sys

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


def try_mode_switch(fd, desc, cmds, wait=1.0):
    """Send commands, wait, then check display mode register."""
    print(f"\n  {desc}")
    for cmd in cmds:
        drain(fd)
        send_cmd(fd, cmd)
        time.sleep(0.05)
    # Commit
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(wait)
    # Check mode
    r5c = read_register(fd, 0x5C)
    mode = r5c[12] if r5c and len(r5c) > 12 else "?"
    b11 = r5c[11] if r5c and len(r5c) > 11 else "?"
    r6c = read_register(fd, 0x6C)
    anim = r6c[2] if r6c and len(r6c) > 2 else "?"
    print(f"    mode={mode} flag={b11} anim={anim}")
    print(f"    >>> CHECK OLED NOW <<<")
    return mode


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Verify
    send_cmd(fd, [0x82])
    resp = read_resp(fd)
    fw = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace') if resp else "?"
    print(f"Firmware: {fw}")

    # === Test 1: Display mode via 0x35 with all possible zone IDs ===
    print("\n=== Test 1: 0x35 display mode with zone sweep ===")
    print("  LiveDash modes: 0=temp, 1=clock, 2=usage, 3=anim, 4=custom, 5=banner")

    # The AURA 0x35 format is: [0x35, zone, mode, speed, dir, colorcount, ...]
    # Try zone IDs that might refer to the OLED
    for zone in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05,
                 0x10, 0x20, 0x30, 0x40, 0x50, 0x60,
                 0x80, 0xFF]:
        for mode_id in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05]:
            drain(fd)
            send_cmd(fd, [0x35, zone, mode_id, 0x00, 0x00, 0x00, 0x00, 0x00])
            time.sleep(0.02)

    # Commit and check
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.5)
    r5c = read_register(fd, 0x5C)
    print(f"  After sweep: mode={r5c[12] if r5c and len(r5c) > 12 else '?'}")

    # === Test 2: Try writing to 0x5C with commit ===
    print("\n=== Test 2: Mode change via 0x5C + commit ===")
    for mode_val in [0x00, 0x01, 0x02, 0x04, 0x05]:
        # Stop anim first
        drain(fd)
        send_cmd(fd, [0x6C, 0x01])
        time.sleep(0.1)

        # Try writing mode at different byte positions
        # The mode is at readback byte 12, which is payload byte 10 (after report ID and register echo)
        drain(fd)
        send_cmd(fd, [0x5C, mode_val])
        time.sleep(0.05)

        # Commit
        drain(fd)
        send_cmd(fd, [0x3F, 0x55])
        time.sleep(0.3)

        # Restart
        drain(fd)
        send_cmd(fd, [0x6E, 0x00])
        time.sleep(1.0)

        r5c = read_register(fd, 0x5C)
        mode = r5c[12] if r5c and len(r5c) > 12 else "?"
        print(f"  wrote 0x5C mode={mode_val}: readback mode={mode}")
        print(f"    >>> CHECK OLED - did mode change? <<<")

    # Restore mode 3
    drain(fd)
    send_cmd(fd, [0x5C, 0x03])
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    # === Test 3: Send CPU temp values to various registers ===
    print("\n=== Test 3: Send temperature values ===")
    # Maybe there's a register to set CPU temp for display
    # Try common offsets where LiveDash might write temp
    cpu_temp = 42  # fake CPU temp

    for reg in [0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
                0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F,
                0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27,
                0x28, 0x29, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F,
                0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67,
                0x68, 0x69, 0x6A]:
        if reg == 0x2A:
            continue  # skip fan duty
        drain(fd)
        send_cmd(fd, [reg, cpu_temp, 0x00, cpu_temp])
        time.sleep(0.01)

    time.sleep(0.5)
    print(f"  Sent temp={cpu_temp}°C to many registers")
    print(f"  >>> CHECK OLED for temperature display <<<")

    # === Test 4: ST7735 display controller commands ===
    print("\n=== Test 4: ST7735/SSD1351 direct commands ===")

    # ST7735 command set:
    # 0x01 = SWRESET, 0x11 = SLPOUT, 0x29 = DISPON
    # 0x2A = CASET (but this is fan duty!)
    # 0x2B = RASET, 0x2C = RAMWR
    # 0x36 = MADCTL, 0x3A = COLMOD

    # Skip 0x2A (fan), 0x3B (LED), 0x3F (save), 0x40 (LED direct)
    # Try the others as potential display controller passthrough

    # SLPOUT - wake display
    drain(fd)
    send_cmd(fd, [0x11])
    time.sleep(0.1)

    # DISPON - turn display on
    drain(fd)
    send_cmd(fd, [0x29])
    time.sleep(0.1)

    # COLMOD - set pixel format to RGB565
    drain(fd)
    send_cmd(fd, [0x3A, 0x05])  # 16-bit color
    time.sleep(0.05)

    # CASET - set column range (but 0x2A is fan duty, so skip)
    # Try with 0x2B (RASET) to set row range
    drain(fd)
    send_cmd(fd, [0x2B, 0x00, 0x00, 0x00, 0x7F])  # rows 0-127
    time.sleep(0.05)

    # RAMWR - write pixel data
    # Red pixels in RGB565 = 0xF800
    drain(fd)
    red_pixels = [0xF8, 0x00] * 31  # 31 red pixels
    send_cmd(fd, [0x2C] + red_pixels)
    time.sleep(0.05)

    # Send more pixel data
    for _ in range(10):
        drain(fd)
        send_cmd(fd, [0x2C] + red_pixels)
        time.sleep(0.01)

    time.sleep(0.5)
    print(f"  Sent ST7735 init + red pixels via 0x2C (RAMWR)")
    print(f"  >>> CHECK OLED for red pixels <<<")

    # === Test 5: Try a different command byte for CASET ===
    # Since 0x2A is fan duty, maybe ASUS remapped CASET to another byte
    # Or maybe the display controller is accessed via a prefix command
    print("\n=== Test 5: Display controller via prefix ===")

    # Theory: command 0x58-0x5F might be display controller pass-through
    for prefix in [0x58, 0x59, 0x5A, 0x5B, 0x5E, 0x5F]:
        drain(fd)
        # Send display init via prefix
        send_cmd(fd, [prefix, 0x2C] + [0xF8, 0x00] * 30)  # RAMWR + red
        time.sleep(0.05)

    time.sleep(0.3)
    print(f"  Tried prefix commands 0x58-0x5F")

    # === Test 6: What if command 0x50 IS the framebuffer write? ===
    # 0x50 register reads as display config. What if WRITING to 0x50
    # with a specific mode puts the device in framebuffer mode?
    print("\n=== Test 6: Framebuffer mode via 0x50 ===")

    drain(fd)
    send_cmd(fd, [0x6C, 0x01])  # stop animation
    time.sleep(0.2)

    # Set framebuffer coordinates
    # [0x50, mode?, x_start, y_start, x_end, y_end, pixel_format?]
    drain(fd)
    send_cmd(fd, [0x50, 0x00, 0x00, 0x00, 0x00, 0xA0, 0x00, 0x80])
    time.sleep(0.05)

    # Then blast red pixels
    red_pixel_data = bytes([0xF8, 0x00] * 31)
    for _ in range(100):
        drain(fd)
        send_cmd(fd, [0x51] + list(red_pixel_data))

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.3)

    print(f"  Sent 100 chunks of red pixels with coord setup")
    print(f"  >>> CHECK OLED <<<")

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    # === Test 7: Try to change display content by writing to 0x6B ===
    # What if we set the path to a built-in mode identifier?
    print("\n=== Test 7: Special file paths ===")
    special_paths = [
        b"0:/cpu_temp\x00",
        b"0:/gpu_temp\x00",
        b"0:/clock\x00",
        b"0:/time\x00",
        b"0:/usage\x00",
        b"0:/banner\x00",
        b"0:/static\x00",
        b"0:/mirror\x00",
        b"cpu_temp\x00",
        b"temp\x00",
    ]
    for path in special_paths:
        drain(fd)
        send_cmd(fd, [0x6C, 0x01])
        time.sleep(0.1)

        drain(fd)
        send_cmd(fd, [0x6B] + list(path))
        time.sleep(0.02)

        drain(fd)
        send_cmd(fd, [0x3F, 0x55])
        time.sleep(0.05)

        drain(fd)
        send_cmd(fd, [0x6E, 0x00])
        time.sleep(0.5)

        print(f"  path='{path.decode().strip(chr(0))}' - check OLED")

    # Restore original path
    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.02)
    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.05)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    # === Test 8: Try the 0x30/0x31 AURA config for OLED zone ===
    print("\n=== Test 8: AURA config registers ===")
    r30 = read_register(fd, 0x30)
    r31 = read_register(fd, 0x31)
    print(f"  reg 0x30: {hex_dump(r30, 20)}")
    print(f"  reg 0x31: {hex_dump(r31, 20)}")

    # What do these bytes mean?
    if r30:
        print(f"    0x30 byte2=0x{r30[2]:02x} byte3=0x{r30[3]:02x}")
        print(f"    0x30 byte4=0x{r30[4]:02x} byte5=0x{r30[5]:02x}")
        print(f"    0x30 byte6=0x{r30[6]:02x} byte7=0x{r30[7]:02x}")
        print(f"    0x30 byte8=0x{r30[8]:02x} byte9=0x{r30[9]:02x}")

    # === Test 9: Try writing OLED config via 0x30 ===
    print("\n=== Test 9: OLED config via AURA registers ===")
    # Try to set up an OLED mode via the AURA LED registers
    # Maybe the OLED is "zone 1" and LED strip is "zone 0"

    for zone in [0, 1, 2]:
        for mode in [0, 1, 2, 3, 4, 5]:
            drain(fd)
            # OpenRGB AURA format: [0x35, 0x00, zone, mode, speed, dir, numcolors, ...]
            send_cmd(fd, [0x35, 0x00, zone, mode, 0x00, 0x00, 0x01,
                         0xFF, 0x00, 0x00])  # red color
            time.sleep(0.02)

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.5)
    print(f"  Tried zone/mode combos with 0x35")
    print(f"  >>> CHECK LED AND OLED <<<")

    os.close(fd)
    print("\nDone. Report what you see on the OLED!")


if __name__ == "__main__":
    main()

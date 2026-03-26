#!/usr/bin/env python3
"""
Test the OLED upload protocol discovered from LiveDash AuraIC.dll disassembly.
This script uploads a test GIF and handles the USB re-enumeration cleanly.
"""
import os
import sys
import time
import glob

REPORT_ID = 0xEC
REPORT_SIZE = 65


def find_device():
    for f in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        content = open(f).read()
        if '00001887' in content:
            return '/dev/' + f.split('/')[4]
    return None


def send_cmd(fd, data):
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    for i, b in enumerate(data[:64]):
        buf[1 + i] = b
    os.write(fd, bytes(buf))


def read_resp(fd, timeout=0.5):
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


def create_test_gif():
    """Create a bright test GIF with Pillow."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (160, 128), (0, 255, 255))  # cyan
        draw = ImageDraw.Draw(img)
        draw.rectangle([5, 5, 155, 123], outline='red', width=4)
        draw.rectangle([30, 30, 130, 98], fill=(0, 0, 200))
        draw.text((42, 45), 'RYUO', fill='white')
        draw.text((38, 65), 'LINUX!', fill='yellow')
        import io
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        return buf.getvalue()
    except ImportError:
        # Minimal 1x1 magenta GIF
        return bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,  # 1x1, 2 colors
            0xFF, 0x00, 0xFF, 0x00, 0x00, 0x00,  # magenta, black
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])


def upload_gif(fd, gif_data, slot_index=1):
    """
    Upload a GIF file to the device using the LiveDash protocol.

    Protocol from AuraIC.dll WriteFileToFW() disassembly:
    1. [0x51, 0xA0]              - init/reset transfer
    2. [0x6B, 0x01, 0x00, idx]   - set file slot
    3. [0x6C, 0x01]              - stop animation
    4. [0x6C, 0x03]              - force stop
    5. [0x6C, 0x04]              - prepare for transfer
    6. [0x6E, count, data...]    - data chunks (62 bytes max)
    7. [0x6C, 0x05]              - transfer complete
    8. [0x6C, 0xFF]              - finalize / start animation
    9. [0x51, 0x10, 0x01, idx]   - commit transfer
    """
    size = len(gif_data)
    print(f"GIF size: {size} bytes, slot: {slot_index}")

    drain(fd)

    # Step 1: Init transfer
    print("  [1] Init transfer (0x51 0xA0)")
    send_cmd(fd, [0x51, 0xA0])
    time.sleep(0.02)

    # Step 2: Set file slot
    print(f"  [2] Set file slot {slot_index} (0x6B)")
    send_cmd(fd, [0x6B, 0x01, 0x00, slot_index])
    time.sleep(0.02)

    # Step 3: Stop animation
    print("  [3] Stop animation (0x6C 0x01)")
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.02)

    # Step 4: Force stop
    print("  [4] Force stop (0x6C 0x03)")
    send_cmd(fd, [0x6C, 0x03])
    time.sleep(0.02)

    # Step 5: Prepare for transfer
    print("  [5] Prepare transfer (0x6C 0x04)")
    send_cmd(fd, [0x6C, 0x04])
    time.sleep(0.02)

    # Step 6: Send data chunks
    offset = 0
    chunks = 0
    while offset < size:
        chunk = gif_data[offset:offset + 62]
        chunk_len = len(chunk)
        send_cmd(fd, [0x6E, chunk_len] + list(chunk))
        offset += chunk_len
        chunks += 1
    print(f"  [6] Sent {chunks} chunks ({size} bytes)")

    # Step 7: Transfer complete
    print("  [7] Transfer complete (0x6C 0x05)")
    send_cmd(fd, [0x6C, 0x05])
    time.sleep(0.05)

    # Step 8: Finalize / start
    print("  [8] Finalize (0x6C 0xFF)")
    send_cmd(fd, [0x6C, 0xFF])
    time.sleep(0.05)

    # Step 9: Commit
    print(f"  [9] Commit (0x51 0x10 0x01 {slot_index})")
    send_cmd(fd, [0x51, 0x10, 0x01, slot_index])
    time.sleep(0.1)

    print("  Upload sequence complete.")


def save_aio_settings(fd, mode=0x10, source=1, index=2):
    """
    SaveAIO_Settings_toFW() - activate GIF display mode.
    Reads register 0x5C, modifies it, writes back.
    WARNING: This may cause a USB re-enumeration!
    """
    print(f"\n  SaveAIO: mode=0x{mode:02x} source={source} index={index}")
    drain(fd)
    send_cmd(fd, [0xDC])  # read register 0x5C
    resp = read_resp(fd)
    if not resp or len(resp) < 12:
        print("  ERROR: Could not read register 0x5C")
        return False

    settings = list(resp[2:])  # skip report ID + echo byte
    print(f"  Before: {' '.join(f'{b:02x}' for b in settings[:12])}")

    settings[0] = 1        # enable/changed flag
    settings[6] = mode     # 0x10 = GIF mode
    settings[7] = source   # 1 = custom
    settings[8] = index    # file slot index
    print(f"  After:  {' '.join(f'{b:02x}' for b in settings[:12])}")

    send_cmd(fd, [0x5C] + settings[:63])
    time.sleep(0.1)
    print("  Settings written to 0x5C")
    return True


def main():
    device_path = find_device()
    if not device_path:
        print("Ryuo not found!")
        sys.exit(1)

    print(f"Device: {device_path}")

    fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
    drain(fd)

    # Verify communication
    send_cmd(fd, [0x82])
    resp = read_resp(fd)
    if resp:
        fw = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"Firmware: {fw}")

    # Check current state
    drain(fd)
    send_cmd(fd, [0xDC])
    resp = read_resp(fd)
    if resp:
        d = resp[2:]
        print(f"Reg 0x5C: {' '.join(f'{b:02x}' for b in d[:12])}")
        print(f"  mode_byte[6]={d[6]:02x} source[7]={d[7]:02x} index[8]={d[8]:02x} disp_mode[10]={d[10]:02x}")

    # Create test GIF
    gif_data = create_test_gif()
    print(f"\nTest GIF: {len(gif_data)} bytes")

    # === Upload to slot 1 ===
    print("\n=== Upload to slot 1 ===")
    upload_gif(fd, gif_data, slot_index=1)

    # Check state after upload (before SaveAIO)
    print("\n>>> CHECK OLED NOW - did it change? <<<")
    time.sleep(1)

    drain(fd)
    send_cmd(fd, [0xEB])
    resp = read_resp(fd)
    if resp:
        print(f"Path reg: {' '.join(f'{b:02x}' for b in resp[2:12])}")

    drain(fd)
    send_cmd(fd, [0xEC])
    resp = read_resp(fd)
    if resp:
        print(f"Anim state: 0x{resp[2]:02x}")

    # === Now activate the uploaded file via SaveAIO ===
    print("\n=== Activating GIF mode (SaveAIO) ===")
    print("WARNING: This may cause USB re-enumeration!")

    save_aio_settings(fd, mode=0x10, source=1, index=1)

    # The device may re-enumerate here, check if fd is still valid
    time.sleep(1)

    try:
        drain(fd)
        send_cmd(fd, [0x82])
        resp = read_resp(fd, timeout=1.0)
        if resp:
            print("\nDevice still connected!")
        else:
            print("\nDevice may have re-enumerated...")
    except OSError:
        print("\nDevice disconnected (re-enumerating)...")

    try:
        os.close(fd)
    except:
        pass

    # Wait for device to come back
    print("\nWaiting for device to re-appear...")
    for i in range(15):
        time.sleep(1)
        new_path = find_device()
        if new_path:
            try:
                fd2 = os.open(new_path, os.O_RDWR | os.O_NONBLOCK)
                drain(fd2)
                send_cmd(fd2, [0x82])
                resp = read_resp(fd2)
                if resp:
                    print(f"Device back at {new_path}")
                    # Check final state
                    drain(fd2)
                    send_cmd(fd2, [0xDC])
                    resp = read_resp(fd2)
                    if resp:
                        d = resp[2:]
                        print(f"Reg 0x5C: {' '.join(f'{b:02x}' for b in d[:12])}")
                    drain(fd2)
                    send_cmd(fd2, [0xEB])
                    resp = read_resp(fd2)
                    if resp:
                        path = bytes(resp[3:]).split(b'\x00')[0].decode('ascii', errors='replace')
                        print(f"File path: {path!r}")
                    drain(fd2)
                    send_cmd(fd2, [0xEC])
                    resp = read_resp(fd2)
                    if resp:
                        print(f"Anim state: 0x{resp[2]:02x}")
                    os.close(fd2)
                    break
            except (PermissionError, OSError) as e:
                print(f"  Device found but: {e}")
                continue
    else:
        print("Device did not come back within 15 seconds")
        print("Try: sudo chmod 666 /dev/hidraw*")

    print("\n>>> CHECK OLED - is the test image showing? <<<")
    print("(Cyan background, red border, blue box, 'RYUO LINUX!' text)")


if __name__ == "__main__":
    main()

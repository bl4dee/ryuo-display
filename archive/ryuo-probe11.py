#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 11
1. Try XOR encryption with various keys
2. Try SET_REPORT via control pipe (different from interrupt OUT)
3. Try writing with the CORRECT Output report format via control pipe
4. Try sending the GIF file path in the data stream itself
5. Try the transfer in a specific order that matches what LiveDash might do
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


def xor_data(data, key):
    """XOR data with a repeating key."""
    if isinstance(key, int):
        return bytes(b ^ key for b in data)
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


def do_transfer(fd, gif_data, label="", xor_key=None, use_path=True):
    """Perform a file upload transfer attempt."""
    size = len(gif_data)
    data = gif_data if xor_key is None else xor_data(gif_data, xor_key)

    drain(fd)
    send_cmd(fd, [0x6C, 0x01])  # stop animation
    time.sleep(0.1)

    if use_path:
        drain(fd)
        send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
        time.sleep(0.02)

    drain(fd)
    send_cmd(fd, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                  (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.02)

    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + 63]
        drain(fd)
        send_cmd(fd, [0x51] + list(chunk))
        offset += 63

    drain(fd)
    send_cmd(fd, [0x52])
    time.sleep(0.05)

    drain(fd)
    send_cmd(fd, [0x3F, 0x55])
    time.sleep(0.1)

    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    if label:
        print(f"  {label} - check OLED")


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {DEVICE}")
    drain(fd)

    # Create test GIF
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
        print(f"Test GIF: {len(gif_data)} bytes")
    except ImportError:
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0x00, 0xFF, 0x00, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])
        print(f"Minimal GIF: {len(gif_data)} bytes")

    # === Test 1: XOR encryption attempts ===
    print("\n=== Test 1: XOR encryption ===")
    keys = [
        ("XOR 0xEC", 0xEC),
        ("XOR 0xFF", 0xFF),
        ("XOR 0x55", 0x55),
        ("XOR 0xAA", 0xAA),
        ("XOR key AURA", b"AURA"),
        ("XOR key EC", bytes([0xEC, 0x51])),
    ]
    for label, key in keys:
        do_transfer(fd, gif_data, label=label, xor_key=key)

    # === Test 2: pyusb SET_REPORT via control pipe ===
    print("\n=== Test 2: pyusb SET_REPORT via control pipe ===")
    try:
        import usb.core
        import usb.util

        # Need to close hidraw first and use pyusb
        os.close(fd)
        fd = None

        dev = usb.core.find(idVendor=0x0B05, idProduct=0x1887)
        if not dev:
            print("  pyusb: Device not found!")
            sys.exit(1)

        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)

        dev.set_configuration()
        usb.util.claim_interface(dev, 0)

        print("  pyusb: Connected")

        # Drain
        try:
            while True:
                dev.read(0x81, 64, timeout=50)
        except:
            pass

        # Verify comms via interrupt
        buf = bytearray(64)
        buf[0] = 0x82
        dev.write(0x01, buf, timeout=1000)
        try:
            resp = bytes(dev.read(0x81, 64, timeout=1000))
            fw = bytes(resp[1:]).split(b'\x00')[0].decode('ascii', errors='replace')
            print(f"  Firmware (interrupt): {fw}")
        except:
            print("  No interrupt response")

        # Now try SET_REPORT (Output type) via control pipe
        # bmRequestType: 0x21 (Host-to-device, Class, Interface)
        # bRequest: 0x09 (SET_REPORT)
        # wValue: (report_type << 8) | report_id
        # wIndex: interface
        # Output report type = 0x02
        SET_REPORT = 0x09
        OUTPUT_TYPE = 0x02
        wValue = (OUTPUT_TYPE << 8) | REPORT_ID

        print("\n  Test 2a: Send commands via SET_REPORT (Output)")
        # Test firmware query via SET_REPORT
        data = bytearray(64)
        data[0] = 0x82  # firmware query
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    SET_REPORT fw query: sent OK")
            # Read response via interrupt
            try:
                resp = bytes(dev.read(0x81, 64, timeout=1000))
                fw = bytes(resp[1:]).split(b'\x00')[0].decode('ascii', errors='replace')
                print(f"    Response: {fw}")
            except:
                print("    No interrupt response")
        except Exception as e:
            print(f"    SET_REPORT fw query: {e}")

        # Test 2b: Full file upload via SET_REPORT
        print("\n  Test 2b: File upload via SET_REPORT")
        size = len(gif_data)

        # Drain
        try:
            while True:
                dev.read(0x81, 64, timeout=50)
        except:
            pass

        # Stop animation
        data = bytearray(64)
        data[0] = 0x6C
        data[1] = 0x01
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    Stop anim: OK")
        except Exception as e:
            print(f"    Stop anim: {e}")
        time.sleep(0.1)

        # Set path
        data = bytearray(64)
        data[0] = 0x6B
        path = b"0:/Animat00.gif\x00"
        for i, b in enumerate(path):
            data[1 + i] = b
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    Set path: OK")
        except Exception as e:
            print(f"    Set path: {e}")
        time.sleep(0.02)

        # Begin transfer
        data = bytearray(64)
        data[0] = 0x50
        data[1] = 0x00
        data[2] = (size >> 24) & 0xFF
        data[3] = (size >> 16) & 0xFF
        data[4] = (size >> 8) & 0xFF
        data[5] = size & 0xFF
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    Begin: OK")
        except Exception as e:
            print(f"    Begin: {e}")
        time.sleep(0.02)

        # Send data via SET_REPORT
        offset = 0
        errors = 0
        while offset < len(gif_data):
            chunk = gif_data[offset:offset + 63]
            data = bytearray(64)
            data[0] = 0x51
            for i, b in enumerate(chunk):
                data[1 + i] = b
            try:
                dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            except Exception as e:
                errors += 1
                if errors == 1:
                    print(f"    Data chunk error: {e}")
            offset += 63
        print(f"    Data: {offset} bytes, {errors} errors")

        # End
        data = bytearray(64)
        data[0] = 0x52
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    End: OK")
        except Exception as e:
            print(f"    End: {e}")
        time.sleep(0.05)

        # Save
        data = bytearray(64)
        data[0] = 0x3F
        data[1] = 0x55
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    Save: OK")
        except Exception as e:
            print(f"    Save: {e}")
        time.sleep(0.1)

        # Restart animation
        data = bytearray(64)
        data[0] = 0x6E
        data[1] = 0x00
        try:
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
            print("    Restart: OK")
        except Exception as e:
            print(f"    Restart: {e}")
        time.sleep(0.3)

        # Test 2c: Try with BOTH control pipe and interrupt for different parts
        print("\n  Test 2c: Mixed control/interrupt transfer")

        # Drain
        try:
            while True:
                dev.read(0x81, 64, timeout=50)
        except:
            pass

        # Commands via control pipe, data via interrupt
        # Stop
        data = bytearray(64)
        data[0] = 0x6C
        data[1] = 0x01
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.1)

        # Path via control
        data = bytearray(64)
        data[0] = 0x6B
        path = b"0:/Animat00.gif\x00"
        for i, b in enumerate(path):
            data[1 + i] = b
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.02)

        # Begin via control
        data = bytearray(64)
        data[0] = 0x50
        data[1] = 0x00
        data[2] = (size >> 24) & 0xFF
        data[3] = (size >> 16) & 0xFF
        data[4] = (size >> 8) & 0xFF
        data[5] = size & 0xFF
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.02)

        # DATA via interrupt endpoint
        offset = 0
        while offset < len(gif_data):
            chunk = gif_data[offset:offset + 63]
            data = bytearray(64)
            data[0] = 0x51
            for i, b in enumerate(chunk):
                data[1 + i] = b
            dev.write(0x01, bytes(data), timeout=1000)
            offset += 63
        print("    Data via interrupt: sent")

        # End via control
        data = bytearray(64)
        data[0] = 0x52
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.05)

        # Save via control
        data = bytearray(64)
        data[0] = 0x3F
        data[1] = 0x55
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.1)

        # Restart
        data = bytearray(64)
        data[0] = 0x6E
        data[1] = 0x00
        dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=1000)
        time.sleep(0.3)

        print("    Check OLED for changes!")

        # Test 2d: Try sending data WITHOUT report ID prefix
        # What if control pipe SET_REPORT already handles the report ID
        # and the device expects raw data without the command prefix?
        print("\n  Test 2d: Data without command prefix")

        # Drain
        try:
            while True:
                dev.read(0x81, 64, timeout=50)
        except:
            pass

        # Stop and setup via interrupt (known working)
        buf = bytearray(64)
        buf[0] = 0x6C; buf[1] = 0x01
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.1)

        buf = bytearray(64)
        buf[0] = 0x6B
        for i, b in enumerate(b"0:/Animat00.gif\x00"):
            buf[1 + i] = b
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.02)

        buf = bytearray(64)
        buf[0] = 0x50; buf[1] = 0x00
        buf[2] = (size >> 24) & 0xFF; buf[3] = (size >> 16) & 0xFF
        buf[4] = (size >> 8) & 0xFF; buf[5] = size & 0xFF
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.02)

        # Send GIF data as RAW (no command byte prefix) via interrupt
        offset = 0
        while offset < len(gif_data):
            chunk = gif_data[offset:offset + 64]
            data = bytearray(64)
            for i, b in enumerate(chunk):
                data[i] = b
            dev.write(0x01, bytes(data), timeout=1000)
            offset += 64
        print(f"    Sent {offset} bytes raw")

        buf = bytearray(64)
        buf[0] = 0x52
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.05)

        buf = bytearray(64)
        buf[0] = 0x3F; buf[1] = 0x55
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.1)

        buf = bytearray(64)
        buf[0] = 0x6E; buf[1] = 0x00
        dev.write(0x01, buf, timeout=1000)
        time.sleep(0.3)
        print("    Check OLED!")

        # Test 2e: Try vendor-specific SET requests with image data
        print("\n  Test 2e: Vendor SET requests with data")
        for bRequest in range(0, 16):
            try:
                # Host-to-device, vendor, device
                dev.ctrl_transfer(0x40, bRequest, 0, 0, gif_data[:64], timeout=200)
                print(f"    Vendor SET req={bRequest}: OK!")
            except:
                pass

        # Clean up pyusb
        usb.util.release_interface(dev, 0)
        try:
            dev.attach_kernel_driver(0)
        except:
            pass

        print("\n  pyusb done. Re-opening hidraw...")
        time.sleep(0.5)

        # Re-open hidraw
        fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
        drain(fd)

    except ImportError:
        print("  pyusb not available")
    except Exception as e:
        print(f"  pyusb error: {e}")
        import traceback
        traceback.print_exc()
        # Try to re-open hidraw
        time.sleep(1)
        try:
            fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
        except:
            print("  Could not re-open hidraw!")
            sys.exit(1)

    if fd is None:
        try:
            fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
        except:
            print("Could not re-open hidraw!")
            sys.exit(1)

    # === Test 3: Try with the GIF preceded by a header ===
    print("\n=== Test 3: GIF with custom header ===")
    # What if the device expects a header before the GIF data?
    # Like: [path_length, path_bytes..., file_size, gif_data...]
    path = b"0:/Animat00.gif"
    size = len(gif_data)

    # Header format 1: path + null + size + data
    header1 = path + b'\x00' + bytes([
        (size >> 24) & 0xFF, (size >> 16) & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF
    ])
    full_data1 = header1 + gif_data
    do_transfer(fd, full_data1, label="Header: path+null+size+data")

    # Header format 2: just the GIF data starting with the file name embedded
    # in 0x50 command
    drain(fd)
    send_cmd(fd, [0x6C, 0x01])
    time.sleep(0.1)

    # Put everything in the 0x50 begin: path + size
    drain(fd)
    cmd = [0x50] + list(path) + [0x00, (size >> 8) & 0xFF, size & 0xFF]
    send_cmd(fd, cmd)
    time.sleep(0.02)

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
    time.sleep(0.1)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)
    print("  Header format 2 (path in 0x50) - check OLED")

    # === Test 4: Try writing data to DIFFERENT file paths ===
    print("\n=== Test 4: Different file paths ===")
    for path_name in [b"0:/test.gif", b"0:/OLED.gif", b"0:/image.gif",
                      b"0:/Animat01.gif", b"0:/custom.gif"]:
        drain(fd)
        send_cmd(fd, [0x6B] + list(path_name) + [0x00])
        time.sleep(0.02)

        do_transfer(fd, gif_data, label=f"path={path_name.decode()}")

        # Point back to the new path and restart
        drain(fd)
        send_cmd(fd, [0x6B] + list(path_name) + [0x00])
        time.sleep(0.02)
        drain(fd)
        send_cmd(fd, [0x6E, 0x00])
        time.sleep(0.3)

    # Restore original path
    drain(fd)
    send_cmd(fd, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.02)
    drain(fd)
    send_cmd(fd, [0x6E, 0x00])
    time.sleep(0.3)

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

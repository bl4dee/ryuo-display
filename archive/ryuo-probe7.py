#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 7
Use pyusb for full USB control: control transfers, SET_REPORT with
larger payloads, vendor-specific requests.
"""

import time
import sys

try:
    import usb.core
    import usb.util
except ImportError:
    print("Need pyusb: nix-shell -p python3 python3Packages.pyusb libusb1")
    sys.exit(1)

VID = 0x0B05
PID = 0x1887

# HID class requests
SET_REPORT = 0x09
GET_REPORT = 0x01

# Report types
REPORT_TYPE_INPUT = 0x01
REPORT_TYPE_OUTPUT = 0x02
REPORT_TYPE_FEATURE = 0x03

REPORT_ID = 0xEC


def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("Device not found!")
        sys.exit(1)
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    dev.set_configuration()
    usb.util.claim_interface(dev, 0)
    return dev


def hid_set_report(dev, report_type, report_id, data, interface=0):
    """Send HID SET_REPORT via control transfer."""
    wValue = (report_type << 8) | report_id
    bmRequestType = 0x21  # Host-to-device, class, interface
    dev.ctrl_transfer(bmRequestType, SET_REPORT, wValue, interface, data, timeout=1000)


def hid_get_report(dev, report_type, report_id, length, interface=0):
    """Send HID GET_REPORT via control transfer."""
    wValue = (report_type << 8) | report_id
    bmRequestType = 0xA1  # Device-to-host, class, interface
    return dev.ctrl_transfer(bmRequestType, GET_REPORT, wValue, interface, length, timeout=1000)


def interrupt_write(dev, data, ep=0x01):
    """Send data via interrupt OUT endpoint."""
    dev.write(ep, data, timeout=1000)


def interrupt_read(dev, size=64, ep=0x81, timeout=300):
    """Read data from interrupt IN endpoint."""
    try:
        return bytes(dev.read(ep, size, timeout=timeout))
    except usb.core.USBTimeoutError:
        return None


def drain(dev):
    while True:
        try:
            dev.read(0x81, 64, timeout=50)
        except:
            break


def hex_dump(data, n=32):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:n])


def send_cmd(dev, cmd_data):
    """Send command via interrupt endpoint (standard method)."""
    buf = bytearray(64)
    for i, b in enumerate(cmd_data[:64]):
        buf[i] = b
    interrupt_write(dev, buf)


def read_register(dev, reg):
    drain(dev)
    send_cmd(dev, [0x80 + reg])
    return interrupt_read(dev)


def main():
    dev = find_device()
    print(f"Connected to ROG Ryuo")
    drain(dev)

    # Verify basic communication
    drain(dev)
    send_cmd(dev, [0x82])
    resp = interrupt_read(dev)
    print(f"Firmware: {hex_dump(resp)}")
    if resp:
        fw_str = bytes(resp[2:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"  = {fw_str}")

    # Test 1: GET_REPORT via control pipe with different report types
    print("\n=== Test 1: GET_REPORT control transfers ===")
    for rtype, rtype_name in [(REPORT_TYPE_INPUT, "Input"),
                               (REPORT_TYPE_OUTPUT, "Output"),
                               (REPORT_TYPE_FEATURE, "Feature")]:
        for rid in [0xEC, 0x00]:
            try:
                data = hid_get_report(dev, rtype, rid, 65)
                print(f"  GET {rtype_name} report_id=0x{rid:02x}: {hex_dump(data, 16)}")
            except Exception as e:
                print(f"  GET {rtype_name} report_id=0x{rid:02x}: {e}")

    # Test 2: SET_REPORT with different report types and data
    print("\n=== Test 2: SET_REPORT control transfers ===")
    cmd_data = bytes([0x82] + [0]*63)
    for rtype, rtype_name in [(REPORT_TYPE_OUTPUT, "Output"),
                               (REPORT_TYPE_FEATURE, "Feature")]:
        for rid in [0xEC, 0x00]:
            try:
                full_data = bytes([rid]) + cmd_data[:63] if rid else cmd_data
                hid_set_report(dev, rtype, rid, full_data)
                print(f"  SET {rtype_name} report_id=0x{rid:02x}: OK")
                # Check for interrupt response
                resp = interrupt_read(dev, timeout=200)
                if resp:
                    print(f"    interrupt response: {hex_dump(resp, 16)}")
            except Exception as e:
                print(f"  SET {rtype_name} report_id=0x{rid:02x}: {e}")

    # Test 3: Try SET_REPORT with LARGER payloads
    print("\n=== Test 3: Large SET_REPORT payloads ===")
    for size in [64, 128, 256, 512, 1024, 2048, 4096]:
        data = bytes([REPORT_ID] + [0xFF] * (size - 1))
        try:
            hid_set_report(dev, REPORT_TYPE_FEATURE, REPORT_ID, data)
            print(f"  Feature report size={size}: OK!")
        except Exception as e:
            err_str = str(e)
            if 'Pipe' in err_str or 'pipe' in err_str:
                print(f"  Feature report size={size}: Pipe error (expected)")
            else:
                print(f"  Feature report size={size}: {err_str[:60]}")

    for size in [64, 128, 256, 512, 1024]:
        data = bytes([REPORT_ID] + [0xFF] * (size - 1))
        try:
            hid_set_report(dev, REPORT_TYPE_OUTPUT, REPORT_ID, data)
            print(f"  Output report size={size}: OK!")
        except Exception as e:
            err_str = str(e)
            if 'Pipe' in err_str or 'pipe' in err_str:
                print(f"  Output report size={size}: Pipe error")
            else:
                print(f"  Output report size={size}: {err_str[:60]}")

    # Test 4: Vendor-specific control transfers
    print("\n=== Test 4: Vendor-specific control transfers ===")
    # Try various vendor request types
    for bRequest in range(0, 32):
        for wValue in [0x0000, 0x0001, 0x00EC]:
            try:
                resp = dev.ctrl_transfer(0xC0, bRequest, wValue, 0, 64, timeout=200)
                print(f"  Vendor IN req={bRequest} wVal=0x{wValue:04x}: {hex_dump(resp, 16)}")
            except:
                pass

    # Test 5: Try sending image data via SET_REPORT Feature
    print("\n=== Test 5: Image data via Feature report ===")
    # Create solid red pixels RGB565
    red_rgb565 = bytes([0xF8, 0x00] * 31)  # 62 bytes of red pixels

    # Try: use SET_REPORT Feature to send pixel data
    for rid in [0xEC, 0x00, 0x50, 0x51]:
        drain(dev)
        # First send "begin" via normal interrupt
        send_cmd(dev, [0x50, 0x00, 0x00, 0x00, 0xA0, 0x00])  # begin, size=40960
        time.sleep(0.05)

        # Then try to send pixel data via SET_REPORT
        try:
            data = bytes([rid]) + red_rgb565
            hid_set_report(dev, REPORT_TYPE_FEATURE, rid, data)
            print(f"  Feature data rid=0x{rid:02x}: OK")
        except Exception as e:
            print(f"  Feature data rid=0x{rid:02x}: {e}")

    # Test 6: Check if we can send data via interrupt OUT with different first byte
    # (not 0x80+ which are reads)
    print("\n=== Test 6: Interrupt OUT with raw pixel data ===")
    # What if we DON'T use report ID 0xEC and just send 64 bytes of pixel data?
    # In pyusb, we write directly to the endpoint
    drain(dev)
    # First, try with a "begin transfer" sequence
    send_cmd(dev, [0x50, 0x01, 0x00, 0x00, 0xA0, 0x00])  # begin
    time.sleep(0.05)

    # Now send raw data without command prefix
    pixel_data = bytes([0xF8, 0x00] * 32)  # 64 bytes
    try:
        dev.write(0x01, pixel_data, timeout=1000)
        print(f"  Raw pixel write (64 bytes): OK")
        # The first byte 0xF8 is in the read command range (0x80+)
        # So the device will interpret it as "read register 0x78"
        resp = interrupt_read(dev, timeout=200)
        if resp:
            print(f"    Response: {hex_dump(resp, 16)}")
    except Exception as e:
        print(f"  Raw pixel write: {e}")

    # What if we need to prefix with a non-command byte?
    # Try using 0x00 as a "data" marker
    drain(dev)
    send_cmd(dev, [0x50, 0x01])  # begin
    data_packet = bytes([0x00] + list(red_rgb565[:63]))  # 0x00 prefix + data
    try:
        dev.write(0x01, data_packet, timeout=1000)
        print(f"  Data with 0x00 prefix: OK")
        resp = interrupt_read(dev, timeout=200)
        if resp:
            print(f"    Response: {hex_dump(resp, 16)}")
    except Exception as e:
        print(f"  Data with 0x00 prefix: {e}")

    # Test 7: Maybe the device uses GET_REPORT to transfer data TO us
    # (unusual but possible - device reads data from host via GET_REPORT response)
    print("\n=== Test 7: Bidirectional data flow check ===")
    # Read current register 0x50
    r50 = read_register(dev, 0x50)
    print(f"  reg 0x50: {hex_dump(r50)}")

    # Try writing to consecutive registers 0x00-0x0F
    print("\n  Writing to registers 0x00-0x0F:")
    for reg in range(0x00, 0x10):
        drain(dev)
        send_cmd(dev, [reg, 0xAA, 0xBB, 0xCC])
        time.sleep(0.01)

    # Read them back
    for reg in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]:
        resp = read_register(dev, reg)
        if resp and not all(b == 0 for b in resp[2:]):
            print(f"  reg 0x{reg:02x}: {hex_dump(resp)}")

    # Cleanup
    usb.util.release_interface(dev, 0)
    try:
        dev.attach_kernel_driver(0)
    except:
        pass
    print("\nDone.")


if __name__ == "__main__":
    main()

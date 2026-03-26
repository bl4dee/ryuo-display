#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 12
Pure pyusb approach:
- Everything via control pipe SET_REPORT
- Also try GET_REPORT to read back data
- Try vendor-specific control transfers with actual image data
- Try USB reset/re-enumeration to trigger device modes
"""

import time
import sys

try:
    import usb.core
    import usb.util
except ImportError:
    print("Need: nix-shell -p python3 python3Packages.pyusb libusb1")
    sys.exit(1)

VID = 0x0B05
PID = 0x1887
REPORT_ID = 0xEC

SET_REPORT = 0x09
GET_REPORT = 0x01
OUTPUT_TYPE = 0x02
INPUT_TYPE = 0x01


def hex_dump(data, n=32):
    if data is None:
        return "TIMEOUT"
    return " ".join(f"{b:02x}" for b in data[:n])


def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("Device not found!")
        sys.exit(1)
    return dev


def setup_device(dev):
    """Detach kernel driver and claim interface."""
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    dev.set_configuration()
    usb.util.claim_interface(dev, 0)


def cleanup_device(dev):
    """Release interface and reattach kernel driver."""
    try:
        usb.util.release_interface(dev, 0)
    except:
        pass
    try:
        dev.attach_kernel_driver(0)
    except:
        pass


def drain(dev):
    """Drain interrupt IN."""
    while True:
        try:
            dev.read(0x81, 64, timeout=50)
        except:
            break


def send_set_report(dev, data):
    """Send 64 bytes via SET_REPORT Output."""
    buf = bytearray(64)
    for i, b in enumerate(data[:64]):
        buf[i] = b
    wValue = (OUTPUT_TYPE << 8) | REPORT_ID
    dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(buf), timeout=1000)


def send_interrupt(dev, data):
    """Send 64 bytes via interrupt OUT."""
    buf = bytearray(64)
    for i, b in enumerate(data[:64]):
        buf[i] = b
    dev.write(0x01, bytes(buf), timeout=1000)


def get_report(dev, length=64):
    """GET_REPORT Input via control pipe."""
    wValue = (INPUT_TYPE << 8) | REPORT_ID
    try:
        return bytes(dev.ctrl_transfer(0xA1, GET_REPORT, wValue, 0, length, timeout=1000))
    except Exception as e:
        return None


def read_interrupt(dev, timeout=300):
    """Read from interrupt IN."""
    try:
        return bytes(dev.read(0x81, 64, timeout=timeout))
    except:
        return None


def main():
    dev = find_device()
    setup_device(dev)
    print("Connected to ROG Ryuo via pyusb")
    drain(dev)

    # Verify communication via SET_REPORT
    print("\n=== Verify communication ===")
    send_set_report(dev, [0x82])
    resp = get_report(dev)
    if resp:
        print(f"  GET_REPORT response: {hex_dump(resp, 16)}")
    resp2 = read_interrupt(dev, timeout=500)
    if resp2:
        fw = bytes(resp2[1:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"  Interrupt response: {fw}")
    else:
        print("  No interrupt response (expected when using SET_REPORT)")

    # Also verify via interrupt
    drain(dev)
    send_interrupt(dev, [0x82])
    resp3 = read_interrupt(dev, timeout=500)
    if resp3:
        fw = bytes(resp3[1:]).split(b'\x00')[0].decode('ascii', errors='replace')
        print(f"  Interrupt cmd/response: {fw}")

    # Create test GIF
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (255, 0, 255))  # magenta
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
        print(f"\nTest GIF: {len(gif_data)} bytes (magenta)")
    except ImportError:
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0xFF, 0x00, 0xFF, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])
        print(f"\nMinimal GIF: {len(gif_data)} bytes")

    size = len(gif_data)

    # === Test 1: Full upload via SET_REPORT only ===
    print("\n=== Test 1: Full upload via SET_REPORT (Output) ===")
    drain(dev)

    # Stop animation
    send_set_report(dev, [0x6C, 0x01])
    time.sleep(0.2)
    print("  Stop animation: OK")

    # Set path
    path = b"0:/Animat00.gif\x00"
    send_set_report(dev, [0x6B] + list(path))
    time.sleep(0.02)
    print("  Set path: OK")

    # Begin
    send_set_report(dev, [0x50, 0x00, (size >> 24) & 0xFF, (size >> 16) & 0xFF,
                          (size >> 8) & 0xFF, size & 0xFF])
    time.sleep(0.02)
    print("  Begin: OK")

    # Data
    offset = 0
    while offset < len(gif_data):
        chunk = gif_data[offset:offset + 63]
        send_set_report(dev, [0x51] + list(chunk))
        offset += 63
    print(f"  Data: {size} bytes OK")

    # End
    send_set_report(dev, [0x52])
    time.sleep(0.05)
    print("  End: OK")

    # Save
    send_set_report(dev, [0x3F, 0x55])
    time.sleep(0.1)
    print("  Save: OK")

    # Read state
    drain(dev)
    send_set_report(dev, [0xD0])  # read reg 0x50
    resp = read_interrupt(dev, timeout=500)
    print(f"  Reg 0x50: {hex_dump(resp, 16)}")

    # Restart
    send_set_report(dev, [0x6E, 0x00])
    time.sleep(0.5)
    print("  Restart: OK")
    print("  >>> CHECK OLED FOR MAGENTA <<<")

    # === Test 2: Try reading the file back via custom commands ===
    print("\n=== Test 2: Read file back ===")
    # Maybe there's a "read file" command that's the reverse of 0x50/0x51/0x52
    # Try: 0x50 to set up read, then 0x53 to read data

    drain(dev)
    # Set path to read
    send_set_report(dev, [0x6B] + list(b"0:/Animat00.gif\x00"))
    time.sleep(0.02)

    # Try "read begin" - maybe mode=0x80 or mode=0x02 for read?
    for mode in [0x80, 0x02, 0x03, 0x04, 0x10, 0x20]:
        drain(dev)
        send_set_report(dev, [0x50, mode, 0x00, 0x00, 0x10, 0x00])  # size=4096
        time.sleep(0.02)

        # Try reading response
        resp = read_interrupt(dev, timeout=200)
        if resp and resp[0] != 0x00:
            print(f"  Mode 0x{mode:02x} begin response: {hex_dump(resp, 16)}")

        # Try reading data
        drain(dev)
        send_set_report(dev, [0xD1])  # read reg 0x51
        resp = read_interrupt(dev, timeout=200)
        if resp and not all(b == 0 for b in resp[1:]):
            print(f"  Mode 0x{mode:02x} read data: {hex_dump(resp, 16)}")

    # === Test 3: Try Class-specific GET_REPORT to read data back ===
    print("\n=== Test 3: GET_REPORT to read OLED data ===")
    for wValue_type in [0x0100, 0x0200]:  # Input=1, Output=2
        for report_id in [0xEC, 0x50, 0x51, 0x00]:
            wValue = wValue_type | report_id
            try:
                resp = dev.ctrl_transfer(0xA1, GET_REPORT, wValue, 0, 64, timeout=500)
                if resp is not None and len(resp) > 0:
                    print(f"  type=0x{wValue_type:04x} id=0x{report_id:02x}: {hex_dump(resp, 16)}")
            except Exception as e:
                pass

    # === Test 4: Try with SET_REPORT using different wValues ===
    print("\n=== Test 4: SET_REPORT with different wValue ===")
    # Maybe the wValue encodes something beyond report type and ID
    for wValue in [0x0200, 0x0300, 0x0200 | 0xEC,
                   0x0000, 0x0001, 0x0050, 0x0051, 0x00EC,
                   0x0200 | 0x50, 0x0200 | 0x51]:
        try:
            data = bytearray(64)
            data[0] = 0x82  # firmware query
            dev.ctrl_transfer(0x21, SET_REPORT, wValue, 0, bytes(data), timeout=500)
            resp = read_interrupt(dev, timeout=300)
            if resp and resp[0] != 0:
                print(f"  wValue=0x{wValue:04x}: response! {hex_dump(resp, 12)}")
        except:
            pass

    # === Test 5: Try USB device requests (not HID class) ===
    print("\n=== Test 5: Standard USB requests ===")
    # Get string descriptors
    for idx in range(0, 8):
        try:
            s = usb.util.get_string(dev, idx)
            print(f"  String {idx}: '{s}'")
        except:
            pass

    # === Test 6: Check if device has DFU capability ===
    print("\n=== Test 6: DFU/bootloader check ===")
    # DFU GET_STATUS
    try:
        resp = dev.ctrl_transfer(0xA1, 0x03, 0, 0, 6, timeout=500)
        print(f"  DFU GET_STATUS: {hex_dump(resp)}")
    except:
        print("  No DFU")

    # Try DFU DETACH
    # (DON'T actually do this as it could brick the device)
    print("  Skipping DFU DETACH (safety)")

    # === Test 7: Try a USB reset and see if device changes mode ===
    print("\n=== Test 7: USB reset test ===")
    # Read state before
    drain(dev)
    send_interrupt(dev, [0x82])
    resp = read_interrupt(dev, timeout=500)
    print(f"  Before reset: {hex_dump(resp, 16)}")

    # Do USB reset
    try:
        dev.reset()
        print("  USB reset: OK")
        time.sleep(1)

        # Re-setup after reset
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        drain(dev)

        # Check state after
        send_interrupt(dev, [0x82])
        resp = read_interrupt(dev, timeout=500)
        print(f"  After reset: {hex_dump(resp, 16)}")

        # Does the device report different capabilities after reset?
        cfg = dev.get_active_configuration()
        for intf in cfg:
            print(f"  Interface {intf.bInterfaceNumber}: class={intf.bInterfaceClass} "
                  f"sub={intf.bInterfaceSubClass} proto={intf.bInterfaceProtocol} "
                  f"eps={intf.bNumEndpoints} alt={intf.bAlternateSetting}")
    except Exception as e:
        print(f"  Reset error: {e}")

    # === Test 8: Try alternate configuration ===
    print("\n=== Test 8: Configuration check ===")
    print(f"  Num configurations: {dev.bNumConfigurations}")
    if dev.bNumConfigurations > 1:
        print("  Multiple configs available!")
        for cfg_val in range(1, dev.bNumConfigurations + 1):
            try:
                dev.set_configuration(cfg_val)
                cfg = dev.get_active_configuration()
                print(f"  Config {cfg_val}: {cfg.bNumInterfaces} interfaces")
            except:
                pass

    # Clean up
    cleanup_device(dev)
    print("\nDone. Kernel driver reattached.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED - Phase 13
Last-resort approaches:
1. Microsoft OS descriptors (might reveal hidden interfaces)
2. USB mode switch (try to switch device to mass storage mode)
3. WebUSB descriptor
4. Try alternate interface with SET_INTERFACE
5. Try reading the HID report descriptor via GET_DESCRIPTOR on control pipe
6. Try sending data with the report ID INSIDE the 64-byte payload (not as prefix)
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


def hex_dump(data, n=32):
    if data is None:
        return "TIMEOUT"
    if isinstance(data, (bytes, bytearray)):
        return " ".join(f"{b:02x}" for b in data[:n])
    return " ".join(f"{b:02x}" for b in bytes(data[:n]))


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("Device not found!")
        sys.exit(1)

    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)

    dev.set_configuration()
    usb.util.claim_interface(dev, 0)
    print("Connected via pyusb")

    # Drain
    try:
        while True:
            dev.read(0x81, 64, timeout=50)
    except:
        pass

    # === Test 1: Microsoft OS Descriptor ===
    print("\n=== Test 1: Microsoft OS Descriptor ===")
    # MS OS Descriptor is at string index 0xEE
    try:
        # GET_DESCRIPTOR for string index 0xEE
        resp = dev.ctrl_transfer(0x80, 0x06, 0x03EE, 0x0000, 256, timeout=1000)
        print(f"  MS OS Descriptor: {hex_dump(resp)}")
        # If it exists, try the vendor request
        if len(resp) > 16:
            bMS_VendorCode = resp[16]
            print(f"  Vendor code: 0x{bMS_VendorCode:02x}")
            # Get Extended Compat ID
            try:
                resp2 = dev.ctrl_transfer(0xC0, bMS_VendorCode, 0x0000, 0x0004, 256, timeout=1000)
                print(f"  Extended Compat ID: {hex_dump(resp2)}")
            except:
                print("  No Extended Compat ID")
    except Exception as e:
        print(f"  No MS OS Descriptor: {e}")

    # === Test 2: BOS Descriptor (USB 3.x) ===
    print("\n=== Test 2: BOS Descriptor ===")
    try:
        resp = dev.ctrl_transfer(0x80, 0x06, 0x0F00, 0x0000, 256, timeout=1000)
        print(f"  BOS: {hex_dump(resp)}")
    except:
        print("  No BOS descriptor")

    # === Test 3: WebUSB Descriptor ===
    print("\n=== Test 3: WebUSB ===")
    # WebUSB uses vendor request, typically found via BOS descriptor
    for vendor_code in [0x01, 0x02, 0x20, 0x21, 0x22]:
        try:
            resp = dev.ctrl_transfer(0xC0, vendor_code, 0x0002, 0x0000, 256, timeout=200)
            print(f"  WebUSB req=0x{vendor_code:02x}: {hex_dump(resp)}")
        except:
            pass

    # === Test 4: Try SET_INTERFACE to switch to alt setting ===
    print("\n=== Test 4: Alternate interface settings ===")
    for alt in range(1, 4):
        try:
            dev.set_interface_altsetting(0, alt)
            print(f"  Alt setting {alt}: OK!")
            # Get interface details
            cfg = dev.get_active_configuration()
            intf = cfg[(0, alt)]
            print(f"    Class={intf.bInterfaceClass} Endpoints={intf.bNumEndpoints}")
        except Exception as e:
            err = str(e)
            if "Pipe" in err or "pipe" in err:
                print(f"  Alt setting {alt}: pipe error (not supported)")
            else:
                print(f"  Alt setting {alt}: {err[:60]}")

    # Reset to alt 0
    try:
        dev.set_interface_altsetting(0, 0)
    except:
        pass

    # === Test 5: Try to read the FULL configuration descriptor ===
    print("\n=== Test 5: Full configuration descriptor ===")
    try:
        # Standard GET_DESCRIPTOR for configuration
        resp = dev.ctrl_transfer(0x80, 0x06, 0x0200, 0x0000, 1024, timeout=1000)
        print(f"  Config descriptor ({len(resp)} bytes):")
        for i in range(0, len(resp), 16):
            hex_part = " ".join(f"{b:02x}" for b in resp[i:i+16])
            print(f"    {i:3d}: {hex_part}")
    except Exception as e:
        print(f"  Error: {e}")

    # === Test 6: Read HID Report Descriptor via control ===
    print("\n=== Test 6: HID Report Descriptor ===")
    try:
        # GET_DESCRIPTOR for HID report descriptor (type=0x22, index=0, interface=0)
        resp = dev.ctrl_transfer(0x81, 0x06, 0x2200, 0x0000, 256, timeout=1000)
        print(f"  HID Report descriptor ({len(resp)} bytes): {hex_dump(resp, len(resp))}")
    except Exception as e:
        print(f"  Error: {e}")

    # === Test 7: Try USB class-specific control transfers for mass storage ===
    print("\n=== Test 7: Mass storage mode switch ===")
    # SCSI Inquiry via control
    try:
        # Bulk-Only Mass Storage Reset
        dev.ctrl_transfer(0x21, 0xFF, 0, 0, None, timeout=500)
        print("  Mass storage reset: accepted!")
    except:
        print("  Mass storage reset: not supported")

    # Try USB mode switch commands (used by Huawei modems etc.)
    # These typically involve sending specific SCSI commands
    # ASUS might have their own
    print("\n  ASUS mode switch attempts:")
    for bRequest in range(0x10, 0x30):
        for wValue in [0x0001, 0x0002, 0x0003, 0x0100, 0x0200]:
            try:
                resp = dev.ctrl_transfer(0xC0, bRequest, wValue, 0, 64, timeout=100)
                if len(resp) > 0:
                    print(f"    req=0x{bRequest:02x} val=0x{wValue:04x}: {hex_dump(resp, 16)}")
            except:
                pass

    # === Test 8: Try HID class specific GET_IDLE and GET_PROTOCOL ===
    print("\n=== Test 8: HID class requests ===")
    # GET_IDLE
    try:
        resp = dev.ctrl_transfer(0xA1, 0x02, 0x00EC, 0, 1, timeout=500)
        print(f"  GET_IDLE: {hex_dump(resp)}")
    except Exception as e:
        print(f"  GET_IDLE: {e}")

    # GET_PROTOCOL
    try:
        resp = dev.ctrl_transfer(0xA1, 0x03, 0, 0, 1, timeout=500)
        print(f"  GET_PROTOCOL: {hex_dump(resp)}")
    except Exception as e:
        print(f"  GET_PROTOCOL: {e}")

    # SET_PROTOCOL (try boot protocol = 0, report protocol = 1)
    for proto in [0, 1]:
        try:
            dev.ctrl_transfer(0x21, 0x0B, proto, 0, None, timeout=500)
            print(f"  SET_PROTOCOL {proto}: OK")
        except Exception as e:
            print(f"  SET_PROTOCOL {proto}: {e}")

    # === Test 9: Try to send data through control pipe WITHOUT HID framing ===
    print("\n=== Test 9: Raw vendor control with GIF data ===")
    # Create minimal GIF header
    gif_header = bytes([0x47, 0x49, 0x46, 0x38, 0x39, 0x61])  # "GIF89a"

    # Try host-to-device vendor requests with GIF data
    for bRequest in range(0, 16):
        try:
            dev.ctrl_transfer(0x40, bRequest, 0, 0, gif_header, timeout=200)
            print(f"  Vendor OUT req={bRequest}: accepted!")
        except:
            pass

    # Try host-to-device class requests with different wValues
    for wValue in [0x0000, 0x0001, 0x0050, 0x0051, 0x0052, 0x006B,
                   0x0200, 0x0300, 0x0200 | 0x50, 0x0200 | 0x51]:
        try:
            data = bytearray(64)
            data[0] = 0x50  # begin command
            data[1] = 0x00
            data[2] = 0x00
            data[3] = 0x00
            data[4] = 0x01
            data[5] = 0x00  # size=256
            dev.ctrl_transfer(0x21, 0x09, wValue, 0, bytes(data), timeout=500)
            print(f"  Class SET wValue=0x{wValue:04x}: accepted")
        except:
            pass

    # === Test 10: Send a complete GIF entirely via a single control transfer ===
    print("\n=== Test 10: Single control transfer with full GIF ===")
    try:
        from PIL import Image
        import io
        img = Image.new('RGB', (160, 128), (0, 255, 255))  # cyan
        buf = io.BytesIO()
        img.save(buf, format='GIF')
        gif_data = buf.getvalue()
    except:
        gif_data = bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00,
            0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4C, 0x01, 0x00, 0x3B
        ])

    # Try sending full GIF as a single vendor control transfer
    for bRequest in [0x09, 0x01, 0x02, 0x50, 0x51]:
        for bmRequestType in [0x21, 0x40, 0x41]:
            try:
                dev.ctrl_transfer(bmRequestType, bRequest, 0x0200 | 0xEC, 0, gif_data[:64], timeout=500)
                print(f"  bmReq=0x{bmRequestType:02x} req=0x{bRequest:02x}: accepted!")
            except:
                pass

    # Clean up
    try:
        usb.util.release_interface(dev, 0)
    except:
        pass
    try:
        dev.attach_kernel_driver(0)
        print("\nKernel driver reattached")
    except:
        try:
            dev.reset()
            print("\nDevice reset")
        except:
            print("\nWarning: could not reattach kernel driver")

    print("Done.")


if __name__ == "__main__":
    main()

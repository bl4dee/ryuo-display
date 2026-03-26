#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Protocol Probe - Phase 6
Try USB control transfers, feature reports, alternate interfaces,
and different report IDs. Also try bulk-mode detection.
"""

import os
import time
import fcntl
import struct
import ctypes

DEVICE = "/dev/hidraw12"
REPORT_ID = 0xEC
REPORT_SIZE = 65

# hidraw ioctl constants
HIDIOCSFEATURE = 0xC0014806  # actually depends on size, need to compute
HIDIOCGFEATURE = 0xC0014807


def hidioc_sfeature(size):
    """Compute HIDIOCSFEATURE ioctl number for given buffer size."""
    # _IOC(IOC_WRITE|IOC_READ, 'H', 0x06, size)
    IOC_WRITE = 1
    IOC_READ = 2
    return (IOC_WRITE | IOC_READ) << 30 | size << 16 | ord('H') << 8 | 0x06


def hidioc_gfeature(size):
    """Compute HIDIOCGFEATURE ioctl number for given buffer size."""
    IOC_WRITE = 1
    IOC_READ = 2
    return (IOC_WRITE | IOC_READ) << 30 | size << 16 | ord('H') << 8 | 0x07


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
    if isinstance(data, (bytes, bytearray)):
        return " ".join(f"{b:02x}" for b in data[:n])
    return str(data)


def main():
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    fobj = os.fdopen(os.dup(fd), 'rb')
    print(f"Opened {DEVICE}")
    drain(fd)

    # Test 1: Try SET_FEATURE report (control pipe)
    print("\n=== Test 1: Feature reports ===")
    for report_id in [0xEC, 0x00, 0x01, 0xCC, 0xB0, 0x50]:
        buf = bytearray(REPORT_SIZE)
        buf[0] = report_id
        buf[1] = 0x82  # firmware query
        try:
            ioctl_num = hidioc_sfeature(REPORT_SIZE)
            result = fcntl.ioctl(fd, ioctl_num, bytes(buf))
            print(f"  SET_FEATURE report_id=0x{report_id:02x}: sent OK")
            # Try to get feature report back
            try:
                gbuf = bytearray(REPORT_SIZE)
                gbuf[0] = report_id
                ioctl_num = hidioc_gfeature(REPORT_SIZE)
                result = fcntl.ioctl(fd, ioctl_num, gbuf)
                print(f"    GET_FEATURE: {hex_dump(result)}")
            except Exception as e:
                print(f"    GET_FEATURE: {e}")
        except Exception as e:
            print(f"  SET_FEATURE report_id=0x{report_id:02x}: {e}")

    # Test 2: Try writing with different report IDs via hidraw
    print("\n=== Test 2: Write with different report IDs ===")
    for report_id in [0x00, 0x01, 0xCC, 0xB0, 0x50, 0x51, 0xAA]:
        buf = bytearray(REPORT_SIZE)
        buf[0] = report_id
        buf[1] = 0x82
        try:
            os.write(fd, bytes(buf))
            resp = read_resp(fd, timeout=0.2)
            if resp:
                print(f"  report_id=0x{report_id:02x}: response {hex_dump(resp, 16)}")
            else:
                print(f"  report_id=0x{report_id:02x}: no response (but write OK)")
        except Exception as e:
            print(f"  report_id=0x{report_id:02x}: {e}")

    # Test 3: Try writing longer reports (some devices accept larger transfers)
    print("\n=== Test 3: Larger report sizes ===")
    for size in [128, 256, 512, 1024]:
        buf = bytearray(size + 1)
        buf[0] = REPORT_ID
        buf[1] = 0x82
        try:
            os.write(fd, bytes(buf))
            resp = read_resp(fd, timeout=0.15)
            if resp:
                print(f"  size={size+1}: response {hex_dump(resp, 16)}")
            else:
                print(f"  size={size+1}: no response (but write OK)")
        except Exception as e:
            print(f"  size={size+1}: {e}")

    # Test 4: Read with different sizes
    print("\n=== Test 4: Read different sizes ===")
    drain(fd)
    send_cmd(fd, [0x82])
    for size in [64, 65, 128, 256, 512, 1024]:
        try:
            data = os.read(fd, size)
            print(f"  read({size}): got {len(data)} bytes: {hex_dump(data, 16)}")
            break  # got something
        except BlockingIOError:
            print(f"  read({size}): no data")
        except Exception as e:
            print(f"  read({size}): {e}")

    # Test 5: Try to detect if device supports a "bulk transfer mode"
    # Some ASUS devices enter a special mode when you send a specific magic sequence
    print("\n=== Test 5: Magic sequences ===")
    magic_sequences = [
        ("ASUS magic 1", [0xEC] + [0xB0, 0x01] + [0x00]*61),
        ("ASUS magic 2", [0xEC] + [0x35, 0x01, 0x00, 0x01, 0x00, 0xA0, 0x00, 0x80] + [0x00]*55),
        ("ASUS magic 3", [0xEC] + [0x35, 0x01, 0x01] + [0x00]*60),
        # LiveDash might use a handshake
        ("Handshake 1", [0xEC] + [0x60, 0xAA, 0x55, 0x00] + [0x00]*59),
        ("Handshake 2", [0xEC] + [0x60, 0x01, 0xAA, 0x55] + [0x00]*59),
        # Maybe there's an "enter transfer mode" in the extended 0x30 space
        ("Enter xfer 1", [0xEC] + [0x35, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01] + [0x00]*54),
        ("Enter xfer 2", [0xEC] + [0x35, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01] + [0x00]*54),
    ]
    for desc, data in magic_sequences:
        drain(fd)
        try:
            os.write(fd, bytes(data[:REPORT_SIZE]))
            resp = read_resp(fd, timeout=0.2)
            if resp:
                print(f"  {desc}: {hex_dump(resp, 16)}")
            else:
                print(f"  {desc}: (no response)")
        except Exception as e:
            print(f"  {desc}: ERROR {e}")

    # Test 6: Try the 0x35 command (set mode in AURA) more thoroughly
    # This is the Ryujin-style display mode command
    print("\n=== Test 6: 0x35 mode command variations ===")
    for byte2 in range(0, 16):
        for byte3 in [0x00, 0x01, 0x02, 0x03, 0x04, 0x10, 0x80, 0xFF]:
            drain(fd)
            send_cmd(fd, [0x35, byte2, byte3])
            resp = read_resp(fd, timeout=0.05)
            if resp:
                print(f"  35 {byte2:02x} {byte3:02x}: {hex_dump(resp, 16)}")

    # Test 7: Check via pyusb for alternate settings, additional descriptors
    print("\n=== Test 7: USB descriptor deep dive ===")
    try:
        import usb.core
        import usb.util
        dev = usb.core.find(idVendor=0x0B05, idProduct=0x1887)
        if dev:
            # Check for additional configurations
            print(f"  Num configs: {dev.bNumConfigurations}")

            cfg = dev.get_active_configuration()
            for intf in cfg:
                print(f"  Interface {intf.bInterfaceNumber}:")
                print(f"    Class={intf.bInterfaceClass} Sub={intf.bInterfaceSubClass} Proto={intf.bInterfaceProtocol}")
                print(f"    Num endpoints: {intf.bNumEndpoints}")
                print(f"    Alt settings: {intf.bAlternateSetting}")
                for ep in intf:
                    d = 'IN' if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else 'OUT'
                    t = {0: 'CTRL', 1: 'ISOC', 2: 'BULK', 3: 'INT'}[usb.util.endpoint_type(ep.bmAttributes)]
                    print(f"    EP 0x{ep.bEndpointAddress:02x} ({d}, {t}): MaxPacket={ep.wMaxPacketSize}")

            # Try to get BOS descriptor (USB 3.0+)
            try:
                bos = dev.ctrl_transfer(0x80, 0x06, 0x0F00, 0, 256)
                print(f"  BOS descriptor: {hex_dump(bos)}")
            except:
                print(f"  No BOS descriptor")

            # Try vendor-specific control transfers
            print("\n  Vendor control transfers:")
            for req in range(0, 16):
                try:
                    data = dev.ctrl_transfer(0xC0, req, 0, 0, 64, timeout=200)
                    print(f"    req={req}: {hex_dump(data, 16)}")
                except:
                    pass

            # Try HID class SET_REPORT (type=Output=0x0200, type=Feature=0x0300)
            print("\n  HID SET_REPORT attempts:")
            test_data = bytes([0x82] + [0x00]*63)
            for report_type in [0x0100, 0x0200, 0x0300]:  # input, output, feature
                for report_id in [0xEC, 0x00]:
                    wValue = report_type | report_id
                    try:
                        dev.ctrl_transfer(0x21, 0x09, wValue, 0, test_data, timeout=500)
                        print(f"    SET_REPORT type=0x{report_type:04x} id=0x{report_id:02x}: OK")
                        # Try reading back
                        try:
                            resp = dev.ctrl_transfer(0xA1, 0x01, wValue, 0, 64, timeout=500)
                            print(f"      GET_REPORT: {hex_dump(resp, 16)}")
                        except:
                            pass
                    except Exception as e:
                        print(f"    SET_REPORT type=0x{report_type:04x} id=0x{report_id:02x}: {e}")
    except ImportError:
        print("  pyusb not available, skipping")
    except Exception as e:
        print(f"  pyusb error: {e}")

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()

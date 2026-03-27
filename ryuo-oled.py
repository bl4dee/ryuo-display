#!/usr/bin/env python3
"""
ROG Ryuo I 240 Controller for Linux

Controls the ASUS ROG Ryuo I 240 AIO liquid cooler via USB HID.
VID: 0x0B05  PID: 0x1887

Commands:
  status              Show device info, sensors, display state
  fan <duty>          Set fan duty cycle (0-100%)
  led <R> <G> <B>     Set LED color (0-255 each) [mode]
  anim <stop|start>   Stop/start OLED animation
  monitor             Live sensor monitoring (Ctrl+C to stop)
  dump                Dump all non-zero registers
  image <file>        Upload image/GIF to OLED display (supports --filter, --brightness, etc.)

Setup:
  sudo cp 99-ryuo.rules /etc/udev/rules.d/ && sudo udevadm control --reload
  Or: sudo chmod 666 /dev/hidraw<N>

Protocol:
  HID report ID 0xEC, 65-byte reports (1 byte ID + 64 data).
  Commands 0x80+: read register (response byte 1 = cmd - 0x80).
  Commands 0x00-0x7F: write register (no response).
  Vendor usage page 0xFF72, usage 0xA1.

OLED image upload:
  Protocol reverse-engineered from ASUS LiveDash v1.05.03 (AuraIC.dll).
  The WriteFileToFW() function in AuraIC.dll implements this sequence:

  1. WriteCommand(0x51, [0xA0])           -- init/reset transfer
  2. WriteCommand(0x6B, [0x01, 0x00, N])  -- set file slot index (N=1 for upload)
  3. WriteCommand(0x6C, [0x01])           -- stop animation
  4. WriteCommand(0x6C, [0x03])           -- stop animation (force)
  5. WriteCommand(0x6C, [0x04])           -- prepare for transfer
  6. Loop: read 62 bytes from GIF file:
     WriteCommand(0x6E, [count, ...data...]) -- send chunk (count=bytes read, max 62)
  7. WriteCommand(0x6C, [0x05])           -- transfer complete signal
  8. WriteCommand(0x6C, [0xFF])           -- finalize / start animation
  9. WriteCommand(0x51, [0x10, 0x01, N])  -- apply/commit transfer

  10. Set slot again and start playback with [0x6E, 0x00]

  IMPORTANT: Do NOT write SaveAIO settings to register 0x5C after upload.
  Any write to 0x5C causes the OLED to go black. The upload works without it.

  Each WriteCommand sends: [0xEC, cmd, data...] padded to 65 bytes total.
  The device uses HID WriteFile (not SetFeature), 65 bytes per report.
"""

import os
import sys
import time
import argparse
import tempfile

VENDOR_ID = 0x0B05
PRODUCT_ID = 0x1887
REPORT_ID = 0xEC
REPORT_SIZE = 65

# Registers (read via 0x80 + reg)
REG_AURA_FW = 0x01
REG_OLED_FW = 0x02
REG_USB_INFO = 0x04
REG_MANUFACTURER = 0x05
REG_PRODUCT = 0x06
REG_HW_CONFIG = 0x08
REG_SERIAL = 0x09
REG_AURA_CFG = 0x30
REG_DISPLAY_CFG = 0x50
REG_DISPLAY_MODE = 0x5C
REG_FILE_PATH = 0x5D
REG_SENSORS = 0x6A
REG_FILE_PATH2 = 0x6B
REG_ANIM_STATE = 0x6C
REG_ANIM_INFO = 0x6D

# Write commands
CMD_FAN_DUTY = 0x2A
CMD_LED_MODE = 0x3B
CMD_SAVE = 0x3F
CMD_LED_DIRECT = 0x40
CMD_XFER_CTRL = 0x51    # Transfer control (init [0xA0], commit [0x10,0x01,idx])
CMD_FILE_SLOT = 0x6B    # Set file slot index [0x01, 0x00, index]
CMD_ANIM_CTRL = 0x6C    # Animation control (0x01=stop, 0x03=force stop,
                        # 0x04=prep xfer, 0x05=xfer done, 0xFF=start)
CMD_XFER_DATA = 0x6E    # File data chunk [count, ...up to 62 bytes...]


def find_device():
    for i in range(30):
        path = f"/sys/class/hidraw/hidraw{i}/device/uevent"
        try:
            with open(path) as f:
                content = f.read()
                if "00000B05" in content and "00001887" in content:
                    return f"/dev/hidraw{i}"
        except FileNotFoundError:
            continue
    return None


class RyuoDevice:
    def __init__(self, device_path=None):
        if device_path is None:
            device_path = find_device()
            if device_path is None:
                print("Error: ROG Ryuo I 240 not found!")
                print("Check USB connection. Look for it with:")
                print("  python3 -c \"import os; [print(f'/dev/hidraw{i}') for i in range(20) if '00001887' in open(f'/sys/class/hidraw/hidraw{i}/device/uevent').read()]\" 2>/dev/null")
                sys.exit(1)
        self.path = device_path
        try:
            self.fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            print(f"Permission denied: {device_path}")
            print(f"Fix: sudo chmod 666 {device_path}")
            print(f"Persistent: install 99-ryuo.rules to /etc/udev/rules.d/")
            sys.exit(1)
        self._drain()

    def close(self):
        os.close(self.fd)

    def _send(self, data):
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_ID
        for i, b in enumerate(data[:64]):
            buf[1 + i] = b
        os.write(self.fd, bytes(buf))

    def _recv(self, timeout=0.3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return os.read(self.fd, REPORT_SIZE)
            except BlockingIOError:
                time.sleep(0.005)
        return None

    def _drain(self):
        while True:
            try:
                os.read(self.fd, REPORT_SIZE)
            except BlockingIOError:
                break

    def read_reg(self, reg):
        self._drain()
        self._send([0x80 + reg])
        return self._recv()

    def write_cmd(self, cmd, data=None):
        payload = [cmd]
        if data:
            payload.extend(data)
        self._drain()
        self._send(payload)

    def _str(self, data, offset=2):
        if data is None:
            return ""
        chars = []
        for b in data[offset:]:
            if b == 0:
                break
            if 0x20 <= b < 0x7F:
                chars.append(chr(b))
        return "".join(chars)

    # --- Info ---

    def get_firmware(self):
        return {
            "aura": self._str(self.read_reg(REG_AURA_FW)),
            "oled": self._str(self.read_reg(REG_OLED_FW)),
        }

    def get_device_info(self):
        return {
            "manufacturer": self._str(self.read_reg(REG_MANUFACTURER)),
            "product": self._str(self.read_reg(REG_PRODUCT)),
            "serial": self._str(self.read_reg(REG_SERIAL)),
            "hw_config": self._str(self.read_reg(REG_HW_CONFIG)),
        }

    def get_display_info(self):
        mode_reg = self.read_reg(REG_DISPLAY_MODE)
        path_reg = self.read_reg(REG_FILE_PATH2)
        anim_reg = self.read_reg(REG_ANIM_STATE)

        info = {}
        if mode_reg and len(mode_reg) > 9:
            info["mode"] = mode_reg[7]   # reg[5] = display mode (0x10=GIF)
            info["source"] = mode_reg[8]  # reg[6] = source (1=custom)
            info["slot"] = mode_reg[9]    # reg[7] = slot index
        if path_reg:
            info["file"] = self._str(path_reg, 3)
        if anim_reg and len(anim_reg) > 2:
            info["anim_running"] = anim_reg[2] != 0
            info["anim_raw"] = anim_reg[2]
        return info

    def get_sensors(self):
        data = self.read_reg(REG_SENSORS)
        if data is None or len(data) < 8:
            return None
        raw = data[2:8]
        # byte 3 (raw[1]) appears to be coolant temperature in °C
        # bytes 4-7 are dynamic values (encoding uncertain)
        return {
            "raw_hex": " ".join(f"{b:02x}" for b in raw),
            "coolant_temp": raw[1],
            "raw": raw,
        }

    # --- Fan ---

    def set_fan(self, duty):
        duty = max(0, min(100, int(duty)))
        self.write_cmd(CMD_FAN_DUTY, [duty])

    # --- LED ---

    def set_led(self, r, g, b, mode=0x01):
        self.write_cmd(CMD_LED_MODE, [
            0x00, 0x22, mode,
            r, g, b,
            0x00, 0x02,
        ])
        self.write_cmd(CMD_SAVE, [0x55])

    def set_led_direct(self, colors):
        data = [0x80, 0x00, len(colors)]
        for r, g, b in colors[:12]:
            data.extend([r, g, b])
        self.write_cmd(CMD_LED_DIRECT, data)

    # --- Animation ---

    def anim_stop(self):
        self.write_cmd(CMD_ANIM_CTRL, [0x01])

    def anim_start(self):
        self.write_cmd(CMD_XFER_DATA, [0x00])  # 0x6E 0x00 = start playback

    # --- Image upload (protocol from LiveDash v1.05.03) ---

    def _write_display_reg(self, flag, modifications):
        """Write to register 0x5C with correct flag-prefix format.

        The device consumes iData[0] as a flag byte. iData[N] for N>=1
        is stored as reg[N-1]. So we prepend the flag and use DLL array
        indices for modifications.

        Args:
            flag: Flag byte (1=SaveAIO settings, 0=on/off/rotate control)
            modifications: dict of {array_index: value} using DLL array indices
        """
        settings = self.read_reg(REG_DISPLAY_MODE)
        if not settings or len(settings) < 10:
            return False
        reg_data = list(settings[2:])  # raw register values
        write_buf = [flag] + reg_data[:62]  # prepend flag, preserve register data
        for idx, val in modifications.items():
            write_buf[idx] = val
        self.write_cmd(REG_DISPLAY_MODE, write_buf[:63])
        return True

    def upload_image(self, image_path, slot=1, rotate=False, progress_cb=None, chunk_delay=0.02):
        try:
            from PIL import Image
            import io
            import hashlib
        except ImportError:
            print("Pillow required: nix-shell -p python3 python3Packages.pillow")
            return False

        # Check cache — skip all image processing if we've done it before
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(image_path)), ".ryuo_cache")
        file_stat = os.stat(image_path)
        cache_key = hashlib.md5(
            f"{image_path}:{file_stat.st_size}:{file_stat.st_mtime}:{rotate}".encode()
        ).hexdigest()
        cache_path = os.path.join(cache_dir, f"{cache_key}.gif")

        if os.path.exists(cache_path):
            print(f"Using cached GIF: {cache_path}")
            if progress_cb:
                progress_cb("cached", 0, 0)
            with open(cache_path, "rb") as f:
                gif_data = f.read()
        else:
            img = Image.open(image_path)
            is_gif = hasattr(img, 'n_frames') and img.n_frames > 1

            if is_gif:
                print(f"GIF: {img.size[0]}x{img.size[1]}, {img.n_frames} frames")
            else:
                print(f"Image: {img.size[0]}x{img.size[1]}")

            # Resize to 160x128
            if img.size != (160, 128):
                if is_gif:
                    frames = []
                    durations = []
                    n = img.n_frames
                    for i in range(n):
                        if progress_cb and i % 3 == 0:
                            progress_cb("resizing", i, n)
                        img.seek(i)
                        frame = img.copy().convert('RGBA')
                        frame.thumbnail((160, 128), Image.LANCZOS)
                        canvas = Image.new('RGBA', (160, 128), (0, 0, 0, 255))
                        canvas.paste(frame, ((160 - frame.size[0]) // 2,
                                            (128 - frame.size[1]) // 2))
                        if rotate:
                            canvas = canvas.rotate(180)
                        frames.append(canvas)
                        durations.append(max(img.info.get('duration', 100) or 100, 33))
                    if progress_cb:
                        progress_cb("encoding", 0, 0)
                    # Quantize to reduce file size = fewer chunks = faster upload
                    p_frames = [f.convert('RGB').quantize(colors=128, method=2)
                                for f in frames]
                    buf = io.BytesIO()
                    p_frames[0].save(buf, format='GIF', save_all=True,
                                     append_images=p_frames[1:],
                                     duration=durations, loop=0, optimize=True)
                    gif_data = buf.getvalue()
                else:
                    img = img.convert('RGB')
                    img.thumbnail((160, 128), Image.LANCZOS)
                    canvas = Image.new('RGB', (160, 128), (0, 0, 0))
                    canvas.paste(img, ((160 - img.size[0]) // 2,
                                       (128 - img.size[1]) // 2))
                    if rotate:
                        canvas = canvas.rotate(180)
                    buf = io.BytesIO()
                    canvas.quantize(colors=128, method=2).save(
                        buf, format='GIF', optimize=True)
                    gif_data = buf.getvalue()
            else:
                if is_gif:
                    if rotate:
                        frames = []
                        durations = []
                        n = img.n_frames
                        for i in range(n):
                            if progress_cb and i % 3 == 0:
                                progress_cb("resizing", i, n)
                            img.seek(i)
                            frame = img.copy().convert('RGBA').rotate(180)
                            frames.append(frame)
                            durations.append(max(img.info.get('duration', 100) or 100, 33))
                        p_frames = [f.convert('RGB').quantize(colors=128, method=2)
                                    for f in frames]
                        buf = io.BytesIO()
                        p_frames[0].save(buf, format='GIF', save_all=True,
                                         append_images=p_frames[1:],
                                         duration=durations, loop=0, optimize=True)
                        gif_data = buf.getvalue()
                    else:
                        # Already correct size, no rotation — re-encode optimized
                        frames = []
                        durations = []
                        for i in range(img.n_frames):
                            img.seek(i)
                            frames.append(img.copy().convert('RGB').quantize(
                                colors=128, method=2))
                            durations.append(max(img.info.get('duration', 100) or 100, 33))
                        buf = io.BytesIO()
                        frames[0].save(buf, format='GIF', save_all=True,
                                       append_images=frames[1:],
                                       duration=durations, loop=0, optimize=True)
                        gif_data = buf.getvalue()
                else:
                    img = img.convert('RGB')
                    if rotate:
                        img = img.rotate(180)
                    buf = io.BytesIO()
                    img.quantize(colors=128, method=2).save(
                        buf, format='GIF', optimize=True)
                    gif_data = buf.getvalue()

            # Save to cache for next time
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(gif_data)
                print(f"Cached processed GIF ({len(gif_data)} bytes)")
            except OSError:
                pass  # cache write failure is non-fatal

        return self.upload_gif_data(gif_data, slot=slot, progress_cb=progress_cb,
                                     chunk_delay=chunk_delay)

    def upload_gif_data(self, gif_data, slot=1, progress_cb=None, chunk_delay=0.02):
        """Upload raw GIF bytes to the device."""
        size = len(gif_data)
        print(f"GIF data: {size} bytes")
        if progress_cb:
            progress_cb("preparing", 0, 0)

        # Protocol reverse-engineered from ASUS LiveDash v1.05.03 AuraIC.dll
        print("Uploading via LiveDash protocol...")

        # Step 1: Init transfer
        self.write_cmd(CMD_XFER_CTRL, [0xA0])
        time.sleep(0.05)

        # Step 2: Set file slot
        self.write_cmd(CMD_FILE_SLOT, [0x01, 0x00, slot])
        time.sleep(0.05)

        # Step 3-5: Stop animation and prepare for transfer
        self.write_cmd(CMD_ANIM_CTRL, [0x01])  # stop
        time.sleep(0.05)
        self.write_cmd(CMD_ANIM_CTRL, [0x03])  # force stop
        time.sleep(0.05)
        self.write_cmd(CMD_ANIM_CTRL, [0x04])  # prepare transfer
        time.sleep(0.05)

        # Step 6: Send file data in 62-byte chunks
        # Delay between chunks prevents display going black on larger files
        offset = 0
        chunks = 0
        total_chunks = (size + 61) // 62
        if progress_cb:
            progress_cb("uploading", 0, total_chunks)
        while offset < size:
            chunk = gif_data[offset:offset + 62]
            self.write_cmd(CMD_XFER_DATA, [len(chunk)] + list(chunk))
            offset += len(chunk)
            chunks += 1
            time.sleep(chunk_delay)
            if progress_cb and chunks % 5 == 0:
                progress_cb("uploading", chunks, total_chunks)
            if chunks % 50 == 0:
                sys.stdout.write(f"\r  {offset}/{size} bytes ({100*offset//size}%)...")
                sys.stdout.flush()

        if chunks >= 50:
            print()
        print(f"  Sent {chunks} chunks, {size} bytes")

        # Step 7: Signal transfer complete
        self.write_cmd(CMD_ANIM_CTRL, [0x05])
        time.sleep(0.1)

        # Step 8: Finalize
        self.write_cmd(CMD_ANIM_CTRL, [0xFF])
        time.sleep(0.1)

        # Step 9: Commit transfer
        self.write_cmd(CMD_XFER_CTRL, [0x10, 0x01, slot])
        time.sleep(0.2)

        # NOTE: Do NOT write SaveAIO settings to register 0x5C here.
        # Writing to 0x5C causes the OLED to go black. The upload works
        # without it — the firmware picks up the new GIF from the slot.

        # Step 10: Set file slot and start playback
        self.write_cmd(CMD_FILE_SLOT, [0x01, 0x00, slot])
        time.sleep(0.1)
        self.anim_start()
        time.sleep(0.3)

        if progress_cb:
            progress_cb("done", total_chunks, total_chunks)
        print("Upload complete!")
        return True

    # --- OLED control (from LiveDash decompilation) ---

    def oled_on(self):
        """Turn OLED display on."""
        # DLL: array[0]=0 (flag), array[3]=0 (on)
        self._write_display_reg(0, {3: 0})

    def oled_off(self):
        """Turn OLED display off."""
        # DLL: array[0]=0 (flag), array[3]=1 (off)
        self._write_display_reg(0, {3: 1})

    def oled_rotate(self, rotated=True):
        """Rotate OLED display 180 degrees."""
        # DLL: array[0]=0, array[4]=0, array[5]=rotate, then cmd 0x60 [0x80]
        self._write_display_reg(0, {4: 0, 5: 1 if rotated else 0})
        time.sleep(0.05)
        self.write_cmd(0x60, [0x80])

    def oled_reset(self):
        """Reset OLED / remount internal USB drive."""
        self.write_cmd(0x11, [0x52, 0x53, 0x54, 0x41, 0x43, 0x54, 0xFF])

    # --- System sensors ---

    @staticmethod
    def get_cpu_temp():
        """Read CPU temperature from Linux hwmon."""
        import glob
        for path in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
            try:
                name_path = os.path.join(os.path.dirname(path), "name")
                name = open(name_path).read().strip()
                label_path = path.replace("_input", "_label")
                label = open(label_path).read().strip() if os.path.exists(label_path) else ""
                # Look for CPU package/Tctl/Tdie temp
                if name in ("coretemp", "k10temp", "zenpower") or "Package" in label or "Tctl" in label or "Tdie" in label:
                    temp = int(open(path).read().strip()) / 1000
                    return temp, f"{name}/{label}" if label else name
            except (IOError, ValueError):
                continue
        # Fallback: return first temp found
        for path in sorted(glob.glob("/sys/class/hwmon/hwmon*/temp1_input")):
            try:
                temp = int(open(path).read().strip()) / 1000
                name = open(os.path.join(os.path.dirname(path), "name")).read().strip()
                return temp, name
            except (IOError, ValueError):
                continue
        return None, None

    @staticmethod
    def get_gpu_temp():
        """Read GPU temperature from Linux hwmon."""
        import glob
        for path in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
            try:
                name_path = os.path.join(os.path.dirname(path), "name")
                name = open(name_path).read().strip()
                if name in ("amdgpu", "nvidia", "nouveau", "radeon", "intel_gpu"):
                    temp = int(open(path).read().strip()) / 1000
                    return temp, name
            except (IOError, ValueError):
                continue
        return None, None

    # --- Register dump ---

    def dump_registers(self):
        print("Scanning all registers 0x00-0x7F...\n")
        for reg in range(0x00, 0x80):
            resp = self.read_reg(reg)
            if resp and not all(b == 0 for b in resp[2:]):
                hex_str = " ".join(f"{b:02x}" for b in resp[2:20])
                ascii_str = self._str(resp)
                line = f"  0x{reg:02x}: {hex_str}"
                if ascii_str and len(ascii_str) > 2:
                    line += f"  [{ascii_str}]"
                print(line)


def cmd_status(dev):
    info = dev.get_device_info()
    fw = dev.get_firmware()
    disp = dev.get_display_info()
    sensors = dev.get_sensors()

    print(f"Device:     {info['product']} ({info['manufacturer']})")
    print(f"Serial:     {info['serial']}")
    print(f"HW Config:  {info['hw_config']}")
    print(f"AURA FW:    {fw['aura']}")
    print(f"OLED FW:    {fw['oled']}")
    print(f"Animation:  {disp.get('file', 'N/A')}")
    anim = disp.get('anim_running')
    print(f"Anim State: {'running' if anim else 'stopped'} (raw: 0x{disp.get('anim_raw', 0):02x})")
    print(f"Disp Mode:  {disp.get('mode', 'N/A')}")
    if sensors:
        print(f"Coolant:    {sensors['coolant_temp']}°C")
        print(f"Sensors:    {sensors['raw_hex']}")


def cmd_monitor(dev):
    print("Live monitoring (Ctrl+C to stop)\n")
    print(f"{'Time':>8}  {'Temp':>5}  {'Raw Sensors':>24}")
    print("-" * 45)
    try:
        while True:
            sensors = dev.get_sensors()
            if sensors:
                t = time.strftime("%H:%M:%S")
                print(f"{t:>8}  {sensors['coolant_temp']:>4}°C  {sensors['raw_hex']:>24}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


OLED_W, OLED_H = 160, 128
MIN_FRAME_MS = 33


def _process_image(path, brightness=0, contrast=0, saturation=0, blur=0,
                   sharpen=0, speed=1.0, filter_name=None, rotate=False):
    """Apply edits/filters to image/GIF, return GIF bytes for upload."""
    import io
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageChops

    FILTERS = {
        "noir":    lambda img: ImageEnhance.Contrast(ImageOps.grayscale(img.convert("RGB")).convert("RGB")).enhance(1.6),
        "neon":    lambda img: ImageChops.lighter(img.convert("RGB"), ImageEnhance.Color(ImageEnhance.Brightness(img.convert("RGB").filter(ImageFilter.FIND_EDGES)).enhance(3.0)).enhance(2.5)),
        "vhs":     lambda img: (lambda i: Image.merge("RGB", (ImageChops.offset(i.split()[0], 2, 0), i.split()[1], ImageChops.offset(i.split()[2], -1, 0))))(ImageEnhance.Color(img.convert("RGB")).enhance(0.6).filter(ImageFilter.GaussianBlur(0.8))),
        "thermal": lambda img: ImageOps.colorize(img.convert("L"), black=(10, 0, 50), mid=(255, 30, 0), white=(255, 255, 50)),
        "matrix":  lambda img: ImageEnhance.Contrast(ImageOps.colorize(img.convert("L"), black=(0, 5, 0), white=(0, 255, 60))).enhance(1.3),
        "vapor":   lambda img: ImageOps.colorize(img.convert("L"), black=(20, 0, 40), mid=(255, 50, 200), white=(80, 255, 255)),
        "pixel":   lambda img: img.convert("RGB").resize((img.width // 5, img.height // 5), Image.NEAREST).resize(img.size, Image.NEAREST),
        "cinema":  lambda img: (lambda i: Image.merge("RGB", (i.split()[0].point(lambda x: min(255, int(x * 1.08))), i.split()[1], i.split()[2].point(lambda x: int(x * 0.92)))))(ImageEnhance.Color(ImageEnhance.Contrast(img.convert("RGB")).enhance(1.4)).enhance(0.85)),
        "sketch":  lambda img: (lambda g, b: ImageChops.divide(g, ImageOps.invert(b), scale=1, offset=0).convert("RGB"))(ImageOps.grayscale(img.convert("RGB")), ImageOps.invert(ImageOps.grayscale(img.convert("RGB"))).filter(ImageFilter.GaussianBlur(8))),
        "chrome":  lambda img: (lambda i: Image.merge("RGB", (i.split()[0], i.split()[1], i.split()[2].point(lambda x: min(255, int(x * 1.15))))))(ImageEnhance.Contrast(ImageEnhance.Color(img.convert("RGB")).enhance(0.2)).enhance(1.8)),
        "glitch":  lambda img: Image.merge("RGB", (ImageChops.offset(img.convert("RGB").split()[0], 4, 1), img.convert("RGB").split()[1], ImageChops.offset(img.convert("RGB").split()[2], -3, -1))),
        "glow":    lambda img: ImageChops.lighter(img.convert("RGB"), ImageEnhance.Brightness(img.convert("RGB").filter(ImageFilter.GaussianBlur(4))).enhance(1.6)),
    }

    def apply_edits(frame):
        result = frame.convert("RGB")
        if brightness != 0:
            result = ImageEnhance.Brightness(result).enhance(1.0 + brightness * 0.12)
        if contrast != 0:
            result = ImageEnhance.Contrast(result).enhance(1.0 + contrast * 0.12)
        if saturation != 0:
            result = ImageEnhance.Color(result).enhance(1.0 + saturation * 0.15)
        if blur > 0:
            result = result.filter(ImageFilter.GaussianBlur(blur * 0.6))
        if sharpen > 0:
            for _ in range(sharpen):
                result = result.filter(ImageFilter.SHARPEN)
        if filter_name and filter_name in FILTERS:
            result = FILTERS[filter_name](result)
        return result

    img = Image.open(path)
    is_gif = hasattr(img, 'n_frames') and img.n_frames > 1

    if is_gif:
        frames, durations = [], []
        for i in range(img.n_frames):
            img.seek(i)
            frame = img.copy().convert('RGBA')
            frame.thumbnail((OLED_W, OLED_H), Image.LANCZOS)
            canvas = Image.new('RGBA', (OLED_W, OLED_H), (0, 0, 0, 255))
            canvas.paste(frame, ((OLED_W - frame.size[0]) // 2,
                                 (OLED_H - frame.size[1]) // 2))
            if rotate:
                canvas = canvas.rotate(180)
            canvas = apply_edits(canvas)
            frames.append(canvas.convert("RGB"))
            d = max(img.info.get('duration', 100) or 100, MIN_FRAME_MS)
            durations.append(max(MIN_FRAME_MS, int(d / speed)))
        p_frames = [f.quantize(colors=256, method=2) for f in frames]
        buf = io.BytesIO()
        p_frames[0].save(buf, format='GIF', save_all=True,
                         append_images=p_frames[1:],
                         duration=durations, loop=0, optimize=True)
        return buf.getvalue()
    else:
        frame = img.convert('RGB')
        frame.thumbnail((OLED_W, OLED_H), Image.LANCZOS)
        canvas = Image.new('RGB', (OLED_W, OLED_H), (0, 0, 0))
        canvas.paste(frame, ((OLED_W - frame.size[0]) // 2,
                             (OLED_H - frame.size[1]) // 2))
        if rotate:
            canvas = canvas.rotate(180)
        canvas = apply_edits(canvas)
        buf = io.BytesIO()
        canvas.quantize(colors=256, method=2).save(buf, format='GIF', optimize=True)
        return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="ROG Ryuo I 240 Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s status              Show device info
  %(prog)s fan 50              Set fan to 50%%
  %(prog)s led 255 0 0         Set LED to red (static)
  %(prog)s led 0 0 255 2       Set LED to blue (breathing)
  %(prog)s image photo.png     Upload image/GIF to OLED
  %(prog)s -r image cat.gif    Upload rotated 180° (for flipped mounts)
  %(prog)s image cat.gif --filter noir --brightness 2
  %(prog)s image cat.gif --filter neon --speed 0.5 --contrast 3
  %(prog)s color 255 0 0       Set OLED to solid red
  %(prog)s color 0 0 0         Clear OLED (black)
  %(prog)s anim stop           Pause OLED animation
  %(prog)s anim start          Resume OLED animation
  %(prog)s temps               Show CPU/GPU/coolant temps
  %(prog)s monitor             Live sensor display
  %(prog)s dump                Show all registers

Notes:
  The uploaded image has full control of the 160x128 OLED display.
  Use -r flag for 180° rotation (for flipped mounts).

  Do NOT write to register 0x5C — it causes the OLED to go
  permanently black until a full power cycle.""")

    parser.add_argument("command",
                        choices=["status", "fan", "image", "color", "dump",
                                 "monitor", "led", "anim",
                                 "oled-reset", "temps"],
                        help="Command to run")
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument("-d", "--device", help="hidraw device path (auto-detected)")
    parser.add_argument("-r", "--rotate", action="store_true",
                        help="Rotate image 180 degrees before upload")
    parser.add_argument("--brightness", type=int, default=0, metavar="N",
                        help="Brightness adjustment (-5 to 5)")
    parser.add_argument("--contrast", type=int, default=0, metavar="N",
                        help="Contrast adjustment (-5 to 5)")
    parser.add_argument("--saturation", type=int, default=0, metavar="N",
                        help="Saturation adjustment (-5 to 5)")
    parser.add_argument("--blur", type=int, default=0, metavar="N",
                        help="Blur amount (0 to 5)")
    parser.add_argument("--sharpen", type=int, default=0, metavar="N",
                        help="Sharpen amount (0 to 5)")
    parser.add_argument("--speed", type=float, default=1.0, metavar="X",
                        help="GIF speed multiplier (e.g. 0.5, 2.0)")
    parser.add_argument("--filter", dest="filter_name", metavar="NAME",
                        choices=["noir", "neon", "vhs", "thermal", "matrix",
                                 "vapor", "pixel", "cinema", "sketch", "chrome",
                                 "glitch", "glow"],
                        help="Apply a filter (noir neon vhs thermal matrix vapor pixel cinema sketch chrome glitch glow)")

    args = parser.parse_args()
    dev = RyuoDevice(args.device)

    try:
        if args.command == "status":
            cmd_status(dev)

        elif args.command == "fan":
            if not args.args:
                print("Usage: fan <duty 0-100>")
                sys.exit(1)
            duty = int(args.args[0])
            dev.set_fan(duty)
            print(f"Fan duty set to {duty}%")

        elif args.command == "image":
            if not args.args:
                print("Usage: image <path/to/image.png|gif>")
                sys.exit(1)
            has_edits = (args.brightness != 0 or args.contrast != 0 or
                         args.saturation != 0 or args.blur > 0 or
                         args.sharpen > 0 or args.speed != 1.0 or
                         args.filter_name)
            if has_edits:
                print(f"Processing {args.args[0]}...")
                data = _process_image(
                    args.args[0], args.brightness, args.contrast,
                    args.saturation, args.blur, args.sharpen,
                    args.speed, args.filter_name, args.rotate)
                dev.upload_gif_data(data)
                print("Uploaded with edits applied")
            else:
                dev.upload_image(args.args[0], rotate=args.rotate)

        elif args.command == "color":
            if not args.args or len(args.args) < 3:
                print("Usage: color <R> <G> <B>")
                print("  color 255 0 0       Red background")
                print("  color 0 0 0         Black (clear display)")
                print("  color 20 20 35      Dark grey (subtle)")
                sys.exit(1)
            r, g, b = int(args.args[0]), int(args.args[1]), int(args.args[2])
            try:
                from PIL import Image
                import io as _io
                img = Image.new("RGB", (160, 128), (r, g, b))
                if args.rotate:
                    img = img.rotate(180)
                buf = _io.BytesIO()
                img.quantize(colors=256, method=2).save(buf, format="GIF", optimize=True)
                dev.upload_gif_data(buf.getvalue())
                print(f"OLED set to RGB({r},{g},{b})")
            except ImportError:
                print("Pillow required: nix-shell -p python3 python3Packages.pillow")

        elif args.command == "dump":
            dev.dump_registers()

        elif args.command == "monitor":
            cmd_monitor(dev)

        elif args.command == "led":
            if len(args.args) < 3:
                print("Usage: led <R> <G> <B> [mode]")
                print("Modes: 1=static 2=breathing 3=flash 4=spectrum 5=rainbow")
                sys.exit(1)
            r, g, b = int(args.args[0]), int(args.args[1]), int(args.args[2])
            mode = int(args.args[3]) if len(args.args) > 3 else 1
            dev.set_led(r, g, b, mode)
            print(f"LED set to RGB({r},{g},{b}) mode={mode}")

        elif args.command == "anim":
            if not args.args or args.args[0] not in ("stop", "start"):
                print("Usage: anim <stop|start>")
                sys.exit(1)
            if args.args[0] == "stop":
                dev.anim_stop()
                print("Animation stopped")
            else:
                dev.anim_start()
                print("Animation started")

        elif args.command == "oled-reset":
            dev.oled_reset()
            print("OLED reset sent (device may remount USB)")

        elif args.command == "temps":
            cpu_temp, cpu_src = RyuoDevice.get_cpu_temp()
            gpu_temp, gpu_src = RyuoDevice.get_gpu_temp()
            sensors = dev.get_sensors()
            if cpu_temp is not None:
                print(f"CPU:     {cpu_temp:.1f}°C  ({cpu_src})")
            else:
                print("CPU:     not found")
            if gpu_temp is not None:
                print(f"GPU:     {gpu_temp:.1f}°C  ({gpu_src})")
            else:
                print("GPU:     not found")
            if sensors:
                print(f"Coolant: {sensors['coolant_temp']}°C")

    finally:
        dev.close()


if __name__ == "__main__":
    main()

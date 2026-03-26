"""
Patch for liquidctl's asus_ryuo.py driver to add OLED screen support.

This implements set_screen() for the ASUS ROG Ryuo I 240 (VID 0x0B05, PID 0x1887).
Protocol reverse-engineered from ASUS LiveDash v1.05.03 (AuraIC.dll).

Supported modes:
  liquidctl set lcd screen static <path>    # Upload static image
  liquidctl set lcd screen gif <path>       # Upload animated GIF
  liquidctl set lcd screen orientation 180  # Rotate before upload (0 or 180)

Usage:
  Apply this as a patch to liquidctl/driver/asus_ryuo.py, replacing the
  existing set_screen() stub. Or reference it for a PR submission.

Protocol notes:
  - OLED is 160x128, 1.77" color display
  - Device accepts raw GIF data uploaded in 62-byte HID chunks
  - Files stored on internal FatFS flash (~3.73MB)
  - 20ms delay between chunks required for reliability
  - Firmware composites ROG logo + temp overlay on top of uploaded image
  - CRITICAL: Never write to register 0x5C — causes permanent black screen
    until power cycle. This means hardware OLED on/off/rotate is unsafe.
    Use software rotation (rotate image before upload) instead.
"""

import logging
import time

_LOGGER = logging.getLogger(__name__)

# -- Constants to add to the driver --

_CMD_XFER_CTRL = 0x51
_CMD_FILE_SLOT = 0x6b
_CMD_ANIM_CTRL = 0x6c
_CMD_XFER_DATA = 0x6e
_CHUNK_SIZE = 62
_CHUNK_DELAY = 0.02  # 20ms between chunks for reliability
_OLED_WIDTH = 160
_OLED_HEIGHT = 128


def set_screen(self, channel, mode, value, **kwargs):
    """Set the LCD/OLED screen mode.

    Valid channels: lcd
    Valid modes:
      - static <path>       Upload a static image (PNG, JPG, BMP, GIF)
      - gif <path>          Upload an animated GIF
      - orientation <0|180> Set rotation (applied to image before upload)

    The device firmware always composites ROG logo and temperature
    overlays on top of the uploaded image. These cannot be removed.

    .. versionadded:: NEXT
    .. unstable_api
    """
    if channel != "lcd":
        raise ValueError(f"unsupported channel: {channel}")

    if mode == "orientation":
        orientation = int(value)
        if orientation not in (0, 180):
            raise ValueError("orientation must be 0 or 180")
        # Store for use during image uploads
        self._oled_orientation = orientation
        _LOGGER.info("oled orientation set to %d°", orientation)
        return []

    if mode in ("static", "gif"):
        rotation = getattr(self, "_oled_orientation", 0)
        gif_data = _prepare_image(value, mode == "gif", rotation)
        _upload_to_device(self, gif_data)
        _LOGGER.info(
            "uploaded %d bytes to oled (rotation=%d°)", len(gif_data), rotation
        )
        return []

    raise ValueError(f"unsupported mode: {mode}")


def _prepare_image(path, is_animated, rotation):
    """Load image, resize to 160x128, rotate, encode as GIF."""
    from PIL import Image
    import io

    img = Image.open(path)
    has_frames = hasattr(img, "n_frames") and img.n_frames > 1

    if has_frames and is_animated:
        frames = []
        durations = []
        for i in range(img.n_frames):
            img.seek(i)
            frame = img.copy().convert("RGBA")
            frame.thumbnail((_OLED_WIDTH, _OLED_HEIGHT), Image.LANCZOS)
            canvas = Image.new("RGBA", (_OLED_WIDTH, _OLED_HEIGHT), (0, 0, 0, 255))
            canvas.paste(
                frame,
                (
                    (_OLED_WIDTH - frame.size[0]) // 2,
                    (_OLED_HEIGHT - frame.size[1]) // 2,
                ),
            )
            if rotation == 180:
                canvas = canvas.rotate(180)
            frames.append(canvas)
            durations.append(img.info.get("duration", 100))

        buf = io.BytesIO()
        frames[0].save(
            buf,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
        )
        return buf.getvalue()
    else:
        img = img.convert("RGB")
        img.thumbnail((_OLED_WIDTH, _OLED_HEIGHT), Image.LANCZOS)
        canvas = Image.new("RGB", (_OLED_WIDTH, _OLED_HEIGHT), (0, 0, 0))
        canvas.paste(
            img,
            (
                (_OLED_WIDTH - img.size[0]) // 2,
                (_OLED_HEIGHT - img.size[1]) // 2,
            ),
        )
        if rotation == 180:
            canvas = canvas.rotate(180)
        buf = io.BytesIO()
        canvas.save(buf, format="GIF")
        return buf.getvalue()


def _upload_to_device(driver, gif_data, slot=1):
    """Upload GIF data to the device using the LiveDash protocol.

    Protocol reverse-engineered from ASUS LiveDash v1.05.03 (AuraIC.dll).
    WriteFileToFW() in AuraIC.dll implements this 9-step sequence:

    1. WriteCommand(0x51, [0xA0])           - init/reset transfer
    2. WriteCommand(0x6B, [0x01, 0x00, N])  - set file slot index
    3. WriteCommand(0x6C, [0x01])           - stop animation
    4. WriteCommand(0x6C, [0x03])           - force stop animation
    5. WriteCommand(0x6C, [0x04])           - prepare for transfer
    6. Loop: WriteCommand(0x6E, [count, ...data...]) - send 62-byte chunks
    7. WriteCommand(0x6C, [0x05])           - transfer complete
    8. WriteCommand(0x6C, [0xFF])           - finalize
    9. WriteCommand(0x51, [0x10, 0x01, N])  - commit transfer

    Then set slot and start playback with [0x6E, 0x00].

    IMPORTANT: Do NOT write to register 0x5C (SaveAIO) after upload.
    Any write to 0x5C causes the OLED to go permanently black until
    a full power cycle (PSU off, unplug USB header, replug).

    A 20ms delay between data chunks is required for reliable transfer
    of files larger than ~300 bytes.
    """
    from liquidctl.util import rpadlist

    def _cmd(data):
        driver.device.write(rpadlist([_PREFIX] + list(data), _REPORT_LENGTH, 0x00))

    _cmd([_CMD_XFER_CTRL, 0xa0])
    time.sleep(0.05)
    _cmd([_CMD_FILE_SLOT, 0x01, 0x00, slot])
    time.sleep(0.05)
    _cmd([_CMD_ANIM_CTRL, 0x01])
    time.sleep(0.05)
    _cmd([_CMD_ANIM_CTRL, 0x03])
    time.sleep(0.05)
    _cmd([_CMD_ANIM_CTRL, 0x04])
    time.sleep(0.05)

    offset = 0
    chunks = 0
    while offset < len(gif_data):
        chunk = gif_data[offset : offset + _CHUNK_SIZE]
        _cmd([_CMD_XFER_DATA, len(chunk)] + list(chunk))
        offset += len(chunk)
        chunks += 1
        time.sleep(_CHUNK_DELAY)

    _LOGGER.debug("sent %d chunks (%d bytes)", chunks, len(gif_data))

    _cmd([_CMD_ANIM_CTRL, 0x05])
    time.sleep(0.1)
    _cmd([_CMD_ANIM_CTRL, 0xff])
    time.sleep(0.1)
    _cmd([_CMD_XFER_CTRL, 0x10, 0x01, slot])
    time.sleep(0.2)

    # Start playback — NO register 0x5C write!
    _cmd([_CMD_FILE_SLOT, 0x01, 0x00, slot])
    time.sleep(0.1)
    _cmd([_CMD_XFER_DATA, 0x00])
    time.sleep(0.3)

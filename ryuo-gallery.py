#!/usr/bin/env python3
"""
Ryuo OLED Gallery — local GIF browser, generators, and uploader.

Usage:
  nix-shell -p python3Packages.tkinter python3Packages.pillow python3Packages.psutil \
    --run "python3 ryuo-gallery.py -r"
"""

import argparse
import io
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import (Image, ImageTk, ImageDraw, ImageFont, ImageEnhance,
                 ImageFilter, ImageOps, ImageChops)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COLLECTION_DIR = os.path.join(SCRIPT_DIR, "collection")
THUMB_SIZE = (160, 128)
GRID_COLS = 4

sys.path.insert(0, SCRIPT_DIR)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ryuo_oled", os.path.join(SCRIPT_DIR, "ryuo-oled.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
RyuoDevice = _mod.RyuoDevice

# -- Theme --
BG      = "#08080c"
BG2     = "#0c0c12"
SURF    = "#141420"
BORDER  = "#1a1a28"
ACCENT  = "#ff2d55"
ACCENT2 = "#cc1a40"
TEXT    = "#e0dfe6"
DIM     = "#888894"
DIM2    = "#50505c"
GREEN   = "#00e676"
RED     = "#ff1744"
CYAN    = "#00e5ff"
FONT    = ("Monospace", 9)
FONT_XS = ("Monospace", 8)
FONT_LG = ("Monospace", 13)
FONT_TT = ("Monospace", 16, "bold")

PREVIEW_W, PREVIEW_H = 272, 218
OLED_W, OLED_H = 160, 128
MIN_FRAME_MS = 33


def get_frame_durations(img):
    durations = []
    for i in range(img.n_frames):
        img.seek(i)
        d = img.info.get("duration", 100)
        if d <= 0:
            d = 100
        d = max(d, MIN_FRAME_MS)
        durations.append(d)
    img.seek(0)
    return durations


def build_pump_mockup(content_img, size=380):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    r = size // 2 - 4
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(12, 12, 16))
    draw.ellipse([cx-r+1, cy-r+1, cx+r-1, cy-r + int(r*0.3)], fill=(20, 20, 26))
    inner_r = int(r * 0.88)
    draw.ellipse([cx-inner_r, cy-inner_r, cx+inner_r, cy+inner_r], fill=(2, 2, 3))
    sw = int(inner_r * 1.65)
    sh = int(sw * OLED_H / OLED_W)
    sx, sy = cx - sw // 2, cy - sh // 2
    content = content_img.copy().convert("RGBA").resize((sw, sh), Image.LANCZOS)
    img.paste(content, (sx, sy), content)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([cx-r, cy-r, cx+r, cy+r], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


# ---------------------------------------------------------------------------
# Filters — cinematic / video-editing style composites
# ---------------------------------------------------------------------------

def _noir(img):
    img = ImageOps.grayscale(img.convert("RGB")).convert("RGB")
    return ImageEnhance.Contrast(img).enhance(1.6)


def _neon(img):
    img = img.convert("RGB")
    edges = img.filter(ImageFilter.FIND_EDGES)
    edges = ImageEnhance.Brightness(edges).enhance(3.0)
    edges = ImageEnhance.Color(edges).enhance(2.5)
    return ImageChops.lighter(img, edges)


def _vhs(img):
    img = img.convert("RGB")
    img = ImageEnhance.Color(img).enhance(0.6)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    r, g, b = img.split()
    # Slight channel offset for VHS look
    r = ImageChops.offset(r, 2, 0)
    b = ImageChops.offset(b, -1, 0)
    return Image.merge("RGB", (r, g, b))


def _thermal(img):
    gray = img.convert("L")
    return ImageOps.colorize(gray, black=(10, 0, 50), mid=(255, 30, 0),
                             white=(255, 255, 50))


def _matrix_green(img):
    gray = img.convert("L")
    result = ImageOps.colorize(gray, black=(0, 5, 0), white=(0, 255, 60))
    return ImageEnhance.Contrast(result).enhance(1.3)


def _vapor(img):
    gray = img.convert("L")
    return ImageOps.colorize(gray, black=(20, 0, 40), mid=(255, 50, 200),
                             white=(80, 255, 255))


def _pixel(img):
    img = img.convert("RGB")
    small = img.resize((img.width // 5, img.height // 5), Image.NEAREST)
    return small.resize(img.size, Image.NEAREST)


def _cinema(img):
    img = img.convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.4)
    img = ImageEnhance.Color(img).enhance(0.85)
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * 1.08)))
    b = b.point(lambda x: int(x * 0.92))
    return Image.merge("RGB", (r, g, b))


def _sketch(img):
    img = img.convert("RGB")
    gray = ImageOps.grayscale(img)
    inv = ImageOps.invert(gray)
    blur = inv.filter(ImageFilter.GaussianBlur(8))
    result = ImageChops.divide(gray, ImageOps.invert(blur), scale=1, offset=0)
    return result.convert("RGB")


def _chrome(img):
    img = img.convert("RGB")
    img = ImageEnhance.Color(img).enhance(0.2)
    img = ImageEnhance.Contrast(img).enhance(1.8)
    r, g, b = img.split()
    b = b.point(lambda x: min(255, int(x * 1.15)))
    return Image.merge("RGB", (r, g, b))


def _glitch(img):
    img = img.convert("RGB")
    r, g, b = img.split()
    r = ImageChops.offset(r, 4, 1)
    b = ImageChops.offset(b, -3, -1)
    return Image.merge("RGB", (r, g, b))


def _glow(img):
    img = img.convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(4))
    bright = ImageEnhance.Brightness(blurred).enhance(1.6)
    return ImageChops.lighter(img, bright)


FILTERS = {
    "noir":    _noir,
    "neon":    _neon,
    "vhs":     _vhs,
    "thermal": _thermal,
    "matrix":  _matrix_green,
    "vapor":   _vapor,
    "pixel":   _pixel,
    "cinema":  _cinema,
    "sketch":  _sketch,
    "chrome":  _chrome,
    "glitch":  _glitch,
    "glow":    _glow,
}


def apply_edits(img, brightness=0, contrast=0, saturation=0, blur=0, sharpen=0,
                active_filter=None):
    """Apply adjustments and optional filter to an RGB image."""
    result = img.convert("RGB")
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
    if active_filter and active_filter in FILTERS:
        result = FILTERS[active_filter](result)
    return result


def apply_edits_to_durations(durations, speed_mult):
    if speed_mult == 1.0:
        return durations
    return [max(MIN_FRAME_MS, int(d / speed_mult)) for d in durations]


def process_for_upload(image_path_or_data, brightness=0, contrast=0,
                       saturation=0, blur=0, sharpen=0,
                       speed_mult=1.0, active_filter=None,
                       rotate=False, progress_cb=None):
    """Process image/GIF with edits+filter, return GIF bytes for upload."""
    if isinstance(image_path_or_data, bytes):
        img = Image.open(io.BytesIO(image_path_or_data))
    else:
        img = Image.open(image_path_or_data)

    is_gif = hasattr(img, 'n_frames') and img.n_frames > 1
    has_edits = brightness != 0 or contrast != 0 or saturation != 0 or blur > 0 or sharpen > 0 or active_filter

    if is_gif:
        frames, durations = [], []
        n = img.n_frames
        for i in range(n):
            if progress_cb and i % 3 == 0:
                progress_cb("processing", i, n)
            img.seek(i)
            frame = img.copy().convert('RGBA')
            frame.thumbnail((OLED_W, OLED_H), Image.LANCZOS)
            canvas = Image.new('RGBA', (OLED_W, OLED_H), (0, 0, 0, 255))
            canvas.paste(frame, ((OLED_W - frame.size[0]) // 2,
                                 (OLED_H - frame.size[1]) // 2))
            if rotate:
                canvas = canvas.rotate(180)
            if has_edits:
                canvas = apply_edits(canvas, brightness, contrast, saturation,
                                     blur, sharpen, active_filter=active_filter)
            frames.append(canvas.convert("RGB"))
            durations.append(max(img.info.get('duration', 100) or 100, MIN_FRAME_MS))

        durations = apply_edits_to_durations(durations, speed_mult)
        if progress_cb:
            progress_cb("encoding", 0, 0)
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
        if has_edits:
            canvas = apply_edits(canvas, brightness, contrast, saturation,
                                 blur, sharpen, active_filter=active_filter)
        buf = io.BytesIO()
        canvas.quantize(colors=256, method=2).save(buf, format='GIF', optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mono_font(size=10):
    paths = [
        "/nix/store/dml7jig0fh35chx530ga4icf68j4qayf-dejavu-fonts-2.37/share/fonts/truetype/DejaVuSansMono.ttf",
        "/run/current-system/sw/share/fonts/truetype/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.truetype("DejaVuSansMono", size)
    except Exception:
        return ImageFont.load_default()


def _draw_bar(draw, x, y, w, h, pct, color, bg=(20, 20, 30)):
    draw.rectangle([x, y, x + w, y + h], fill=bg)
    bar_w = int(w * min(pct, 100) / 100)
    if bar_w > 0:
        draw.rectangle([x, y, x + bar_w, y + h], fill=color)


def _try_import_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _get_gpu_info():
    try:
        r = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            parts = r.stdout.strip().split(', ')
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None, None


def _frames_to_gif(frames, durations):
    p_frames = [f.convert('RGB').quantize(colors=256, method=2) for f in frames]
    buf = io.BytesIO()
    p_frames[0].save(buf, format='GIF', save_all=True,
                     append_images=p_frames[1:],
                     duration=durations, loop=0, optimize=True)
    return buf.getvalue()


def _single_to_gif(img):
    buf = io.BytesIO()
    img.convert('RGB').quantize(colors=256, method=2).save(
        buf, format='GIF', optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_stats_frame(psutil_mod):
    cpu_pct = psutil_mod.cpu_percent(interval=0)
    mem = psutil_mod.virtual_memory()
    cpu_temp = None
    temps = psutil_mod.sensors_temperatures()
    for key in ('k10temp', 'coretemp', 'cpu_thermal'):
        if key in temps and temps[key]:
            cpu_temp = temps[key][0].current
            break
    gpu_temp, gpu_pct = _get_gpu_info()

    img = Image.new("RGB", (OLED_W, OLED_H), (4, 4, 8))
    draw = ImageDraw.Draw(img)
    font = _mono_font(10)
    font_sm = _mono_font(9)

    y = 4
    draw.text((4, y), "SYSTEM", fill=(255, 45, 85), font=font)
    y += 14
    draw.text((4, y), f"CPU {cpu_pct:4.0f}%", fill=(224, 224, 230), font=font_sm)
    if cpu_temp:
        draw.text((110, y), f"{cpu_temp:.0f}\u00b0", fill=(136, 136, 148), font=font_sm)
    y += 12
    _draw_bar(draw, 4, y, 152, 6, cpu_pct, (0, 229, 255))
    y += 12
    if gpu_pct is not None:
        draw.text((4, y), f"GPU {gpu_pct:4.0f}%", fill=(224, 224, 230), font=font_sm)
        if gpu_temp:
            draw.text((110, y), f"{gpu_temp}\u00b0", fill=(136, 136, 148), font=font_sm)
        y += 12
        _draw_bar(draw, 4, y, 152, 6, gpu_pct, (0, 230, 118))
        y += 12
    draw.text((4, y), f"RAM {mem.percent:4.0f}%", fill=(224, 224, 230), font=font_sm)
    ug = mem.used / (1024**3)
    tg = mem.total / (1024**3)
    draw.text((95, y), f"{ug:.0f}/{tg:.0f}G", fill=(136, 136, 148), font=font_sm)
    y += 12
    _draw_bar(draw, 4, y, 152, 6, mem.percent, (255, 234, 0))
    y += 14
    cores = psutil_mod.cpu_percent(interval=0, percpu=True)
    draw.text((4, y), "CORES", fill=(80, 80, 92), font=font_sm)
    y += 11
    bw = max(2, (152 - (len(cores) - 1)) // len(cores))
    for i, c in enumerate(cores):
        bx = 4 + i * (bw + 1)
        bh = int(20 * c / 100)
        col = (int(255 * c / 100), int(229 * (1 - c / 100)), int(80 * (1 - c / 100))) if c >= 50 else (0, int(229 * (1 - c / 100)), 255)
        draw.rectangle([bx, y + 20 - bh, bx + bw - 1, y + 20], fill=col)
        draw.rectangle([bx, y, bx + bw - 1, y + 20], outline=(20, 20, 30))
    return img


def generate_stats_gif(progress_cb=None):
    ps = _try_import_psutil()
    if not ps:
        return None
    ps.cpu_percent(interval=0.1)
    ps.cpu_percent(interval=0, percpu=True)
    frames, durs = [], []
    for i in range(8):
        if progress_cb:
            progress_cb("generating", i, 8)
        frames.append(generate_stats_frame(ps))
        durs.append(500)
        if i < 7:
            time.sleep(0.5)
    if progress_cb:
        progress_cb("encoding", 0, 0)
    return _frames_to_gif(frames, durs)


def _draw_nix_logo(draw, cx, cy, radius):
    """Draw NixOS snowflake logo — 6 bars arranged hexagonally."""
    light = (126, 186, 228)
    dark = (82, 119, 195)
    bar_len = radius * 0.85
    bar_w = radius * 0.26

    for i in range(6):
        angle = math.radians(i * 60)
        color = light if i % 2 == 0 else dark
        dx, dy = math.cos(angle), math.sin(angle)
        px, py = -dy, dx
        inner = radius * 0.16
        outer = inner + bar_len
        points = [
            (cx + dx * inner + px * bar_w, cy + dy * inner + py * bar_w),
            (cx + dx * outer + px * bar_w, cy + dy * outer + py * bar_w),
            (cx + dx * outer - px * bar_w, cy + dy * outer - py * bar_w),
            (cx + dx * inner - px * bar_w, cy + dy * inner - py * bar_w),
        ]
        draw.polygon(points, fill=color)
    # Center void
    hr = radius * 0.22
    hex_pts = [(cx + hr * math.cos(math.radians(a)),
                cy + hr * math.sin(math.radians(a))) for a in range(0, 360, 60)]
    draw.polygon(hex_pts, fill=(4, 4, 8))


def generate_fastfetch_image(progress_cb=None):
    if progress_cb:
        progress_cb("generating", 0, 1)
    info = []
    try:
        r = subprocess.run(['fastfetch', '--pipe', '--logo', 'none'],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().split('\n'):
            l = line.strip()
            if l and not l.startswith('-'):
                info.append(l)
    except Exception:
        info = ["no data"]

    img = Image.new("RGB", (OLED_W, OLED_H), (4, 4, 8))
    draw = ImageDraw.Draw(img)
    fk = _mono_font(8)
    fv = _mono_font(7)
    ft = _mono_font(10)

    # Logo
    _draw_nix_logo(draw, 28, 30, 24)

    # Username
    name = info[0][:16] if info and '@' in info[0] else "NixOS"
    draw.text((58, 6), name, fill=(255, 255, 255), font=ft)
    draw.line([(58, 20), (156, 20)], fill=(255, 45, 85), width=1)

    key_order = ['OS', 'Kernel', 'CPU', 'GPU', 'Memory', 'Uptime', 'DE', 'Shell', 'WM']
    shown = []
    for pfx in key_order:
        for line in info:
            if line.startswith(pfx + ':'):
                shown.append(line)
                break

    # Info beside logo
    y = 24
    for line in shown[:3]:
        if ':' in line:
            k, _, v = line.partition(':')
            v = v.strip()[:15]
            draw.text((58, y), k[:6], fill=(0, 200, 255), font=fk)
            draw.text((100, y), v, fill=(200, 200, 210), font=fv)
        y += 10

    # Info below logo
    draw.line([(4, 58), (156, 58)], fill=(30, 30, 50), width=1)
    y = 62
    for line in shown[3:]:
        if y > 122:
            break
        if ':' in line:
            k, _, v = line.partition(':')
            v = v.strip()
            if len(v) > 20:
                v = v[:19] + "\u2026"
            draw.text((4, y), k[:7], fill=(0, 200, 255), font=fk)
            draw.text((50, y), v, fill=(200, 200, 210), font=fv)
        y += 10

    if progress_cb:
        progress_cb("encoding", 0, 0)
    return _single_to_gif(img)


def generate_clock_gif(progress_cb=None):
    frames, durs = [], []
    fb = _mono_font(28)
    fm = _mono_font(12)
    fs = _mono_font(9)
    now = time.localtime()
    for i in range(12):
        if progress_cb:
            progress_cb("generating", i, 12)
        t = time.localtime(time.mktime(now) + i * 5)
        img = Image.new("RGB", (OLED_W, OLED_H), (4, 4, 8))
        d = ImageDraw.Draw(img)
        h, m, s = t.tm_hour, t.tm_min, (t.tm_sec + i * 5) % 60
        colon = ":" if i % 2 == 0 else " "
        ts = f"{h:02d}{colon}{m:02d}"
        tw = d.textlength(ts, font=fb)
        d.text(((OLED_W - tw) / 2, 20), ts, fill=(255, 255, 255), font=fb)
        ss = f":{s:02d}"
        sw = d.textlength(ss, font=fm)
        d.text(((OLED_W - sw) / 2, 55), ss, fill=(136, 136, 148), font=fm)
        ds = time.strftime("%a %b %d", t)
        dw = d.textlength(ds, font=fm)
        d.text(((OLED_W - dw) / 2, 78), ds, fill=(0, 229, 255), font=fm)
        d.line([(20, 100), (140, 100)], fill=(255, 45, 85), width=1)
        ys = time.strftime("%Y", t)
        yw = d.textlength(ys, font=fs)
        d.text(((OLED_W - yw) / 2, 106), ys, fill=(80, 80, 92), font=fs)
        frames.append(img)
        durs.append(5000)
    if progress_cb:
        progress_cb("encoding", 0, 0)
    return _frames_to_gif(frames, durs)


def generate_text_image(text, progress_cb=None):
    if progress_cb:
        progress_cb("generating", 0, 1)
    img = Image.new("RGB", (OLED_W, OLED_H), (4, 4, 8))
    draw = ImageDraw.Draw(img)
    lines = text.strip().split('\\n') if text.strip() else ["ryuo"]
    for sz in [24, 20, 16, 14, 12, 10, 8]:
        f = _mono_font(sz)
        mw = max(draw.textlength(l, font=f) for l in lines)
        th = len(lines) * (sz + 4)
        if mw <= OLED_W - 8 and th <= OLED_H - 8:
            break
    th = len(lines) * (sz + 4)
    y = (OLED_H - th) // 2
    for l in lines:
        tw = draw.textlength(l, font=f)
        draw.text(((OLED_W - tw) // 2, y), l, fill=(255, 45, 85), font=f)
        y += sz + 4
    if progress_cb:
        progress_cb("encoding", 0, 0)
    return _single_to_gif(img)


def generate_matrix_gif(progress_cb=None):
    cols, rows = 20, 16
    cw, ch = OLED_W // cols, OLED_H // rows
    n_frames = 40
    chars = "abcdefghijklmnopqrstuvwxyz0123456789@#$%&*<>/"
    drops = [{'y': random.randint(-rows, 0), 'speed': random.randint(1, 3),
              'chars': [random.choice(chars) for _ in range(rows + 10)],
              'length': random.randint(5, 14)} for _ in range(cols)]
    font = _mono_font(8)
    frames, durs = [], []
    for fi in range(n_frames):
        if progress_cb:
            progress_cb("generating", fi, n_frames)
        img = Image.new("RGB", (OLED_W, OLED_H), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        for c, dr in enumerate(drops):
            for r in range(rows):
                ci = dr['y'] - r
                if ci < 0 or ci >= len(dr['chars']):
                    continue
                if r == 0:
                    color = (200, 255, 200)
                elif r < dr['length']:
                    fade = 1.0 - (r / dr['length'])
                    color = (0, max(30, int(200 * fade)), 0)
                else:
                    continue
                draw.text((c * cw + 1, r * ch), dr['chars'][ci % len(dr['chars'])],
                          fill=color, font=font)
            dr['y'] += dr['speed']
            if dr['y'] - dr['length'] > rows:
                dr['y'] = random.randint(-10, -1)
                dr['speed'] = random.randint(1, 3)
                dr['length'] = random.randint(5, 14)
                dr['chars'] = [random.choice(chars) for _ in range(rows + 10)]
            if random.random() < 0.4:
                dr['chars'][random.randint(0, len(dr['chars']) - 1)] = random.choice(chars)
        frames.append(img)
        durs.append(80)
    if progress_cb:
        progress_cb("encoding", 0, 0)
    return _frames_to_gif(frames, durs)


# ---------------------------------------------------------------------------
# MockupWindow
# ---------------------------------------------------------------------------

class MockupWindow:
    def __init__(self, root, source_img):
        self.win = tk.Toplevel(root)
        self.win.title("pump preview")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.thumbs = {}
        self.anim_frames = []
        self.anim_idx = 0
        self.anim_after = None
        size = 380
        frame = tk.Frame(self.win, bg=BG, padx=10, pady=10)
        frame.pack()
        self.label = tk.Label(frame, bg=BG)
        self.label.pack()
        tk.Label(frame, text="pump preview  \u2022  160x128 oled  \u2022  1.77\"",
                 fg=DIM2, bg=BG, font=FONT_XS).pack(pady=(4, 0))
        root.update_idletasks()
        rx, ry, rw = root.winfo_x(), root.winfo_y(), root.winfo_width()
        pw, ph = size + 20, size + 50
        px = rx + rw - pw - 10
        py = ry - ph - 10
        if py < 0:
            py = ry + 40
            px = rx + rw + 10
        self.win.geometry(f"+{px}+{py}")
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        has_frames = hasattr(source_img, "n_frames") and source_img.n_frames > 1
        if has_frames:
            durs = get_frame_durations(source_img)
            for i in range(min(source_img.n_frames, 80)):
                source_img.seek(i)
                fr = source_img.copy().convert("RGBA")
                self.anim_frames.append(ImageTk.PhotoImage(build_pump_mockup(fr, size)))
            self.durations = durs
            self._animate()
        else:
            fr = source_img.copy().convert("RGBA")
            photo = ImageTk.PhotoImage(build_pump_mockup(fr, size))
            self.thumbs["s"] = photo
            self.label.configure(image=photo)

    def _animate(self):
        if not self.anim_frames or not self.win.winfo_exists():
            return
        f = self.anim_frames[self.anim_idx]
        self.label.configure(image=f)
        self.thumbs["c"] = f
        d = self.durations[self.anim_idx % len(self.durations)]
        self.anim_idx = (self.anim_idx + 1) % len(self.anim_frames)
        self.anim_after = self.win.after(d, self._animate)

    def _close(self):
        if self.anim_after:
            self.win.after_cancel(self.anim_after)
        self.win.destroy()


# ---------------------------------------------------------------------------
# Gallery App
# ---------------------------------------------------------------------------

MODES = ["collection", "stats", "fastfetch", "clock", "text", "matrix"]
LIVE_MODES = {"stats"}


class GalleryApp:
    def __init__(self, root, rotate=False):
        self.root = root
        self.rotate = rotate
        self.device = None
        self.selected = None
        self.selected_widgets = {}
        self.thumbs = {}
        self.anim_frames = []
        self.anim_durations = []
        self.anim_idx = 0
        self.anim_after = None
        self.uploading = False
        self.source_img = None
        self.mockup_win = None
        self.current_mode = "collection"
        self.live_running = False
        self.generated_gif_data = None
        # Edit state
        self.brightness = 0
        self.contrast = 0
        self.saturation = 0
        self.blur = 0
        self.sharpen = 0
        self.speed_mult = 1.0
        self.active_filter = None

        root.title("ryuo")
        root.configure(bg=BG)
        root.geometry("1100x740")
        root.minsize(700, 500)
        self._build_ui()
        self._connect_device()
        self._load_gallery()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG, padx=20, pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ryuo", font=FONT_TT, fg=ACCENT, bg=BG).pack(side="left")
        tk.Label(hdr, text=" gallery", font=("Monospace", 16),
                 fg=DIM2, bg=BG).pack(side="left")
        self.status_dot = tk.Label(hdr, text="\u25cf", fg=DIM2, bg=BG,
                                    font=("Monospace", 8))
        self.status_dot.pack(side="right", padx=(0, 4))
        self.status_label = tk.Label(hdr, text="connecting...", fg=DIM2,
                                      bg=BG, font=FONT_XS)
        self.status_label.pack(side="right")
        tk.Frame(self.root, bg=ACCENT, height=1).pack(fill="x", padx=20)

        # Tabs
        self.tab_frame = tk.Frame(self.root, bg=BG, padx=20, pady=6)
        self.tab_frame.pack(fill="x")
        self.tab_btns = {}
        for mode in MODES:
            b = tk.Label(self.tab_frame, text=mode, fg=DIM2, bg=BG, font=FONT,
                         padx=10, pady=3, cursor="hand2")
            b.pack(side="left", padx=(0, 2))
            b.bind("<Button-1>", lambda e, m=mode: self._switch_mode(m))
            self.tab_btns[mode] = b
        self.tab_btns["collection"].configure(fg=ACCENT, bg=SURF)

        # Collection toolbar
        self.toolbar = tk.Frame(self.root, bg=BG, padx=20, pady=4)
        self.toolbar.pack(fill="x")
        self._btn(self.toolbar, "+ add", self._add_files).pack(side="left", padx=(0, 4))
        self._btn(self.toolbar, "folder", self._open_folder).pack(side="left", padx=(0, 4))
        self._btn(self.toolbar, "refresh", self._load_gallery).pack(side="left")
        self.rotate_var = tk.BooleanVar(value=self.rotate)
        tk.Checkbutton(self.toolbar, text="rotate 180\u00b0", variable=self.rotate_var,
                       bg=BG, fg=DIM, selectcolor=SURF, activebackground=BG,
                       activeforeground=TEXT, font=FONT_XS,
                       highlightthickness=0, bd=0).pack(side="right")

        # Generator toolbar
        self.gen_toolbar = tk.Frame(self.root, bg=BG, padx=20, pady=4)
        self.gen_rotate_cb = tk.Checkbutton(self.gen_toolbar, text="rotate 180\u00b0",
                                             variable=self.rotate_var,
                                             bg=BG, fg=DIM, selectcolor=SURF,
                                             activebackground=BG, activeforeground=TEXT,
                                             font=FONT_XS, highlightthickness=0, bd=0)
        self.gen_rotate_cb.pack(side="right")
        self._btn(self.gen_toolbar, "regenerate",
                  lambda: self._generate_preview(self.current_mode)).pack(side="left")
        self.text_input_frame = tk.Frame(self.gen_toolbar, bg=BG)
        tk.Label(self.text_input_frame, text="text:", fg=DIM, bg=BG,
                 font=FONT_XS).pack(side="left")
        self.text_entry = tk.Entry(self.text_input_frame, bg=SURF, fg=TEXT, font=FONT,
                                    insertbackground=ACCENT, relief="flat",
                                    highlightthickness=1, highlightbackground=BORDER,
                                    width=25)
        self.text_entry.pack(side="left", padx=(4, 4))
        self.text_entry.insert(0, "ryuo")
        self.text_entry.bind("<Return>", lambda e: self._generate_preview("text"))

        # Main frame
        self.main_frame = tk.Frame(self.root, bg=BG)
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        # Gallery grid (left)
        self.gal_border = tk.Frame(self.main_frame, bg=BORDER)
        self.gal_border.pack(side="left", fill="both", expand=True, padx=(0, 10))
        gal_inner = tk.Frame(self.gal_border, bg=BG2)
        gal_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas = tk.Canvas(gal_inner, bg=BG2, highlightthickness=0)
        sb = tk.Scrollbar(gal_inner, orient="vertical", command=self.canvas.yview,
                          bg=SURF, troughcolor=BG2, highlightthickness=0, bd=0, width=6)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=BG2, padx=6, pady=6)
        self.cw = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self.cw, width=e.width))
        self.canvas.bind("<Button-4>",
            lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>",
            lambda e: self.canvas.yview_scroll(3, "units"))

        # Generator panel (left, hidden)
        self.gen_panel = tk.Frame(self.main_frame, bg=BG2)
        self.gen_preview_label = tk.Label(self.gen_panel, bg="#000")
        self.gen_preview_label.pack(expand=True)

        # Right panel
        right = tk.Frame(self.main_frame, bg=SURF, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # Preview
        pb = tk.Frame(right, bg=BORDER, padx=1, pady=1)
        pb.pack(padx=10, pady=(10, 0))
        self.preview_label = tk.Label(pb, bg="#000", width=PREVIEW_W, height=PREVIEW_H)
        self.preview_label.pack()

        # Info
        inf = tk.Frame(right, bg=SURF, padx=10, pady=4)
        inf.pack(fill="x")
        self.preview_name = tk.Label(inf, text="no selection", fg=DIM,
                                      bg=SURF, font=FONT, anchor="w")
        self.preview_name.pack(fill="x")
        self.preview_info = tk.Label(inf, text="", fg=DIM2, bg=SURF,
                                      font=FONT_XS, anchor="w")
        self.preview_info.pack(fill="x")

        # --- Edit controls (sliders) ---
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)
        edit_frame = tk.Frame(right, bg=SURF, padx=10, pady=2)
        edit_frame.pack(fill="x")
        ehdr = tk.Frame(edit_frame, bg=SURF)
        ehdr.pack(fill="x")
        tk.Label(ehdr, text="edit", fg=DIM2, bg=SURF, font=FONT_XS).pack(side="left")
        rst = tk.Label(ehdr, text="reset", fg=DIM2, bg=SURF, font=FONT_XS, cursor="hand2")
        rst.pack(side="right")
        rst.bind("<Button-1>", lambda e: self._reset_edits())

        self.sliders = {}
        slider_defs = [
            ("brightness", -5, 5, 0),
            ("contrast",   -5, 5, 0),
            ("saturation", -5, 5, 0),
            ("blur",        0, 5, 0),
            ("sharpen",     0, 5, 0),
            ("speed",       1, 8, 4),  # maps to [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]
        ]
        for name, lo, hi, default in slider_defs:
            row = tk.Frame(edit_frame, bg=SURF)
            row.pack(fill="x")
            tk.Label(row, text=name[:6], fg=DIM, bg=SURF, font=FONT_XS,
                     width=6, anchor="w").pack(side="left")
            var = tk.IntVar(value=default)
            s = tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var,
                         bg=SURF, fg=TEXT, troughcolor=BORDER, highlightthickness=0,
                         bd=0, sliderrelief="flat", length=140,
                         sliderlength=14, width=12, font=FONT_XS,
                         activebackground=ACCENT, showvalue=False,
                         command=lambda val, n=name: self._on_slider(n))
            s.pack(side="left", fill="x", expand=True)
            val_lbl = tk.Label(row, text=str(default), fg=TEXT, bg=SURF,
                               font=FONT_XS, width=4)
            val_lbl.pack(side="right")
            self.sliders[name] = (var, val_lbl)

        # --- Filters ---
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)
        ff = tk.Frame(right, bg=SURF, padx=10, pady=2)
        ff.pack(fill="x")
        tk.Label(ff, text="filters", fg=DIM2, bg=SURF, font=FONT_XS).pack(anchor="w")
        chip_frame = tk.Frame(ff, bg=SURF)
        chip_frame.pack(fill="x", pady=(2, 0))
        self.filter_chips = {}
        row_frame = None
        names = list(FILTERS.keys())
        for i, name in enumerate(names):
            if i % 4 == 0:
                row_frame = tk.Frame(chip_frame, bg=SURF)
                row_frame.pack(fill="x", pady=1)
            chip = tk.Label(row_frame, text=name, fg=DIM2, bg=BG2, font=FONT_XS,
                           padx=5, pady=2, cursor="hand2",
                           highlightthickness=1, highlightbackground=BORDER)
            chip.pack(side="left", padx=(0, 3))
            chip.bind("<Button-1>", lambda e, n=name: self._toggle_filter(n))
            self.filter_chips[name] = chip

        # Progress
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)
        pf = tk.Frame(right, bg=SURF, padx=10)
        pf.pack(fill="x")
        self.prog_canvas = tk.Canvas(pf, height=3, bg=BG, highlightthickness=0)
        self.prog_canvas.pack(fill="x", pady=(0, 2))
        self.prog_label = tk.Label(pf, text="", fg=DIM2, bg=SURF,
                                    font=FONT_XS, anchor="w")
        self.prog_label.pack(fill="x")

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)

        # Buttons
        btf = tk.Frame(right, bg=SURF, padx=10)
        btf.pack(fill="x")
        self.apply_btn = self._btn(btf, "apply to display", self._apply, accent=True)
        self.apply_btn.pack(fill="x", pady=(0, 3))
        self.apply_btn.configure(state="disabled")
        self.live_btn = self._btn(btf, "live mode", self._toggle_live)
        self.mockup_btn = self._btn(btf, "preview on pump", self._show_mockup)
        self.mockup_btn.pack(fill="x", pady=(0, 3))
        self.mockup_btn.configure(state="disabled")
        self.delete_btn = self._btn(btf, "delete", self._delete)
        self.delete_btn.pack(fill="x")
        self.delete_btn.configure(state="disabled")

        tk.Frame(right, bg=SURF).pack(fill="both", expand=True)
        self.hint_label = tk.Label(right, text="dbl-click \u2192 apply",
                 fg=DIM2, bg=SURF, font=FONT_XS)
        self.hint_label.pack(pady=(0, 8))

    def _btn(self, parent, text, cmd, accent=False):
        bg = ACCENT if accent else SURF
        fg = "#fff" if accent else DIM
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         activebackground=ACCENT2 if accent else BORDER,
                         activeforeground="#fff", relief="flat",
                         padx=12, pady=3, font=FONT, cursor="hand2",
                         highlightthickness=1,
                         highlightbackground=ACCENT if accent else BORDER)

    # --- Edit controls ---

    _SPEED_STEPS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]

    def _on_slider(self, name):
        var, val_lbl = self.sliders[name]
        v = var.get()
        if name == "speed":
            idx = max(0, min(len(self._SPEED_STEPS) - 1, v - 1))
            self.speed_mult = self._SPEED_STEPS[idx]
            val_lbl.configure(text=f"{self.speed_mult}x")
        else:
            setattr(self, name, v)
            val_lbl.configure(text=str(v))
        self._refresh_preview()

    def _reset_edits(self):
        self.brightness = 0
        self.contrast = 0
        self.saturation = 0
        self.blur = 0
        self.sharpen = 0
        self.speed_mult = 1.0
        self.active_filter = None
        defaults = {"brightness": 0, "contrast": 0, "saturation": 0,
                     "blur": 0, "sharpen": 0, "speed": 4}
        for name, (var, val_lbl) in self.sliders.items():
            var.set(defaults.get(name, 0))
            if name == "speed":
                val_lbl.configure(text="1.0x")
            else:
                val_lbl.configure(text="0")
        for chip in self.filter_chips.values():
            chip.configure(fg=DIM2, bg=BG2, highlightbackground=BORDER)
        self._refresh_preview()

    def _toggle_filter(self, name):
        if self.active_filter == name:
            self.active_filter = None
            self.filter_chips[name].configure(fg=DIM2, bg=BG2,
                                               highlightbackground=BORDER)
        else:
            if self.active_filter and self.active_filter in self.filter_chips:
                self.filter_chips[self.active_filter].configure(
                    fg=DIM2, bg=BG2, highlightbackground=BORDER)
            self.active_filter = name
            self.filter_chips[name].configure(fg=ACCENT, bg=BG,
                                               highlightbackground=ACCENT)
        self._refresh_preview()

    def _refresh_preview(self):
        """Re-render preview with current edits/filter."""
        if self.current_mode == "collection" and self.selected:
            self._update_collection_preview()
        elif self.current_mode != "collection" and self.generated_gif_data:
            self._update_gen_preview()

    def _update_collection_preview(self):
        if not self.selected:
            return
        try:
            img = Image.open(self.selected)
            has_frames = hasattr(img, "n_frames") and img.n_frames > 1
            has_edits = self.brightness != 0 or self.contrast != 0 or self.saturation != 0 or self.blur > 0 or self.sharpen > 0 or self.active_filter

            if has_frames:
                durs = get_frame_durations(img)
                durs = apply_edits_to_durations(durs, self.speed_mult)
                self.anim_frames = []
                self.anim_durations = durs
                for i in range(min(img.n_frames, 100)):
                    img.seek(i)
                    fr = img.copy().convert("RGBA")
                    fr = fr.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                    if has_edits:
                        fr = apply_edits(fr, self.brightness, self.contrast,
                                         self.saturation, self.blur, self.sharpen,
                                         active_filter=self.active_filter)
                    self.anim_frames.append(ImageTk.PhotoImage(fr))
                if self.anim_after:
                    self.root.after_cancel(self.anim_after)
                self.anim_idx = 0
                self._animate()
            else:
                fr = img.copy().convert("RGB")
                fr = fr.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                if has_edits:
                    fr = apply_edits(fr, self.brightness, self.contrast,
                                     self.saturation, self.blur, self.sharpen,
                                     active_filter=self.active_filter)
                photo = ImageTk.PhotoImage(fr)
                self.thumbs["_preview"] = photo
                self.preview_label.configure(image=photo)
        except Exception:
            pass

    def _update_gen_preview(self):
        """Re-render generated preview with edits/filter."""
        if not self.generated_gif_data:
            return
        has_edits = self.brightness != 0 or self.contrast != 0 or self.saturation != 0 or self.blur > 0 or self.sharpen > 0 or self.active_filter
        try:
            img = Image.open(io.BytesIO(self.generated_gif_data))
            has_frames = hasattr(img, "n_frames") and img.n_frames > 1
            if has_frames:
                durs = get_frame_durations(img)
                durs = apply_edits_to_durations(durs, self.speed_mult)
                self.anim_frames = []
                self.anim_durations = durs
                for i in range(img.n_frames):
                    img.seek(i)
                    fr = img.copy().convert("RGBA")
                    fr = fr.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                    if has_edits:
                        fr = apply_edits(fr, self.brightness, self.contrast,
                                         self.saturation, self.blur, self.sharpen,
                                         active_filter=self.active_filter)
                    self.anim_frames.append(ImageTk.PhotoImage(fr))
                if self.anim_after:
                    self.root.after_cancel(self.anim_after)
                self.anim_idx = 0
                self._animate()
            else:
                fr = img.copy().convert("RGB")
                fr = fr.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                if has_edits:
                    fr = apply_edits(fr, self.brightness, self.contrast,
                                     self.saturation, self.blur, self.sharpen,
                                     active_filter=self.active_filter)
                photo = ImageTk.PhotoImage(fr)
                self.thumbs["_preview"] = photo
                self.preview_label.configure(image=photo)
        except Exception:
            pass

    # --- Mode switching ---

    def _switch_mode(self, mode):
        if mode == self.current_mode:
            return
        self._stop_live()
        self.current_mode = mode
        for m, b in self.tab_btns.items():
            b.configure(fg=ACCENT if m == mode else DIM2,
                        bg=SURF if m == mode else BG)
        if mode == "collection":
            self.gen_panel.pack_forget()
            self.gen_toolbar.pack_forget()
            self.text_input_frame.pack_forget()
            self.toolbar.pack(fill="x", after=self.tab_frame)
            self.gal_border.pack(side="left", fill="both", expand=True, padx=(0, 10))
            self.delete_btn.pack(fill="x")
            self.live_btn.pack_forget()
            self.hint_label.configure(text="dbl-click \u2192 apply")
            self._load_gallery()
        else:
            self.gal_border.pack_forget()
            self.toolbar.pack_forget()
            self.delete_btn.pack_forget()
            self.hint_label.configure(text="")
            self.gen_toolbar.pack(fill="x", after=self.tab_frame)
            self.gen_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))
            if mode == "text":
                self.text_input_frame.pack(side="left", padx=(0, 10))
            else:
                self.text_input_frame.pack_forget()
            if mode in LIVE_MODES:
                self.live_btn.pack(fill="x", pady=(0, 3), before=self.mockup_btn)
            else:
                self.live_btn.pack_forget()
            self.apply_btn.configure(state="normal")
            self.mockup_btn.configure(state="disabled")
            self._generate_preview(mode)

    def _generate_preview(self, mode):
        self.generated_gif_data = None
        self.preview_name.configure(text=mode, fg=TEXT)
        self.preview_info.configure(text="generating...")
        if self.anim_after:
            self.root.after_cancel(self.anim_after)
            self.anim_after = None

        def progress_cb(stage, cur, total):
            self.root.after(0, self._update_progress, stage, cur, total)

        text_val = self.text_entry.get() if mode == "text" else None

        def go():
            try:
                if mode == "stats":
                    data = generate_stats_gif(progress_cb=progress_cb)
                    desc = "system stats  \u2022  8 frames"
                elif mode == "fastfetch":
                    data = generate_fastfetch_image(progress_cb=progress_cb)
                    desc = "fastfetch  \u2022  static"
                elif mode == "clock":
                    data = generate_clock_gif(progress_cb=progress_cb)
                    desc = "clock  \u2022  12 frames  \u2022  60s"
                elif mode == "text":
                    data = generate_text_image(text_val or "ryuo",
                                              progress_cb=progress_cb)
                    desc = "custom text  \u2022  static"
                elif mode == "matrix":
                    data = generate_matrix_gif(progress_cb=progress_cb)
                    desc = "matrix  \u2022  40 frames  \u2022  3.2s"
                else:
                    return
                if data:
                    self.generated_gif_data = data
                    self.root.after(0, self._show_gen_result, data, desc)
            except Exception as e:
                self.root.after(0, lambda: self.preview_info.configure(
                    text=f"error: {e}"))

        threading.Thread(target=go, daemon=True).start()

    def _show_gen_result(self, gif_data, desc):
        self.preview_info.configure(text=f"{desc}  \u2022  {len(gif_data)//1024}kb")
        self.apply_btn.configure(state="normal")
        try:
            img = Image.open(io.BytesIO(gif_data))
            self.source_img = img
            has_frames = hasattr(img, "n_frames") and img.n_frames > 1
            has_edits = self.brightness != 0 or self.contrast != 0 or self.saturation != 0 or self.blur > 0 or self.sharpen > 0 or self.active_filter
            if has_frames:
                durs = get_frame_durations(img)
                durs = apply_edits_to_durations(durs, self.speed_mult)
                self.anim_frames = []
                self.anim_durations = durs
                for i in range(img.n_frames):
                    img.seek(i)
                    fr = img.copy().convert("RGBA").resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                    if has_edits:
                        fr = apply_edits(fr, self.brightness, self.contrast,
                                         self.saturation, self.blur, self.sharpen,
                                         active_filter=self.active_filter)
                    self.anim_frames.append(ImageTk.PhotoImage(fr))
                self.anim_idx = 0
                self._animate()
            else:
                self.anim_frames = []
                fr = img.copy().convert("RGB").resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                if has_edits:
                    fr = apply_edits(fr, self.brightness, self.contrast,
                                     self.saturation, self.blur, self.sharpen,
                                     active_filter=self.active_filter)
                photo = ImageTk.PhotoImage(fr)
                self.thumbs["_preview"] = photo
                self.preview_label.configure(image=photo)
            # Gen panel preview
            gf = Image.open(io.BytesIO(gif_data)).convert("RGB")
            gf = gf.resize((320, 256), Image.NEAREST)
            gp = ImageTk.PhotoImage(gf)
            self.thumbs["_gen"] = gp
            self.gen_preview_label.configure(image=gp)
            self.mockup_btn.configure(state="normal")
        except Exception as e:
            self.preview_info.configure(text=f"preview error: {e}")

    # --- Live mode ---

    def _toggle_live(self):
        if self.live_running:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        if not self.device:
            messagebox.showerror("offline", "device not connected")
            return
        self.live_running = True
        self.live_btn.configure(text="stop live", bg=RED, highlightbackground=RED)

        def live_loop():
            while self.live_running:
                try:
                    def progress_cb(stage, cur, total):
                        self.root.after(0, self._update_progress, stage, cur, total)
                    data = generate_stats_gif(progress_cb=progress_cb)
                    if data and self.live_running:
                        self.generated_gif_data = data
                        self.root.after(0, self._show_gen_result, data, "stats (live)")
                        # Apply edits+filter+rotate for upload
                        upload = process_for_upload(
                            data, self.brightness, self.contrast,
                            self.saturation, self.blur, self.sharpen,
                            self.speed_mult, self.active_filter,
                            self.rotate_var.get(), progress_cb)
                        self.device.upload_gif_data(upload, progress_cb=progress_cb)
                except Exception as e:
                    self.root.after(0, lambda: self.prog_label.configure(
                        text=f"live error: {e}"))
                    break
                for _ in range(50):
                    if not self.live_running:
                        break
                    time.sleep(0.1)
            self.root.after(0, lambda: self.live_btn.configure(
                text="live mode", bg=SURF, highlightbackground=BORDER))

        threading.Thread(target=live_loop, daemon=True).start()

    def _stop_live(self):
        self.live_running = False
        self.live_btn.configure(text="live mode", bg=SURF, highlightbackground=BORDER)

    # --- Connection ---

    def _connect_device(self):
        def go():
            try:
                self.device = RyuoDevice()
                self.root.after(0, lambda: (
                    self.status_label.configure(text=self.device.path, fg=DIM),
                    self.status_dot.configure(fg=GREEN)))
            except (Exception, SystemExit):
                self.root.after(0, lambda: (
                    self.status_label.configure(text="offline", fg=DIM2),
                    self.status_dot.configure(fg=RED)))
        threading.Thread(target=go, daemon=True).start()

    # --- Gallery ---

    def _load_gallery(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.thumbs = {k: v for k, v in self.thumbs.items() if k.startswith("_")}
        self.selected_widgets.clear()
        os.makedirs(COLLECTION_DIR, exist_ok=True)
        files = sorted(f for f in os.listdir(COLLECTION_DIR)
                       if f.lower().endswith((".gif",".png",".jpg",".jpeg",".bmp",".webp")))
        if not files:
            tk.Label(self.grid_frame, text="collection/ is empty\n\nclick + add",
                     fg=DIM2, bg=BG2, font=FONT_LG, justify="center"
                     ).grid(row=0, column=0, columnspan=GRID_COLS, pady=80)
            return
        for i, fname in enumerate(files):
            path = os.path.join(COLLECTION_DIR, fname)
            row, col = divmod(i, GRID_COLS)
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                cv = Image.new("RGB", THUMB_SIZE, (8, 8, 12))
                cv.paste(img, ((THUMB_SIZE[0]-img.size[0])//2,
                               (THUMB_SIZE[1]-img.size[1])//2))
                photo = ImageTk.PhotoImage(cv)
                self.thumbs[path] = photo
            except Exception:
                continue
            cell = tk.Frame(self.grid_frame, bg=BG2, padx=3, pady=3)
            cell.grid(row=row, column=col, sticky="nsew")
            border = tk.Frame(cell, bg=BORDER, padx=1, pady=1)
            border.pack()
            lbl = tk.Label(border, image=photo, bg="#000", cursor="hand2")
            lbl.pack()
            lbl.bind("<Button-1>",
                     lambda e, p=path, f=fname, b=border: self._select(p, f, b))
            lbl.bind("<Double-Button-1>", lambda e, p=path: self._apply(path=p))
            tk.Label(cell, text=fname[:22], fg=DIM2, bg=BG2,
                     font=FONT_XS).pack(pady=(1, 0))
            self.selected_widgets[path] = border
        for c in range(GRID_COLS):
            self.grid_frame.columnconfigure(c, weight=1)

    def _select(self, path, fname, border_widget):
        for bw in self.selected_widgets.values():
            bw.configure(bg=BORDER)
        border_widget.configure(bg=ACCENT)
        self.selected = path
        self.apply_btn.configure(state="normal")
        self.mockup_btn.configure(state="normal")
        self.delete_btn.configure(state="normal")
        self.preview_name.configure(text=fname, fg=TEXT)
        if self.anim_after:
            self.root.after_cancel(self.anim_after)
            self.anim_after = None
        try:
            self.source_img = Image.open(path)
            has_frames = hasattr(self.source_img, "n_frames") and self.source_img.n_frames > 1
            fsize = os.path.getsize(path)
            info = f"{self.source_img.size[0]}x{self.source_img.size[1]}"
            if has_frames:
                info += f"  {self.source_img.n_frames}f"
                durs = get_frame_durations(self.source_img)
                avg = sum(durs) / len(durs)
                info += f"  {1000/avg:.0f}fps" if avg > 0 else ""
            info += f"  {fsize//1024}kb"
            self.preview_info.configure(text=info)
            self._update_collection_preview()
        except Exception as e:
            self.preview_info.configure(text=f"error: {e}")

    def _animate(self):
        if not self.anim_frames:
            return
        f = self.anim_frames[self.anim_idx]
        self.preview_label.configure(image=f)
        self.thumbs["_anim"] = f
        d = self.anim_durations[self.anim_idx % len(self.anim_durations)]
        self.anim_idx = (self.anim_idx + 1) % len(self.anim_frames)
        self.anim_after = self.root.after(d, self._animate)

    def _show_mockup(self):
        if self.current_mode == "collection" and self.selected:
            img = Image.open(self.selected)
        elif self.generated_gif_data:
            img = Image.open(io.BytesIO(self.generated_gif_data))
        else:
            return
        self.mockup_win = MockupWindow(self.root, img)

    # --- Progress ---

    def _update_progress(self, stage, cur, total):
        w = self.prog_canvas.winfo_width()
        self.prog_canvas.delete("all")
        tbl = {"cached": (0, "cached"), "encoding": (0.95, "encoding..."),
               "preparing": (0, "preparing..."), "done": (1.0, "done!")}
        if stage in tbl:
            pct, txt = tbl[stage]
        elif stage in ("resizing", "generating", "rendering", "processing"):
            pct = cur / max(total, 1)
            txt = f"{stage} {cur}/{total}"
        elif stage == "uploading":
            pct = cur / max(total, 1)
            txt = f"chunk {cur}/{total}"
        else:
            pct, txt = 0, stage
        bw = int(w * pct)
        if bw > 0:
            self.prog_canvas.create_rectangle(0, 0, bw, 3, fill=ACCENT, outline="")
        self.prog_label.configure(text=txt, fg=GREEN if stage == "done" else DIM2)

    # --- Apply ---

    def _apply(self, path=None):
        if self.uploading:
            return
        if not self.device:
            messagebox.showerror("offline", "device not connected")
            return
        rotate = self.rotate_var.get()
        self.uploading = True
        self.apply_btn.configure(state="disabled", text="uploading...")

        def progress_cb(stage, cur, total):
            self.root.after(0, self._update_progress, stage, cur, total)

        has_edits = self.brightness != 0 or self.contrast != 0 or self.saturation != 0 or self.blur > 0 or self.sharpen > 0 or self.active_filter or self.speed_mult != 1.0

        def go():
            try:
                if self.current_mode == "collection":
                    target = path or self.selected
                    if not target:
                        return
                    if has_edits:
                        data = process_for_upload(
                            target, self.brightness, self.contrast,
                            self.saturation, self.blur, self.sharpen,
                            self.speed_mult, self.active_filter,
                            rotate, progress_cb)
                        self.device.upload_gif_data(data, progress_cb=progress_cb)
                    else:
                        self.device.upload_image(target, rotate=rotate,
                                                  progress_cb=progress_cb)
                else:
                    if self.generated_gif_data:
                        if has_edits or rotate:
                            data = process_for_upload(
                                self.generated_gif_data, self.brightness, self.contrast,
                                self.saturation, self.blur, self.sharpen,
                                self.speed_mult, self.active_filter,
                                rotate, progress_cb)
                            self.device.upload_gif_data(data, progress_cb=progress_cb)
                        else:
                            # No edits, no rotation — upload raw generated data
                            self.device.upload_gif_data(self.generated_gif_data,
                                                         progress_cb=progress_cb)

                self.root.after(0, lambda: self.apply_btn.configure(
                    state="normal", text="apply to display"))
                self.root.after(3000, lambda: self.prog_label.configure(text=""))
            except Exception as e:
                self.root.after(0, lambda: (
                    self.apply_btn.configure(state="normal", text="apply to display"),
                    self._update_progress("error: " + str(e), 0, 0)))
            finally:
                self.uploading = False

        threading.Thread(target=go, daemon=True).start()

    def _delete(self):
        if not self.selected:
            return
        if not messagebox.askyesno("delete", f"delete {os.path.basename(self.selected)}?"):
            return
        try: os.unlink(self.selected)
        except OSError: pass
        self.selected = None
        self.source_img = None
        self.apply_btn.configure(state="disabled")
        self.mockup_btn.configure(state="disabled")
        self.delete_btn.configure(state="disabled")
        self.preview_name.configure(text="no selection", fg=DIM)
        self.preview_info.configure(text="")
        self.preview_label.configure(image="")
        if self.anim_after:
            self.root.after_cancel(self.anim_after)
            self.anim_after = None
        self._load_gallery()

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="select images/gifs",
            filetypes=[("Images", "*.gif *.png *.jpg *.jpeg *.bmp *.webp"),
                       ("All", "*.*")])
        if not paths:
            return
        os.makedirs(COLLECTION_DIR, exist_ok=True)
        for p in paths:
            dest = os.path.join(COLLECTION_DIR, os.path.basename(p))
            if os.path.abspath(p) != os.path.abspath(dest):
                shutil.copy2(p, dest)
        self._load_gallery()

    def _open_folder(self):
        os.makedirs(COLLECTION_DIR, exist_ok=True)
        try: subprocess.Popen(["xdg-open", COLLECTION_DIR])
        except Exception: pass


def main():
    parser = argparse.ArgumentParser(description="ryuo oled gallery")
    parser.add_argument("-r", "--rotate", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    GalleryApp(root, rotate=args.rotate)
    root.mainloop()


if __name__ == "__main__":
    main()

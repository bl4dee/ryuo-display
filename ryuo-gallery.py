#!/usr/bin/env python3
"""
Ryuo OLED Gallery — local GIF browser and uploader.

Usage:
  nix-shell -p python3Packages.tkinter python3Packages.pillow \
    --run "python3 ryuo-gallery.py -r"
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk, ImageDraw

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
FONT    = ("Monospace", 9)
FONT_XS = ("Monospace", 8)
FONT_LG = ("Monospace", 13)
FONT_TT = ("Monospace", 16, "bold")

PREVIEW_W, PREVIEW_H = 272, 218

# Minimum frame duration — GIFs with 0 or tiny durations play way too fast.
# Browsers use 100ms for duration=0. We use 33ms (30fps cap) as minimum.
MIN_FRAME_MS = 33


def get_frame_durations(img):
    """Extract per-frame durations from a GIF, handling all the weird edge cases."""
    durations = []
    for i in range(img.n_frames):
        img.seek(i)
        d = img.info.get("duration", 100)
        # duration=0 means "no delay" in GIF spec but browsers treat it as ~100ms
        if d <= 0:
            d = 100
        # Cap minimum to prevent insanely fast playback
        d = max(d, MIN_FRAME_MS)
        durations.append(d)
    img.seek(0)
    return durations


def build_pump_mockup(content_img, size=380):
    """Render image inside the Ryuo I 240 circular pump head.

    The pump face is a circle. The OLED fills most of it.
    Thin glossy black NCVM bezel ring around the edge.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    r = size // 2 - 4

    # 1) Outer ring — glossy black NCVM bezel
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(12, 12, 16))
    # Subtle sheen on the bezel
    draw.ellipse([cx-r+1, cy-r+1, cx+r-1, cy-r + int(r*0.3)],
                 fill=(20, 20, 26))

    # 2) Inner dark face — the OLED sits here
    inner_r = int(r * 0.88)
    draw.ellipse([cx-inner_r, cy-inner_r, cx+inner_r, cy+inner_r],
                 fill=(2, 2, 3))

    # 3) OLED screen — 5:4 aspect, fills most of the circle
    # The screen is as wide as it can be within the circle
    sw = int(inner_r * 1.65)  # screen width — nearly fills the inner circle
    sh = int(sw * 128 / 160)  # 5:4 aspect

    sx = cx - sw // 2
    sy = cy - sh // 2

    # Screen content
    content = content_img.copy().convert("RGBA")
    content = content.resize((sw, sh), Image.LANCZOS)
    img.paste(content, (sx, sy), content)

    # 4) Circular mask — clip to pump head circle
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([cx-r, cy-r, cx+r, cy+r], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


class MockupWindow:
    """Compact popup showing the image on the pump head mockup."""

    def __init__(self, root, source_img):
        self.root = root
        self.source_img = source_img
        self.win = tk.Toplevel(root)
        self.win.title("pump preview")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.overrideredirect(False)
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

        # Position: above the main window, right-aligned
        root.update_idletasks()
        rx = root.winfo_x()
        ry = root.winfo_y()
        rw = root.winfo_width()
        popup_w = size + 20
        popup_h = size + 50
        # Place above the main window, aligned to the right side
        px = rx + rw - popup_w - 10
        py = ry - popup_h - 10
        # If it would go off the top of screen, place below instead
        if py < 0:
            py = ry + 40
            px = rx + rw + 10  # to the right instead
        self.win.geometry(f"+{px}+{py}")

        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # Build frames
        has_frames = hasattr(source_img, "n_frames") and source_img.n_frames > 1

        if has_frames:
            durations = get_frame_durations(source_img)
            self.anim_frames = []
            for i in range(min(source_img.n_frames, 80)):
                source_img.seek(i)
                frame = source_img.copy().convert("RGBA")
                mockup = build_pump_mockup(frame, size)
                self.anim_frames.append(ImageTk.PhotoImage(mockup))
            self.durations = durations
            self.anim_idx = 0
            self._animate()
        else:
            frame = source_img.copy().convert("RGBA")
            mockup = build_pump_mockup(frame, size)
            photo = ImageTk.PhotoImage(mockup)
            self.thumbs["static"] = photo
            self.label.configure(image=photo)

    def _animate(self):
        if not self.anim_frames or not self.win.winfo_exists():
            return
        f = self.anim_frames[self.anim_idx]
        self.label.configure(image=f)
        self.thumbs["cur"] = f
        dur = self.durations[self.anim_idx % len(self.durations)]
        self.anim_idx = (self.anim_idx + 1) % len(self.anim_frames)
        self.anim_after = self.win.after(dur, self._animate)

    def _close(self):
        if self.anim_after:
            self.win.after_cancel(self.anim_after)
        self.win.destroy()


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

        root.title("ryuo")
        root.configure(bg=BG)
        root.geometry("1060x680")
        root.minsize(700, 450)

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

        # Toolbar
        bar = tk.Frame(self.root, bg=BG, padx=20, pady=8)
        bar.pack(fill="x")
        self._btn(bar, "+ add", self._add_files).pack(side="left", padx=(0, 4))
        self._btn(bar, "folder", self._open_folder).pack(side="left", padx=(0, 4))
        self._btn(bar, "refresh", self._load_gallery).pack(side="left")

        self.rotate_var = tk.BooleanVar(value=self.rotate)
        tk.Checkbutton(bar, text="rotate 180\u00b0", variable=self.rotate_var,
                       bg=BG, fg=DIM, selectcolor=SURF, activebackground=BG,
                       activeforeground=TEXT, font=FONT_XS,
                       highlightthickness=0, bd=0).pack(side="right")

        # Main
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        # Gallery (left)
        gal_border = tk.Frame(main, bg=BORDER)
        gal_border.pack(side="left", fill="both", expand=True, padx=(0, 10))

        gal_inner = tk.Frame(gal_border, bg=BG2)
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
        self.canvas.bind_all("<Button-4>",
            lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>",
            lambda e: self.canvas.yview_scroll(3, "units"))

        # Right panel
        right = tk.Frame(main, bg=SURF, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # Preview
        pb = tk.Frame(right, bg=BORDER, padx=1, pady=1)
        pb.pack(padx=10, pady=(10, 0))
        self.preview_label = tk.Label(pb, bg="#000", width=PREVIEW_W, height=PREVIEW_H)
        self.preview_label.pack()

        # Info
        inf = tk.Frame(right, bg=SURF, padx=10, pady=6)
        inf.pack(fill="x")
        self.preview_name = tk.Label(inf, text="no selection", fg=DIM,
                                      bg=SURF, font=FONT, anchor="w")
        self.preview_name.pack(fill="x")
        self.preview_info = tk.Label(inf, text="", fg=DIM2, bg=SURF,
                                      font=FONT_XS, anchor="w")
        self.preview_info.pack(fill="x")

        # Progress
        pf = tk.Frame(right, bg=SURF, padx=10)
        pf.pack(fill="x")
        self.prog_canvas = tk.Canvas(pf, height=3, bg=BG, highlightthickness=0)
        self.prog_canvas.pack(fill="x", pady=(0, 2))
        self.prog_label = tk.Label(pf, text="", fg=DIM2, bg=SURF,
                                    font=FONT_XS, anchor="w")
        self.prog_label.pack(fill="x")

        # Separator
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", padx=10, pady=6)

        # Buttons
        bf = tk.Frame(right, bg=SURF, padx=10)
        bf.pack(fill="x")

        self.apply_btn = self._btn(bf, "apply to display", self._apply, accent=True)
        self.apply_btn.pack(fill="x", pady=(0, 4))
        self.apply_btn.configure(state="disabled")

        self.mockup_btn = self._btn(bf, "preview on pump", self._show_mockup)
        self.mockup_btn.pack(fill="x", pady=(0, 4))
        self.mockup_btn.configure(state="disabled")

        self.delete_btn = self._btn(bf, "delete", self._delete)
        self.delete_btn.pack(fill="x")
        self.delete_btn.configure(state="disabled")

        # Bottom
        tk.Frame(right, bg=SURF).pack(fill="both", expand=True)
        tk.Label(right, text="dbl-click \u2192 apply",
                 fg=DIM2, bg=SURF, font=FONT_XS).pack(pady=(0, 10))

    def _btn(self, parent, text, cmd, accent=False):
        bg = ACCENT if accent else SURF
        fg = "#fff" if accent else DIM
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg,
                         activebackground=ACCENT2 if accent else BORDER,
                         activeforeground="#fff", relief="flat",
                         padx=12, pady=4, font=FONT, cursor="hand2",
                         highlightthickness=1,
                         highlightbackground=ACCENT if accent else BORDER)

    def _connect_device(self):
        def go():
            try:
                self.device = RyuoDevice()
                self.root.after(0, lambda: (
                    self.status_label.configure(text=self.device.path, fg=DIM),
                    self.status_dot.configure(fg=GREEN)))
            except Exception:
                self.root.after(0, lambda: (
                    self.status_label.configure(text="offline", fg=DIM2),
                    self.status_dot.configure(fg=RED)))
        threading.Thread(target=go, daemon=True).start()

    def _load_gallery(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.thumbs.clear()
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
                durations = get_frame_durations(self.source_img)
                avg_dur = sum(durations) / len(durations)
                fps = 1000 / avg_dur if avg_dur > 0 else 0
                info += f"  {fps:.0f}fps"
            info += f"  {fsize//1024}kb"
            self.preview_info.configure(text=info)

            if has_frames:
                durations = get_frame_durations(self.source_img)
                self.anim_frames = []
                self.anim_durations = durations
                for i in range(min(self.source_img.n_frames, 100)):
                    self.source_img.seek(i)
                    frame = self.source_img.copy().convert("RGBA")
                    frame = frame.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                    self.anim_frames.append(ImageTk.PhotoImage(frame))
                self.anim_idx = 0
                self._animate()
            else:
                self.anim_frames = []
                self.anim_durations = []
                frame = self.source_img.copy().convert("RGB")
                frame = frame.resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
                photo = ImageTk.PhotoImage(frame)
                self.thumbs["_preview"] = photo
                self.preview_label.configure(image=photo)
        except Exception as e:
            self.preview_info.configure(text=f"error: {e}")

    def _animate(self):
        if not self.anim_frames:
            return
        f = self.anim_frames[self.anim_idx]
        self.preview_label.configure(image=f)
        self.thumbs["_anim"] = f
        dur = self.anim_durations[self.anim_idx % len(self.anim_durations)]
        self.anim_idx = (self.anim_idx + 1) % len(self.anim_frames)
        self.anim_after = self.root.after(dur, self._animate)

    def _show_mockup(self):
        if not self.source_img:
            return
        # Re-open the image fresh so seeking works
        img = Image.open(self.selected)
        self.mockup_win = MockupWindow(self.root, img)

    def _update_progress(self, stage, cur, total):
        w = self.prog_canvas.winfo_width()
        self.prog_canvas.delete("all")
        tbl = {"cached": (0, "cached - skip resize"),
               "encoding": (0.95, "encoding..."),
               "preparing": (0, "preparing..."),
               "done": (1.0, "done!")}
        if stage in tbl:
            pct, txt = tbl[stage]
        elif stage == "resizing":
            pct, txt = cur / max(total, 1), f"resize {cur}/{total}"
        elif stage == "uploading":
            pct, txt = cur / max(total, 1), f"chunk {cur}/{total}"
        else:
            pct, txt = 0, stage
        bw = int(w * pct)
        if bw > 0:
            self.prog_canvas.create_rectangle(0, 0, bw, 3, fill=ACCENT, outline="")
        self.prog_label.configure(text=txt, fg=GREEN if stage == "done" else DIM2)

    def _apply(self, path=None):
        path = path or self.selected
        if not path or self.uploading:
            return
        if not self.device:
            messagebox.showerror("offline", "device not connected")
            return

        rotate = self.rotate_var.get()
        self.uploading = True
        self.apply_btn.configure(state="disabled", text="uploading...")
        self.prog_label.configure(text="starting...", fg=DIM2)

        def progress_cb(stage, cur, total):
            self.root.after(0, self._update_progress, stage, cur, total)

        def go():
            try:
                self.device.upload_image(path, rotate=rotate,
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
        name = os.path.basename(self.selected)
        if not messagebox.askyesno("delete", f"delete {name}?"):
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
        self.prog_label.configure(text="")
        self.prog_canvas.delete("all")
        if self.anim_after:
            self.root.after_cancel(self.anim_after)
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

# ryuo-display

Linux tools for the ASUS ROG Ryuo I 240 AIO cooler OLED (160x128).

## Tools

- `ryuo-oled.py` — CLI for uploading images/GIFs, fan control, LED, sensors
- `ryuo-gallery.py` — GUI app with collection browser, generators, filters, live mode

## Quick start

```
# NixOS
nix-shell -p python3Packages.tkinter python3Packages.pillow python3Packages.psutil

# permissions
sudo cp 99-ryuo.rules /etc/udev/rules.d/ && sudo udevadm control --reload

# CLI
python3 ryuo-oled.py status
python3 ryuo-oled.py image cat.gif -r
python3 ryuo-oled.py image cat.gif --filter neon --brightness 2

# GUI
python3 ryuo-gallery.py -r
```

## CLI options

```
status              device info + temps
image <file>        upload to OLED
fan <0-100>         set fan duty
led <R> <G> <B>     set LED color
color <R> <G> <B>   solid color on OLED
temps               CPU/GPU/coolant temps
monitor             live sensor view
anim stop|start     pause/resume OLED
```

Image flags: `--brightness`, `--contrast`, `--saturation`, `--blur`, `--sharpen`, `--speed`, `--filter`

Filters: noir, neon, vhs, thermal, matrix, vapor, pixel, cinema, sketch, chrome, glitch, glow

## Gallery features

- Browse/manage a local GIF collection
- Generators: system stats, fastfetch, clock, custom text, matrix rain
- Edit sliders: brightness, contrast, saturation, blur, sharpen, speed
- 12 filters
- Live mode (auto-refresh stats to display)
- Pump head mockup preview

## Also

- `99-ryuo.rules` — udev rules
- `ryuo-oled.service` — systemd service
- `ryuo-daemon.py` — REST API daemon
- Submitted a [liquidctl PR](https://github.com/liquidctl/liquidctl/pull/880) to upstream this

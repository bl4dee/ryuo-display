#!/usr/bin/env python3
"""Generate a more distinctive test GIF for the Ryuo OLED."""
from PIL import Image, ImageDraw
import os

# Bright gradient with text
img = Image.new('RGB', (160, 128), (0, 0, 0))
draw = ImageDraw.Draw(img)

# Gradient background
for y in range(128):
    r = int(255 * y / 128)
    g = int(255 * (128 - y) / 128)
    b = 128
    draw.line([(0, y), (159, y)], fill=(r, g, b))

# White box with text
draw.rectangle([20, 35, 140, 93], fill=(0, 0, 0), outline='white', width=2)
draw.text((35, 42), 'ROG RYUO', fill='cyan')
draw.text((45, 58), 'LINUX', fill=(255, 100, 0))
draw.text((30, 74), 'IT WORKS!', fill=(0, 255, 0))

path = '/home/blink/test_oled2.gif'
img.save(path, format='GIF')
print(f'Created {path}: {os.path.getsize(path)} bytes')

#!/usr/bin/env python3
from PIL import Image, ImageDraw
import os

img = Image.new('RGB', (160, 128), (0, 255, 255))
draw = ImageDraw.Draw(img)
draw.rectangle([10, 10, 150, 118], outline='red', width=3)
draw.rectangle([30, 30, 130, 98], fill='blue')
draw.text((45, 50), 'RYUO', fill='white')
draw.text((40, 70), 'LINUX!', fill='yellow')
img.save('/home/blink/test_oled.gif', format='GIF')
path = '/home/blink/test_oled.gif'
print(f'Created {path}: {os.path.getsize(path)} bytes')

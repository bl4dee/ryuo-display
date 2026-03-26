#!/usr/bin/env python3
"""Full end-to-end test of the Ryuo OLED upload protocol."""
import os
import sys
import time

sys.path.insert(0, '/home/blink')

# Import classes from ryuo-oled.py
exec(open('/home/blink/ryuo-oled.py').read().split('\ndef main')[0])

def hex_str(data, start=2, end=14):
    return " ".join(f"{b:02x}" for b in data[start:end])

dev = RyuoDevice()

print("=== PRE-UPLOAD STATE ===")
disp = dev.get_display_info()
print(f"  File: {disp.get('file', 'N/A')}")
print(f"  Anim: {'running' if disp.get('anim_running') else 'stopped'} (0x{disp.get('anim_raw',0):02x})")
r = dev.read_reg(0x5C)
if r:
    print(f"  Reg 0x5C: {hex_str(r)}")

print("\n=== UPLOAD TEST (test_oled2.gif) ===")
ok = dev.upload_image('/home/blink/test_oled2.gif')

print("\n=== POST-UPLOAD STATE ===")
disp = dev.get_display_info()
file_path = disp.get('file', '')
anim_running = disp.get('anim_running', False)
print(f"  File: {file_path}")
print(f"  Anim: {'running' if anim_running else 'stopped'} (0x{disp.get('anim_raw',0):02x})")
r = dev.read_reg(0x5C)
if r:
    print(f"  Reg 0x5C: {hex_str(r)}")

print("\n=== STOP/START CYCLE ===")
dev.anim_stop()
time.sleep(0.3)
disp = dev.get_display_info()
print(f"  After stop:  anim={'running' if disp.get('anim_running') else 'stopped'}")

dev.anim_start()
time.sleep(0.3)
disp = dev.get_display_info()
file_after = disp.get('file', '')
anim_after = disp.get('anim_running', False)
print(f"  After start: anim={'running' if anim_after else 'stopped'}, file={file_after}")

print("\n=== SENSOR CHECK ===")
sensors = dev.get_sensors()
if sensors:
    print(f"  Coolant: {sensors['coolant_temp']}C")
cpu_temp, cpu_src = RyuoDevice.get_cpu_temp()
if cpu_temp:
    print(f"  CPU: {cpu_temp:.1f}C ({cpu_src})")

print("\n" + "=" * 50)
print("VERDICT:")
if 'RYU_GIF' in file_after and anim_after:
    print("  PASS - Upload protocol CONFIRMED WORKING")
    print(f"  Device playing: {file_after}")
    print("  >> Look at your OLED - you should see the gradient test image <<")
elif anim_after and 'Animat' in file_after:
    print("  PARTIAL - Running but reverted to default animation")
    print("  Upload may have written data but device didn't switch to it")
elif not anim_after:
    print("  FAIL - Animation not running after start command")
else:
    print(f"  UNKNOWN - file={file_after}, anim={anim_after}")
print("=" * 50)

dev.close()

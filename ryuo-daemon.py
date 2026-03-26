#!/usr/bin/env python3
"""
ROG Ryuo I 240 OLED Daemon

REST API server that manages the Ryuo OLED display.
Other applications (CoolerControl, OpenRGB, scripts, etc.) can control
the display via HTTP requests.

Usage:
  ryuo-daemon.py                     # Start on default port 5300
  ryuo-daemon.py --port 8080         # Custom port
  ryuo-daemon.py --rotate            # All images rotated 180°

API Endpoints:
  GET  /status                       Device info + display state
  GET  /sensors                      Temperature + sensor data
  POST /image     file=@photo.png    Upload image/GIF to OLED
  POST /color     {"r":255,"g":0,"b":0}  Solid color background
  POST /led       {"r":0,"g":255,"b":0,"mode":1}  Set LED color
  POST /fan       {"duty":50}        Set fan duty (0-100%)
  POST /anim      {"action":"stop"}  Stop/start animation
  GET  /health                       Daemon health check

Example:
  curl localhost:5300/status
  curl -X POST -F file=@cat.gif localhost:5300/image
  curl -X POST -d '{"r":0,"g":255,"b":255}' localhost:5300/color
  curl -X POST -d '{"duty":60}' localhost:5300/fan
"""

import argparse
import io
import json
import os
import sys
import tempfile
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Import the device class from ryuo-oled
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We can't import "ryuo-oled" directly due to the hyphen, so we load it manually
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ryuo_oled",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ryuo-oled.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
RyuoDevice = _mod.RyuoDevice
find_device = _mod.find_device

# Global state
device = None
device_lock = threading.Lock()
rotate_default = False
last_upload_time = 0
COLLECTION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collection")
UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ryuo-browser.html")


def json_response(handler, code, data):
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps(data).encode())


def read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return b""
    return handler.rfile.read(length)


def parse_multipart(handler):
    """Extract uploaded file from multipart/form-data."""
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        return None

    boundary = content_type.split("boundary=")[-1].encode()
    body = read_body(handler)

    parts = body.split(b"--" + boundary)
    for part in parts:
        if b"filename=" in part:
            # Find the file data after the double newline
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            file_data = part[header_end + 4:]
            # Strip trailing \r\n--
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]
            if file_data.endswith(b"--"):
                file_data = file_data[:-2]
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]
            return file_data
    return None


class RyuoHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            json_response(self, 200, {"status": "ok", "device": device.path})
            return

        if path == "/status":
            with device_lock:
                try:
                    info = device.get_device_info()
                    fw = device.get_firmware()
                    display = device.get_display_info()
                    sensors = device.get_sensors()
                    json_response(self, 200, {
                        "device": info,
                        "firmware": fw,
                        "display": display,
                        "sensors": sensors,
                    })
                except Exception as e:
                    json_response(self, 500, {"error": str(e)})
            return

        if path == "/sensors":
            with device_lock:
                try:
                    sensors = device.get_sensors()
                    cpu_temp, cpu_src = RyuoDevice.get_cpu_temp()
                    gpu_temp, gpu_src = RyuoDevice.get_gpu_temp()
                    result = {}
                    if sensors:
                        result["coolant_temp"] = sensors["coolant_temp"]
                        result["sensors_hex"] = sensors["raw_hex"]
                    if cpu_temp is not None:
                        result["cpu"] = {"temp": cpu_temp, "source": cpu_src}
                    if gpu_temp is not None:
                        result["gpu"] = {"temp": gpu_temp, "source": gpu_src}
                    json_response(self, 200, result)
                except Exception as e:
                    json_response(self, 500, {"error": str(e)})
            return

        if path == "/ui":
            try:
                with open(UI_FILE, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                json_response(self, 404, {"error": "ryuo-browser.html not found"})
            return

        if path == "/collection":
            os.makedirs(COLLECTION_DIR, exist_ok=True)
            files = sorted([
                f for f in os.listdir(COLLECTION_DIR)
                if f.lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".bmp"))
            ])
            json_response(self, 200, {"files": files})
            return

        if path.startswith("/collection/"):
            name = path[len("/collection/"):]
            filepath = os.path.join(COLLECTION_DIR, name)
            if not os.path.isfile(filepath) or ".." in name:
                json_response(self, 404, {"error": "not found"})
                return
            self.send_response(200)
            ext = name.rsplit(".", 1)[-1].lower()
            ct = {"gif": "image/gif", "png": "image/png", "jpg": "image/jpeg",
                  "jpeg": "image/jpeg", "bmp": "image/bmp"}.get(ext, "application/octet-stream")
            self.send_header("Content-Type", ct)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
            return

        json_response(self, 404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/collection/"):
            name = path[len("/collection/"):]
            filepath = os.path.join(COLLECTION_DIR, name)
            if not os.path.isfile(filepath) or ".." in name:
                json_response(self, 404, {"error": "not found"})
                return
            os.unlink(filepath)
            json_response(self, 200, {"status": "deleted", "name": name})
            return
        json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        global last_upload_time
        path = urlparse(self.path).path

        if path == "/image":
            content_type = self.headers.get("Content-Type", "")

            if "multipart/form-data" in content_type:
                file_data = parse_multipart(self)
                if not file_data:
                    json_response(self, 400, {"error": "no file in request"})
                    return
            else:
                file_data = read_body(self)
                if not file_data:
                    json_response(self, 400, {"error": "no data"})
                    return

            # Check for rotate query param or use default
            rotate = rotate_default
            if "rotate=true" in self.path or "rotate=1" in self.path:
                rotate = True
            elif "rotate=false" in self.path or "rotate=0" in self.path:
                rotate = False

            # Write to temp file and upload
            try:
                with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as f:
                    f.write(file_data)
                    tmp_path = f.name

                with device_lock:
                    device.upload_image(tmp_path, rotate=rotate)
                    last_upload_time = time.time()

                os.unlink(tmp_path)
                json_response(self, 200, {
                    "status": "uploaded",
                    "size": len(file_data),
                    "rotated": rotate,
                })
            except Exception as e:
                json_response(self, 500, {"error": str(e)})
            return

        if path == "/color":
            try:
                data = json.loads(read_body(self))
                r = int(data.get("r", 0))
                g = int(data.get("g", 0))
                b = int(data.get("b", 0))
            except (json.JSONDecodeError, ValueError) as e:
                json_response(self, 400, {"error": f"invalid json: {e}"})
                return

            try:
                from PIL import Image
                img = Image.new("RGB", (160, 128), (r, g, b))
                buf = io.BytesIO()
                img.save(buf, format="GIF")

                with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as f:
                    f.write(buf.getvalue())
                    tmp_path = f.name

                with device_lock:
                    device.upload_image(tmp_path, rotate=rotate_default)
                    last_upload_time = time.time()

                os.unlink(tmp_path)
                json_response(self, 200, {
                    "status": "color set",
                    "color": {"r": r, "g": g, "b": b},
                })
            except Exception as e:
                json_response(self, 500, {"error": str(e)})
            return

        if path == "/fan":
            try:
                data = json.loads(read_body(self))
                duty = int(data.get("duty", 0))
            except (json.JSONDecodeError, ValueError) as e:
                json_response(self, 400, {"error": f"invalid json: {e}"})
                return

            with device_lock:
                try:
                    device.set_fan(duty)
                    json_response(self, 200, {"status": "fan set", "duty": duty})
                except Exception as e:
                    json_response(self, 500, {"error": str(e)})
            return

        if path == "/led":
            try:
                data = json.loads(read_body(self))
                r = int(data.get("r", 0))
                g = int(data.get("g", 0))
                b = int(data.get("b", 0))
                mode = int(data.get("mode", 1))
            except (json.JSONDecodeError, ValueError) as e:
                json_response(self, 400, {"error": f"invalid json: {e}"})
                return

            with device_lock:
                try:
                    device.set_led(r, g, b, mode)
                    json_response(self, 200, {
                        "status": "led set",
                        "color": {"r": r, "g": g, "b": b},
                        "mode": mode,
                    })
                except Exception as e:
                    json_response(self, 500, {"error": str(e)})
            return

        if path == "/anim":
            try:
                data = json.loads(read_body(self))
                action = data.get("action", "")
            except (json.JSONDecodeError, ValueError) as e:
                json_response(self, 400, {"error": f"invalid json: {e}"})
                return

            if action not in ("stop", "start"):
                json_response(self, 400, {"error": "action must be 'stop' or 'start'"})
                return

            with device_lock:
                try:
                    if action == "stop":
                        device.anim_stop()
                    else:
                        device.anim_start()
                    json_response(self, 200, {"status": f"animation {action}"})
                except Exception as e:
                    json_response(self, 500, {"error": str(e)})
            return

        if path == "/collection":
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" in content_type:
                file_data = parse_multipart(self)
            else:
                file_data = read_body(self)

            if not file_data:
                json_response(self, 400, {"error": "no file"})
                return

            os.makedirs(COLLECTION_DIR, exist_ok=True)
            # Generate unique filename
            import hashlib
            h = hashlib.md5(file_data).hexdigest()[:8]
            name = f"ryuo_{h}.gif"
            filepath = os.path.join(COLLECTION_DIR, name)
            with open(filepath, "wb") as f:
                f.write(file_data)
            json_response(self, 200, {"status": "saved", "name": name})
            return

        json_response(self, 404, {"error": "not found"})


def main():
    global device, rotate_default

    parser = argparse.ArgumentParser(
        description="ROG Ryuo I 240 OLED Daemon — REST API server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""API Endpoints:
  GET  /ui                           GIF browser web interface
  GET  /status                       Device info + display state
  GET  /sensors                      Temperature + sensor data
  GET  /health                       Daemon health check
  GET  /collection                   List saved GIFs
  GET  /collection/<name>            Serve a saved GIF
  POST /image     file=@photo.png    Upload image/GIF to OLED
  POST /color     {"r":255,"g":0,"b":0}  Solid color background
  POST /led       {"r":0,"g":255,"b":0}  Set LED color
  POST /fan       {"duty":50}        Set fan duty (0-100%)
  POST /anim      {"action":"stop"}  Stop/start animation
  POST /collection file=@img.gif     Save GIF to collection
  DEL  /collection/<name>            Delete from collection

Examples:
  Open http://localhost:5300/ui in your browser
  curl localhost:5300/status
  curl -X POST -F file=@cat.gif localhost:5300/image
  curl -X POST -d '{"r":0,"g":255,"b":255}' localhost:5300/color""")

    parser.add_argument("-p", "--port", type=int, default=5300,
                        help="HTTP port (default: 5300)")
    parser.add_argument("-d", "--device", help="hidraw device path (auto-detected)")
    parser.add_argument("-r", "--rotate", action="store_true",
                        help="Rotate all images 180° by default")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")

    args = parser.parse_args()
    rotate_default = args.rotate

    print(f"Connecting to Ryuo...")
    device = RyuoDevice(args.device)

    info = device.get_device_info()
    fw = device.get_firmware()
    print(f"Device:   {info['product']} ({info['manufacturer']})")
    print(f"Firmware: AURA={fw['aura']} OLED={fw['oled']}")
    print(f"Rotate:   {'on' if rotate_default else 'off'}")
    print()

    server = HTTPServer((args.host, args.port), RyuoHandler)
    print(f"Ryuo OLED daemon listening on http://{args.host}:{args.port}")
    print(f"Browse GIFs: http://{args.host}:{args.port}/ui")
    print(f"API:         curl localhost:{args.port}/status")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
        device.close()


if __name__ == "__main__":
    main()

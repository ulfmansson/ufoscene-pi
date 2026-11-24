#!/usr/bin/env python3
"""ufoscene-pi: the small server on the Pi.

* serves ufoscene itself (/ufo/) and the TV page that wraps it (/tv) to the kiosk browser
* serves the phone remote (/) to anything on the home network
* passes remote commands to the TV page as server-sent events (/api/events)
* collects state from the TV page and from the Spotify Connect speaker (/api/state, /api/spotify)

Standard library only, plus python3-qrcode for the QR code on the TV.
"""

import argparse
import io
import json
import mimetypes
import os
import queue
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

# Commands the phone may send. The TV page decides what each one does.
COMMANDS = {
    "start", "next", "prev", "random", "scene", "song-next", "song-prev", "hud", "tv",
    "panel", "demo", "audio", "overlay", "selftest", "reload",
}
AUDIO_SOURCES = {"radio", "mic", "spotify"}


class Hub:
    """Fan-out of commands to every connected TV page, plus the shared state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners: list[queue.Queue] = []
        self.state = {
            "version": VERSION,
            "fps": None,
            "audio": "radio",
            "demo": None,
            "spotify": None,  # {"artist", "title", "playing"}
            "tv_seen": None,
        }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def publish(self, event: dict):
        with self.lock:
            for q in list(self.listeners):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def update(self, **kv):
        with self.lock:
            self.state.update(kv)

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.state)
            s["tv_connected"] = len(self.listeners) > 0
        return s


def local_ip() -> str:
    """The address phones on the same network can reach (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def remote_urls() -> dict:
    host = socket.gethostname().split(".")[0]
    ip = local_ip()
    return {
        "mdns": f"http://{host}.local/",
        "ip": f"http://{ip}/" if ip else "",
    }


def set_default_input(source: str):
    """Point PipeWire's default input where ufoscene should listen.

    spotify: the monitor of the output (what the Pi itself plays); mic: a real input device.
    ufoscene's microphone mode then simply opens the default input.
    """
    import subprocess

    def pactl(*args) -> str:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout.strip()

    try:
        if source == "spotify":
            sink = pactl("get-default-sink")
            if sink:
                pactl("set-default-source", f"{sink}.monitor")
        elif source == "mic":
            for line in pactl("list", "short", "sources").splitlines():
                name = line.split("\t")[1] if "\t" in line else ""
                if name and not name.endswith(".monitor"):
                    pactl("set-default-source", name)
                    break
    except (OSError, subprocess.SubprocessError, IndexError):
        pass


def qr_svg(text: str) -> bytes:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>'
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathFillImage, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = f"ufoscene-pi/{VERSION}"
    hub: Hub
    www: Path
    ufo: Path

    def log_message(self, fmt, *args):  # quieter journal: errors only
        pass

    # ---------------------------------------------------------------- helpers

    def from_this_pi(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def send_bytes(self, body: bytes, ctype: str, code=HTTPStatus.OK, cache="no-cache"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=HTTPStatus.OK):
        self.send_bytes(json.dumps(obj).encode(), "application/json", code)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 64 * 1024:
            return {}
        try:
            v = json.loads(self.rfile.read(n))
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def send_file(self, root: Path, rel: str):
        root = root.resolve()
        path = (root / rel.lstrip("/")).resolve()
        if root not in path.parents and path != root:
            return self.send_error(HTTPStatus.NOT_FOUND)
        if path.is_dir():
            path = path / "index.html"
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "text/javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(path.read_bytes(), ctype)

    # ---------------------------------------------------------------- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p in ("/", "/remote"):
            return self.send_file(self.www, "remote.html")
        if p == "/tv":
            return self.send_file(self.www, "tv.html")
        if p.startswith("/static/"):
            return self.send_file(self.www, p[len("/static/"):])
        if p == "/ufo" or p.startswith("/ufo/"):
            if p == "/ufo":
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", "/ufo/")
                self.end_headers()
                return
            return self.send_file(self.ufo, p[len("/ufo/"):])
        if p == "/api/state":
            return self.send_json({**self.hub.snapshot(), "urls": remote_urls()})
        if p == "/api/qr.svg":
            text = parse_qs(url.query).get("text", [remote_urls()["mdns"]])[0][:200]
            return self.send_bytes(qr_svg(text), "image/svg+xml")
        if p == "/api/events":
            return self.events()
        if p == "/healthz":
            return self.send_bytes(b"ok", "text/plain")
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/cmd":
            body = self.read_json()
            cmd = body.get("cmd")
            if cmd not in COMMANDS:
                return self.send_json({"error": "unknown command"}, HTTPStatus.BAD_REQUEST)
            arg = body.get("arg")
            if cmd == "audio" and arg not in AUDIO_SOURCES:
                return self.send_json({"error": "unknown audio source"}, HTTPStatus.BAD_REQUEST)
            if cmd == "demo" and not (isinstance(arg, int) and 1 <= arg <= 999):
                return self.send_json({"error": "demo is a number"}, HTTPStatus.BAD_REQUEST)
            if cmd == "audio":
                set_default_input(arg)
            self.hub.publish({"cmd": cmd, "arg": arg})
            return self.send_json({"ok": True})
        # The two below are only for programs on the Pi itself.
        if p == "/api/state" and self.from_this_pi():
            body = self.read_json()
            allowed = {k: body[k] for k in ("fps", "audio", "demo") if k in body}
            self.hub.update(tv_seen=time.time(), **allowed)
            return self.send_json({"ok": True})
        if p == "/api/spotify" and self.from_this_pi():
            body = self.read_json()
            before = self.hub.snapshot().get("spotify") or {}
            now = {
                # play/pause events carry no track: keep the one we know
                "artist": str(body.get("artist", before.get("artist", "")))[:200],
                "title": str(body.get("title", before.get("title", "")))[:200],
                "playing": bool(body.get("playing", True)),
            }
            if now["playing"] and before.get("playing") is not True:
                set_default_input("spotify")
            self.hub.update(spotify=now)
            self.hub.publish({"cmd": "spotify", "arg": now})
            return self.send_json({"ok": True})
        self.send_error(HTTPStatus.NOT_FOUND)

    def events(self):
        q = self.hub.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b": hello\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("UFO_PORT", "80")))
    ap.add_argument("--www", default=os.environ.get("UFO_WWW", "/opt/ufoscene/www"))
    ap.add_argument("--ufo", default=os.environ.get("UFO_WEB", "/opt/ufoscene/ufo"))
    a = ap.parse_args()

    Handler.hub = Hub()
    Handler.www = Path(a.www)
    Handler.ufo = Path(a.ufo)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        address_family = socket.AF_INET6
        allow_reuse_address = True

        def server_bind(self):
            # answer on IPv4 and IPv6
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    srv = Server(("::", a.port), Handler)
    print(f"ufoscene-pi {VERSION} on port {a.port}; remote at {remote_urls()}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

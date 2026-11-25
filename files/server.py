#!/usr/bin/env python3
"""ufoscene-pi: the small server on the Pi.

* serves ufoscene itself (/ufo/) and the TV page that wraps it (/tv) to the kiosk browser
* serves the phone remote (/) to anything on the home network
* passes remote commands to the TV page as server-sent events (/api/events)
* collects state from the TV page (/api/state) and from the phone speakers, Bluetooth and
  AirPlay (/api/nowplaying, /api/bluetooth; phone.py sends those)

Standard library only, plus python3-qrcode for the QR code on the TV.
"""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import queue
import re
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.2.0"

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

# Commands the phone may send. The TV page decides what each one does.
COMMANDS = {
    "start", "next", "prev", "random", "scene", "song-next", "song-prev", "hud", "tv",
    "panel", "demo", "audio", "overlay", "selftest", "reload", "pair", "lastfm",
    "spotify-app",
}
# phone: whatever a phone plays to the Pi over Bluetooth or AirPlay
AUDIO_SOURCES = {"radio", "mic", "phone"}
PHONE_SOURCES = {"bluetooth", "airplay"}
LASTFM_USER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,14}")  # Last.fm's own rule
LASTFM_KEY = re.compile(r"[0-9a-f]{32}")
SPOTIFY_CLIENT_ID = re.compile(r"[0-9a-f]{32}")


class Settings:
    """What the remote set that must survive a restart: the Last.fm username and API key.

    The image may come with an API key for everyone (built_in_key); a key typed on the remote
    takes its place. Keys never go out to the network, only to the TV page on the Pi itself.
    """

    def __init__(self, path: Path | None, built_in_key: str = ""):
        self.path = path
        self.built_in_key = built_in_key if LASTFM_KEY.fullmatch(built_in_key) else ""
        self.lock = threading.Lock()
        self.data = {}
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text()) or {}
            except (OSError, ValueError):
                self.data = {}

    def lastfm(self) -> dict:
        with self.lock:
            return {"user": self.data.get("lastfm_user") or "",
                    "key": self.data.get("lastfm_key") or self.built_in_key}

    def spotify_client_id(self) -> str:
        with self.lock:
            return self.data.get("spotify_client_id") or ""

    def set_spotify_client_id(self, client_id: str):
        with self.lock:
            if client_id:
                self.data["spotify_client_id"] = client_id
            else:
                self.data.pop("spotify_client_id", None)
            self._save()

    def _save(self):
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.chmod(0o600)
            tmp.replace(self.path)

    def set_lastfm(self, user: str, key: str | None):
        with self.lock:
            if user:
                self.data["lastfm_user"] = user
            else:
                self.data.pop("lastfm_user", None)
            if key is not None:
                if key:
                    self.data["lastfm_key"] = key
                else:
                    self.data.pop("lastfm_key", None)
            self._save()


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
            "nowplaying": None,  # {"source", "artist", "title", "playing"}
            "bluetooth": None,  # {"available", "pairing", "device"} from phone.py
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

    phone: the monitor of the output (what the Pi itself plays); mic: a real input device.
    ufoscene's microphone mode then simply opens the default input.
    """
    import subprocess

    def pactl(*args) -> str:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout.strip()

    try:
        if source == "phone":
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
    settings: "Settings"
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
            lf = self.settings.lastfm()
            lastfm = {"user": lf["user"], "key": bool(lf["key"]), "built_in_key": bool(self.settings.built_in_key)}
            # a Spotify Client ID is public (it names the app); the phones keep their own tokens
            spotify = {"client_id": self.settings.spotify_client_id()}
            return self.send_json({**self.hub.snapshot(), "urls": remote_urls(), "lastfm": lastfm, "spotify": spotify})
        if p == "/api/lastfm" and self.from_this_pi():  # with the key: for the TV page only
            return self.send_json(self.settings.lastfm())
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
            if cmd == "lastfm":
                if not isinstance(arg, dict):
                    return self.send_json({"error": "lastfm needs a user"}, HTTPStatus.BAD_REQUEST)
                user = str(arg.get("user") or "").strip()
                key = arg.get("key")
                key = None if key is None else str(key).strip().lower()
                if user and not LASTFM_USER.fullmatch(user):
                    return self.send_json({"error": "that is not a Last.fm username"}, HTTPStatus.BAD_REQUEST)
                if key and not LASTFM_KEY.fullmatch(key):
                    return self.send_json({"error": "a Last.fm API key is 32 letters and digits"}, HTTPStatus.BAD_REQUEST)
                self.settings.set_lastfm(user, key)
                arg = None  # the TV page fetches it from /api/lastfm: the key stays off the network
            if cmd == "spotify-app":
                cid = str((arg or {}).get("client_id") or "").strip().lower() if isinstance(arg, dict) else None
                if cid is None or (cid and not SPOTIFY_CLIENT_ID.fullmatch(cid)):
                    return self.send_json({"error": "a Spotify Client ID is 32 letters and digits"}, HTTPStatus.BAD_REQUEST)
                self.settings.set_spotify_client_id(cid)
                arg = None
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
        if p == "/api/nowplaying" and self.from_this_pi():
            body = self.read_json()
            source = body.get("source")
            if source not in PHONE_SOURCES:
                return self.send_json({"error": "unknown source"}, HTTPStatus.BAD_REQUEST)
            before = self.hub.snapshot().get("nowplaying") or {}
            same = before.get("source") == source
            now = {
                "source": source,
                # play/pause events carry no track: keep the one we know from the same source
                "artist": str(body.get("artist", before.get("artist", "") if same else ""))[:200],
                "title": str(body.get("title", before.get("title", "") if same else ""))[:200],
                "playing": bool(body.get("playing", True)),
            }
            if now["playing"] and not (same and before.get("playing") is True):
                set_default_input("phone")
            self.hub.update(nowplaying=now)
            self.hub.publish({"cmd": "nowplaying", "arg": now})
            return self.send_json({"ok": True})
        if p == "/api/bluetooth" and self.from_this_pi():
            body = self.read_json()
            bt = {
                "available": bool(body.get("available")),
                "pairing": bool(body.get("pairing")),
                "device": str(body.get("device") or "")[:100] or None,
            }
            self.hub.update(bluetooth=bt)
            self.hub.publish({"cmd": "bluetooth", "arg": bt})
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
    ap.add_argument("--state", default=os.environ.get("STATE_DIRECTORY", "/var/lib/ufoscene"))
    ap.add_argument("--lastfm-key-file", default="/etc/ufoscene/lastfm-api-key")
    a = ap.parse_args()

    try:
        built_in = Path(a.lastfm_key_file).read_text().strip()
    except OSError:
        built_in = ""
    Path(a.state).mkdir(parents=True, exist_ok=True)
    Handler.settings = Settings(Path(a.state) / "settings.json", built_in)
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

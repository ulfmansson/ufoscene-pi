#!/usr/bin/env python3
"""ufoscene-pi: the small server on the Pi.

* serves ufoscene itself (/ufo/) and the TV page that wraps it (/tv) to the kiosk browser
* serves the phone remote (/) to anything on the home network
* passes remote commands to the TV page as server-sent events (/api/events)
* collects state from the TV page (/api/state) and from the phone speakers, Bluetooth and
  AirPlay (/api/nowplaying, /api/bluetooth; phone.py sends those)
* takes a new Wi-Fi network from the remote, confirmed with a code shown on the TV, and hands it
  to wifi.py, which runs as root and does the change (/api/wifi*)

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
import secrets
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
WIFI_PSK = re.compile(r"[\x20-\x7e]{8,63}|[0-9a-fA-F]{64}")  # WPA2: a passphrase, or the key in hex
SETUP_IP = "10.42.0.1"  # the Pi on its own setup network (wifi.py)


def valid_wifi(ssid, psk) -> bool:
    return (
        isinstance(ssid, str) and isinstance(psk, str)
        and 1 <= len(ssid.encode()) <= 32 and not any(ord(c) < 32 for c in ssid)
        and WIFI_PSK.fullmatch(psk) is not None
    )


class Wifi:
    """A Wi-Fi change from the remote, on its way to wifi.py.

    Anyone on the network can open the remote, so a change needs a code that only the TV shows:
    six digits, good for five minutes and three tries, and a new one at most every 30 seconds.
    The password goes to wifi.py once and is never sent back to a phone. The setup network's
    password (wifi.py opens that network when the Pi is offline) only goes to the TV page.
    """

    CODE_S = 300
    CODE_GAP_S = 30
    TRIES = 3

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.status: dict | None = None  # from wifi.py
        self.setup: dict | None = None  # {"ssid", "password", "ip"} while the setup network is open
        self.pending: dict | None = None
        self.code = ""
        self.code_at = -1e9
        self.tries = 0

    def public(self) -> dict | None:
        with self.lock:
            if self.status is None:
                return None
            s = dict(self.status)
            s["setup"] = {"ssid": self.setup["ssid"], "ip": self.setup["ip"]} if self.setup else None
            s["pending"] = self.pending is not None
            return s

    def in_setup(self) -> bool:
        with self.lock:
            return bool(self.setup)

    def set_status(self, body: dict) -> dict | None:
        nets = []
        for n in body.get("networks") or []:
            if isinstance(n, dict) and isinstance(n.get("ssid"), str) and 0 < len(n["ssid"]) <= 32:
                sig = n.get("signal")
                nets.append({"ssid": n["ssid"], "signal": sig if isinstance(sig, int) else 0, "secure": bool(n.get("secure"))})
        last = body.get("last")
        if isinstance(last, dict):
            last = {k: str(last.get(k) or "")[:200] for k in ("result", "ssid", "error")}
        else:
            last = None
        setup = body.get("setup")
        with self.lock:
            self.status = {
                "available": bool(body.get("available")),
                "state": str(body.get("state") or "")[:20],
                "ssid": str(body.get("ssid") or "")[:64],
                "cable": bool(body.get("cable")),
                "networks": nets[:20],
                "last": last,
            }
            self.setup = (
                {k: str(setup.get(k) or "")[:64] for k in ("ssid", "password", "ip")} if isinstance(setup, dict) else None
            )
        return self.public()

    def new_code(self) -> bool:
        """A code for the TV: the one still good, or a new one. False when the last one was used
        up less than 30 seconds after it was made (someone guessing)."""
        with self.lock:
            now = self.clock()
            if self.code and now - self.code_at < self.CODE_S:
                return True  # the TV shows the same code again
            if now - self.code_at < self.CODE_GAP_S:
                return False
            self.code = f"{secrets.randbelow(10**6):06d}"
            self.code_at = now
            self.tries = 0
            return True

    def code_for_tv(self) -> dict:
        with self.lock:
            left = self.CODE_S - (self.clock() - self.code_at)
            if not self.code or left <= 0:
                return {}
            return {"code": self.code, "seconds": int(left)}

    def submit(self, ssid, psk, code) -> tuple[int, str]:
        if not valid_wifi(ssid, psk):
            return 400, "A network name is 1 to 32 characters, and a Wi-Fi password 8 to 63."
        with self.lock:
            if self.status is None or not self.status.get("available"):
                return 409, "This Pi has no Wi-Fi it can change."
            if self.pending is not None or self.status.get("state") == "connecting":
                return 409, "The Pi is already changing its Wi-Fi."
            if not self.code or self.clock() - self.code_at >= self.CODE_S:
                self.code = ""
                return 403, "Ask for a code on the TV first."
            if not (isinstance(code, str) and secrets.compare_digest(code.strip(), self.code)):
                self.tries += 1
                if self.tries >= self.TRIES:
                    self.code = ""
                    return 403, "Wrong code. Ask for a new one."
                return 403, f"Wrong code. {self.TRIES - self.tries} more tries."
            self.code = ""
            self.pending = {"ssid": ssid, "psk": psk}
            return 200, ""

    def take(self) -> dict:
        with self.lock:
            p, self.pending = self.pending, None
            return p or {}


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
    wifi: Wifi
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

    def captive(self) -> bool:
        """On the setup network every name leads to the Pi (dnsmasq): send phones checking for
        internet to the remote, so they open it by themselves."""
        if self.from_this_pi() or not self.wifi.in_setup():
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        name = socket.gethostname().split(".")[0].lower()
        if host in ("", SETUP_IP, name, f"{name}.local", "localhost", "127.0.0.1", "::1", local_ip()):
            return False
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"http://{SETUP_IP}/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if self.captive():
            return
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
            return self.send_json({**self.hub.snapshot(), "urls": remote_urls(), "lastfm": lastfm, "spotify": spotify,
                                   "wifi": self.wifi.public()})
        if p == "/api/lastfm" and self.from_this_pi():  # with the key: for the TV page only
            return self.send_json(self.settings.lastfm())
        # for the TV page and wifi.py on the Pi itself
        if p == "/api/wifi/code" and self.from_this_pi():
            return self.send_json(self.wifi.code_for_tv())
        if p == "/api/wifi/setup" and self.from_this_pi():
            with self.wifi.lock:
                return self.send_json(self.wifi.setup or {})
        if p == "/api/wifi/pending" and self.from_this_pi():
            return self.send_json(self.wifi.take())
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
        if p == "/api/wifi/code":
            if not (self.wifi.public() or {}).get("available"):
                return self.send_json({"error": "This Pi has no Wi-Fi it can change."}, HTTPStatus.CONFLICT)
            if not self.wifi.new_code():
                return self.send_json({"error": "A code was just made: wait half a minute."}, HTTPStatus.TOO_MANY_REQUESTS)
            self.hub.publish({"cmd": "wifi-code", "arg": None})  # the TV fetches it: not on the network
            return self.send_json({"ok": True})
        if p == "/api/wifi":
            body = self.read_json()
            code, error = self.wifi.submit(body.get("ssid"), body.get("psk"), body.get("code"))
            if error:
                return self.send_json({"error": error}, code)
            self.hub.publish({"cmd": "wifi", "arg": self.wifi.public()})
            return self.send_json({"ok": True})
        # The ones below are only for programs on the Pi itself.
        if p == "/api/wifi/status" and self.from_this_pi():
            before = self.wifi.public()
            now = self.wifi.set_status(self.read_json())
            keys = ("state", "ssid", "last", "setup", "available")
            if before is None or any(before.get(k) != now.get(k) for k in keys):
                self.hub.publish({"cmd": "wifi", "arg": now})
            return self.send_json({"ok": True})
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
    Handler.wifi = Wifi()
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

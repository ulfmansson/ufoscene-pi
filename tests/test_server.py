"""Tests for files/server.py: requests are fed straight to the handler, no network needed.

Run:  python3 -m unittest discover -s tests -v
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "files"))
import server  # noqa: E402


class FakeSocket:
    def __init__(self, raw: bytes):
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()

    def makefile(self, mode, *a, **kw):
        return self.rfile if "r" in mode else _Writer(self.wfile)

    def sendall(self, b):
        self.wfile.write(b)


class _Writer(io.RawIOBase):
    def __init__(self, sink):
        self.sink = sink

    def writable(self):
        return True

    def write(self, b):
        self.sink.write(b)
        return len(b)


def request(method, path, body=None, client="192.168.1.20", host="ufoscene.local"):
    data = b"" if body is None else json.dumps(body).encode()
    raw = (
        f"{method} {path} HTTP/1.1\r\nHost: {host}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n"
    ).encode() + data
    sock = FakeSocket(raw)
    server.Handler(sock, (client, 50000), None)
    head, _, payload = sock.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    headers = dict(
        line.split(": ", 1) for line in head.decode("latin-1").split("\r\n")[1:] if ": " in line
    )
    return status, headers, payload


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        ufo = Path(cls.tmp.name) / "ufo"
        ufo.mkdir()
        (ufo / "index.html").write_text("<title>ufo</title>")
        (ufo / "ufo.wasm").write_bytes(b"\0asm\1\0\0\0")
        server.Handler.www = HERE.parent / "files" / "www"
        server.Handler.ufo = ufo
        server.set_default_input = lambda source: None  # no pactl here

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        server.Handler.hub = server.Hub()
        self.clock = [1000.0]
        server.Handler.wifi = server.Wifi(clock=lambda: self.clock[0])
        server.Handler.settings = server.Settings(Path(self.tmp.name) / "settings.json")
        server.Handler.diagnostics = server.diag.Diagnostics({"pi": lambda: {"model": "Raspberry Pi 5"}})

    def test_pages(self):
        for path, ctype in [
            ("/", "text/html"),
            ("/tv", "text/html"),
            ("/static/tv.js", "text/javascript"),
            ("/static/remote.css", "text/css"),
            ("/diag", "text/html"),
            ("/static/diag.js", "text/javascript"),
            ("/static/diag.css", "text/css"),
            ("/ufo/", "text/html"),
            ("/ufo/ufo.wasm", "application/wasm"),
            ("/healthz", "text/plain"),
        ]:
            status, headers, _ = request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith(ctype), (path, headers["Content-Type"]))

    def test_no_escape_from_the_web_roots(self):
        for path in ["/ufo/../server.py", "/static/../server.py", "/ufo/%2e%2e/server.py", "/nope"]:
            status, _, body = request("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"import", body)

    def test_commands_reach_the_tv(self):
        q = server.Handler.hub.subscribe()
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "next"})[0], 200)
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "demo", "arg": 42})[0], 200)
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "audio", "arg": "phone"})[0], 200)
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "pair"})[0], 200)
        self.assertEqual(q.get_nowait(), {"cmd": "next", "arg": None})
        self.assertEqual(q.get_nowait(), {"cmd": "demo", "arg": 42})
        self.assertEqual(q.get_nowait(), {"cmd": "audio", "arg": "phone"})
        self.assertEqual(q.get_nowait(), {"cmd": "pair", "arg": None})

    def test_bad_commands_are_refused(self):
        for body in [{"cmd": "rm -rf"}, {"cmd": "demo", "arg": "1; drop"}, {"cmd": "demo", "arg": 0},
                     {"cmd": "audio", "arg": "tab"}, {"cmd": "audio", "arg": "spotify"}, {}]:
            self.assertEqual(request("POST", "/api/cmd", body)[0], 400, body)

    def test_state_nowplaying_and_bluetooth_only_from_the_pi(self):
        self.assertEqual(request("POST", "/api/state", {"fps": 30})[0], 404)
        self.assertEqual(request("POST", "/api/nowplaying", {"source": "airplay", "title": "x"})[0], 404)
        self.assertEqual(request("POST", "/api/bluetooth", {"available": True})[0], 404)
        self.assertEqual(request("POST", "/api/state", {"fps": 30, "evil": 1}, client="127.0.0.1")[0], 200)
        _, _, body = request("GET", "/api/state")
        s = json.loads(body)
        self.assertEqual(s["fps"], 30)
        self.assertNotIn("evil", s)
        self.assertIn("mdns", s["urls"])

    def test_nowplaying_keeps_the_track_on_pause(self):
        q = server.Handler.hub.subscribe()
        pi = "127.0.0.1"
        request("POST", "/api/nowplaying", {"source": "airplay", "artist": "Jean-Michel Jarre", "title": "Oxygène 4"}, client=pi)
        request("POST", "/api/nowplaying", {"source": "airplay", "playing": False}, client=pi)
        s = json.loads(request("GET", "/api/state")[2])
        self.assertEqual(s["nowplaying"], {"source": "airplay", "artist": "Jean-Michel Jarre", "title": "Oxygène 4", "playing": False})
        self.assertEqual(q.get_nowait()["cmd"], "nowplaying")
        # another source does not inherit the song
        request("POST", "/api/nowplaying", {"source": "bluetooth", "playing": True}, client=pi)
        s = json.loads(request("GET", "/api/state")[2])
        self.assertEqual(s["nowplaying"], {"source": "bluetooth", "artist": "", "title": "", "playing": True})
        self.assertEqual(request("POST", "/api/nowplaying", {"source": "spotify"}, client=pi)[0], 400)

    def test_bluetooth_state(self):
        q = server.Handler.hub.subscribe()
        request("POST", "/api/bluetooth", {"available": True, "pairing": True, "device": "Pixel 9", "x": 1}, client="127.0.0.1")
        s = json.loads(request("GET", "/api/state")[2])
        self.assertEqual(s["bluetooth"], {"available": True, "pairing": True, "device": "Pixel 9"})
        self.assertEqual(q.get_nowait(), {"cmd": "bluetooth", "arg": s["bluetooth"]})

    def test_lastfm_username_and_key(self):
        q = server.Handler.hub.subscribe()
        key = "0123456789abcdef0123456789abcdef"
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "lastfm", "arg": {"user": "ufo_fan", "key": key}})[0], 200)
        self.assertEqual(q.get_nowait(), {"cmd": "lastfm", "arg": None})  # the key is not broadcast
        s = json.loads(request("GET", "/api/state")[2])
        self.assertEqual(s["lastfm"], {"user": "ufo_fan", "key": True, "built_in_key": False})
        self.assertNotIn(key, request("GET", "/api/state")[2].decode())
        self.assertEqual(request("GET", "/api/lastfm")[0], 404)  # not from the network
        self.assertEqual(json.loads(request("GET", "/api/lastfm", client="127.0.0.1")[2]), {"user": "ufo_fan", "key": key})
        # it survives a restart, and an empty username switches it off (the key stays)
        again = server.Settings(Path(self.tmp.name) / "settings.json")
        self.assertEqual(again.lastfm(), {"user": "ufo_fan", "key": key})
        request("POST", "/api/cmd", {"cmd": "lastfm", "arg": {"user": ""}})
        self.assertEqual(server.Handler.settings.lastfm(), {"user": "", "key": key})

    def test_lastfm_built_in_key_and_bad_input(self):
        server.Handler.settings = server.Settings(None, "f" * 32)
        request("POST", "/api/cmd", {"cmd": "lastfm", "arg": {"user": "someone"}})
        self.assertEqual(server.Handler.settings.lastfm(), {"user": "someone", "key": "f" * 32})
        for arg in [None, "someone", {"user": "1abc"}, {"user": "a b"}, {"user": "x" * 16},
                    {"user": "ok_user", "key": "not-a-key"}]:
            self.assertEqual(request("POST", "/api/cmd", {"cmd": "lastfm", "arg": arg})[0], 400, arg)

    def test_spotify_client_id(self):
        cid = "abcdef0123456789abcdef0123456789"
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "spotify-app", "arg": {"client_id": cid.upper()}})[0], 200)
        self.assertEqual(json.loads(request("GET", "/api/state")[2])["spotify"], {"client_id": cid})
        for arg in [None, "x", {"client_id": "short"}]:
            self.assertEqual(request("POST", "/api/cmd", {"cmd": "spotify-app", "arg": arg})[0], 400, arg)
        request("POST", "/api/cmd", {"cmd": "spotify-app", "arg": {"client_id": ""}})
        self.assertEqual(json.loads(request("GET", "/api/state")[2])["spotify"], {"client_id": ""})

    def test_diagnostics(self):
        key = "0123456789abcdef0123456789abcdef"
        request("POST", "/api/cmd", {"cmd": "lastfm", "arg": {"user": "ufo_fan", "key": key}})
        request("POST", "/api/state", {"fps": 28.5, "demo": 7}, client="127.0.0.1")
        status, headers, body = request("GET", "/api/diag", client="::ffff:192.168.1.20")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        d = json.loads(body)
        self.assertEqual(d["pi"], {"model": "Raspberry Pi 5"})
        self.assertEqual((d["tv"]["fps"], d["tv"]["demo"]), (28.5, 7))
        self.assertEqual(d["network"]["you"], {"ip": "192.168.1.20", "host": "ufoscene.local"})
        self.assertEqual(d["lastfm"], {"user": "ufo_fan", "key": True})
        self.assertNotIn(key.encode(), body)

    def test_nightly_update_status(self):
        self.assertIsNone(json.loads(request("GET", "/api/state")[2])["update"])
        status = Path(self.tmp.name) / "update-status.json"
        status.write_text(json.dumps({"result": "failed", "message": "v0.2.1 did not start", "running": "v0.2.0",
                                      "latest": "v0.2.1", "checked": 1, "bad": ["v0.2.1"]}))
        server.Handler.update_status = status
        try:
            up = json.loads(request("GET", "/api/state")[2])["update"]
        finally:
            server.Handler.update_status = None
        self.assertEqual((up["result"], up["running"], up["latest"]), ("failed", "v0.2.0", "v0.2.1"))
        self.assertNotIn("bad", up)

    def test_qr(self):
        status, headers, body = request("GET", "/api/qr.svg?text=http://ufoscene.local/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertIn(b"<svg", body)

    # ---------------------------------------------------------------- Wi-Fi

    PI = "127.0.0.1"

    def wifi_status(self, **kv):
        body = {"available": True, "state": "connected", "ssid": "Home", "cable": False,
                "networks": [{"ssid": "Home", "signal": 80, "secure": True}], "last": None, "setup": None, **kv}
        self.assertEqual(request("POST", "/api/wifi/status", body, client=self.PI)[0], 200)

    def code(self):
        self.assertEqual(request("POST", "/api/wifi/code")[0], 200)
        return json.loads(request("GET", "/api/wifi/code", client=self.PI)[2])["code"]

    def test_wifi_needs_the_helper(self):
        self.assertIsNone(json.loads(request("GET", "/api/state")[2])["wifi"])
        self.assertEqual(request("POST", "/api/wifi/code")[0], 409)
        self.assertEqual(request("POST", "/api/wifi", {"ssid": "Home", "psk": "12345678", "code": "1"})[0], 409)

    def test_wifi_helper_endpoints_only_from_the_pi(self):
        for method, path in [("POST", "/api/wifi/status"), ("GET", "/api/wifi/code"),
                             ("GET", "/api/wifi/setup"), ("GET", "/api/wifi/pending")]:
            self.assertEqual(request(method, path, {"available": True} if method == "POST" else None)[0], 404, path)

    def test_wifi_change_with_the_code_from_the_tv(self):
        q = server.Handler.hub.subscribe()
        self.wifi_status()
        self.assertEqual(q.get_nowait()["cmd"], "wifi")
        code = self.code()
        self.assertEqual(q.get_nowait(), {"cmd": "wifi-code", "arg": None})  # the code is not broadcast
        self.assertRegex(code, r"^[0-9]{6}$")
        self.assertEqual(self.code(), code)  # asking again shows the same code
        ok = request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": code})
        self.assertEqual(ok[0], 200, ok[2])
        self.assertTrue(json.loads(request("GET", "/api/state")[2])["wifi"]["pending"])
        self.assertNotIn(b"cabin password", request("GET", "/api/state")[2])
        self.assertEqual(request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": code})[0], 409)
        # wifi.py takes it, once
        self.assertEqual(json.loads(request("GET", "/api/wifi/pending", client=self.PI)[2]), {"ssid": "Cabin", "psk": "cabin password"})
        self.assertEqual(json.loads(request("GET", "/api/wifi/pending", client=self.PI)[2]), {})
        # the code is used up
        self.wifi_status()
        self.assertEqual(request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": code})[0], 403)

    def test_wifi_wrong_codes(self):
        self.wifi_status()
        code = self.code()
        wrong = f"{(int(code) + 1) % 10**6:06d}"
        for _ in range(3):
            self.assertEqual(request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": wrong})[0], 403)
        # three wrong tries void it, even the right code no longer works
        self.assertEqual(request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": code})[0], 403)
        self.assertEqual(json.loads(request("GET", "/api/wifi/code", client=self.PI)[2]), {})
        self.assertEqual(request("POST", "/api/wifi/code")[0], 429)  # not right away
        self.clock[0] += 31
        self.assertEqual(request("POST", "/api/wifi/code")[0], 200)

    def test_wifi_code_expires(self):
        self.wifi_status()
        code = self.code()
        self.clock[0] += 301
        self.assertEqual(json.loads(request("GET", "/api/wifi/code", client=self.PI)[2]), {})
        self.assertEqual(request("POST", "/api/wifi", {"ssid": "Cabin", "psk": "cabin password", "code": code})[0], 403)

    def test_wifi_bad_input(self):
        self.wifi_status()
        code = self.code()
        for body in [{"ssid": "", "psk": "12345678"}, {"ssid": "x" * 33, "psk": "12345678"},
                     {"ssid": "Home", "psk": "short"}, {"ssid": "Home", "psk": "x" * 64},
                     {"ssid": ["Home"], "psk": "12345678"}, {"ssid": "a\nb", "psk": "12345678"}]:
            self.assertEqual(request("POST", "/api/wifi", {**body, "code": code})[0], 400, body)

    def test_wifi_setup_network(self):
        q = server.Handler.hub.subscribe()
        self.wifi_status(state="setup", ssid="ufoscene-setup",
                         setup={"ssid": "ufoscene-setup", "password": "k3mp2qrstu", "ip": "10.42.0.1"})
        ev = q.get_nowait()
        self.assertEqual(ev["arg"]["setup"], {"ssid": "ufoscene-setup", "ip": "10.42.0.1"})
        # its password only for the TV page
        self.assertNotIn(b"k3mp2qrstu", request("GET", "/api/state", host="10.42.0.1")[2])
        self.assertEqual(json.loads(request("GET", "/api/wifi/setup", client=self.PI)[2])["password"], "k3mp2qrstu")
        # a phone checking for internet is sent to the remote; the remote itself is served
        status, headers, _ = request("GET", "/generate_204", client="10.42.0.23", host="connectivitycheck.gstatic.com")
        self.assertEqual((status, headers.get("Location")), (302, "http://10.42.0.1/"))
        self.assertEqual(request("GET", "/", client="10.42.0.23", host="10.42.0.1")[0], 200)
        self.assertEqual(request("GET", "/tv", client=self.PI, host="captive.apple.com")[0], 200)
        # with the Pi on its Wi-Fi again, no more redirects
        self.wifi_status()
        self.assertEqual(request("GET", "/generate_204", host="connectivitycheck.gstatic.com")[0], 404)


if __name__ == "__main__":
    unittest.main()

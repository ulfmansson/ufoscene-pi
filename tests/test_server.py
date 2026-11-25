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


def request(method, path, body=None, client="192.168.1.20"):
    data = b"" if body is None else json.dumps(body).encode()
    raw = (
        f"{method} {path} HTTP/1.1\r\nHost: ufoscene.local\r\n"
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
        server.Handler.settings = server.Settings(Path(self.tmp.name) / "settings.json")

    def test_pages(self):
        for path, ctype in [
            ("/", "text/html"),
            ("/tv", "text/html"),
            ("/static/tv.js", "text/javascript"),
            ("/static/remote.css", "text/css"),
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

    def test_qr(self):
        status, headers, body = request("GET", "/api/qr.svg?text=http://ufoscene.local/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertIn(b"<svg", body)


if __name__ == "__main__":
    unittest.main()

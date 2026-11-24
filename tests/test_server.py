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
        self.assertEqual(request("POST", "/api/cmd", {"cmd": "audio", "arg": "spotify"})[0], 200)
        self.assertEqual(q.get_nowait(), {"cmd": "next", "arg": None})
        self.assertEqual(q.get_nowait(), {"cmd": "demo", "arg": 42})
        self.assertEqual(q.get_nowait(), {"cmd": "audio", "arg": "spotify"})

    def test_bad_commands_are_refused(self):
        for body in [{"cmd": "rm -rf"}, {"cmd": "demo", "arg": "1; drop"}, {"cmd": "demo", "arg": 0},
                     {"cmd": "audio", "arg": "tab"}, {}]:
            self.assertEqual(request("POST", "/api/cmd", body)[0], 400, body)

    def test_state_and_spotify_only_from_the_pi(self):
        self.assertEqual(request("POST", "/api/state", {"fps": 30})[0], 404)
        self.assertEqual(request("POST", "/api/spotify", {"title": "x"})[0], 404)
        self.assertEqual(request("POST", "/api/state", {"fps": 30, "evil": 1}, client="127.0.0.1")[0], 200)
        _, _, body = request("GET", "/api/state")
        s = json.loads(body)
        self.assertEqual(s["fps"], 30)
        self.assertNotIn("evil", s)
        self.assertIn("mdns", s["urls"])

    def test_spotify_keeps_the_track_on_pause(self):
        q = server.Handler.hub.subscribe()
        request("POST", "/api/spotify", {"artist": "Jean-Michel Jarre", "title": "Oxygène 4"}, client="127.0.0.1")
        request("POST", "/api/spotify", {"playing": False}, client="127.0.0.1")
        s = json.loads(request("GET", "/api/state")[2])
        self.assertEqual(s["spotify"], {"artist": "Jean-Michel Jarre", "title": "Oxygène 4", "playing": False})
        self.assertEqual(q.get_nowait()["cmd"], "spotify")

    def test_qr(self):
        status, headers, body = request("GET", "/api/qr.svg?text=http://ufoscene.local/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertIn(b"<svg", body)


if __name__ == "__main__":
    unittest.main()

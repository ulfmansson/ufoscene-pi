#!/usr/bin/env python3
"""librespot --onevent hook: tell the server what the Spotify Connect speaker is playing."""

import json
import os
import urllib.request

ev = os.environ.get("PLAYER_EVENT", "")
body = None
if ev == "track_changed":
    artists = os.environ.get("ARTISTS", "").replace("\n", ", ")
    body = {"artist": artists, "title": os.environ.get("NAME", ""), "playing": True}
elif ev in ("playing", "started"):
    body = {"playing": True}
elif ev in ("paused", "stopped", "session_disconnected"):
    body = {"playing": False}

if body is not None:
    req = urllib.request.Request(
        "http://127.0.0.1/api/spotify",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass

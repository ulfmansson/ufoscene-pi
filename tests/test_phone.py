"""Tests for files/phone.py's parts that need no Bluetooth or AirPlay: the metadata parsing.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "files"))
import phone  # noqa: E402


def item(typ: str, code: str, data: bytes | None = None) -> str:
    head = f"<item><type>{typ.encode().hex()}</type><code>{code.encode().hex()}</code>"
    if data is None:
        return head + "<length>0</length></item>\n"
    b64 = base64.b64encode(data).decode()
    return head + f"<length>{len(data)}</length>\n<data encoding=\"base64\">\n{b64}</data></item>\n"


class AirplayTest(unittest.TestCase):
    def test_parse_items_keeps_a_half_written_item(self):
        stream = item("ssnc", "pbeg") + item("core", "asar", "Röyksopp".encode())
        items, rest = phone.parse_items(stream + stream[:30])
        self.assertEqual(items, [("ssnc", "pbeg", b""), ("core", "asar", "Röyksopp".encode())])
        self.assertEqual(rest.lstrip(), stream[:30])

    def test_a_song_and_play_state(self):
        t = phone.AirplayTracker()
        stream = (item("ssnc", "pbeg") + item("ssnc", "mdst") + item("core", "asar", "Röyksopp".encode())
                  + item("core", "minm", b"Eple") + item("core", "asal", b"Melody A.M.") + item("ssnc", "mden")
                  + item("ssnc", "pfls") + item("ssnc", "pfls") + item("ssnc", "prsm"))
        items, _ = phone.parse_items(stream)
        updates = [u for u in (t.feed(*i) for i in items) if u]
        self.assertEqual(updates, [
            {"source": "airplay", "playing": True},
            {"source": "airplay", "artist": "Röyksopp", "title": "Eple", "playing": True},
            {"source": "airplay", "playing": False},  # once, not per flush
            {"source": "airplay", "playing": True},
        ])


class BluetoothTest(unittest.TestCase):
    def test_track_update(self):
        self.assertEqual(
            phone.track_update({"Track": {"Artist": "Daft Punk", "Title": "Aerodynamic"}, "Status": "playing"}),
            {"source": "bluetooth", "artist": "Daft Punk", "title": "Aerodynamic", "playing": True},
        )
        self.assertEqual(phone.track_update({"Status": "paused"}), {"source": "bluetooth", "playing": False})
        self.assertIsNone(phone.track_update({"Position": 1234}))


if __name__ == "__main__":
    unittest.main()

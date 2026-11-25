"""Checks of the remote's own modules (eggs.js, spotify.js) in a JavaScript engine.

Uses node, or JavaScriptCore's jsc on a Mac; skipped when neither is there.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WWW = Path(__file__).resolve().parent.parent / "files" / "www"
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def run_module(body: str) -> dict:
    """Run an ES module that imports from files/www and prints one JSON line."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "check.mjs"
        if shutil.which("node"):
            f.write_text(body.replace("@WWW@", WWW.as_uri()).replace("print(", "console.log("))
            cmd = ["node", str(f)]
        else:
            f.write_text(body.replace("@WWW@", str(WWW)))
            cmd = [JSC, "-m", str(f)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise AssertionError(out.stderr or out.stdout)
        return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(shutil.which("node") or Path(JSC).exists(), "no JavaScript engine")
class RemoteJsTest(unittest.TestCase):
    def test_101_plays_pianobasso_and_real_demos_stay_demos(self):
        r = run_module("""
import { egg } from '@WWW@/eggs.js';
import { contextUri } from '@WWW@/spotify.js';
const e = egg(101);
print(JSON.stringify({ title: e && e.title, uri: e && contextUri(e.link),
  demos: [1, 42, 100, 102, 0, NaN].map((n) => egg(n)), proto: egg('toString') }));
""")
        self.assertEqual(r["title"], "Pianobasso")
        self.assertEqual(r["uri"], "spotify:album:4hNSxY4wMhnx2VPO00Pnsr")
        self.assertEqual(r["demos"], [None] * 6)
        self.assertIsNone(r["proto"])

    def test_spotify_links(self):
        r = run_module("""
import { contextUri } from '@WWW@/spotify.js';
print(JSON.stringify([
  'https://open.spotify.com/playlist/37i9dQZF1DX4UtSsGT1Sbe',
  'https://open.spotify.com/album/4hNSxY4wMhnx2VPO00Pnsr?si=abc',
  'https://open.spotify.com/intl-sv/album/4hNSxY4wMhnx2VPO00Pnsr',
  'https://open.spotify.com/track/4hNSxY4wMhnx2VPO00Pnsr',
  'https://evil.example/album/4hNSxY4wMhnx2VPO00Pnsr',
  'spotify:album:x',
].map(contextUri)));
""")
        self.assertEqual(r, [
            "spotify:playlist:37i9dQZF1DX4UtSsGT1Sbe",
            "spotify:album:4hNSxY4wMhnx2VPO00Pnsr",
            "spotify:album:4hNSxY4wMhnx2VPO00Pnsr",
            "", "", "",
        ])


if __name__ == "__main__":
    unittest.main()

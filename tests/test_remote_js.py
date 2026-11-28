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

    def test_setlist_lines_and_searches(self):
        r = run_module("""
import { setlistSongs, trackUri, searchQueries } from '@WWW@/spotify.js';
const text = '# name: ufoscene.net · The concert\\r\\n\\n# Act 1\\nQueen - We Will Rock You\\n  Kalush Orchestra – Stefania  \\n'
  + 'https://open.spotify.com/track/7ouMYWpwJ422jRcDASZB7P?si=x\\n';
print(JSON.stringify({
  songs: setlistSongs(text),
  uris: setlistSongs(text).map(trackUri),
  q: [searchQueries('Queen - We Will Rock You'), searchQueries('AC/DC - Back - In Black'), searchQueries('Stefania')],
}));
""")
        self.assertEqual(r["songs"], [
            "Queen - We Will Rock You",
            "Kalush Orchestra – Stefania",
            "https://open.spotify.com/track/7ouMYWpwJ422jRcDASZB7P?si=x",
        ])
        self.assertEqual(r["uris"], ["", "", "spotify:track:7ouMYWpwJ422jRcDASZB7P"])
        self.assertEqual(r["q"], [
            ["track:We Will Rock You artist:Queen", "Queen - We Will Rock You"],
            ["track:Back - In Black artist:AC/DC", "AC/DC - Back - In Black"],
            ["Stefania"],
        ])

    def test_setlist_plays_in_order_on_the_phone_when_nothing_is_active(self):
        r = run_module("""
const kv = { 'ufopi.spotify.access': 't', 'ufopi.spotify.expires': String(Date.now() + 1e6), 'ufopi.spotify.refresh': 'r' };
globalThis.localStorage = { getItem: (k) => kv[k] ?? null, setItem: (k, v) => (kv[k] = v), removeItem: (k) => delete kv[k] };
const calls = [];
let active = false;
const res = (status, body) => ({ status, ok: status < 300, json: async () => body });
globalThis.fetch = async (url, opts = {}) => {
  const u = new URL(url);
  calls.push(`${opts.method} ${u.pathname}${u.search}`);
  if (u.pathname.endsWith('/search')) {
    const q = u.searchParams.get('q');
    return res(200, { tracks: { items: q === 'track:Under Pressure artist:Queen' ? [{ uri: 'spotify:track:pressure' }] : [] } });
  }
  if (u.pathname.endsWith('/devices')) return res(200, { devices: [
    { id: 'tv', type: 'TV', is_restricted: true }, { id: 'pc', type: 'Computer' }, { id: 'phone 1', type: 'Smartphone' }] });
  if (u.pathname.endsWith('/play') && !u.searchParams.get('device_id') && !active)
    return res(404, { error: { reason: 'NO_ACTIVE_DEVICE', message: 'no' } });
  if (u.pathname.endsWith('/play')) { active = true; calls.push(opts.body); return res(204); }
  return res(204);
};
const sp = await import('@WWW@/spotify.js');
const found = await Promise.all(['Queen - Under Pressure', 'nobody', 'spotify:track:7ouMYWpwJ422jRcDASZB7P'].map(sp.findTrack));
await sp.playInOrder(found.filter(Boolean));
print(JSON.stringify({ found, calls: calls.filter((c) => !c.includes('/search')) }));
""")
        self.assertEqual(r["found"], ["spotify:track:pressure", "", "spotify:track:7ouMYWpwJ422jRcDASZB7P"])
        self.assertEqual(r["calls"], [
            "PUT /v1/me/player/play",
            "GET /v1/me/player/devices",
            "PUT /v1/me/player/play?device_id=phone%201",
            '{"uris":["spotify:track:pressure","spotify:track:7ouMYWpwJ422jRcDASZB7P"]}',
            "PUT /v1/me/player/shuffle?state=false&device_id=phone%201",
            "PUT /v1/me/player/repeat?state=off&device_id=phone%201",
        ])


if __name__ == "__main__":
    unittest.main()

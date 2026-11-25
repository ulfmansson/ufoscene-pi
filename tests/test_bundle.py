"""image/make_bundle.sh: what goes into the bundle that install.sh and the image build install.

Builds a bundle from a pretend ufoscene checkout (no Rust needed) and looks inside.
"""

import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("bash") and shutil.which("tar"), "needs bash and tar")
class BundleTest(unittest.TestCase):
    def test_bundle_has_the_release_version_and_every_page_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ufo = Path(tmp) / "ufoscene"
            (ufo / "web").mkdir(parents=True)
            (ufo / "web" / "index.html").write_text("<title>ufo</title>")
            (ufo / "web" / "ufo.wasm").write_bytes(b"\0asm\1\0\0\0")
            (ufo / "LICENSE").write_text("license")
            out = Path(tmp) / "bundle.tar.gz"
            subprocess.run(["bash", str(ROOT / "image" / "make_bundle.sh"), str(ufo), str(out), "v9.8.7-rc6"],
                           check=True, capture_output=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": tmp})
            with tarfile.open(out) as t:
                names = {n.lstrip("./") for n in t.getnames()}
                server = t.extractfile("./files/server.py").read().decode()
                version = t.extractfile("./VERSION").read().decode()
        # the remote and the TV show the tag the build was made from
        self.assertEqual(re.findall(r'(?m)^VERSION = "(.*)"$', server), ["v9.8.7-rc6"])
        self.assertTrue(version.startswith("ufoscene-pi v9.8.7-rc6 "), version)
        # every file the pages load is in it (remote.js imports eggs.js and spotify.js)
        for f in (ROOT / "files" / "www").iterdir():
            self.assertIn(f"files/www/{f.name}", names)
        for f in ("files/www/eggs.js", "files/www/spotify.js", "ufo/ufo.wasm", "ufo/index.html"):
            self.assertIn(f, names)


if __name__ == "__main__":
    unittest.main()

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
            # without ufo-native the bundle has the browser only, unless CI asks for both
            r = subprocess.run(["bash", str(ROOT / "image" / "make_bundle.sh"), str(ufo), str(out), "v9.8.7-rc6"],
                               capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "TMPDIR": tmp, "REQUIRE_NATIVE": "1"})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("native/build-pi.sh", r.stderr)
            native = ufo / "target" / "pi-native"
            native.mkdir(parents=True)
            for arch in ("arm64", "armhf"):
                (native / f"ufo-native-{arch}").write_bytes(b"\x7fELF" + arch.encode())
            (native / "ufoscene-native.service").write_text("[Service]\nExecStart=@BIN@\n")
            subprocess.run(["bash", str(ROOT / "image" / "make_bundle.sh"), str(ufo), str(out), "v9.8.7-rc6"],
                           check=True, capture_output=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": tmp, "REQUIRE_NATIVE": "1"})
            with tarfile.open(out) as t:
                names = {n.lstrip("./") for n in t.getnames()}
                modes = {m.name.lstrip("./"): m.mode for m in t.getmembers()}
                server = t.extractfile("./files/server.py").read().decode()
                version = t.extractfile("./VERSION").read().decode()
        # the remote and the TV show the tag the build was made from
        self.assertEqual(re.findall(r'(?m)^VERSION = "(.*)"$', server), ["v9.8.7-rc6"])
        self.assertTrue(version.startswith("ufoscene-pi v9.8.7-rc6 "), version)
        # every file the pages load is in it (remote.js imports eggs.js and spotify.js)
        for f in (ROOT / "files" / "www").iterdir():
            self.assertIn(f"files/www/{f.name}", names)
        # the installer travels with its files (the nightly update installs and rolls back with it)
        self.assertIn("install.sh", names)
        for f in ("files/www/eggs.js", "files/update.py", "files/systemd/ufoscene-update.timer", "files/www/spotify.js", "ufo/ufo.wasm", "ufo/index.html"):
            self.assertIn(f, names)
        # both players: the native one for both Pi families, with its unit and the switch
        for f in ("native/ufo-native-arm64", "native/ufo-native-armhf", "native/ufoscene-native.service", "files/player.sh"):
            self.assertIn(f, names)
        self.assertTrue(modes["native/ufo-native-arm64"] & 0o111)


if __name__ == "__main__":
    unittest.main()

"""Tests for files/update.py, the nightly update: a pretend GitHub, installer and server.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "files"))
import update  # noqa: E402

URL = "https://github.com/ulfmansson/ufoscene-pi/releases/download/{tag}/ufoscene-pi-bundle.tar.gz"


def make_bundle(tag: str) -> bytes:
    """A bundle as make_bundle.sh lays it out, for release `tag`."""
    files = {
        "./files/server.py": f'VERSION = "{tag}"\n',
        "./files/www/remote.html": "<title>remote</title>",
        "./ufo/ufo.wasm": "\0asm",
        "./VERSION": f"ufoscene-pi {tag} (ufoscene abc1234)\n",
        "./install.sh": "#!/bin/bash\n",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def install_into(prefix: Path, bundle: Path):
    """What install.sh does with the files: files/ into the prefix, plus ufo/, VERSION, install.sh."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(bundle) as t:
            t.extractall(tmp)
        shutil.rmtree(prefix, ignore_errors=True)
        shutil.copytree(Path(tmp) / "files", prefix)
        for extra in ("ufo", "VERSION", "install.sh"):
            src = Path(tmp) / extra
            if src.is_dir():
                shutil.copytree(src, prefix / extra)
            elif src.exists():
                shutil.copy(src, prefix / extra)


class FakeUpdater(update.Updater):
    def __init__(self, root: Path):
        super().__init__(root / "opt", root / "state", root / "auto-update")
        self.release = {"tag_name": "v0.2.0", "prerelease": False, "assets": []}
        self.broken: set[str] = set()  # releases that do not start
        self.tv = True  # a TV page that reports while its release runs
        self.t = 1_000_000.0
        self.downloads: list[str] = []
        self.installs: list[str] = []

    def publish(self, tag: str, prerelease=False, digest: str | None = None):
        data = make_bundle(tag)
        self.bundles = getattr(self, "bundles", {})
        self.bundles[URL.format(tag=tag)] = data
        asset = {"name": update.ASSET, "browser_download_url": URL.format(tag=tag),
                 "digest": digest if digest is not None else "sha256:" + hashlib.sha256(data).hexdigest()}
        self.release = {"tag_name": tag, "prerelease": prerelease, "assets": [asset]}

    def latest(self):
        return self.release

    def download(self, url, to):
        self.downloads.append(url)
        to.write_bytes(self.bundles[url])

    def install(self, installer, bundle):
        install_into(self.prefix, bundle)
        self.installs.append(update.installed_version(self.prefix))
        return True

    def server_state(self):
        running = update.installed_version(self.prefix)
        if running in self.broken:
            return {}
        return {"version": running, "tv_seen": self.t if self.tv else None}

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t

    def say(self, text):
        pass


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.u = FakeUpdater(Path(self.tmp.name))
        bundle = Path(self.tmp.name) / "first.tar.gz"
        bundle.write_bytes(make_bundle("v0.2.0"))
        install_into(self.u.prefix, bundle)

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        return json.loads(self.u.status_path.read_text())

    def test_versions(self):
        p = update.parse
        self.assertLess(p("v0.2.0-rc6"), p("v0.2.0"))
        self.assertLess(p("v0.2.0-rc9"), p("v0.2.0-rc10"))
        self.assertLess(p("v0.2.0"), p("v0.2.1"))
        self.assertLess(p("v0.9.9"), p("v0.10.0"))
        for v in ("dev-1a2b3c4", "", None, "latest", "v1.2"):
            self.assertIsNone(p(v), v)
        self.assertEqual(update.installed_version(self.u.prefix), "v0.2.0")

    def test_up_to_date(self):
        self.u.publish("v0.2.0")
        self.assertEqual(self.u.run(), "current")
        self.assertEqual(self.u.downloads, [])
        self.u.publish("v0.1.1")  # never back to an older release
        self.assertEqual(self.u.run(), "current")

    def test_a_newer_release_is_installed(self):
        self.u.publish("v0.2.1")
        self.assertEqual(self.u.run(), "updated")
        self.assertEqual(update.installed_version(self.u.prefix), "v0.2.1")
        self.assertEqual(update.installed_version(self.u.previous), "v0.2.0")
        s = self.status()
        self.assertEqual((s["result"], s["running"], s["previous"]), ("updated", "v0.2.1", "v0.2.0"))
        self.assertEqual(self.u.run(), "current")

    def test_a_release_that_does_not_start_is_rolled_back_and_not_tried_again(self):
        self.u.publish("v0.2.1")
        self.u.broken.add("v0.2.1")
        self.assertEqual(self.u.run(), "failed")
        self.assertEqual(update.installed_version(self.u.prefix), "v0.2.0")
        self.assertEqual(self.u.installs, ["v0.2.1", "v0.2.0"])  # back with the previous version's own bundle
        self.assertTrue((self.u.prefix / "ufo" / "ufo.wasm").is_file())
        s = self.status()
        self.assertEqual((s["result"], s["running"], s["bad"]), ("failed", "v0.2.0", ["v0.2.1"]))
        # the next night: not again
        self.assertEqual(self.u.run(), "skipped")
        self.assertEqual(len(self.u.downloads), 1)
        # a fixed release is tried as usual
        self.u.publish("v0.2.2")
        self.assertEqual(self.u.run(), "updated")

    def test_the_tv_has_to_come_back(self):
        self.u.publish("v0.2.1")
        self.u.tv = True
        real = self.u.server_state

        def tv_gone():
            s = real()
            if s.get("version") == "v0.2.1":
                s["tv_seen"] = None  # the server runs, the TV page does not
            return s

        self.u.server_state = tv_gone
        self.assertEqual(self.u.run(), "failed")
        self.assertEqual(update.installed_version(self.u.prefix), "v0.2.0")

    def test_no_tv_before_means_the_server_is_enough(self):
        self.u.publish("v0.2.1")
        self.u.tv = False
        self.assertEqual(self.u.run(), "updated")

    def test_pre_releases_are_not_installed(self):
        self.u.publish("v0.3.0-rc1", prerelease=True)
        self.assertEqual(self.u.run(), "current")
        self.assertEqual(self.u.downloads, [])

    def test_a_pi_on_a_pre_release_moves_on_to_the_full_release(self):
        bundle = Path(self.tmp.name) / "rc.tar.gz"
        bundle.write_bytes(make_bundle("v0.2.0-rc6"))
        install_into(self.u.prefix, bundle)
        self.u.publish("v0.2.0")
        self.assertEqual(self.u.run(), "updated")

    def test_a_pi_built_from_a_branch_is_left_alone(self):
        bundle = Path(self.tmp.name) / "dev.tar.gz"
        bundle.write_bytes(make_bundle("dev-1a2b3c4"))
        install_into(self.u.prefix, bundle)
        self.u.publish("v0.9.0")
        self.assertEqual(self.u.run(), "dev")
        self.assertEqual(self.u.downloads, [])

    def test_off(self):
        self.u.opt_out.write_text("off\n")
        self.u.publish("v0.9.0")
        self.assertEqual(self.u.run(), "off")
        self.assertEqual(self.u.downloads, [])

    def test_a_download_that_does_not_match_its_checksum_is_not_installed(self):
        self.u.publish("v0.2.1", digest="sha256:" + "0" * 64)
        self.assertEqual(self.u.run(), "error")
        self.assertEqual(self.u.installs, [])
        self.assertEqual(update.installed_version(self.u.prefix), "v0.2.0")

    def test_installed_files_pack_back_into_a_bundle(self):
        out = Path(self.tmp.name) / "back.tar.gz"
        update.bundle_from_install(self.u.prefix, out)
        with tarfile.open(out) as t:
            names = {n.lstrip("./") for n in t.getnames()}
        for n in ("files/server.py", "files/www/remote.html", "ufo/ufo.wasm", "VERSION", "install.sh"):
            self.assertIn(n, names)
        self.assertNotIn("files/ufo", names)


if __name__ == "__main__":
    unittest.main()

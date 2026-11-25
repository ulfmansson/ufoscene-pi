#!/usr/bin/env python3
"""ufoscene-pi: installs new releases by itself, at night (ufoscene-update.timer).

* Only full releases: GitHub's "latest" release, which is never a pre-release. Only a newer one
  than the Pi runs. A Pi set up from a pre-release (v0.2.0-rc5) moves on to the full release that
  follows it (v0.2.0); a Pi built from a branch (dev-...) is left alone.
* Before installing, it keeps a copy of what runs. The new release is installed with the
  installer from its own bundle, then it has to come up within five minutes: the server answers
  with the new version, and the TV page reports again if it was running before.
* If it does not, the previous version is installed again (with its own installer) and that
  release is never tried again. A later release is tried as usual.
* Off with:  echo off | sudo tee /etc/ufoscene/auto-update
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "ulfmansson/ufoscene-pi"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "ufoscene-pi-bundle.tar.gz"
PREFIX = Path("/opt/ufoscene")
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/ufoscene-update"))
OPT_OUT = Path("/etc/ufoscene/auto-update")
SERVER = "http://127.0.0.1"
HEALTH_S = 300
TV_FRESH_S = 30  # the TV page reports every 5 s
UA = "ufoscene-pi-updater"

VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc\.?(\d+))?")


def parse(v: str | None) -> tuple | None:
    """v0.2.0 -> (0, 2, 0, big); v0.2.0-rc5 -> (0, 2, 0, 5): a release sorts after its rcs."""
    m = VERSION.fullmatch((v or "").strip())
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch), int(rc) if rc else 10**6)


def installed_version(prefix: Path) -> str:
    """The release this Pi runs: VERSION ("ufoscene-pi v0.2.0 (ufoscene abc1234)"), or server.py."""
    try:
        words = (prefix / "VERSION").read_text().split()
        if len(words) >= 2:
            return words[1]
    except OSError:
        pass
    try:
        m = re.search(r'(?m)^VERSION = "(.*)"$', (prefix / "server.py").read_text())
        return m.group(1) if m else ""
    except OSError:
        return ""


def bundle_from_install(prefix: Path, out: Path):
    """Pack an installed ufoscene-pi back into a bundle, the way make_bundle.sh lays it out:
    install.sh copies files/ into the prefix, plus ufo/ and VERSION."""
    with tarfile.open(out, "w:gz") as t:
        for entry in sorted(prefix.iterdir()):
            if entry.name in ("ufo", "VERSION", "install.sh"):
                t.add(entry, arcname=f"./{entry.name}")
            else:
                t.add(entry, arcname=f"./files/{entry.name}")


class Updater:
    def __init__(self, prefix: Path = PREFIX, state_dir: Path = STATE_DIR, opt_out: Path = OPT_OUT):
        self.prefix = prefix
        self.state_dir = state_dir
        self.previous = state_dir / "previous"
        self.status_path = state_dir / "status.json"
        self.opt_out = opt_out

    # ------------------------------------------------------------ the outside world (tests replace these)

    def latest(self) -> dict:
        req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def download(self, url: str, to: Path):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r, open(to, "wb") as f:
            shutil.copyfileobj(r, f)

    def install(self, installer: Path, bundle: Path) -> bool:
        try:
            p = subprocess.run(["bash", str(installer), "--bundle", str(bundle)], timeout=3600)
            return p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def server_state(self) -> dict:
        try:
            with urllib.request.urlopen(SERVER + "/api/state", timeout=5) as r:
                v = json.loads(r.read())
                return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def sleep(self, s: float):
        time.sleep(s)

    def now(self) -> float:
        return time.time()

    def say(self, text: str):
        print(text, flush=True)  # the journal

    # ------------------------------------------------------------ status

    def load(self) -> dict:
        try:
            v = json.loads(self.status_path.read_text())
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, state: dict, result: str, message: str, **kv):
        state.update(kv, result=result, message=message, checked=int(self.now()))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.chmod(0o644)  # server.py shows it on the remote
        tmp.replace(self.status_path)
        self.say(message)
        return result

    # ------------------------------------------------------------ one night

    def tv_alive(self, s: dict) -> bool:
        seen = s.get("tv_seen")
        return isinstance(seen, (int, float)) and self.now() - seen < TV_FRESH_S

    def healthy(self, tag: str, tv_before: bool, since: float) -> bool:
        deadline = self.now() + HEALTH_S
        while True:
            s = self.server_state()
            tv_ok = not tv_before or (isinstance(s.get("tv_seen"), (int, float)) and s["tv_seen"] >= since)
            if s.get("version") == tag and tv_ok:
                return True
            if self.now() >= deadline:
                return False
            self.sleep(10)

    def run(self) -> str:
        state = self.load()
        bad = [t for t in state.get("bad", []) if isinstance(t, str)]
        try:
            off = self.opt_out.read_text().strip().lower() in ("off", "no", "0", "false")
        except OSError:
            off = False
        running = installed_version(self.prefix)
        if off:
            return self.save(state, "off", "Automatic updates are off (/etc/ufoscene/auto-update).", running=running)
        if parse(running) is None:
            return self.save(state, "dev", f"Not a release ({running or 'unknown'}): not updated automatically.", running=running)
        try:
            rel = self.latest()
        except (OSError, ValueError) as e:
            return self.save(state, "error", f"Could not ask GitHub for the latest release: {e}", running=running)
        tag = str(rel.get("tag_name") or "")
        if rel.get("prerelease") or rel.get("draft") or parse(tag) is None:
            return self.save(state, "current", f"No full release to compare with ({tag or 'none'}).", running=running)
        if parse(tag) <= parse(running):
            return self.save(state, "current", f"Up to date: {running} (latest release {tag}).", running=running, latest=tag)
        if tag in bad:
            return self.save(state, "skipped", f"{tag} did not start here before: staying on {running}.", running=running, latest=tag)
        asset = next((a for a in rel.get("assets") or [] if a.get("name") == ASSET), None)
        if not asset or not str(asset.get("browser_download_url", "")).startswith("https://github.com/"):
            return self.save(state, "error", f"{tag} has no {ASSET}.", running=running, latest=tag)

        with tempfile.TemporaryDirectory(prefix="ufoscene-update.") as tmp:
            bundle = Path(tmp) / ASSET
            try:
                self.download(asset["browser_download_url"], bundle)
            except OSError as e:
                return self.save(state, "error", f"Could not download {tag}: {e}", running=running, latest=tag)
            digest = str(asset.get("digest") or "")
            if digest.startswith("sha256:") and hashlib.sha256(bundle.read_bytes()).hexdigest() != digest[7:]:
                return self.save(state, "error", f"{tag}: the download does not match its checksum.", running=running, latest=tag)
            new = Path(tmp) / "new"
            new.mkdir()
            try:
                with tarfile.open(bundle) as t:
                    if hasattr(tarfile, "data_filter"):
                        t.extractall(new, filter="data")
                    else:
                        t.extractall(new)
            except (OSError, tarfile.TarError) as e:
                return self.save(state, "error", f"{tag}: the bundle does not unpack: {e}", running=running, latest=tag)
            if not (new / "install.sh").is_file() or not (new / "files" / "server.py").is_file():
                return self.save(state, "error", f"{tag}: the bundle has no installer.", running=running, latest=tag)

            before = self.server_state()
            tv_before = self.tv_alive(before)
            if self.previous.exists():
                shutil.rmtree(self.previous)
            shutil.copytree(self.prefix, self.previous, symlinks=True)

            self.say(f"Installing {tag} (was {running})")
            started = self.now()
            if self.install(new / "install.sh", bundle) and self.healthy(tag, tv_before, started):
                return self.save(state, "updated", f"Updated from {running} to {tag}.", running=tag, latest=tag, previous=running)

            self.say(f"{tag} did not come up: going back to {running}")
            self.roll_back(Path(tmp))
            bad.append(tag)
            return self.save(state, "failed", f"{tag} did not start, so the Pi went back to {running}.",
                             running=installed_version(self.prefix), latest=tag, bad=bad[-10:])

    def roll_back(self, tmp: Path):
        old = tmp / "previous.tar.gz"
        if (self.previous / "install.sh").is_file():
            bundle_from_install(self.previous, old)
            if self.install(self.previous / "install.sh", old):
                return
        # no installer of its own (or it failed): put the files back and restart
        shutil.rmtree(self.prefix, ignore_errors=True)
        shutil.copytree(self.previous, self.prefix, symlinks=True)
        subprocess.run(["systemctl", "restart", "ufoscene-server.service", "ufoscene-kiosk.service"], check=False)


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        Updater().run()


if __name__ == "__main__":
    main()

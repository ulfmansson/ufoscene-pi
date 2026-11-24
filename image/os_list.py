#!/usr/bin/env python3
"""Write os_list.json for Raspberry Pi Imager's "content repository" setting.

    image/os_list.py <tag> <dir with ufoscene-*.img.xz> > os_list.json

Imager fetches it from https://github.com/ulfmansson/ufoscene-pi/releases/latest/download/os_list.json
and shows "ufoscene" as an OS; its settings (Wi-Fi, user, hostname) work as for Raspberry Pi OS,
because the image is Raspberry Pi OS Lite underneath.
"""

import datetime
import hashlib
import json
import lzma
import sys
from pathlib import Path

REPO = "ulfmansson/ufoscene-pi"
ICON = f"https://raw.githubusercontent.com/{REPO}/main/image/icon.png"

VARIANTS = {
    "arm64": {
        "name": "ufoscene (64-bit)",
        "description": "Amiga-style demos that dance to your music, on your TV, run from your phone. "
                       "For Raspberry Pi 3, 4, 400, 5, 500 and Zero 2 W.",
        "devices": ["pi5-64bit", "pi4-64bit", "pi3-64bit"],
    },
    "armhf": {
        "name": "ufoscene (32-bit, for Raspberry Pi 2)",
        "description": "Amiga-style demos that dance to your music, on your TV, run from your phone. "
                       "For Raspberry Pi 2 and 3; use the 64-bit one on newer models.",
        "devices": ["pi2-32bit", "pi3-32bit"],
    },
}


def digest(path: Path, unpack: bool) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    f = lzma.open(path) if unpack else open(path, "rb")
    with f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def main():
    tag, folder = sys.argv[1], Path(sys.argv[2])
    entries = []
    for arch, meta in VARIANTS.items():
        img = folder / f"ufoscene-{arch}.img.xz"
        if not img.exists():
            continue
        dl_size, dl_sha = digest(img, unpack=False)
        ex_size, ex_sha = digest(img, unpack=True)
        entries.append({
            **meta,
            "icon": ICON,
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{img.name}",
            "extract_size": ex_size,
            "extract_sha256": ex_sha,
            "image_download_size": dl_size,
            "image_download_sha256": dl_sha,
            "release_date": datetime.date.today().isoformat(),
            "init_format": "cloudinit-rpi",
        })
    json.dump({"os_list": entries}, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()

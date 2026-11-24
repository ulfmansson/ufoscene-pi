#!/usr/bin/env bash
# Pack what install.sh needs: our files plus a built ufoscene (web/ with ufo.wasm).
#
#   image/make_bundle.sh <path to a ufoscene checkout> <out.tar.gz> [version]
#
# The ufoscene checkout must already have its wasm built (cargo build -p ufo-demo --release
# --target wasm32-unknown-unknown); this copies it in.
set -euo pipefail
UFO=$(cd "$1" && pwd)
OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
VERSION=${3:-dev}
HERE=$(cd "$(dirname "$0")/.." && pwd)

WASM="$UFO/target/wasm32-unknown-unknown/release/ufo_demo.wasm"
[[ -f "$WASM" ]] || WASM="$UFO/web/ufo.wasm"
[[ -f "$WASM" ]] || { echo "no ufo.wasm: build ufoscene first" >&2; exit 1; }

S=$(mktemp -d "${TMPDIR:-/tmp}/ufoscene-pi.XXXXXX")
trap 'rm -rf "$S"' EXIT
cp -r "$HERE/files" "$S/files"
cp -r "$UFO/web" "$S/ufo"
cp "$WASM" "$S/ufo/ufo.wasm"
cp "$UFO/LICENSE" "$S/ufo/LICENSE"
UFO_REV=$(git -C "$UFO" rev-parse --short HEAD 2>/dev/null || echo unknown)
echo "ufoscene-pi $VERSION (ufoscene $UFO_REV)" > "$S/VERSION"
sed -i.bak "s/^VERSION = .*/VERSION = \"$VERSION\"/" "$S/files/server.py" && rm "$S/files/server.py.bak"
find "$S" \( -name __pycache__ -o -name '.?*' \) -prune -exec rm -rf {} +
tar -czf "$OUT" -C "$S" .
ls -la "$OUT"

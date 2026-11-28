#!/usr/bin/env bash
# Pack what install.sh needs: our files plus a built ufoscene (web/ with ufo.wasm), and
# ufo-native, the player that draws the demos without a browser.
#
#   image/make_bundle.sh <path to a ufoscene checkout> <out.tar.gz> [version]
#
# The ufoscene checkout must already have its wasm built (cargo build -p ufo-demo --release
# --target wasm32-unknown-unknown); this copies it in. ufo-native comes from
# target/pi-native/ (native/build-pi.sh in ufoscene); without it the bundle has the browser
# kiosk only, unless REQUIRE_NATIVE=1 (CI), which makes that an error.
#
# LASTFM_API_KEY (optional): a Last.fm API key built into the bundle, so people only type their
# Last.fm username on the remote for song names. CI takes it from the repository variable.
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
# the native player for both Pi families, with its systemd unit; install.sh picks this Pi's
NATIVE="$UFO/target/pi-native"
if [[ -f "$NATIVE/ufo-native-arm64" && -f "$NATIVE/ufo-native-armhf" && -f "$NATIVE/ufoscene-native.service" ]]; then
  mkdir -p "$S/native"
  install -m 755 "$NATIVE/ufo-native-arm64" "$NATIVE/ufo-native-armhf" "$S/native/"
  install -m 644 "$NATIVE/ufoscene-native.service" "$S/native/"
elif [[ -n "${REQUIRE_NATIVE:-}" ]]; then
  echo "no ufo-native in $NATIVE: run native/build-pi.sh in ufoscene first" >&2
  exit 1
else
  echo "note: no ufo-native in $NATIVE, so this bundle has the browser kiosk only" >&2
fi
# the installer travels with its files: the nightly update installs with the new release's own,
# and can go back with the previous one
cp "$HERE/install.sh" "$S/install.sh"
UFO_REV=$(git -C "$UFO" rev-parse --short HEAD 2>/dev/null || echo unknown)
echo "ufoscene-pi $VERSION (ufoscene $UFO_REV)" > "$S/VERSION"
sed -i.bak "s/^VERSION = .*/VERSION = \"$VERSION\"/" "$S/files/server.py" && rm "$S/files/server.py.bak"
if [[ -n "${LASTFM_API_KEY:-}" ]]; then
  [[ "$LASTFM_API_KEY" =~ ^[0-9a-f]{32}$ ]] || { echo "LASTFM_API_KEY is not a Last.fm API key" >&2; exit 1; }
  printf '%s\n' "$LASTFM_API_KEY" > "$S/files/lastfm-api-key"
fi
find "$S" \( -name __pycache__ -o -name '.?*' \) -prune -exec rm -rf {} +
tar -czf "$OUT" -C "$S" .
ls -la "$OUT"

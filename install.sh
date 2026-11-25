#!/usr/bin/env bash
# ufoscene-pi installer: turns Raspberry Pi OS Lite (Trixie or Bookworm, 32- or 64-bit) into a
# ufoscene box. It boots straight into ufoscene on the TV, takes a phone remote at
# http://<hostname>.local/, and shows up in the Spotify app as the speaker "UFOSCENE".
#
# On a Pi that already runs Raspberry Pi OS Lite:
#   curl -fsSL https://raw.githubusercontent.com/ulfmansson/ufoscene-pi/main/install.sh | sudo bash
#
# The image build runs the same script inside the image (--image).
#
# Options:
#   --image          building an image in a chroot: enable services but do not start anything
#   --bundle FILE    use this ufoscene-pi-bundle.tar.gz instead of downloading the latest release
#   --no-spotify     skip the Spotify Connect speaker
set -euo pipefail

REPO="ulfmansson/ufoscene-pi"
BUNDLE_URL="https://github.com/$REPO/releases/latest/download/ufoscene-pi-bundle.tar.gz"
PREFIX=/opt/ufoscene
KIOSK_USER=ufo

IMAGE=0
BUNDLE=""
SPOTIFY=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE=1 ;;
    --bundle) BUNDLE="$2"; shift ;;
    --no-spotify) SPOTIFY=0 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo bash install.sh)"
[[ -f /etc/os-release ]] && . /etc/os-release
[[ "${ID:-}" == "debian" || "${ID:-}" == "raspbian" ]] || die "this is for Raspberry Pi OS (Debian); found ${ID:-unknown}"
ARCH=$(dpkg --print-architecture)
[[ "$ARCH" == "arm64" || "$ARCH" == "armhf" ]] || die "this is for a Raspberry Pi (arm64/armhf); found $ARCH"
export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------------ files
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
if [[ -z "$BUNDLE" ]]; then
  say "Downloading ufoscene-pi"
  command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl ca-certificates; }
  curl -fsSL "$BUNDLE_URL" -o "$WORK/bundle.tar.gz"
  BUNDLE="$WORK/bundle.tar.gz"
fi
tar -xzf "$BUNDLE" -C "$WORK"
[[ -f "$WORK/files/server.py" && -f "$WORK/ufo/ufo.wasm" ]] || die "the bundle lacks files/server.py or ufo/ufo.wasm"

# ------------------------------------------------------------------ packages
say "Installing packages (this takes a while on a small Pi)"
apt-get update -qq
CHROMIUM=chromium
apt-cache show chromium >/dev/null 2>&1 || CHROMIUM=chromium-browser
apt-get install -y -qq --no-install-recommends \
  cage "$CHROMIUM" \
  pipewire pipewire-pulse wireplumber pulseaudio-utils \
  python3 python3-qrcode avahi-daemon libnss-mdns \
  fonts-dejavu-core curl ca-certificates

if [[ $SPOTIFY -eq 1 ]]; then
  say "Installing the Spotify Connect speaker (librespot from raspotify)"
  curl -fsSL https://dtcooper.github.io/raspotify/key.asc -o /usr/share/keyrings/raspotify_key.asc
  echo "deb [signed-by=/usr/share/keyrings/raspotify_key.asc] https://dtcooper.github.io/raspotify raspotify main" \
    > /etc/apt/sources.list.d/raspotify.list
  apt-get update -qq
  apt-get install -y -qq raspotify
  # raspotify's own service plays straight to ALSA as its own user; ours plays into the kiosk
  # user's PipeWire so ufoscene can listen to it.
  systemctl disable raspotify.service >/dev/null 2>&1 || true
  systemctl mask raspotify.service >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------ user
say "Creating the kiosk user '$KIOSK_USER'"
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/$KIOSK_USER --shell /bin/bash "$KIOSK_USER"
  passwd -l "$KIOSK_USER" >/dev/null
fi
for g in video render input audio; do getent group $g >/dev/null && usermod -aG $g "$KIOSK_USER"; done
KUID=$(id -u "$KIOSK_USER")
# a user manager (and so PipeWire) from boot, before anyone logs in
mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/$KIOSK_USER

# ------------------------------------------------------------------ install
say "Installing ufoscene into $PREFIX"
rm -rf "$PREFIX.new"
mkdir -p "$PREFIX.new"
cp -r "$WORK/files/." "$PREFIX.new/"
cp -r "$WORK/ufo" "$PREFIX.new/ufo"
[[ -f "$WORK/VERSION" ]] && cp "$WORK/VERSION" "$PREFIX.new/VERSION"
chmod 755 "$PREFIX.new/server.py" "$PREFIX.new/kiosk.sh" "$PREFIX.new/spotify-event.py"
rm -rf "$PREFIX.old"
[[ -d "$PREFIX" ]] && mv "$PREFIX" "$PREFIX.old"
mv "$PREFIX.new" "$PREFIX"
rm -rf "$PREFIX.old"

mkdir -p /var/cache/ufoscene-spotify && chown "$KIOSK_USER:" /var/cache/ufoscene-spotify

# sound through HDMI (the TV) before the headphone jack
mkdir -p /etc/wireplumber/wireplumber.conf.d
cp "$PREFIX/wireplumber/51-ufoscene-hdmi.conf" /etc/wireplumber/wireplumber.conf.d/

for unit in "$PREFIX"/systemd/*.service; do
  sed -e "s/@KUID@/$KUID/g" -e "s/@USER@/$KIOSK_USER/g" -e "s#@PREFIX@#$PREFIX#g" "$unit" \
    > "/etc/systemd/system/$(basename "$unit")"
done

BACKEND=alsa
if [[ $SPOTIFY -eq 1 ]] && librespot --backend '?' 2>&1 | grep -qi pulseaudio; then BACKEND=pulseaudio; fi
sed -i "s/@BACKEND@/$BACKEND/" /etc/systemd/system/ufoscene-spotify.service

# ------------------------------------------------------------------ boot
say "Booting straight into ufoscene"
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl enable ufoscene-server.service ufoscene-kiosk.service avahi-daemon.service
if [[ $SPOTIFY -eq 1 ]]; then systemctl enable ufoscene-spotify.service; else systemctl disable ufoscene-spotify.service >/dev/null 2>&1 || true; fi
systemctl set-default multi-user.target >/dev/null

# quiet boot: no rainbow square, no console text before the demos
CONFIG=/boot/firmware/config.txt; [[ -f $CONFIG ]] || CONFIG=/boot/config.txt
if [[ -f $CONFIG ]] && ! grep -q '^disable_splash=1' "$CONFIG"; then echo 'disable_splash=1' >> "$CONFIG"; fi

if [[ $IMAGE -eq 1 ]]; then
  # a fresh image is called ufoscene, so the remote is at http://ufoscene.local/
  # (Raspberry Pi Imager can still give it another name)
  echo ufoscene > /etc/hostname
  sed -i 's/127\.0\.1\.1.*/127.0.1.1\tufoscene/' /etc/hosts
  apt-get clean
  rm -rf /var/lib/apt/lists/*
  say "Done (image mode: nothing started)"
else
  systemctl daemon-reload
  systemctl restart ufoscene-server.service
  [[ $SPOTIFY -eq 1 ]] && systemctl restart ufoscene-spotify.service || true
  systemctl restart ufoscene-kiosk.service
  say "Done. ufoscene is starting on the TV; the remote is at http://$(hostname).local/"
fi

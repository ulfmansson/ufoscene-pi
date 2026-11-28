#!/usr/bin/env bash
# ufoscene-pi installer: turns Raspberry Pi OS Lite (Trixie or Bookworm, 32- or 64-bit) into a
# ufoscene box. It boots straight into ufoscene on the TV, takes a phone remote at
# http://<hostname>.local/, and is a speaker called "UFOSCENE" for phones over Bluetooth and AirPlay.
#
# On a Pi that already runs Raspberry Pi OS Lite:
#   curl -fsSL https://raw.githubusercontent.com/ulfmansson/ufoscene-pi/main/install.sh | sudo bash
#
# Every night it installs a newer full release by itself, and goes back if that one does not
# start (files/update.py). Off with: echo off | sudo tee /etc/ufoscene/auto-update
#
# The remote can change the Pi's Wi-Fi, and with no Wi-Fi it opens a setup network (NetworkManager).
#
# The TV shows ufoscene in Chromium (the default) or in ufo-native, the player without a browser:
#   sudo /opt/ufoscene/player.sh native     (or browser; kept in /etc/ufoscene/player)
#
# The image build runs the same script inside the image (--image).
#
# Options:
#   --image          building an image in a chroot: enable services but do not start anything
#   --bundle FILE    use this ufoscene-pi-bundle.tar.gz instead of downloading the latest release
#   --bundle-url URL download the bundle from here instead, e.g. a pre-release to try out
set -euo pipefail

REPO="ulfmansson/ufoscene-pi"
BUNDLE_URL="https://github.com/$REPO/releases/latest/download/ufoscene-pi-bundle.tar.gz"
PREFIX=/opt/ufoscene
KIOSK_USER=ufo

IMAGE=0
BUNDLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE=1 ;;
    --bundle) BUNDLE="$2"; shift ;;
    --bundle-url) BUNDLE_URL="$2"; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
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
# In three calls, not one: with everything in a single call (mpv included) apt ran out of memory
# working out what to install in the 32-bit image build (armhf under qemu, std::bad_alloc), and
# so might a Pi with little memory. The two big ones, Chromium and mpv, come on their own.
apt-get install -y -qq --no-install-recommends \
  cage \
  pipewire pipewire-pulse wireplumber pulseaudio-utils libspa-0.2-bluetooth rtkit \
  bluez python3-dbus python3-gi shairport-sync \
  python3 python3-qrcode avahi-daemon libnss-mdns iw \
  fonts-dejavu-core curl ca-certificates
apt-get install -y -qq --no-install-recommends "$CHROMIUM"
# mpv plays the radio for ufo-native, the player without a browser
apt-get install -y -qq --no-install-recommends mpv
# the Pi 3 and 4 radio needs Raspberry Pi's Bluetooth helper (already in Raspberry Pi OS)
if apt-cache show pi-bluetooth >/dev/null 2>&1; then apt-get install -y -qq --no-install-recommends pi-bluetooth; fi
# the Wi-Fi setup network: NetworkManager shares it with dnsmasq (Raspberry Pi OS since Bookworm)
if dpkg -s network-manager >/dev/null 2>&1; then apt-get install -y -qq --no-install-recommends dnsmasq-base; fi
# shairport-sync's own service plays as its own user; ours plays into the kiosk user's PipeWire
systemctl disable --now shairport-sync.service >/dev/null 2>&1 || true
systemctl mask shairport-sync.service >/dev/null 2>&1 || true

# Up to 0.1: a Spotify Connect speaker (librespot). Spotify no longer gives librespot the keys
# to play with (librespot-org/librespot#1649), so phones play over Bluetooth or AirPlay instead.
if [[ -f /etc/systemd/system/ufoscene-spotify.service ]]; then
  say "Removing the Spotify Connect speaker (Spotify stopped working with it)"
  systemctl disable --now ufoscene-spotify.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/ufoscene-spotify.service
  rm -rf /var/cache/ufoscene-spotify
fi
if dpkg -s raspotify >/dev/null 2>&1; then
  systemctl unmask raspotify.service >/dev/null 2>&1 || true
  apt-get purge -y -qq raspotify
  rm -f /etc/apt/sources.list.d/raspotify.list /usr/share/keyrings/raspotify_key.asc
fi

# ------------------------------------------------------------------ user
say "Creating the kiosk user '$KIOSK_USER'"
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/$KIOSK_USER --shell /bin/bash "$KIOSK_USER"
  passwd -l "$KIOSK_USER" >/dev/null
fi
for g in video render input audio bluetooth; do getent group $g >/dev/null && usermod -aG $g "$KIOSK_USER"; done
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
[[ -f "$WORK/install.sh" ]] && install -m 755 "$WORK/install.sh" "$PREFIX.new/install.sh"
chmod 755 "$PREFIX.new/server.py" "$PREFIX.new/kiosk.sh" "$PREFIX.new/phone.py" "$PREFIX.new/wifi.py" "$PREFIX.new/update.py" \
  "$PREFIX.new/player.sh"
# ufo-native, the player without a browser: this Pi's binary from the bundle (none in older ones)
if [[ -f "$WORK/native/ufo-native-$ARCH" ]]; then
  mkdir -p "$PREFIX.new/native"
  install -m 755 "$WORK/native/ufo-native-$ARCH" "$PREFIX.new/native/ufo-native"
  install -m 644 "$WORK/native/ufoscene-native.service" "$PREFIX.new/native/ufoscene-native.service"
fi
rm -rf "$PREFIX.old"
[[ -d "$PREFIX" ]] && mv "$PREFIX" "$PREFIX.old"
mv "$PREFIX.new" "$PREFIX"
rm -rf "$PREFIX.old"

# the phone speaker: AirPlay settings, BlueZ access for phone.py, and a speaker icon on phones
mkdir -p /etc/ufoscene
sed "s/@KUID@/$KUID/g" "$PREFIX/shairport-sync.conf" > /etc/ufoscene/shairport-sync.conf
sed "s/@USER@/$KIOSK_USER/g" "$PREFIX/dbus/ufoscene-bluetooth.conf" > /etc/dbus-1/system.d/ufoscene-bluetooth.conf
# a Last.fm API key built into the bundle, for song names (the remote can set its own)
if [[ -f "$PREFIX/lastfm-api-key" ]]; then install -m 644 "$PREFIX/lastfm-api-key" /etc/ufoscene/lastfm-api-key; fi
if [[ -f /etc/bluetooth/main.conf ]]; then
  # major class audio/video, minor loudspeaker
  sed -i -E 's/^#? *Class *=.*/Class = 0x000414/' /etc/bluetooth/main.conf
  grep -q '^Class = 0x000414' /etc/bluetooth/main.conf || sed -i '/^\[General\]/a Class = 0x000414' /etc/bluetooth/main.conf
fi

# the Wi-Fi setup network sends phones to the remote
if [[ -d /etc/NetworkManager ]]; then
  mkdir -p /etc/NetworkManager/dnsmasq-shared.d
  cp "$PREFIX/networkmanager/ufoscene-setup.conf" /etc/NetworkManager/dnsmasq-shared.d/ufoscene-setup.conf
fi

# sound through HDMI (the TV) before the headphone jack, at full volume
mkdir -p /etc/wireplumber/wireplumber.conf.d
cp "$PREFIX"/wireplumber/*.conf /etc/wireplumber/wireplumber.conf.d/

for unit in "$PREFIX"/systemd/*.service "$PREFIX"/systemd/*.timer; do
  sed -e "s/@KUID@/$KUID/g" -e "s/@USER@/$KIOSK_USER/g" -e "s#@PREFIX@#$PREFIX#g" "$unit" \
    > "/etc/systemd/system/$(basename "$unit")"
done
if [[ -x "$PREFIX/native/ufo-native" ]]; then
  sed -e "s/@KUID@/$KUID/g" -e "s/@USER@/$KIOSK_USER/g" -e "s#@BIN@#$PREFIX/native/ufo-native#g" \
    "$PREFIX/native/ufoscene-native.service" > /etc/systemd/system/ufoscene-native.service
else
  systemctl disable ufoscene-native.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/ufoscene-native.service
fi

# What the TV shows ufoscene with: the browser kiosk, or ufo-native when /etc/ufoscene/player
# says native and this Pi can run it (not a Pi 1 or Zero: ARMv6, and too slow for either)
PLAYER=browser
[[ "$(tr -d '[:space:]' < /etc/ufoscene/player 2>/dev/null || true)" == native ]] && PLAYER=native
if [[ $PLAYER == native ]] && ! "$PREFIX/player.sh" can-run-native; then
  say "ufo-native cannot run on this Pi: the TV uses the browser"
  PLAYER=browser
fi
if [[ $PLAYER == native ]]; then PLAYER_UNIT=ufoscene-native.service; OTHER_UNIT=ufoscene-kiosk.service
else PLAYER_UNIT=ufoscene-kiosk.service; OTHER_UNIT=ufoscene-native.service; fi

# ------------------------------------------------------------------ boot
say "Booting straight into ufoscene"
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl disable "$OTHER_UNIT" >/dev/null 2>&1 || true
systemctl enable ufoscene-server.service "$PLAYER_UNIT" avahi-daemon.service \
  bluetooth.service ufoscene-phone.service ufoscene-airplay.service ufoscene-wifi.service
systemctl enable ufoscene-update.timer
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
  systemctl reload dbus.service >/dev/null 2>&1 || true
  systemctl restart bluetooth.service >/dev/null 2>&1 || true
  systemctl restart ufoscene-server.service
  systemctl restart ufoscene-phone.service ufoscene-airplay.service ufoscene-wifi.service
  # starting one player stops the other (the native unit has Conflicts= with the kiosk)
  systemctl restart "$PLAYER_UNIT"
  systemctl start ufoscene-update.timer
  say "Done. ufoscene is starting on the TV; the remote is at http://$(hostname).local/"
fi

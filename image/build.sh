#!/usr/bin/env bash
# Build a ufoscene SD-card image from the official Raspberry Pi OS Lite image by running
# install.sh inside it. Linux only, as root (GitHub Actions runs it; see .github/workflows).
#
#   sudo image/build.sh <arm64|armhf> <ufoscene-pi-bundle.tar.gz> <out.img.xz>
#
# arm64 builds natively on an arm64 machine; armhf needs qemu-user-static with binfmt.
set -euo pipefail

ARCH=$1
BUNDLE=$(realpath "$2")
OUT=$(realpath -m "$3")
HERE=$(cd "$(dirname "$0")/.." && pwd)

case "$ARCH" in
  arm64) BASE_URL=https://downloads.raspberrypi.com/raspios_lite_arm64_latest ;;
  armhf) BASE_URL=https://downloads.raspberrypi.com/raspios_lite_armhf_latest ;;
  *) echo "arch must be arm64 or armhf" >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

W=$(mktemp -d)
M="$W/root"
LOOP=""
cleanup() {
  set +e
  for p in dev/pts dev proc sys boot/firmware ""; do mountpoint -q "$M/$p" && umount -l "$M/$p"; done
  [[ -n "$LOOP" ]] && losetup -d "$LOOP"
  rm -rf "$W"
}
trap cleanup EXIT

echo "==> Downloading Raspberry Pi OS Lite ($ARCH)"
curl -fsSL "$BASE_URL" -o "$W/base.img.xz"
xz -d "$W/base.img.xz"
IMG="$W/base.img"

echo "==> Making room for Chromium"
truncate -s +1536M "$IMG"
parted -s "$IMG" resizepart 2 100%
LOOP=$(losetup -fP --show "$IMG")
e2fsck -fy "${LOOP}p2" || true
resize2fs "${LOOP}p2"

mkdir -p "$M"
mount "${LOOP}p2" "$M"
mount "${LOOP}p1" "$M/boot/firmware"
mount --bind /dev "$M/dev"
mount --bind /dev/pts "$M/dev/pts"
mount -t proc proc "$M/proc"
mount -t sysfs sys "$M/sys"

# name resolution inside the chroot, and no services starting there
RESOLV_SAVED=""
if [[ -L "$M/etc/resolv.conf" || -e "$M/etc/resolv.conf" ]]; then
  mv "$M/etc/resolv.conf" "$M/etc/resolv.conf.ufoscene-saved"
  RESOLV_SAVED=1
fi
cp -L /etc/resolv.conf "$M/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$M/usr/sbin/policy-rc.d"
chmod +x "$M/usr/sbin/policy-rc.d"

echo "==> Installing ufoscene in the image"
mkdir -p "$M/tmp/ufoscene"
cp "$HERE/install.sh" "$M/tmp/ufoscene/install.sh"
cp "$BUNDLE" "$M/tmp/ufoscene/bundle.tar.gz"
chroot "$M" /bin/bash /tmp/ufoscene/install.sh --image --bundle /tmp/ufoscene/bundle.tar.gz

echo "==> Cleaning up"
rm -rf "$M/tmp/ufoscene" "$M/usr/sbin/policy-rc.d"
rm -f "$M/etc/resolv.conf"
[[ -n "$RESOLV_SAVED" ]] && mv "$M/etc/resolv.conf.ufoscene-saved" "$M/etc/resolv.conf"
# free blocks become holes, so the compressed image stays small
fstrim -v "$M" || true

for p in dev/pts dev proc sys boot/firmware; do umount "$M/$p"; done
umount "$M"
e2fsck -fy "${LOOP}p2" || true
losetup -d "$LOOP"
LOOP=""

echo "==> Compressing"
xz -T0 -6 -c "$IMG" > "$OUT"
ls -la "$OUT"

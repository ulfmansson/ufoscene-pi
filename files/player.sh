#!/usr/bin/env bash
# What the TV shows ufoscene with: Chromium in kiosk mode (the default), or ufo-native, the
# player that draws the demos straight to the screen without a browser (a few MB instead of
# several hundred; no Spotify or artist photos yet, see ufoscene's native/README.md).
#
#   sudo /opt/ufoscene/player.sh native     switch to ufo-native
#   sudo /opt/ufoscene/player.sh browser    back to Chromium
#   /opt/ufoscene/player.sh status          which one is on
#
# The choice is kept in /etc/ufoscene/player, so the nightly update keeps it.
set -euo pipefail
PREFIX=$(cd "$(dirname "$0")" && pwd)
SETTING=/etc/ufoscene/player

# ufo-native is built for ARMv7 and arm64: a Pi 2 and newer
can_run_native() {
  [[ -x "$PREFIX/native/ufo-native" && -f /etc/systemd/system/ufoscene-native.service ]] || return 1
  case "$(uname -m)" in aarch64 | armv7l | armv8l) return 0 ;; *) return 1 ;; esac
}

current() { [[ "$(tr -d '[:space:]' < "$SETTING" 2>/dev/null || true)" == native ]] && echo native || echo browser; }

switch_to() {
  local want=$1 on off
  [[ $EUID -eq 0 ]] || { echo "run it with sudo" >&2; exit 1; }
  if [[ $want == native ]]; then
    can_run_native || { echo "ufo-native is not installed for this Pi (or the Pi is too old for it)" >&2; exit 1; }
    on=ufoscene-native.service; off=ufoscene-kiosk.service
  else
    on=ufoscene-kiosk.service; off=ufoscene-native.service
  fi
  mkdir -p "$(dirname "$SETTING")"
  echo "$want" > "$SETTING"
  systemctl disable "$off" >/dev/null 2>&1 || true
  systemctl enable "$on" >/dev/null
  systemctl stop "$off" >/dev/null 2>&1 || true
  systemctl restart "$on"
  echo "The TV now shows ufoscene with the $want player. Log: journalctl -u ${on%.service} -f"
}

case "${1:-}" in
  native | browser) switch_to "$1" ;;
  status)
    echo "player: $(current)$(can_run_native || echo ' (ufo-native is not available on this Pi)')"
    for u in ufoscene-kiosk ufoscene-native; do
      echo "  $u: $(systemctl is-active "$u.service" 2>/dev/null || true)"
    done
    ;;
  can-run-native) can_run_native ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

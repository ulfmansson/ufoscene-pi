#!/usr/bin/env bash
# Started by cage (ufoscene-kiosk.service) on the TV as the kiosk user: Chromium, fullscreen,
# showing the TV page from the server on this Pi.
set -u
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# wait for the local server
for _ in $(seq 1 60); do
  curl -fs http://127.0.0.1/healthz >/dev/null && break
  sleep 1
done

CHROMIUM=$(command -v chromium || command -v chromium-browser)
PROFILE="$HOME/.config/ufoscene-chromium"
mkdir -p "$PROFILE"
# never show "Chromium didn't shut down correctly" after a power cut
sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true

exec "$CHROMIUM" \
  --kiosk --noerrdialogs --disable-infobars --no-first-run --no-default-browser-check \
  --ozone-platform=wayland \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  --disable-session-crashed-bubble --disable-features=Translate,MediaRouter \
  --check-for-update-interval=31536000 --password-store=basic \
  --user-data-dir="$PROFILE" \
  http://127.0.0.1/tv

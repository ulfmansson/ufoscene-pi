# ufoscene-pi

Turns a Raspberry Pi into a **[ufoscene](https://ufoscene.net) box**: 100 Amiga-style demos that
dance to your music, fullscreen on your TV, controlled from your phone.

- It boots straight into the demos, with no keyboard or mouse needed.
- **Phone remote**: scan the QR code on the TV, or open `http://ufoscene.local/` on a phone on
  the same Wi-Fi.
- **Music**: built-in internet radio; **Spotify** (the Pi shows up as the speaker **UFOSCENE**
  in your Spotify app, Premium needed); or a USB microphone or line in.
- Everything runs on the Pi itself. No account, no cloud.

> ufoscene Pis are given away for a donation to the defence of Ukraine at
> **pi.ufoscene.net** (coming soon). If you set up your own Pi with this, please
> donate too. 💙💛

## Install

### With Raspberry Pi Imager (recommended)

You only need the official [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
(version 2.0 or newer).

1. Open Imager, go to **App Options → Content Repository**, choose **Use custom URL** and paste:

   ```
   https://github.com/ulfmansson/ufoscene-pi/releases/latest/download/os_list.json
   ```

   From a terminal, the same thing is `rpi-imager --repo <that URL>`.
2. Pick your Raspberry Pi model, then **ufoscene** as the operating system, then your SD card.
3. In the customisation step, set:
   - **Hostname:** `ufoscene` (then the remote is at `http://ufoscene.local/`)
   - **Username and password**, so you can log in over SSH if you ever need to
   - **Wi-Fi:** your network name and password (not needed with an Ethernet cable)
4. Write the card, put it in the Pi, connect HDMI to the TV and power it up. The first boot
   takes a few minutes; then the demos start, with a QR code for the remote.

### On a Pi that already runs Raspberry Pi OS Lite

On a Pi with Raspberry Pi OS **Lite** (Trixie or Bookworm, 32- or 64-bit), with no desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/ulfmansson/ufoscene-pi/main/install.sh | sudo bash
```

Add `-s -- --no-spotify` after `bash` to leave out the Spotify speaker.

### Download the image yourself

The `.img.xz` files are on the [releases page](https://github.com/ulfmansson/ufoscene-pi/releases):

- `ufoscene-arm64` for Raspberry Pi 3, 4, 400, 5, 500 and Zero 2 W
- `ufoscene-armhf` for Raspberry Pi 2

Write one with Imager (**Use custom**) or any other tool.

## Which Pi?

The demos need about **25 frames per second** to look right. Press **Self-test** on the remote
to see what your Pi manages.

| Model | Expected |
|---|---|
| Pi 5 / 500, Pi 4 / 400 | smooth |
| Pi 3 / 3B+ / Zero 2 W | being measured |
| Pi 2 | being measured |
| Pi 1, Zero / Zero W | too slow |

## Using it

| Remote | What it does |
|---|---|
| ◀ ⟳ ▶ | previous, random and next demo |
| Next part | next part of the current demo |
| ⏮ Song / Song ⏭ | previous or next radio station or Spotify song |
| Demo # | jump to a demo (1–100) |
| Radio · Spotify · Mic / line in | where the music comes from |
| Info · TV mode · QR code | ufoscene's info overlay, 30 fps TV mode, the connect screen |
| Settings · Self-test · Reload | ufoscene's own panel, the fps meter, restart the TV page |

**Spotify**: open the Spotify app on any device on the same network, tap the speaker icon and
pick **UFOSCENE**. The Pi plays the music through the TV, and the demos follow it.

## How it works

| Part | Where |
|---|---|
| `install.sh` | installs everything on Raspberry Pi OS Lite. The image build runs the same script. |
| `files/server.py` | a small server on port 80 (Python, standard library). It serves ufoscene, the TV page and the remote, and passes remote commands to the TV over server-sent events. |
| `files/www/tv.*` | the TV page. It runs ufoscene in a frame and presses its keys for the remote. |
| `files/www/remote.*` | the phone remote |
| `files/kiosk.sh`, `files/systemd/` | Chromium in kiosk mode under [cage](https://github.com/cage-kiosk/cage) on the TV, and the Spotify Connect speaker ([librespot](https://github.com/librespot-org/librespot), packaged by [raspotify](https://github.com/dtcooper/raspotify)) |
| `image/` | builds the SD-card images from Raspberry Pi OS Lite, plus the image list for Imager |

When the music comes from Spotify or the mic, PipeWire's default input is pointed at the right
place: for Spotify, the monitor of the TV output. ufoscene's microphone mode then hears it.

Troubleshooting over SSH:

```bash
journalctl -u ufoscene-kiosk -u ufoscene-server -u ufoscene-spotify -b
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

A tag `v*` builds the images on GitHub Actions and publishes a release.

## Licence

MIT, like ufoscene. See [LICENSE](LICENSE).

# ufoscene-pi

Turns a Raspberry Pi into a **[ufoscene](https://ufoscene.net) box**: 100 Amiga-style demos that
dance to your music, fullscreen on your TV, controlled from your phone.

- It boots straight into the demos, with no keyboard or mouse needed.
- **Phone remote**: scan the QR code on the TV, or open `http://ufoscene.local/` on a phone on
  the same Wi-Fi.
- **Music**: built-in internet radio; **your phone** (Spotify or any other app) over
  **Bluetooth** or **AirPlay**, where the Pi is the speaker **UFOSCENE**; or a USB microphone or
  line in. The demos follow the songs.
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
   - **Wi-Fi:** your network name and password (not needed with an Ethernet cable). If you skip
     it, or the Wi-Fi changes later, the Pi opens a setup network after two minutes (see
     **Wi-Fi** below).
4. Write the card, put it in the Pi, connect HDMI to the TV and power it up. The first boot
   takes a few minutes; then the demos start, with a QR code for the remote.

### On a Pi that already runs Raspberry Pi OS Lite

On a Pi with Raspberry Pi OS **Lite** (Trixie or Bookworm, 32- or 64-bit), with no desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/ulfmansson/ufoscene-pi/main/install.sh | sudo bash
```

To try a pre-release, add `-s -- --bundle-url` and the bundle's address from the
[releases page](https://github.com/ulfmansson/ufoscene-pi/releases) after `bash`. Run the same
command again to update.

### Download the image yourself

The `.img.xz` files are on the [releases page](https://github.com/ulfmansson/ufoscene-pi/releases):

- `ufoscene-arm64` for Raspberry Pi 3, 4, 400, 5, 500 and Zero 2 W
- `ufoscene-armhf` for Raspberry Pi 2

Write one with Imager (**Use custom**) or any other tool.

### Updates

A Pi updates itself: every night, at a random time between 3 and 5, it looks for a newer **full
release** (never a pre-release) and installs it. It keeps the version it ran; if the new one does
not come up within five minutes (the server answers with the new version, and the TV page runs
again), it goes back to that version and never tries that release again. The remote's footer
says what happened. A Pi set up from a pre-release moves on to the full release that follows it;
one built from a branch is left alone.

Off:  `echo off | sudo tee /etc/ufoscene/auto-update`. Now:  `sudo systemctl start
ufoscene-update.service`, then `journalctl -u ufoscene-update` for what it did.

A Pi on an older version (up to v0.2.0-rc6) has no updater yet: update it once by hand with the
command above.

### Without a browser

The TV shows ufoscene in Chromium. Since v0.2.0 the Pi also has **ufo-native**, the same demos
compiled for the Pi's CPU and drawn straight to the screen: a few MB of memory instead of
Chromium's several hundred, which helps a Pi 2, 3 or Zero 2 W. The remote works the same; Spotify,
Last.fm song names and artist photos are not in it yet. Over SSH:

```bash
sudo /opt/ufoscene/player.sh native
```

```bash
sudo /opt/ufoscene/player.sh browser
```

The choice is kept in `/etc/ufoscene/player`, so the nightly update keeps it. A Pi 1 or Zero
(ARMv6) has the browser only.

## Which Pi?

The demos need about **25 frames per second** to look right. Press **Self-test** on the remote
to see what your Pi manages.

| Model | Expected |
|---|---|
| Pi 5 / 500, Pi 4 / 400 | smooth |
| Pi 3 / 3B+ / Zero 2 W | being measured |
| Pi 2 | being measured |
| Pi 1, Zero / Zero W | too slow |

The first start on a Pi 2 takes a minute or two before the demo and the music begin.

## Using it

| Remote | What it does |
|---|---|
| ◀ ⟳ ▶ | previous, random and next demo |
| Next part | next part of the current demo |
| ⏮ Song / Song ⏭ | previous or next radio station |
| 🎸 Concert · 🇺🇦 Ukraine | ufoscene's 30-minute pixel concert or 10-minute Ukraine at Eurovision show (keys C and U); pressed again it ends. With Spotify control (below) the phone also plays the show's setlist in order. The Ukraine show puts the flag and ukraine.ufoscene.net (with a QR code) in a corner of the TV. Needs a bundle with ufoscene's shows in it |
| Demo # | jump to a demo (1–100) |
| Radio · Phone · Mic / line in | where the music comes from |
| Pair Bluetooth | lets a new phone connect over Bluetooth for two minutes |
| Info · TV mode · QR code | ufoscene's info overlay, 30 fps TV mode, the connect screen |
| Settings · Self-test · Reload | ufoscene's own panel, the fps meter, restart the TV page |
| 🛠 Pi info & troubleshooting | what the Pi sees (Wi-Fi, AirPlay, sound, TV, temperature, power), and step-by-step guides when something doesn't work (`http://ufoscene.local/diag`) |

**Music from your phone** (Spotify, YouTube Music, anything that plays sound):

- **iPhone, iPad, Mac**: AirPlay to **UFOSCENE** (on the same Wi-Fi).
- **Android** (or any phone): press **Pair Bluetooth** on the remote, then pick **UFOSCENE** in
  the phone's Bluetooth settings. No PIN. Next time the phone connects by itself. Needs a Pi with
  Bluetooth: a Pi 3, 4 or 5, or a Pi 2 with a USB Bluetooth adapter.

The music plays through the TV at full level (use the TV's remote for the volume; the phone's
volume buttons don't change it); ufoscene switches to it, starts a new demo per song and shows the
artist and title. Spotify Free works too.

**Song names from anywhere (Last.fm)**: Spotify on a Mac or PC, and a phone that sends no track
info, give the Pi only the sound. Link Spotify to Last.fm
([last.fm/settings/applications](https://www.last.fm/settings/applications)), then type your
Last.fm username under **Song names** on the remote: ufoscene shows what you play, from any
device, within about 10 seconds. Images built with the repository variable `LASTFM_API_KEY` have
an API key built in; otherwise paste your own
([last.fm/api/account/create](https://www.last.fm/api/account/create), free).

**Playlists**: the remote lists ufoscene's playlists. Tapping one opens it in Spotify on the
phone and switches the Pi to the phone; press play and pick UFOSCENE.

**Spotify control (optional, Premium)**: with your own Spotify app (a Client ID from
[developer.spotify.com](https://developer.spotify.com/dashboard), redirect URI
`https://pi.ufoscene.net/spotify`), the remote shows what plays, skips songs and starts the
playlists without leaving the page. 🎸 Concert and 🇺🇦 Ukraine then start their setlists (the
Queen concert, Ukraine's three Eurovision winners) in order on the phone, and switch the Pi to the
phone; send the sound to UFOSCENE as usual. The login runs in the phone's browser and the tokens stay on
the phone; the Pi only keeps the Client ID. See [`docs/spotify-bounce/`](docs/spotify-bounce/)
for the redirect page. Off until you set it up. (Up to 0.1 the Pi was a Spotify Connect speaker, but
Spotify no longer lets [librespot](https://github.com/librespot-org/librespot/issues/1649) play.)

**Wi-Fi**: under **Wi-Fi** on the remote, give the Pi a new network name and password. Press
**Show code on TV** and type the six digits the TV shows: anyone on your network can open the
remote, but only someone at the TV can change the Wi-Fi. The Pi keeps its old network until the
new one has connected; with a wrong password nothing changes. If the new network is another one,
join it with the phone too and open `http://ufoscene.local/` again.

**No Wi-Fi** (the router got a new password, the Pi moved house, or Imager had no Wi-Fi): after
two minutes without a known network the Pi opens its own network, **ufoscene-setup**, and the TV
shows its password and two QR codes. Scan the first to join it, the second to open the remote
(`http://10.42.0.1/`; most phones open it by themselves), and give your Wi-Fi under **Wi-Fi**.
Every five minutes with no phone on it, the Pi tries its old Wi-Fi again, so a router that was
only restarting brings it back by itself. With a network cable plugged in, it never opens the
setup network. Needs NetworkManager (Raspberry Pi OS Bookworm and later).

## How it works

| Part | Where |
|---|---|
| `install.sh` | installs everything on Raspberry Pi OS Lite. The image build runs the same script. |
| `files/server.py` | a small server on port 80 (Python, standard library). It serves ufoscene, the TV page and the remote, and passes remote commands to the TV over server-sent events. |
| `files/diag.py`, `files/www/diag.*` | the troubleshooting page: read-only facts from `/proc`, `/sys`, `systemctl`, `pactl`, `iw`, `ip` and `vcgencmd` (no passwords, keys or logs), and the guides that use them |
| `files/update.py` | the nightly update (`ufoscene-update.timer`, as root): installs a newer full release with the installer from its own bundle, and goes back to the previous version if the new one does not start. |
| `files/wifi.py` | runs as root: joins the network the remote gave (going back to the old one if it fails) and opens the setup network when the Pi is offline. It only takes requests from `server.py` on the Pi itself, through NetworkManager's `nmcli`. |
| `files/www/tv.*` | the TV page. It runs ufoscene in a frame and presses its keys for the remote. |
| `files/www/remote.*` | the phone remote |
| `files/kiosk.sh`, `files/systemd/` | Chromium in kiosk mode under [cage](https://github.com/cage-kiosk/cage) on the TV |
| `native/` in the bundle, `files/player.sh` | ufo-native, the player without a browser (built from ufoscene's `native/`), and the switch between it and the kiosk |
| `files/phone.py`, `files/shairport-sync.conf` | the phone speaker: Bluetooth pairing and now-playing (BlueZ), AirPlay ([shairport-sync](https://github.com/mikebrady/shairport-sync)) |
| `image/` | builds the SD-card images from Raspberry Pi OS Lite, plus the image list for Imager |

Sound goes to the TV over HDMI: a WirePlumber rule (`files/wireplumber/`) puts HDMI ahead of the
3.5 mm jack, which is still used when the screen has no speakers.

When the music comes from a phone or the mic, PipeWire's default input is pointed at the right
place: for a phone, the monitor of the TV output. ufoscene's microphone mode then hears it.

Troubleshooting: open **🛠 Pi info & troubleshooting** on the remote first. Over SSH:

```bash
journalctl -u ufoscene-kiosk -u ufoscene-native -u ufoscene-server -u ufoscene-phone -u ufoscene-airplay -b
```

No sound? See where it goes (the HDMI one should be the default):

```bash
sudo -u ufo XDG_RUNTIME_DIR=/run/user/$(id -u ufo) pactl list short sinks
sudo -u ufo XDG_RUNTIME_DIR=/run/user/$(id -u ufo) pactl get-default-sink
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

A tag `v*` builds the images on GitHub Actions and publishes a release. Every bundle carries both
players: CI builds ufoscene's wasm and `native/build-pi.sh all`, and `image/make_bundle.sh` (with
`REQUIRE_NATIVE=1`) refuses a bundle without the native binaries.

## Licence

MIT, like ufoscene. See [LICENSE](LICENSE).

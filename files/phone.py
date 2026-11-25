#!/usr/bin/env python3
"""ufoscene-pi: the Pi as a speaker for phones, over Bluetooth and AirPlay.

The sound itself needs nothing from here: a phone's Bluetooth audio is played by PipeWire, and
AirPlay by shairport-sync into PipeWire, both to the TV. This program does the rest:

* Bluetooth: names the Pi UFOSCENE, keeps it hidden and unpairable except for two minutes after
  "Pair" on the remote, accepts phones during that window (no PIN, like a speaker), trusts them
  so they come back on their own, and reads what they play (AVRCP) from BlueZ.
* AirPlay: reads artist, title and play/stop from shairport-sync's metadata pipe.

What is playing goes to the server (/api/nowplaying), which hands it to the TV page; ufoscene
then starts a new demo per song and shows the artist and title. Runs as the kiosk user.
"""

import base64
import json
import os
import re
import stat
import sys
import threading
import time
import urllib.request

SERVER = os.environ.get("UFO_SERVER", "http://127.0.0.1")
NAME = os.environ.get("UFO_SPEAKER_NAME", "UFOSCENE")
PAIR_SECONDS = 120
AIRPLAY_PIPE = os.environ.get("UFO_AIRPLAY_PIPE", f"/run/user/{os.getuid()}/airplay-metadata")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def post(path: str, body: dict):
    req = urllib.request.Request(
        SERVER + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError as e:
        log(f"server: {path}: {e}")


# ------------------------------------------------------------------ AirPlay metadata

ITEM = re.compile(
    r"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>"
    r"(?:\s*<data encoding=\"base64\">\s*([^<]*)</data>)?\s*</item>",
    re.S,
)


def parse_items(buf: str):
    """Split shairport-sync's metadata stream into (type, code, data bytes) items.

    Returns the items and the unparsed rest of the buffer (an item still being written).
    """
    items = []
    end = 0
    for m in ITEM.finditer(buf):
        typ = bytes.fromhex(m.group(1)).decode("latin-1")
        code = bytes.fromhex(m.group(2)).decode("latin-1")
        data = base64.b64decode(m.group(4)) if m.group(4) else b""
        items.append((typ, code, data))
        end = m.end()
    rest = buf[end:]
    if len(rest) > 1 << 20:  # a broken stream: start over rather than grow for ever
        rest = ""
    return items, rest


class AirplayTracker:
    """Turns metadata items into now-playing updates for the server."""

    def __init__(self):
        self.artist = ""
        self.title = ""
        self.playing = None

    def feed(self, typ: str, code: str, data: bytes):
        if typ == "core" and code == "asar":
            self.artist = data.decode("utf-8", "replace")
        elif typ == "core" and code == "minm":
            self.title = data.decode("utf-8", "replace")
        elif typ == "ssnc" and code == "mdst":  # a new set of metadata begins
            self.artist = self.title = ""
        elif typ == "ssnc" and code == "mden":  # ... and is complete
            if self.artist or self.title:
                return {"source": "airplay", "artist": self.artist, "title": self.title,
                        "playing": self.playing is not False}
        elif typ == "ssnc" and code in ("pbeg", "prsm"):
            self.playing = True
            return {"source": "airplay", "playing": True}
        elif typ == "ssnc" and code in ("pend", "pfls"):
            if self.playing is not False:
                self.playing = False
                return {"source": "airplay", "playing": False}
        return None


def airplay_loop(pipe=AIRPLAY_PIPE):
    """Read the metadata pipe for ever. shairport-sync writes it while a phone plays."""
    tracker = AirplayTracker()
    while True:
        try:
            if not os.path.exists(pipe):
                os.mkfifo(pipe, 0o600)
            elif not stat.S_ISFIFO(os.stat(pipe).st_mode):
                log(f"airplay: {pipe} is not a pipe")
                time.sleep(60)
                continue
            fd = os.open(pipe, os.O_RDONLY)  # waits until shairport-sync opens it
            try:
                buf = ""
                while chunk := os.read(fd, 4096):  # b"" when shairport-sync lets go
                    buf += chunk.decode("latin-1")  # the stream is ASCII; data is base64
                    items, buf = parse_items(buf)
                    for item in items:
                        update = tracker.feed(*item)
                        if update:
                            post("/api/nowplaying", update)
            finally:
                os.close(fd)
        except OSError as e:
            log(f"airplay: {e}")
            time.sleep(5)


# ------------------------------------------------------------------ commands from the remote

def command_loop(on_pair):
    """Follow the server's event stream, the same one the TV page gets, for "pair"."""
    while True:
        try:
            with urllib.request.urlopen(SERVER + "/api/events", timeout=60) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    if ev.get("cmd") == "pair":
                        on_pair()
        except OSError:
            pass
        time.sleep(3)


# ------------------------------------------------------------------ Bluetooth

def track_update(props: dict):
    """A now-playing update from a BlueZ MediaPlayer1's changed properties, or None."""
    out = {}
    track = props.get("Track")
    if track:
        out["artist"] = str(track.get("Artist", ""))
        out["title"] = str(track.get("Title", ""))
    status = props.get("Status")
    if status is not None:
        out["playing"] = str(status) == "playing"
    if not out:
        return None
    return {"source": "bluetooth", **out}


def bluetooth_main():
    """Run the Bluetooth part on a GLib main loop. Returns False when there is no Bluetooth."""
    try:
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib
    except ImportError as e:
        log(f"bluetooth: {e}")
        return False

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    BLUEZ = "org.bluez"
    PROPS = "org.freedesktop.DBus.Properties"

    def objects():
        om = dbus.Interface(bus.get_object(BLUEZ, "/"), "org.freedesktop.DBus.ObjectManager")
        return om.GetManagedObjects()

    try:
        adapter_path = next(p for p, ifs in objects().items() if "org.bluez.Adapter1" in ifs)
    except (StopIteration, dbus.DBusException) as e:
        log(f"bluetooth: no adapter ({e.__class__.__name__})")
        return False

    adapter = dbus.Interface(bus.get_object(BLUEZ, adapter_path), PROPS)
    state = {"pairing_until": 0.0, "device": None}

    def set_adapter(**kv):
        for k, v in kv.items():
            try:
                adapter.Set("org.bluez.Adapter1", k, v)
            except dbus.DBusException as e:
                log(f"bluetooth: {k}: {e.get_dbus_message()}")

    def pairing() -> bool:
        return time.time() < state["pairing_until"]

    def report():
        post("/api/bluetooth", {"available": True, "pairing": pairing(), "device": state["device"]})
        return True  # keep the GLib timer

    class Rejected(dbus.DBusException):
        _dbus_error_name = "org.bluez.Error.Rejected"

    def is_paired(device) -> bool:
        try:
            return bool(dbus.Interface(bus.get_object(BLUEZ, device), PROPS).Get("org.bluez.Device1", "Paired"))
        except dbus.DBusException:
            return False

    def trust(device):
        try:
            dbus.Interface(bus.get_object(BLUEZ, device), PROPS).Set("org.bluez.Device1", "Trusted", True)
        except dbus.DBusException as e:
            log(f"bluetooth: trust: {e.get_dbus_message()}")

    class Agent(dbus.service.Object):
        """Accepts phones like a speaker does (no PIN), but only while pairing is open."""

        def allow(self, device):
            if not pairing():
                raise Rejected("Press Pair on the ufoscene remote first")
            GLib.timeout_add_seconds(2, lambda: trust(device) or False)

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            if not is_paired(device):
                self.allow(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            self.allow(device)
            return "0000"

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            self.allow(device)
            return dbus.UInt32(0)

        @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            self.allow(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            self.allow(device)

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            pass

    agent_path = "/net/ufoscene/agent"
    Agent(bus, agent_path)
    manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
    manager.RegisterAgent(agent_path, "NoInputNoOutput")
    manager.RequestDefaultAgent(agent_path)

    set_adapter(Powered=dbus.Boolean(True), Alias=dbus.String(NAME),
                Discoverable=dbus.Boolean(False), Pairable=dbus.Boolean(False))

    def end_pairing():
        if not pairing():
            set_adapter(Discoverable=dbus.Boolean(False), Pairable=dbus.Boolean(False))
            report()
        return False

    def start_pairing():
        state["pairing_until"] = time.time() + PAIR_SECONDS
        set_adapter(DiscoverableTimeout=dbus.UInt32(PAIR_SECONDS), PairableTimeout=dbus.UInt32(PAIR_SECONDS),
                    Pairable=dbus.Boolean(True), Discoverable=dbus.Boolean(True))
        log("bluetooth: pairing open")
        report()
        GLib.timeout_add_seconds(PAIR_SECONDS + 1, end_pairing)
        return False

    def device_name(path) -> str:
        try:
            p = dbus.Interface(bus.get_object(BLUEZ, path), PROPS)
            return str(p.Get("org.bluez.Device1", "Alias"))
        except dbus.DBusException:
            return "a phone"

    def changed(iface, props, invalidated, path=None):
        if iface == "org.bluez.MediaPlayer1":
            update = track_update(props)
            if update:
                post("/api/nowplaying", update)
        elif iface == "org.bluez.Device1" and "Connected" in props:
            if props["Connected"]:
                state["device"] = device_name(path)
            elif state["device"] == device_name(path):
                state["device"] = None
                post("/api/nowplaying", {"source": "bluetooth", "playing": False})
            report()
        elif iface == "org.bluez.Device1" and props.get("Paired"):
            trust(path)

    def added(path, ifaces):
        if "org.bluez.MediaPlayer1" in ifaces:
            update = track_update(ifaces["org.bluez.MediaPlayer1"])
            if update:
                post("/api/nowplaying", update)

    bus.add_signal_receiver(changed, signal_name="PropertiesChanged", dbus_interface=PROPS,
                            bus_name=BLUEZ, path_keyword="path")
    bus.add_signal_receiver(added, signal_name="InterfacesAdded",
                            dbus_interface="org.freedesktop.DBus.ObjectManager", bus_name=BLUEZ)

    threading.Thread(target=command_loop, args=(lambda: GLib.idle_add(start_pairing),), daemon=True).start()
    report()
    GLib.timeout_add_seconds(30, report)  # the server forgets on restart
    log(f"bluetooth: ready as {NAME} on {adapter_path}")
    GLib.MainLoop().run()
    return True


def main():
    threading.Thread(target=airplay_loop, daemon=True).start()
    if not bluetooth_main():
        # no Bluetooth (a Pi 2 without a dongle): AirPlay only
        def no_bluetooth():
            while True:
                post("/api/bluetooth", {"available": False})
                time.sleep(30)

        no_bluetooth()


if __name__ == "__main__":
    main()

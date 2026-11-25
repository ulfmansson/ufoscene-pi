#!/usr/bin/env python3
"""ufoscene-pi: keeps the Pi on Wi-Fi. Runs as root and drives NetworkManager through nmcli.

* Joins a new network that the phone remote asked for (server.py holds the request until this
  fetches it). The old network stays saved until the new one has connected; if the new one does
  not connect, it is removed and the Pi goes back to the old one. A typo cannot cut the Pi off.
* When no known network has been reachable for two minutes (the router got a new password, or
  the Pi moved house), it opens a setup network, "ufoscene-setup", whose password only the TV
  shows. A phone that joins it gets the remote at http://10.42.0.1/ and gives the new network.
  Every five minutes with no phone on it, it tries the known networks again: a router that was
  only restarting brings the Pi back by itself.
* With a network cable plugged in, it never opens the setup network.

It talks only to server.py on the Pi itself. The new network's password goes to nmcli as an
argument, so other local users could see it in the process list for that moment: on this
appliance those are the owner's own accounts.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import time
import urllib.request

SERVER = "http://127.0.0.1"
NEW = "ufoscene-wifi-new"  # a network being tried
KEEP = "ufoscene-wifi"  # the network the remote set last, once it worked
SETUP = "ufoscene-setup"  # the setup network's name, as profile and as SSID
SETUP_IP = "10.42.0.1"
OFFLINE_S = 120  # no network this long: open the setup network
SETUP_RETRY_S = 300  # setup network open this long with no phone on it: try known networks again
JOIN_WAIT_S = 60
PSK = re.compile(r"[\x20-\x7e]{8,63}|[0-9a-fA-F]{64}")
PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # nothing to confuse on a TV


def valid(ssid, psk) -> bool:
    return (
        isinstance(ssid, str) and isinstance(psk, str)
        and 1 <= len(ssid.encode()) <= 32 and not any(ord(c) < 32 for c in ssid)
        and PSK.fullmatch(psk) is not None
    )


def split_terse(line: str) -> list[str]:
    """One line of `nmcli -t`: fields split on ':', with '\\:' and '\\\\' escaped."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


class Nmcli:
    def run(self, *args: str, timeout: float = 90) -> tuple[int, str]:
        try:
            p = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    def ok(self, *args: str, timeout: float = 90) -> bool:
        return self.run(*args, timeout=timeout)[0] == 0

    def rows(self, fields: str, *args: str, width: int = 0) -> list[list[str]]:
        rc, out = self.run("-t", "-f", fields, *args)
        if rc != 0:
            return []
        n = width or fields.count(",") + 1
        return [(split_terse(line) + [""] * n)[:n] for line in out.splitlines() if line]

    def stations(self, dev: str) -> bool:
        """Is any phone connected to the setup network? (iw; unknown counts as yes)"""
        try:
            p = subprocess.run(["iw", "dev", dev, "station", "dump"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return True
        return p.returncode != 0 or "Station" in p.stdout


class Keeper:
    def __init__(self, nm: Nmcli, report, clock=time.monotonic):
        self.nm = nm
        self.report = report
        self.clock = clock
        self.offline_since: float | None = None
        self.setup_since: float | None = None
        self.setup_password = ""
        self.networks: list[dict] = []
        self.last: dict | None = None  # the remote's last change: {"result", "ssid", "error"?}
        self.state = "offline"
        self.ssid = ""

    # ------------------------------------------------------------ what NetworkManager says

    def devices(self) -> tuple[list[str] | None, bool]:
        """(the Wi-Fi device as [name, state, connection], or None; is a cable connected?)"""
        wifi, cable = None, False
        for dev, typ, st, conn in self.nm.rows("DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
            if typ == "wifi" and wifi is None:
                wifi = [dev, st, conn]
            if typ == "ethernet" and st.startswith("connected"):
                cable = True
        return wifi, cable

    def scan(self, dev: str, rescan: str = "no"):
        best: dict[str, dict] = {}
        for ssid, signal, security in self.nm.rows("SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", dev, "--rescan", rescan):
            if not ssid or ssid == SETUP:
                continue
            n = {"ssid": ssid, "signal": int(signal) if signal.isdigit() else 0, "secure": bool(security and security != "--")}
            if ssid not in best or n["signal"] > best[ssid]["signal"]:
                best[ssid] = n
        if best:
            self.networks = sorted(best.values(), key=lambda n: -n["signal"])[:20]

    def active_uuid(self, dev: str) -> str:
        for uuid, device in self.nm.rows("UUID,DEVICE", "connection", "show", "--active"):
            if device == dev:
                return uuid
        return ""

    def profile_ssid(self, uuid: str) -> str:
        rc, out = self.nm.run("-g", "802-11-wireless.ssid", "connection", "show", uuid)
        return split_terse(out.strip())[0] if rc == 0 else ""

    # ------------------------------------------------------------ reporting

    def publish(self, cable: bool = False, available: bool = True):
        self.report({
            "available": available,
            "state": self.state,
            "ssid": self.ssid,
            "cable": cable,
            "networks": self.networks,
            "last": self.last,
            "setup": {"ssid": SETUP, "password": self.setup_password, "ip": SETUP_IP} if self.state == "setup" else None,
        })

    # ------------------------------------------------------------ the loop

    def tick(self):
        now = self.clock()
        wifi, cable = self.devices()
        if wifi is None:
            self.state, self.ssid = ("cable" if cable else "offline"), ""
            return self.publish(cable, available=False)
        dev, st, conn = wifi
        if conn == SETUP:
            if self.setup_since is None or not self.setup_password:
                # left over from before a restart: its password is gone, open it again
                self.stop_setup()
                return self.start_setup(dev, cable)
            self.state, self.ssid = "setup", SETUP
            if now - self.setup_since >= SETUP_RETRY_S and not self.nm.stations(dev):
                return self.try_known(dev, cable)
        elif st.startswith("connected") and conn:
            self.state, self.ssid = "connected", self.current_ssid(dev) or conn
            self.offline_since = None
            self.scan(dev)
        elif cable:
            self.state, self.ssid = "cable", ""
            self.offline_since = None
        else:
            self.state, self.ssid = "offline", ""
            if self.offline_since is None:
                self.offline_since = now
            elif now - self.offline_since >= OFFLINE_S:
                return self.start_setup(dev, cable)
        self.publish(cable)

    def current_ssid(self, dev: str) -> str:
        for active, ssid in self.nm.rows("ACTIVE,SSID", "device", "wifi", "list", "ifname", dev, "--rescan", "no"):
            if active == "yes":
                return ssid
        return ""

    def start_setup(self, dev: str, cable: bool = False):
        self.scan(dev, "yes")  # the remote can offer nearby networks; an access point cannot scan
        self.nm.run("radio", "wifi", "on")
        self.nm.run("connection", "delete", SETUP)
        password = "".join(secrets.choice(PASSWORD_LETTERS) for _ in range(10))
        made = self.nm.ok(
            "connection", "add", "type", "wifi", "ifname", dev, "con-name", SETUP, "autoconnect", "no",
            "ssid", SETUP, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
            "ipv4.method", "shared", "ipv4.addresses", f"{SETUP_IP}/24", "ipv6.method", "disabled",
            # WPA2 with CCMP only: what the Pi's Wi-Fi chip can do as an access point
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp",
            "wifi-sec.group", "ccmp", "wifi-sec.pmf", "disable", "wifi-sec.psk", password,
        ) and self.nm.ok("--wait", "30", "connection", "up", SETUP, timeout=45)
        if made:
            self.setup_since, self.setup_password = self.clock(), password
            self.state, self.ssid = "setup", SETUP
        else:
            self.nm.run("connection", "delete", SETUP)
            self.offline_since = self.clock()  # try again later
            self.state, self.ssid = "offline", ""
        self.publish(cable)

    def stop_setup(self):
        self.nm.run("connection", "down", SETUP, timeout=30)
        self.nm.run("connection", "delete", SETUP)
        self.setup_since, self.setup_password = None, ""

    def try_known(self, dev: str, cable: bool):
        """Close the setup network for a while and let NetworkManager try the saved networks."""
        self.stop_setup()
        self.state, self.ssid = "offline", ""
        self.publish(cable)
        self.nm.run("--wait", str(JOIN_WAIT_S), "device", "connect", dev, timeout=JOIN_WAIT_S + 15)
        wifi, cable = self.devices()
        if wifi and wifi[1].startswith("connected") and wifi[2] and wifi[2] != SETUP:
            self.offline_since = None
            return self.tick()
        self.start_setup(dev, cable)

    def change(self, ssid: str, psk: str):
        """Join the network the remote gave; keep the old one until the new one works."""
        if not valid(ssid, psk):
            return
        wifi, cable = self.devices()
        if wifi is None:
            self.last = {"result": "failed", "ssid": ssid, "error": "This Pi has no Wi-Fi."}
            return self.publish(cable, available=False)
        dev, st, conn = wifi
        in_setup = conn == SETUP
        old = "" if in_setup or not st.startswith("connected") else self.active_uuid(dev)
        self.state, self.ssid = "connecting", ssid
        self.publish(cable)
        if in_setup:
            self.stop_setup()
        else:
            self.scan(dev, "yes")
        hidden = ssid not in {n["ssid"] for n in self.networks}
        self.nm.run("connection", "delete", NEW)
        add = ["connection", "add", "type", "wifi", "ifname", dev, "con-name", NEW, "ssid", ssid,
               "connection.autoconnect-priority", "50", "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
        if hidden:
            add += ["802-11-wireless.hidden", "yes"]
        joined = self.nm.ok(*add) and self.nm.ok("--wait", str(JOIN_WAIT_S), "connection", "up", NEW, timeout=JOIN_WAIT_S + 15)
        if joined:
            # older profiles for the same network hold the old password; the last one from the
            # remote is replaced. Others (another place the Pi has been) stay.
            for name, uuid, typ in self.nm.rows("NAME,UUID,TYPE", "connection", "show"):
                if typ != "802-11-wireless" or name in (NEW, SETUP):
                    continue
                if name == KEEP or self.profile_ssid(uuid) == ssid:
                    self.nm.run("connection", "delete", uuid)
            self.nm.run("connection", "modify", NEW, "connection.id", KEEP)
            self.last = {"result": "joined", "ssid": ssid}
            self.offline_since = None
            self.state = "connected"
            return self.publish(cable)
        self.nm.run("connection", "delete", NEW)
        self.last = {
            "result": "failed", "ssid": ssid,
            "error": f"Could not join {ssid}: a wrong password, or the network is out of reach. Nothing changed.",
        }
        if old:
            self.nm.run("--wait", "30", "connection", "up", old, timeout=45)
        if in_setup:
            return self.start_setup(dev, cable)
        self.tick()


# ---------------------------------------------------------------- talking to server.py

def http(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(SERVER + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            v = json.loads(r.read() or b"{}")
            return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def main():
    def report(status: dict):
        http("POST", "/api/wifi/status", status)

    if not shutil.which("nmcli"):
        # no NetworkManager (an older Raspberry Pi OS): nothing to do but say so
        while True:
            report({"available": False, "state": "offline", "ssid": "", "cable": False, "networks": [], "last": None, "setup": None})
            time.sleep(60)

    nm = Nmcli()
    keeper = Keeper(nm, report)
    next_tick = 0.0
    while True:
        req = http("GET", "/api/wifi/pending")
        if req.get("ssid"):
            keeper.change(req.get("ssid"), req.get("psk"))
            next_tick = time.monotonic() + 10
        elif time.monotonic() >= next_tick:
            keeper.tick()
            next_tick = time.monotonic() + 10
        time.sleep(2)


if __name__ == "__main__":
    main()

"""ufoscene-pi: what the troubleshooting page shows about the Pi (/api/diag).

Facts only, gathered from /proc, /sys and a few read-only commands (systemctl, pactl, iw, ip,
vcgencmd). Every command has a timeout and every missing tool or file gives None instead of an
error, so the page works on any Pi and the tests run anywhere. Nothing here changes the Pi.

Not included on purpose: Wi-Fi passwords, the setup network's password, API keys, MAC
addresses and logs. Anyone on the home network can open the remote, and so this page.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SPEAKER = "UFOSCENE"
UNITS = (
    "ufoscene-server", "ufoscene-kiosk", "ufoscene-native", "ufoscene-airplay", "ufoscene-phone", "ufoscene-wifi",
    "avahi-daemon", "bluetooth", "NetworkManager",
)
AIRPLAY_PORTS = (5000, 7000)  # AirPlay 1 (RAOP) and AirPlay 2
# vcgencmd get_throttled: bit n now, bit n + 16 since boot
THROTTLE_BITS = {0: "under_voltage", 1: "freq_capped", 2: "throttled", 3: "temp_limit"}


# ------------------------------------------------------------------ reading the system

def run(*cmd: str, timeout: float = 3) -> str | None:
    """A command's output, or None when it is missing, fails or takes too long."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def read(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


# ------------------------------------------------------------------ parsing (pure, tested)

def parse_os_release(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def parse_meminfo(text: str | None) -> dict | None:
    kb = {}
    for line in (text or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            kb[m.group(1)] = int(m.group(2))
    if "MemTotal" not in kb:
        return None
    return {"total_mb": kb["MemTotal"] // 1024, "available_mb": kb.get("MemAvailable", 0) // 1024}


def parse_throttled(text: str | None) -> dict | None:
    """'throttled=0x50005' -> what is wrong now, and what went wrong since boot."""
    m = re.search(r"0x([0-9a-fA-F]+)", text or "")
    if not m:
        return None
    v = int(m.group(1), 16)
    return {
        "raw": f"0x{v:x}",
        "now": [name for bit, name in THROTTLE_BITS.items() if v & (1 << bit)],
        "since_boot": [name for bit, name in THROTTLE_BITS.items() if v & (1 << (bit + 16))],
    }


def parse_iw_link(text: str | None) -> dict | None:
    """`iw dev wlan0 link` -> network name, signal, band and speed (None when not connected)."""
    if not text or "Not connected" in text:
        return None
    out: dict = {}
    if m := re.search(r"^\s*SSID: (.*)$", text, re.M):
        out["ssid"] = m.group(1).strip()
    if m := re.search(r"signal: (-?\d+) dBm", text):
        out["signal_dbm"] = int(m.group(1))
    if m := re.search(r"freq: (\d+)", text):
        out["freq_mhz"] = int(m.group(1))
    if m := re.search(r"tx bitrate: ([\d.]+ \S+)", text):
        out["tx_bitrate"] = m.group(1)
    return out or None


def parse_power_save(text: str | None) -> bool | None:
    m = re.search(r"Power save:\s*(on|off)", text or "")
    return None if not m else m.group(1) == "on"


def parse_systemctl_show(text: str | None) -> dict:
    """`systemctl show -p Id,ActiveState,SubState,NRestarts a b` -> {unit: {...}}."""
    units = {}
    for block in (text or "").strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = props.get("Id", "").removesuffix(".service")
        if not unit:
            continue
        restarts = props.get("NRestarts", "")
        units[unit] = {
            "active": props.get("ActiveState") or None,
            "sub": props.get("SubState") or None,
            "restarts": int(restarts) if restarts.isdigit() else None,
            "found": props.get("LoadState") != "not-found",
        }
    return units


def parse_volume(text: str | None) -> int | None:
    m = re.search(r"(\d+)%", text or "")
    return int(m.group(1)) if m else None


def parse_short_list(text: str | None) -> list[dict]:
    """`pactl list short sinks|sources` -> [{"name", "state"}]."""
    out = []
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            out.append({"name": cols[1], "state": cols[4] if len(cols) > 4 else None})
    return out


def parse_app_names(text: str | None) -> list[str]:
    """`pactl list sink-inputs` -> the programs playing sound right now."""
    names = re.findall(r'application\.name = "([^"]*)"', text or "")
    return sorted(set(n[:60] for n in names))


def listening_ports(*proc_net_tcp: str | None) -> set[int]:
    """Ports in LISTEN state, from /proc/net/tcp and /proc/net/tcp6."""
    ports = set()
    for text in proc_net_tcp:
        for line in (text or "").splitlines()[1:]:
            cols = line.split()
            if len(cols) > 3 and cols[3] == "0A" and ":" in cols[1]:
                ports.add(int(cols[1].rsplit(":", 1)[1], 16))
    return ports


def parse_ip_addr(text: str | None) -> list[dict] | None:
    """`ip -j addr` -> IPv4 addresses per interface, without the loopback (None: no `ip`)."""
    if text is None:
        return None
    try:
        ifs = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for i in ifs if isinstance(ifs, list) else []:
        name = i.get("ifname", "")
        if name == "lo":
            continue
        for a in i.get("addr_info", []):
            if a.get("family") == "inet":
                out.append({"ifname": name, "ip": a.get("local"), "up": i.get("operstate") == "UP"})
    return out


def parse_default_route(text: str | None) -> str | None:
    try:
        routes = json.loads(text or "[]")
    except ValueError:
        return None
    for r in routes if isinstance(routes, list) else []:
        if r.get("gateway"):
            return r["gateway"]
    return None


def parse_nameservers(text: str | None) -> list[str]:
    return re.findall(r"^\s*nameserver\s+(\S+)", text or "", re.M)


# ------------------------------------------------------------------ gathering

def pi_facts() -> dict:
    model = read("/proc/device-tree/model")
    uptime = read("/proc/uptime")
    temp = read("/sys/class/thermal/thermal_zone0/temp")
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    try:
        du = shutil.disk_usage("/")
        disk = {"total_gb": round(du.total / 1e9, 1), "free_gb": round(du.free / 1e9, 1)}
    except OSError:
        disk = None
    u = os.uname()
    return {
        "model": model.strip("\0\n ") if model else None,
        "os": parse_os_release(read("/etc/os-release")),
        "kernel": u.release,
        "arch": u.machine,
        "uptime_s": int(float(uptime.split()[0])) if uptime else None,
        "load": load,
        "cpus": os.cpu_count(),
        "temp_c": round(int(temp) / 1000, 1) if temp and temp.strip().lstrip("-").isdigit() else None,
        "throttled": parse_throttled(run("vcgencmd", "get_throttled")),
        "mem": parse_meminfo(read("/proc/meminfo")),
        "disk": disk,
    }


def _resolves(name: str, timeout: float = 2) -> bool:
    """Does DNS work? getaddrinfo has no timeout of its own, so it runs in a thread."""
    ok = []

    def lookup():
        try:
            ok.append(bool(socket.getaddrinfo(name, 443)))
        except OSError:
            ok.append(False)

    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    return bool(ok and ok[0])


def _reaches(host: str, port: int, timeout: float = 2) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def network_facts() -> dict:
    addrs = parse_ip_addr(run("ip", "-j", "-4", "addr", "show"))
    wlan = next((a["ifname"] for a in addrs or [] if a["ifname"].startswith("wl")), None)
    if wlan is None and Path("/sys/class/net/wlan0").exists():
        wlan = "wlan0"
    wifi = None
    if wlan:
        wifi = {
            "ifname": wlan,
            "link": parse_iw_link(run("iw", "dev", wlan, "link")),
            "power_save": parse_power_save(run("iw", "dev", wlan, "get", "power_save")),
        }
    resolved = _resolves("all.api.radio-browser.info")  # where the radio finds its stations
    return {
        "addresses": addrs,
        "gateway": parse_default_route(run("ip", "-j", "route", "show", "default")),
        "dns": parse_nameservers(read("/etc/resolv.conf"))[:4],
        "dns_ok": resolved,
        "internet": _reaches("1.1.1.1", 443) or _reaches("8.8.8.8", 53),
        "wifi": wifi,
    }


def service_facts() -> dict:
    out = run("systemctl", "show", "-p", "Id,LoadState,ActiveState,SubState,NRestarts",
              *[f"{u}.service" for u in UNITS])
    return parse_systemctl_show(out) if out is not None else {}


def airplay_facts() -> dict:
    version = run("shairport-sync", "-V")
    tcp, tcp6 = read("/proc/net/tcp"), read("/proc/net/tcp6")
    ports = listening_ports(tcp, tcp6)
    pipe = Path(os.environ.get("UFO_AIRPLAY_PIPE", f"/run/user/{os.getuid()}/airplay-metadata"))
    try:
        is_pipe = stat.S_ISFIFO(pipe.stat().st_mode)
    except OSError:
        is_pipe = False
    return {
        "name": SPEAKER,
        "version": version.strip()[:80] if version else None,
        "listening": None if tcp is None and tcp6 is None else any(p in ports for p in AIRPLAY_PORTS),
        "ports": sorted(p for p in AIRPLAY_PORTS if p in ports),
        "metadata_pipe": is_pipe,
    }


def audio_facts() -> dict:
    sink = run("pactl", "get-default-sink")
    sink = sink.strip() if sink else None
    source = run("pactl", "get-default-source")
    mute = run("pactl", "get-sink-mute", "@DEFAULT_SINK@")
    return {
        "server": run("pactl", "info") is not None,
        "default_sink": sink,
        "default_source": source.strip() if source else None,
        "hdmi": None if sink is None else "hdmi" in sink.lower(),
        "volume_pct": parse_volume(run("pactl", "get-sink-volume", "@DEFAULT_SINK@")),
        "muted": None if mute is None else "yes" in mute,
        "sinks": parse_short_list(run("pactl", "list", "short", "sinks"))[:8],
        "sources": [s for s in parse_short_list(run("pactl", "list", "short", "sources"))
                    if not s["name"].endswith(".monitor")][:8],
        "playing": parse_app_names(run("pactl", "list", "sink-inputs")),
    }


def screen_facts() -> dict:
    ports = []
    for d in sorted(Path("/sys/class/drm").glob("card*-HDMI-A-*")):
        status = (read(d / "status") or "").strip()
        modes = (read(d / "modes") or "").split()
        ports.append({
            "port": d.name.split("-", 1)[1],
            "connected": status == "connected",
            "mode": modes[0] if modes else None,
        })
    return {"hdmi": ports}


def bundle_version(prefix: str = "/opt/ufoscene") -> str | None:
    v = read(Path(prefix) / "VERSION")
    return v.strip()[:40] if v else None


COLLECTORS = {
    "pi": pi_facts,
    "network": network_facts,
    "services": service_facts,
    "airplay": airplay_facts,
    "audio": audio_facts,
    "screen": screen_facts,
}


class Diagnostics:
    """The system facts, gathered at most every few seconds however many phones ask."""

    TTL_S = 3

    def __init__(self, collectors: dict | None = None, clock=time.monotonic):
        self.collectors = collectors or COLLECTORS
        self.clock = clock
        self.lock = threading.Lock()
        self.cached: dict | None = None
        self.at = -1e9

    def system(self) -> dict:
        with self.lock:  # one gathering at a time; the others wait for it and share it
            if self.cached is not None and self.clock() - self.at < self.TTL_S:
                return self.cached
            with ThreadPoolExecutor(max_workers=len(self.collectors)) as pool:
                futures = {k: pool.submit(fn) for k, fn in self.collectors.items()}
            facts = {}
            for k, f in futures.items():
                try:
                    facts[k] = f.result()
                except Exception as e:  # a broken collector must not take the page down
                    facts[k] = {"error": type(e).__name__}
            self.cached, self.at = facts, self.clock()
            return facts

    def report(self, hub_state: dict, wifi: dict | None, lastfm: dict, urls: dict,
               client: str, host: str, version: str) -> dict:
        sysf = self.system()
        net = dict(sysf.get("network") or {})
        if wifi is not None:
            # what wifi.py knows, without the list of nearby networks
            net["manager"] = {k: wifi.get(k) for k in ("available", "state", "ssid", "cable", "last", "setup")}
        net["urls"] = urls
        net["hostname"] = socket.gethostname()
        net["you"] = {"ip": client, "host": host}
        seen = hub_state.get("tv_seen")
        return {
            "time": int(time.time()),
            "version": version,
            "bundle": bundle_version(),
            **sysf,
            "network": net,
            "tv": {
                "connected": bool(hub_state.get("tv_connected")),
                "seen_s_ago": None if seen is None else max(0, int(time.time() - seen)),
                "fps": hub_state.get("fps"),
                "demo": hub_state.get("demo"),
                "audio": hub_state.get("audio"),
            },
            "nowplaying": hub_state.get("nowplaying"),
            "bluetooth": hub_state.get("bluetooth"),
            "lastfm": {"user": lastfm.get("user") or "", "key": bool(lastfm.get("key"))},
        }

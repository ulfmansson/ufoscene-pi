"""Tests for files/wifi.py against a small pretend NetworkManager (no nmcli needed).

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "files"))
import wifi  # noqa: E402
from wifi import KEEP, NEW, SETUP  # noqa: E402


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:")


class FakeNm(wifi.Nmcli):
    """Profiles, one Wi-Fi device, and the networks in reach with their passwords."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.profiles = {"uuid-pre": {"name": "preconfigured", "ssid": "Home", "psk": "oldpassword", "ap": False}}
        self.reach = {"Home": "oldpassword"}  # SSID -> the password that works
        self.active = "uuid-pre"  # profile uuid on wlan0, or ""
        self.cable = False
        self.has_wifi = True
        self.phones = False
        self.ap_works = True
        self.n = 0

    def find(self, key):
        for uuid, p in self.profiles.items():
            if key in (uuid, p["name"]):
                return uuid
        return None

    def activate(self, uuid) -> bool:
        p = self.profiles[uuid]
        if p["ap"]:
            ok = self.ap_works
        else:
            ok = self.reach.get(p["ssid"]) == p["psk"]
        self.active = uuid if ok else ""
        return ok

    def stations(self, dev):
        return self.phones

    def run(self, *args, timeout=90):
        self.calls.append(args)
        a = list(args)
        if a[:1] == ["--wait"]:
            a = a[2:]
        if a[:3] == ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION"]:
            lines = []
            if self.has_wifi:
                name = self.profiles[self.active]["name"] if self.active else ""
                lines.append(f"wlan0:wifi:{'connected' if self.active else 'disconnected'}:{esc(name)}")
            lines.append(f"eth0:ethernet:{'connected' if self.cable else 'unavailable'}:")
            lines.append("lo:loopback:connected (externally):lo")
            return 0, "\n".join(lines) + "\n"
        if a[:3] == ["-t", "-f", "SSID,SIGNAL,SECURITY"]:
            if self.active and self.profiles[self.active]["ap"]:
                return 0, ""
            return 0, "".join(f"{esc(s)}:70:WPA2\n" for s in self.reach)
        if a[:3] == ["-t", "-f", "ACTIVE,SSID"]:
            ssid = self.profiles[self.active]["ssid"] if self.active else ""
            return 0, "".join(f"{'yes' if s == ssid else 'no'}:{esc(s)}\n" for s in self.reach)
        if a[:3] == ["-t", "-f", "UUID,DEVICE"]:
            return 0, f"{self.active}:wlan0\n" if self.active else ""
        if a[:3] == ["-t", "-f", "NAME,UUID,TYPE"]:
            return 0, "".join(f"{esc(p['name'])}:{u}:802-11-wireless\n" for u, p in self.profiles.items())
        if a[:2] == ["-g", "802-11-wireless.ssid"]:
            u = self.find(a[4])
            return (0, esc(self.profiles[u]["ssid"]) + "\n") if u else (10, "")
        if a[:2] == ["connection", "add"]:
            kv = dict(zip(a[2::2], a[3::2]))
            self.n += 1
            self.profiles[f"uuid-{self.n}"] = {
                "name": kv["con-name"], "ssid": kv["ssid"], "psk": kv.get("wifi-sec.psk", ""),
                "ap": kv.get("802-11-wireless.mode") == "ap", "args": kv,
            }
            return 0, ""
        if a[:2] == ["connection", "up"]:
            u = self.find(a[2])
            return (0, "") if u and self.activate(u) else (4, "")
        if a[:2] == ["connection", "down"]:
            u = self.find(a[2])
            if u and self.active == u:
                self.active = ""
            return (0, "") if u else (10, "")
        if a[:2] == ["connection", "delete"]:
            u = self.find(a[2])
            if not u:
                return 10, ""
            if self.active == u:
                self.active = ""
            del self.profiles[u]
            return 0, ""
        if a[:2] == ["connection", "modify"]:
            u = self.find(a[2])
            self.profiles[u]["name"] = a[4]
            return 0, ""
        if a[:2] == ["device", "connect"]:
            for u, p in self.profiles.items():
                if not p["ap"] and self.activate(u):
                    return 0, ""
            return 4, ""
        if a[:3] == ["radio", "wifi", "on"]:
            return 0, ""
        raise AssertionError(f"unexpected nmcli {args}")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.reports: list[dict] = []
        self.clock = Clock()
        self.k = wifi.Keeper(self.nm, self.reports.append, self.clock)

    def names(self):
        return sorted(p["name"] for p in self.nm.profiles.values())

    def test_terse_lines(self):
        self.assertEqual(wifi.split_terse(r"a\:b:c\\d:"), ["a:b", "c\\d", ""])

    def test_validation(self):
        self.assertTrue(wifi.valid("Home", "12345678"))
        self.assertTrue(wifi.valid("Kök & 🛸", "a" * 63))
        self.assertTrue(wifi.valid("x" * 32, "ab" * 32))  # the key itself, 64 hex digits
        for ssid, psk in [("", "12345678"), ("x" * 33, "12345678"), ("Home", "short"), ("Home", "g" * 64),
                          ("a\nb", "12345678"), ("Home", "pässword"), (None, "12345678"), ("Home", None)]:
            self.assertFalse(wifi.valid(ssid, psk), (ssid, psk))

    def test_connected_reports_network_and_neighbours(self):
        self.nm.reach["Cabin"] = "cabinpass"
        self.k.tick()
        r = self.reports[-1]
        self.assertEqual((r["available"], r["state"], r["ssid"], r["setup"]), (True, "connected", "Home", None))
        self.assertEqual({n["ssid"] for n in r["networks"]}, {"Home", "Cabin"})

    def test_join_another_network_keeps_the_first(self):
        self.nm.reach["Cabin"] = "cabinpass"
        self.k.change("Cabin", "cabinpass")
        self.assertEqual(self.k.last, {"result": "joined", "ssid": "Cabin"})
        self.assertEqual(self.names(), ["preconfigured", KEEP])
        self.assertEqual(self.nm.profiles[self.nm.active]["ssid"], "Cabin")
        self.assertEqual(self.reports[-1]["state"], "connected")
        # a later change replaces the remote's last network
        self.nm.reach["Office"] = "officepass"
        self.k.change("Office", "officepass")
        self.assertEqual(self.names(), ["preconfigured", KEEP])
        self.assertEqual(self.nm.profiles[self.nm.find(KEEP)]["ssid"], "Office")

    def test_new_router_password_replaces_the_old_profile(self):
        self.nm.reach["Home"] = "newpassword"  # the old profile still says oldpassword
        self.k.change("Home", "newpassword")
        self.assertEqual(self.k.last["result"], "joined")
        self.assertEqual(self.names(), [KEEP])

    def test_a_typo_changes_nothing(self):
        self.nm.reach["Cabin"] = "cabinpass"
        self.k.change("Cabin", "cabinpasx")
        self.assertEqual(self.k.last["result"], "failed")
        self.assertIn("Nothing changed", self.k.last["error"])
        self.assertEqual(self.names(), ["preconfigured"])
        self.assertEqual(self.nm.active, "uuid-pre")  # back on the old network
        self.assertNotIn(NEW, self.names())
        # the password never goes into a report
        self.assertNotIn("cabinpasx", repr(self.reports))

    def test_a_network_not_in_sight_is_tried_as_hidden(self):
        self.k.change("Secret", "secretpass")
        added = [p for p in self.nm.calls if p[:2] == ("connection", "add")][0]
        self.assertIn("802-11-wireless.hidden", added)

    def test_bad_input_is_ignored(self):
        self.k.change("Home", "short")
        self.assertFalse([c for c in self.nm.calls if c[:2] == ("connection", "add")])

    def test_offline_for_two_minutes_opens_the_setup_network(self):
        self.nm.reach = {}
        self.nm.active = ""
        self.k.tick()
        self.clock.t += 60
        self.k.tick()
        self.assertEqual(self.reports[-1]["state"], "offline")
        self.assertNotIn(SETUP, self.names())
        self.clock.t += 61
        self.k.tick()
        r = self.reports[-1]
        self.assertEqual(r["state"], "setup")
        self.assertEqual(r["setup"]["ssid"], SETUP)
        self.assertEqual(r["setup"]["ip"], "10.42.0.1")
        self.assertEqual(len(r["setup"]["password"]), 10)
        ap = self.nm.profiles[self.nm.active]["args"]
        self.assertEqual((ap["802-11-wireless.mode"], ap["ipv4.method"], ap["autoconnect"]), ("ap", "shared", "no"))

    def test_never_with_a_cable(self):
        self.nm.reach = {}
        self.nm.active = ""
        self.nm.cable = True
        for _ in range(5):
            self.k.tick()
            self.clock.t += 100
        self.assertEqual(self.reports[-1]["state"], "cable")
        self.assertNotIn(SETUP, self.names())

    def open_setup(self):
        self.nm.reach = {}
        self.nm.active = ""
        self.k.tick()
        self.clock.t += 121
        self.k.tick()
        self.assertEqual(self.reports[-1]["state"], "setup")

    def test_setup_network_tries_the_old_wifi_again_when_no_phone_is_on_it(self):
        self.open_setup()
        self.nm.phones = True
        self.clock.t += 301
        self.k.tick()
        self.assertEqual(self.reports[-1]["state"], "setup")  # a phone is on it: stay
        self.nm.phones = False
        self.nm.reach = {"Home": "oldpassword"}  # the router is back
        self.k.tick()
        self.assertEqual(self.reports[-1]["state"], "connected")
        self.assertNotIn(SETUP, self.names())

    def test_setup_network_comes_back_when_the_old_wifi_is_still_gone(self):
        self.open_setup()
        first = self.reports[-1]["setup"]["password"]
        self.clock.t += 301
        self.k.tick()
        self.assertEqual(self.reports[-1]["state"], "setup")
        self.assertIn(SETUP, self.names())
        self.assertEqual(len(self.reports[-1]["setup"]["password"]), 10)
        self.assertIsInstance(first, str)

    def test_change_from_the_setup_network(self):
        self.open_setup()
        self.nm.reach = {"Home": "newpassword"}
        self.k.change("Home", "newpassword")
        self.assertEqual(self.k.last["result"], "joined")
        self.assertEqual(self.names(), [KEEP])
        self.assertEqual(self.reports[-1]["state"], "connected")

    def test_failed_change_from_the_setup_network_opens_it_again(self):
        self.open_setup()
        self.nm.reach = {"Home": "newpassword"}
        self.k.change("Home", "wrongpassword")
        self.assertEqual(self.k.last["result"], "failed")
        self.assertEqual(self.reports[-1]["state"], "setup")
        self.assertEqual(self.names(), ["preconfigured", SETUP])

    def test_setup_left_from_before_a_restart_is_opened_again(self):
        self.nm.reach = {}
        self.nm.profiles["uuid-old"] = {"name": SETUP, "ssid": SETUP, "psk": "x", "ap": True}
        self.nm.active = "uuid-old"
        self.k.tick()
        r = self.reports[-1]
        self.assertEqual(r["state"], "setup")
        self.assertNotEqual(r["setup"]["password"], "x")

    def test_no_wifi(self):
        self.nm.has_wifi = False
        self.nm.cable = True
        self.k.tick()
        self.assertEqual((self.reports[-1]["available"], self.reports[-1]["state"]), (False, "cable"))


if __name__ == "__main__":
    unittest.main()

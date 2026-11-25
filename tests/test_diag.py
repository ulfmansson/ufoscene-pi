"""Tests for files/diag.py: the parsers, and that a broken or missing tool never breaks the page.

Run:  python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "files"))
import diag  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_throttled(self):
        self.assertEqual(diag.parse_throttled("throttled=0x0"), {"raw": "0x0", "now": [], "since_boot": []})
        t = diag.parse_throttled("throttled=0x50005")
        self.assertEqual(t["now"], ["under_voltage", "throttled"])
        self.assertEqual(t["since_boot"], ["under_voltage", "throttled"])
        self.assertEqual(diag.parse_throttled("throttled=0x80000")["since_boot"], ["temp_limit"])
        self.assertIsNone(diag.parse_throttled(None))
        self.assertIsNone(diag.parse_throttled("VCHI initialization failed"))

    def test_iw_link(self):
        text = """Connected to 11:22:33:44:55:66 (on wlan0)
\tSSID: Home Net
\tfreq: 5180
\tRX: 1234 bytes (10 packets)
\tsignal: -67 dBm
\trx bitrate: 433.3 MBit/s
\ttx bitrate: 390.0 MBit/s VHT-MCS 8 80MHz short GI VHT-NSS 1
"""
        self.assertEqual(diag.parse_iw_link(text), {"ssid": "Home Net", "signal_dbm": -67, "freq_mhz": 5180, "tx_bitrate": "390.0 MBit/s"})
        self.assertIsNone(diag.parse_iw_link("Not connected."))
        self.assertIsNone(diag.parse_iw_link(None))

    def test_power_save(self):
        self.assertTrue(diag.parse_power_save("Power save: on"))
        self.assertFalse(diag.parse_power_save("Power save: off\n"))
        self.assertIsNone(diag.parse_power_save(None))

    def test_systemctl_show(self):
        text = (
            "Id=ufoscene-airplay.service\nLoadState=loaded\nActiveState=active\nSubState=running\nNRestarts=4\n\n"
            "Id=NetworkManager.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nNRestarts=\n"
        )
        s = diag.parse_systemctl_show(text)
        self.assertEqual(s["ufoscene-airplay"], {"active": "active", "sub": "running", "restarts": 4, "found": True})
        self.assertEqual(s["NetworkManager"]["found"], False)
        self.assertIsNone(s["NetworkManager"]["restarts"])
        self.assertEqual(diag.parse_systemctl_show(None), {})

    def test_pactl(self):
        self.assertEqual(diag.parse_volume("Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB"), 100)
        self.assertIsNone(diag.parse_volume(None))
        sinks = "47\talsa_output.platform-fef00700.hdmi.hdmi-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
        self.assertEqual(diag.parse_short_list(sinks), [{"name": "alsa_output.platform-fef00700.hdmi.hdmi-stereo", "state": "RUNNING"}])
        inputs = 'Sink Input #80\n\tProperties:\n\t\tapplication.name = "AirPlay"\nSink Input #81\n\t\tapplication.name = "Chromium"\n\t\tapplication.name = "AirPlay"\n'
        self.assertEqual(diag.parse_app_names(inputs), ["AirPlay", "Chromium"])

    def test_listening_ports(self):
        tcp = (
            "  sl  local_address rem_address   st tx_queue rx_queue\n"
            "   0: 00000000:1B58 00000000:0000 0A 00000000:00000000\n"  # 7000, LISTEN
            "   1: 0100007F:0050 0100007F:9C40 01 00000000:00000000\n"  # 80, ESTABLISHED
        )
        tcp6 = (
            "  sl  local_address rem_address st\n"
            "   0: 00000000000000000000000000000000:1388 00000000000000000000000000000000:0000 0A 0\n"  # 5000
        )
        self.assertEqual(diag.listening_ports(tcp, tcp6, None), {7000, 5000})

    def test_ip(self):
        addr = '[{"ifname":"lo","operstate":"UNKNOWN","addr_info":[{"family":"inet","local":"127.0.0.1"}]},' \
               '{"ifname":"wlan0","operstate":"UP","addr_info":[{"family":"inet","local":"192.168.1.42"}]}]'
        self.assertEqual(diag.parse_ip_addr(addr), [{"ifname": "wlan0", "ip": "192.168.1.42", "up": True}])
        self.assertEqual(diag.parse_ip_addr("not json"), [])
        self.assertIsNone(diag.parse_ip_addr(None))  # no `ip` here: unknown, not "no network"
        self.assertEqual(diag.parse_default_route('[{"dst":"default","gateway":"192.168.1.1","dev":"wlan0"}]'), "192.168.1.1")
        self.assertIsNone(diag.parse_default_route(None))
        self.assertEqual(diag.parse_nameservers("# x\nnameserver 192.168.1.1\nnameserver ::1\n"), ["192.168.1.1", "::1"])

    def test_os_and_memory(self):
        self.assertEqual(diag.parse_os_release('ID=debian\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'), "Debian GNU/Linux 13 (trixie)")
        self.assertEqual(diag.parse_meminfo("MemTotal:  3884000 kB\nMemAvailable: 2048000 kB\n"), {"total_mb": 3792, "available_mb": 2000})
        self.assertIsNone(diag.parse_meminfo(""))


class DiagnosticsTest(unittest.TestCase):
    def test_a_broken_collector_does_not_break_the_report(self):
        def broken():
            raise RuntimeError("boom")

        d = diag.Diagnostics({"pi": lambda: {"model": "Raspberry Pi 4"}, "audio": broken})
        r = d.report({"tv_connected": True, "fps": 30, "tv_seen": None}, None, {"user": "", "key": ""},
                     {"mdns": "http://ufoscene.local/"}, client="192.168.1.20", host="ufoscene.local", version="0.2.0")
        self.assertEqual(r["pi"], {"model": "Raspberry Pi 4"})
        self.assertEqual(r["audio"], {"error": "RuntimeError"})
        self.assertEqual(r["tv"]["fps"], 30)
        self.assertEqual(r["network"]["you"], {"ip": "192.168.1.20", "host": "ufoscene.local"})

    def test_cached_for_a_few_seconds(self):
        calls = []
        clock = [100.0]
        d = diag.Diagnostics({"pi": lambda: calls.append(1) or {"n": len(calls)}}, clock=lambda: clock[0])
        self.assertEqual(d.system()["pi"], {"n": 1})
        clock[0] += 1
        self.assertEqual(d.system()["pi"], {"n": 1})
        clock[0] += diag.Diagnostics.TTL_S
        self.assertEqual(d.system()["pi"], {"n": 2})

    def test_secrets_stay_out(self):
        d = diag.Diagnostics({})
        wifi = {"available": True, "state": "setup", "ssid": "ufoscene-setup", "cable": False, "last": None,
                "setup": {"ssid": "ufoscene-setup", "ip": "10.42.0.1"}, "networks": [{"ssid": "Neighbour"}]}
        r = d.report({}, wifi, {"user": "ufo_fan", "key": "0123456789abcdef0123456789abcdef"}, {}, "1.2.3.4", "x", "0")
        self.assertEqual(r["lastfm"], {"user": "ufo_fan", "key": True})
        self.assertNotIn("networks", r["network"]["manager"])
        self.assertNotIn("0123456789abcdef", str(r))

    def test_missing_tools_give_none(self):
        self.assertIsNone(diag.run("ufoscene-no-such-tool-here"))
        self.assertIsNone(diag.read("/no/such/file"))
        # the real collectors run on any machine, with Pi-only facts as None
        self.assertIn("hdmi", diag.screen_facts())
        self.assertIn("listening", diag.airplay_facts())
        self.assertIn("kernel", diag.pi_facts())


if __name__ == "__main__":
    unittest.main()

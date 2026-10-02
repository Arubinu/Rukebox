"""The network diagnostic (src/net_diag.py): what it reads from the link, and
how it tells a Wi-Fi drop caused by the Bluetooth radio from any other."""
import unittest
from unittest import mock

import _path  # noqa: F401
import net_diag

STATION = """Station 8a:78:48:e2:60:79 (on wlan0)
\tinactive time:\t0 ms
\ttx failed:\t6
\tsignal:  \t-46 dBm
\ttx bitrate:\t65.0 MBit/s
\trx bitrate:\t58.5 MBit/s
\tconnected time:\t404 seconds
"""
SUPPLICANT = """1790971730.100000 rukebox wpa_supplicant[551]: wlan0: CTRL-EVENT-DISCONNECTED bssid=8a:78:48:e2:60:79 reason=0 locally_generated=1
1790971731.300000 rukebox wpa_supplicant[551]: wlan0: CTRL-EVENT-ASSOC-REJECT bssid=00:00:00:00:00:00 status_code=16
1790971735.000000 rukebox wpa_supplicant[551]: wlan0: CTRL-EVENT-CONNECTED - Connection to 8a:78:48:e2:60:79 completed
1790972834.170000 rukebox wpa_supplicant[551]: wlan0: CTRL-EVENT-DISCONNECTED bssid=8a:78:48:e2:60:79 reason=0 locally_generated=1
1790973900.000000 rukebox wpa_supplicant[551]: wlan0: CTRL-EVENT-DISCONNECTED bssid=8a:78:48:e2:60:79 reason=7
"""
BLUETOOTH = """1790971730.900000 rukebox bluetoothd[530]: src/profile.c:record_cb() Unable to get Hands-Free Voice gateway SDP record: Host is down
1790972000.000000 rukebox bluetoothd[530]: src/profile.c:record_cb() Unable to get Hands-Free Voice gateway SDP record: Host is down
1790972834.900000 rukebox bluetoothd[530]: src/profile.c:record_cb() Unable to get Hands-Free Voice gateway SDP record: Host is down
1790972900.000000 rukebox bluetoothd[530]: Endpoint registered
"""
PING = """--- 192.168.42.254 ping statistics ---
10 packets transmitted, 7 received, 30% packet loss, time 1810ms
rtt min/avg/max/mdev = 3.1/96.4/479.0/120.2 ms
"""
BUILTIN = {"name": "hci0", "bus": "UART", "address": "B8:27:EB:62:82:CB"}
DONGLE = {"name": "hci1", "bus": "USB", "address": "00:A7:50:72:14:C4"}


class ParsingTest(unittest.TestCase):
    def test_the_link_is_read_from_the_station_dump(self):
        station = net_diag.parse_station(STATION)
        self.assertEqual((station["associated"], station["signal"], station["tx_bitrate"],
                          station["rx_bitrate"], station["tx_failed"], station["connected_sec"]),
                         (True, -46.0, 65.0, 58.5, 6.0, 404.0))
        self.assertFalse(net_diag.parse_station("")["associated"])
        self.assertIsNone(net_diag.parse_station(None)["signal"])

    def test_only_disconnections_are_drops_and_each_says_who_ended_it(self):
        drops = net_diag.parse_drops(SUPPLICANT)
        self.assertEqual([(d["reason"], d["local"]) for d in drops], [(0, True), (0, True), (7, False)])
        self.assertEqual(net_diag.parse_drops(""), [])

    def test_a_page_is_a_connection_attempt_that_gave_up(self):
        self.assertEqual(len(net_diag.parse_pages(BLUETOOTH)), 3)

    def test_a_drop_is_set_against_the_pages_around_it(self):
        drops, pages = net_diag.parse_drops(SUPPLICANT), net_diag.parse_pages(BLUETOOTH)
        self.assertEqual(net_diag.drops_at_a_page(drops, pages), 2)
        self.assertEqual(net_diag.drops_at_a_page(drops, []), 0)

    def test_ping_gives_what_was_lost(self):
        self.assertEqual(net_diag.parse_ping(PING), {"sent": 10, "lost": 3, "avg_ms": 96.4})
        self.assertIsNone(net_diag.parse_ping("ping: connect: Network is unreachable"))

    def test_the_speakers_controller_is_the_one_named_or_the_default(self):
        found = [DONGLE, BUILTIN]
        self.assertIs(net_diag.speaker_controller({"SPEAKER_BT_ADAPTER": "b8:27:eb:62:82:cb"}, found), BUILTIN)
        self.assertIs(net_diag.speaker_controller({"SPEAKER_BT_ADAPTER": "hci1"}, found), DONGLE)
        with mock.patch.object(net_diag, "_out",
                               return_value="Controller 00:A7:50:72:14:C4 rukebox #2 [default]\n"):
            self.assertIs(net_diag.speaker_controller({}, found), DONGLE)
        with mock.patch.object(net_diag, "_out", return_value=""):
            self.assertIsNone(net_diag.speaker_controller({}, found))


class VerdictTest(unittest.TestCase):
    def data(self, **changes):
        data = {"station": net_diag.parse_station(STATION), "drops": [], "pages": [],
                "speaker_controller": BUILTIN, "power_save": "off", "throttled": "0x0",
                "ping_small": {"sent": 10, "lost": 0, "avg_ms": 5.0},
                "ping_big": {"sent": 10, "lost": 0, "avg_ms": 7.0}}
        data.update(changes)
        return data

    def drops(self, *moments, local=True):
        return [{"at": float(at), "reason": 0 if local else 7, "local": local} for at in moments]

    def test_a_clean_link_has_nothing_to_say(self):
        self.assertEqual(net_diag.verdict(self.data()), [])

    def test_drops_at_bluetooth_attempts_name_the_shared_radio(self):
        lines = net_diag.verdict(self.data(drops=self.drops(100, 200, 300), pages=[101.0, 150.0, 299.0]))
        self.assertEqual(len(lines), 1)
        self.assertIn("2 of the 3 drops", lines[0])
        self.assertIn("shares the Wi-Fi's radio", lines[0])

    def test_a_usb_controller_is_not_blamed_for_the_radio(self):
        lines = net_diag.verdict(self.data(drops=self.drops(100, 200), pages=[100.0, 200.0],
                                           speaker_controller=DONGLE))
        self.assertIn("2 of the 2 drops", lines[0])
        self.assertNotIn("shares", lines[0])

    def test_drops_with_no_attempt_near_them_are_the_radios_own(self):
        lines = net_diag.verdict(self.data(drops=self.drops(100, 200, 300), pages=[150.0]))
        self.assertIn("the Pi itself gave the link up 3 time(s)", lines[0])

    def test_an_access_point_that_ends_the_link_is_named_with_its_reason(self):
        lines = net_diag.verdict(self.data(drops=self.drops(100, 200, local=False)))
        self.assertIn("the access point ended the link 2 time(s) (reason 7)", lines[0])

    def test_large_frames_lost_alone_is_the_air(self):
        lines = net_diag.verdict(self.data(ping_big={"sent": 10, "lost": 3, "avg_ms": 96.0}))
        self.assertIn("interference on the air", lines[0])

    def test_no_link_a_weak_signal_power_save_and_throttling_are_said(self):
        self.assertIn("not connected", net_diag.verdict(self.data(station=net_diag.parse_station("")))[0])
        weak = dict(net_diag.parse_station(STATION), signal=-78.0)
        lines = net_diag.verdict(self.data(station=weak, power_save="on", throttled="0x50005"))
        self.assertEqual([line.split(":")[0].split(" (")[0] for line in lines],
                         ["weak signal", "Wi-Fi power save is on", "the Pi reports under-voltage or throttling"])


class ReportTest(unittest.TestCase):
    def answers(self, cmd, timeout=8):
        line = " ".join(str(part) for part in cmd)
        if "wlan0 station dump" in line:
            return STATION
        if "get power_save" in line:
            return "Power save: off\n"
        if "wpa_supplicant" in line:
            return SUPPLICANT
        if "-u bluetooth" in line:
            return BLUETOOTH
        if "ip route" in line:
            return "default via 192.168.42.254 dev wlan0 proto dhcp\n"
        if "get_throttled" in line:
            return "throttled=0x0\n"
        return ""

    def test_the_report_holds_the_link_the_drops_and_a_finding(self):
        with mock.patch.object(net_diag, "_out", side_effect=self.answers), \
                mock.patch.object(net_diag, "ping", return_value={"sent": 10, "lost": 0, "avg_ms": 6.0}), \
                mock.patch.object(net_diag.audio_diag, "controller_list", return_value=[BUILTIN]), \
                mock.patch.object(net_diag.audio_diag, "daemon_status", return_value={"mode": "music"}), \
                mock.patch.object(net_diag.audio_diag, "_temperature", return_value="48.3'C"):
            text = net_diag.report(cfg={"SPEAKER_BT_ADAPTER": "hci0"})
        self.assertIn("wlan0: signal -46 dBm, tx 65 MBit/s", text)
        self.assertIn("ping 192.168.42.254, 1400 bytes: 0 of 10 lost, 6 ms", text)
        self.assertIn(": 3, of which 2 at a Bluetooth connection attempt (3 attempts gave up)", text)
        self.assertIn("reason 7, by the access point", text)
        self.assertIn("speaker's controller: hci0 (UART), the Wi-Fi's own radio", text)
        self.assertIn(" - 2 of the 3 drops came within 3s", text)

    def test_nothing_readable_is_a_report_all_the_same(self):
        with mock.patch.object(net_diag, "_out", return_value=""), \
                mock.patch.object(net_diag.audio_diag, "controller_list", return_value=None), \
                mock.patch.object(net_diag.audio_diag, "daemon_status", return_value=None), \
                mock.patch.object(net_diag.audio_diag, "_temperature", return_value="temperature unknown"):
            text = net_diag.report(cfg={})
        self.assertIn("wlan0: not connected", text)
        self.assertIn("no route through wlan0", text)


if __name__ == "__main__":
    unittest.main()

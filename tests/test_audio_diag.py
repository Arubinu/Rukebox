"""The audio diagnostic (src/audio_diag.py): what it reads from a controller
listing, which SBC bitpool a measured bitrate means, and what it concludes."""
import unittest

import _path  # noqa: F401
import audio_diag

HCICONFIG = """hci0:\tType: Primary  Bus: USB
\tBD Address: 00:A7:50:72:14:C4  ACL MTU: 1021:6  SCO MTU: 240:1
\tUP RUNNING PSCAN
\tRX bytes:5462 acl:79 sco:0 events:306 errors:0
\tTX bytes:92877 acl:225 sco:0 commands:73 errors:0
hci1:\tType: Primary  Bus: UART
\tBD Address: B8:27:EB:62:82:CB  ACL MTU: 1021:8  SCO MTU: 64:1
\tDOWN
\tRX bytes:0 acl:0 sco:0 events:0 errors:0
\tTX bytes:0 acl:0 sco:0 commands:0 errors:0
"""


class HciTest(unittest.TestCase):
    def test_reads_every_controller(self):
        found = audio_diag.parse_hciconfig(HCICONFIG)
        self.assertEqual([c["name"] for c in found], ["hci0", "hci1"])
        self.assertEqual(found[0]["bus"], "USB")
        self.assertEqual(found[0]["address"], "00:A7:50:72:14:C4")
        self.assertTrue(found[0]["up"])
        self.assertEqual(found[0]["tx_bytes"], 92877)
        self.assertFalse(found[1]["up"])
        self.assertEqual(found[1]["tx_bytes"], 0)

    def test_nothing_to_read_is_empty(self):
        self.assertEqual(audio_diag.parse_hciconfig(""), [])
        self.assertEqual(audio_diag.parse_hciconfig(None), [])


class BitpoolTest(unittest.TestCase):
    def test_the_table_answers_the_three_quality_rungs(self):
        self.assertEqual(audio_diag.bitpool_from_kbps(210), 35)
        self.assertEqual(audio_diag.bitpool_from_kbps(320), 53)
        self.assertEqual(audio_diag.bitpool_from_kbps(450), 76)
        self.assertIsNone(audio_diag.bitpool_from_kbps(40))


class VerdictTest(unittest.TestCase):
    def data(self, **over):
        base = {
            "output": "bluetooth", "codec": "sbc",
            "sinks": [{"kind": "bluetooth", "description": "speaker",
                       "codec": "sbc", "address": "7C:E9:13:69:66:55"}],
            "default_sink": "bluez_output.7C_E9_13_69_66_55.1", "default_description": "speaker",
            "controllers": [], "mpv": {}, "daemon": {}, "kbps": None, "kbps_seconds": 0,
        }
        base.update(over)
        return base

    def test_a_healthy_path_says_the_codec(self):
        lines = audio_diag.verdict(self.data(kbps=450))
        self.assertTrue(any("Bluetooth codec: sbc" in line for line in lines), lines)
        self.assertTrue(any("about 76, out of 76" in line for line in lines), lines)

    def test_the_weakest_rung_is_named(self):
        lines = audio_diag.verdict(self.data(kbps=205))
        self.assertTrue(any("lowest rung" in line for line in lines), lines)

    def test_playing_into_the_void_is_the_first_finding(self):
        lines = audio_diag.verdict(self.data(default_sink="auto_null",
                                             default_description="Dummy Output"))
        self.assertIn("something the radio makes no sound with", lines[0])

    def test_kernel_timeouts_and_the_compression_are_reported(self):
        lines = audio_diag.verdict(self.data(
            speaker={"connected": True, "controller": "00:A7:50:72:14:C4"},
            controllers=[{"name": "hci0", "bus": "USB", "up": True, "tx_bytes": 1, "marks": 7,
                          "address": "00:A7:50:72:14:C4", "speaker": True}],
            mpv={"af": [{"name": "acompressor", "params": {}},
                        {"name": "volume", "params": {"@0": "5dB"}}]},
        ))
        self.assertTrue(any("7 kernel timeout" in line for line in lines), lines)
        self.assertTrue(any("volume boost is ON" in line for line in lines), lines)

    def test_a_timeout_on_the_other_controller_is_not_a_finding(self):
        lines = audio_diag.verdict(self.data(controllers=[
            {"name": "hci1", "bus": "UART", "up": True, "tx_bytes": 1, "marks": 6, "speaker": False},
        ]))
        self.assertEqual([line for line in lines if "kernel timeout" in line], [])


if __name__ == "__main__":
    unittest.main()

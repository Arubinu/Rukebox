"""The audio diagnostic (src/audio_diag.py): what it reads from a controller
listing, which SBC bitpool a measured bitrate means, and what it concludes."""
import types
import unittest
from unittest import mock

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


# What `pactl list sink-inputs` prints, trimmed to what is read.
STREAMS = """Sink Input #2534
\tDriver: PipeWire
\tSink: 2545
\tClient: 2533
\t\tapplication.name = "mpv"
\t\tmedia.name = "Outside - mpv"

Sink Input #2601
\tSink: 59
\tClient: 2600
\t\tapplication.name = "firefox"
"""


class StreamTest(unittest.TestCase):
    """Keeping the sound on the output the settings chose, when WirePlumber
    would hand it to a speaker that just connected."""

    def test_every_stream_is_read_with_its_output_and_its_owner(self):
        self.assertEqual(audio_diag.parse_streams(STREAMS),
                         [(2534, 2545, "mpv"), (2601, 59, "firefox")])
        self.assertEqual(audio_diag.parse_streams(""), [])
        self.assertEqual(audio_diag.parse_streams(None), [])

    def run_with(self, streams, sinks):
        """The commands sent, with `pactl` answering by name."""
        sent = []

        def fake(cmd, timeout=6, env=None):
            sent.append(cmd)
            text = ""
            if cmd[:3] == ["pactl", "list", "short"]:
                text = sinks
            elif cmd[:3] == ["pactl", "list", "sink-inputs"]:
                text = streams
            return types.SimpleNamespace(returncode=0, stdout=text)

        with mock.patch.object(audio_diag, "_run", side_effect=fake):
            moved = audio_diag.move_streams_to("alsa_output.usb-X")
        return moved, sent

    def test_a_stream_that_drifted_is_put_back(self):
        moved, sent = self.run_with(STREAMS, "59\talsa_output.usb-X\tPipeWire\n"
                                             "2545\tbluez_output.SPK.1\tPipeWire\n")
        self.assertEqual(moved, 1, "the browser stream is not ours to move")
        self.assertIn(["pactl", "move-sink-input", "2534", "59"], sent)

    def test_a_stream_already_there_is_left_alone(self):
        moved, sent = self.run_with(STREAMS, "59\talsa_output.usb-X\tPipeWire\n"
                                             "2545\talsa_output.usb-X\tPipeWire\n")
        self.assertEqual(moved, 0)
        self.assertNotIn(["pactl", "move-sink-input", "2534", "59"], sent)

    def test_an_output_that_is_not_there_moves_nothing(self):
        moved, sent = self.run_with(STREAMS, "2545\tbluez_output.SPK.1\tPipeWire\n")
        self.assertEqual(moved, 0)
        self.assertEqual(sent, [["pactl", "list", "short", "sinks"]])


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

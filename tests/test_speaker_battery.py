"""The speaker's battery: one warning when it runs low, given back once it is
charged again, and a statistics row per ten-percent step."""
import unittest
from unittest import mock

import _path  # noqa: F401
from test_empty_library import DaemonCase


class SpeakerBatteryTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.cfg.update({"SPEAKER_BATTERY_LOW": 15, "BATTERY_LOW_SOUND": "/sounds/low.wav"})
        self.sounds = []
        self.events = []
        self.daemon._play_cue_sound = lambda path, key=None: self.sounds.append(path)
        self.daemon.stats = mock.Mock()
        self.daemon.stats.record.side_effect = lambda kind, **kw: self.events.append((kind, kw.get("label")))

    def feed(self, *levels):
        for level in levels:
            self.daemon._note_speaker_battery(level)

    def test_one_warning_per_discharge(self):
        self.feed(40, 16, 15, 12, 9)
        self.assertEqual(self.sounds, ["/sounds/low.wav"])
        self.assertEqual([e for e in self.events if e[0] == "speaker_battery_low"],
                         [("speaker_battery_low", "15 %")])

    def test_a_charge_gives_the_warning_back(self):
        self.feed(12, 20, 14)
        self.assertEqual(len(self.sounds), 1, "20 % is not charged enough to warn again")
        self.feed(30, 14)
        self.assertEqual(len(self.sounds), 2)

    def test_each_ten_percent_step_is_recorded_once(self):
        self.feed(85, 84, 80, 79, None, 79)
        self.assertEqual([e for e in self.events if e[0] == "speaker_battery"],
                         [("speaker_battery", "85 %"), ("speaker_battery", "79 %")])

    def test_zero_turns_the_warning_off(self):
        self.daemon.cfg["SPEAKER_BATTERY_LOW"] = 0
        self.feed(5)
        self.assertEqual(self.sounds, [])

    def test_no_sound_set_still_warns_on_the_page(self):
        self.daemon.cfg["BATTERY_LOW_SOUND"] = ""
        self.feed(10)
        self.assertEqual(self.sounds, [])
        self.assertIn(("speaker_battery_low", "10 %"), self.events)


if __name__ == "__main__":
    unittest.main()

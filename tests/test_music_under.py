"""The music kept under an announcement: the announcement plays in a second
player, the music goes down and comes back, and nothing pauses."""
import time
import unittest
from unittest import mock

import _path  # noqa: F401
from test_empty_library import DaemonCase


class MusicUnderTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.cfg["ANNOUNCE_MUSIC_UNDER"] = 20
        self.daemon.mode = "music"
        self.played = []
        self.factors = []
        self.daemon._play_cue_blocking = lambda path, volume=None: self.played.append((path, volume))
        self.daemon._set_duck = lambda factor, seconds=0.8: self.factors.append(factor)

    def wait(self):
        deadline = time.monotonic() + 3
        while self.daemon._duck_proc is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)

    def test_the_announcement_plays_over_the_music(self):
        self.assertTrue(self.daemon._play_ducked("custom:morning", ["/a/jingle.mp3"]))
        self.wait()
        self.assertEqual(self.played, [("/a/jingle.mp3", None)])
        self.assertEqual(self.factors, [0.2, 1.0], "down to 20 %, then back")
        self.assertEqual(self.daemon.mode, "music")
        self.assertNotEqual(self.daemon.mpv.paused, True, "the music never paused")

    def test_its_action_follows(self):
        with mock.patch.object(self.daemon, "_perform_direct_action") as action:
            self.daemon._play_ducked("custom:evening", ["/a/x.mp3"], after="standby")
            self.wait()
        action.assert_called_once_with("standby", "announcement")

    def test_off_paused_or_already_playing_one_is_the_old_way(self):
        self.daemon.cfg["ANNOUNCE_MUSIC_UNDER"] = 0
        self.assertFalse(self.daemon._play_ducked("custom:a", ["/a/x.mp3"]))
        self.daemon.cfg["ANNOUNCE_MUSIC_UNDER"] = 20
        self.daemon._paused = True
        self.assertFalse(self.daemon._play_ducked("custom:a", ["/a/x.mp3"]))

    def test_a_scheduled_announcement_does_not_pause_the_music(self):
        item = {"id": "morning", "name": "Morning", "folder": self.dir, "trigger": "time"}
        with mock.patch.object(self.daemon, "_next_announce_file", return_value=["/a/jingle.mp3"]), \
                mock.patch.object(self.daemon, "_fade_out_and_pause") as pause:
            self.daemon._trigger_custom_announcement(item)
            self.wait()
        pause.assert_not_called()
        self.assertEqual(self.played[0][0], "/a/jingle.mp3")
        self.assertTrue(self.daemon.state.already_triggered_today("custom_morning"))

    def test_a_skip_stops_the_sound_over_the_music(self):
        proc = mock.Mock()
        self.daemon._duck_proc = proc
        self.assertTrue(self.daemon._skip_ducked())
        proc.terminate.assert_called_once()
        self.assertFalse(self.daemon._skip_ducked())


if __name__ == "__main__":
    unittest.main()

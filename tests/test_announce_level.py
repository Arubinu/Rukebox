"""Announcements brought to the music's loudness (src/announce_level.py), and the
music faded back in after a paused announcement."""
import os
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import announce_level
from test_empty_library import DaemonCase


class GainTest(unittest.TestCase):
    def test_a_quiet_file_is_raised_without_clipping(self):
        self.assertEqual(announce_level.gain_for((-27.3, -12.4), -14), 11.4, "the peak stops it at -1 dBFS")
        self.assertEqual(announce_level.gain_for((-30.0, -20.0), -14), 12.0, "never more than 12 dB")
        self.assertEqual(announce_level.gain_for((-10.0, -0.5), -14), -4.0, "a loud one comes down")
        self.assertEqual(announce_level.gain_for(None, -14), 0.0)


class LevelsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sound = os.path.join(self.dir, "a.mp3")
        with open(self.sound, "wb") as f:
            f.write(b"x")
        self.store = os.path.join(self.dir, "levels.json")

    def test_a_file_is_measured_once_until_it_changes(self):
        levels = announce_level.Levels(self.store)
        with mock.patch.object(announce_level, "measure", return_value=(-20.0, -3.0)) as measure:
            self.assertEqual(levels.measure(self.sound), (-20.0, -3.0))
            self.assertEqual(announce_level.Levels(self.store).measure(self.sound), (-20.0, -3.0))
            self.assertEqual(measure.call_count, 1, "remembered across restarts")
            with open(self.sound, "wb") as f:
                f.write(b"longer")
            levels.measure(self.sound)
            self.assertEqual(measure.call_count, 2)

    def test_a_failure_is_not_measured_again_and_a_gone_file_is_forgotten(self):
        levels = announce_level.Levels(self.store)
        with mock.patch.object(announce_level, "measure", return_value=None) as measure:
            levels.warm([self.dir])
            levels.warm([self.dir])
            self.assertEqual(measure.call_count, 1)
        self.assertIsNone(levels.known(self.sound))
        os.remove(self.sound)
        levels.warm([self.dir])
        self.assertEqual(levels._data, {})


    def test_a_measure_written_by_the_other_process_is_seen(self):
        radio = announce_level.Levels(self.store)
        self.assertIsNone(radio.known(self.sound))
        with mock.patch.object(announce_level, "measure", return_value=(-18.0, -2.0)):
            announce_level.Levels(self.store).warm([self.dir])
        self.assertEqual(radio.known(self.sound), (-18.0, -2.0))


class ResumeFadeTest(DaemonCase):
    def test_the_music_comes_back_faded_in_after_a_paused_announcement(self):
        self.daemon.cfg["INTERACTIVE_FADE_DURATION_SEC"] = 2
        self.daemon._resume_mode = "music"
        with mock.patch.object(self.daemon, "_faded_in") as faded, \
                mock.patch.object(self.daemon, "_play_next_track") as nxt:
            self.daemon._resume_after_announce()
        start, fade = faded.call_args[0]
        self.assertEqual(fade, 2)
        self.assertTrue(faded.call_args[1]["music_only"])
        start()
        nxt.assert_called_once()

    def test_an_announcement_file_is_played_with_its_gain(self):
        with mock.patch.object(self.daemon, "_announce_gain", return_value=6.5):
            self.daemon._begin_play("custom:morning", "/a/jingle.mp3")
            self.assertEqual(self.daemon.mpv.volume_gain, 6.5)
            self.daemon._begin_play("music", "/a/song.mp3")
            self.assertEqual(self.daemon.mpv.volume_gain, 0.0)


if __name__ == "__main__":
    unittest.main()

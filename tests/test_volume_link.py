"""The speaker's own volume as the radio's volume (speaker_volume_link): the
slider then moves what the speaker does, and a press on the speaker's buttons
moves the slider. wpctl and mpv are both faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
import audio_diag
from config_and_scan import load_config
import rukebox_daemon


class FakeMpv:
    def __init__(self):
        self.volume = None
        self.files = []

    def loadfile(self, path):
        self.files.append(path)

    def set_volume(self, volume):
        self.volume = volume

    def set_pause(self, paused):
        pass

    def set_loop(self, mode="no"):
        pass

    def stop_playback(self):
        pass


class VolumeLinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.dir,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "BASE_VOLUME": 30,
            "VOLUME_CHANGE": "instant",
        })
        self.cfg = cfg
        self.daemon = None
        # session_env() reads /run/user/<uid>, which Windows has no notion of.
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(rukebox_daemon, "audio_env", return_value={}).start()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, linked):
        self.cfg["SPEAKER_VOLUME_LINK"] = linked
        daemon = rukebox_daemon.RadioDaemon(self.cfg)
        daemon.mpv = FakeMpv()
        daemon._clock_ready = threading.Event()
        daemon.mode = "music"
        return daemon

    def test_the_slider_moves_the_speaker_and_mpv_keeps_its_gain(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume",
                               return_value=True) as sink:
            daemon._set_user_volume(55, "web")
        self.assertEqual(sink.call_args[0][0], 55.0)
        self.assertEqual(daemon.mpv.volume, 100.0)
        self.assertEqual(daemon._shown_volume, 55)
        self.assertEqual(daemon._sink_level, 55.0)

    def test_a_sound_keeps_its_own_level_inside_the_speakers_volume(self):
        daemon = self.build(linked=True)
        daemon._sound_volume = 80
        with mock.patch.object(audio_diag, "set_default_sink_volume", return_value=True):
            daemon._write_level(50)
        self.assertEqual(daemon.mpv.volume, 80)

    def test_without_the_option_the_slider_stays_software(self):
        daemon = self.build(linked=False)
        with mock.patch.object(audio_diag, "set_default_sink_volume") as sink:
            daemon._set_user_volume(55, "web")
        self.assertFalse(sink.called, "the speaker is not touched")
        self.assertEqual(daemon.mpv.volume, 55.0)
        self.assertEqual(daemon._shown_volume, 55)

    def test_the_first_turn_hands_the_interface_volume_to_the_speaker(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume",
                               return_value=True) as sink:
            daemon._follow_sink_volume()
        self.assertEqual(sink.call_args[0][0], 30.0)   # BASE_VOLUME
        self.assertEqual(daemon._sink_level, 30.0)

    def test_a_press_on_the_speaker_moves_the_slider(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume", return_value=True):
            daemon._follow_sink_volume()             # the first turn: link it up
        with mock.patch.object(audio_diag, "default_sink_volume", return_value=0.42):
            daemon._follow_sink_volume()
        self.assertEqual(daemon._shown_volume, 42.0)
        self.assertEqual(daemon._user_volume, 42.0)
        self.assertEqual(daemon._current_volume, 42.0)
        self.assertEqual(daemon._sink_level, 42.0)

    def test_our_own_write_is_not_read_back_as_a_press(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume", return_value=True):
            daemon._set_user_volume(55, "web")
        with mock.patch.object(audio_diag, "default_sink_volume", return_value=0.55):
            daemon._follow_sink_volume()
        self.assertEqual(daemon._shown_volume, 55)
        self.assertEqual(daemon._user_volume, 55)

    def test_the_watcher_forgets_the_level_when_the_option_goes_off(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume", return_value=True):
            daemon._follow_sink_volume()
        daemon.cfg["SPEAKER_VOLUME_LINK"] = False
        daemon._follow_sink_volume()
        self.assertIsNone(daemon._sink_level)

    def test_a_speaker_that_cannot_be_reached_falls_back_to_mpv(self):
        daemon = self.build(linked=True)
        with mock.patch.object(audio_diag, "set_default_sink_volume", return_value=False):
            daemon._set_user_volume(55, "web")
        self.assertEqual(daemon.mpv.volume, 55.0, "the radio keeps its own volume")
        self.assertIsNone(daemon._sink_level)


class SinkCommandTest(unittest.TestCase):
    def test_the_arg_is_a_fraction_and_never_above_full_scale(self):
        with mock.patch.object(audio_diag, "_run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(audio_diag.set_default_sink_volume(40))
            self.assertEqual(run.call_args[0][0], ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.40"])
            audio_diag.set_default_sink_volume(150)
            self.assertEqual(run.call_args[0][0][-1], "1.00")

    def test_something_that_is_not_a_number_is_refused(self):
        with mock.patch.object(audio_diag, "_run") as run:
            self.assertFalse(audio_diag.set_default_sink_volume("loud"))
        self.assertFalse(run.called)

    def test_wpctl_being_unavailable_is_answered_not_raised(self):
        with mock.patch.object(audio_diag, "_run", return_value=None):
            self.assertFalse(audio_diag.set_default_sink_volume(50))
        with mock.patch.object(audio_diag, "_run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="")
            self.assertFalse(audio_diag.set_default_sink_volume(50))

    def test_the_current_level_is_read_back(self):
        with mock.patch.object(audio_diag, "_run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="Volume: 0.70\n")
            self.assertEqual(audio_diag.default_sink_volume(), 0.70)
            run.return_value = mock.Mock(returncode=0, stdout="nonsense")
            self.assertIsNone(audio_diag.default_sink_volume())


if __name__ == "__main__":
    unittest.main()

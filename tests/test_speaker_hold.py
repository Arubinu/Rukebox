"""A song that starts while the speaker is away stays paused, and resumes with
it: a click otherwise undoes the pause `_on_speaker_lost()` just made, and the
radio plays to nothing for as long as the speaker is gone. mpv is faked, so
this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import audio_output
import rukebox_daemon

MAC = "7C:E9:13:69:66:55"


class FakeMpv:
    """Records what the daemon asked mpv to do."""

    def __init__(self):
        self.files = []
        self.volume = None
        self.paused = None

    def loadfile(self, path):
        self.files.append(path)

    def set_volume(self, volume):
        self.volume = volume

    def set_pause(self, paused):
        self.paused = paused

    def set_loop(self, mode="no"):
        pass

    def stop_playback(self):
        self.files.append(None)

    def set_audio_filter(self, chain):
        return True

    def seek(self, seconds):
        self.seeked = seconds

    def set_replaygain(self, mode):
        pass

    def set_mute(self, muted):
        pass

    def set_audio_device(self, device):
        pass

    def observe(self, prop_id, name):
        pass

    def on_event(self, callback):
        pass


class SpeakerHoldTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.music = os.path.join(self.dir, "music")
        os.makedirs(self.music, exist_ok=True)
        self.track = os.path.join(self.music, "a.mp3")
        with open(self.track, "wb") as f:
            f.write(b"x" * 10)

        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "SPEAKER_MAC": MAC,
            "SPEAKER_LOSS_PAUSE": True,
            "SPEAKER_RESUME_FADE_SEC": 0,
            "MUSIC_START_MODE": "boot",
            "AUDIO_OUTPUT": "bluetooth",
            "INTERACTIVE_FADE_DURATION_SEC": 0,
            "BASE_VOLUME": 30,
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.daemon.mode = "music"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_waiting_for_the_speaker_only_matters_when_the_speaker_is_the_output(self):
        """Reported as: with the sound going to the USB sound card, the radio
        still waited for the Bluetooth speaker and said so on Now playing. The
        setting keeps its value - the interface strikes it through - and it means
        "at startup" while the output is a wired one."""
        self.assertEqual(self.daemon._start_mode(), "boot", "the chosen mode")
        self.daemon.cfg["MUSIC_START_MODE"] = "bluetooth"
        self.assertEqual(self.daemon._start_mode(), "bluetooth",
                         "the speaker IS the output: waiting makes sense")
        self.daemon.cfg["AUDIO_OUTPUT"] = "usb"
        self.assertEqual(self.daemon._start_mode(), "boot",
                         "the speaker is not the output: nothing to wait for")
        status = self.daemon._build_status()
        self.assertTrue(status["music_start_mode_inert"])
        self.assertEqual(status["music_start_mode"], "bluetooth",
                         "and the setting itself is left alone")

    def speaker_goes_away(self):
        self.daemon._on_speaker_lost(MAC)

    def test_a_nudge_from_the_web_server_checks_the_speaker_at_once(self):
        turns = []
        self.daemon._speaker_watch_turn = lambda: turns.append(1) or 10.0
        self.assertEqual(self.daemon._speaker_watch_now(), 10.0)
        self.assertEqual(turns, [1])

    def test_a_fallback_output_plays_on_instead_of_pausing(self):
        self.daemon.cfg["AUDIO_FALLBACK_OUTPUT"] = "usb"
        with mock.patch.object(rukebox_daemon, "audio_env", return_value={}), \
                mock.patch.object(audio_output, "list_sinks", return_value=[]), \
                mock.patch.object(audio_output, "find", return_value={"name": "usb"}), \
                mock.patch.object(audio_output, "mpv_device", return_value=("pipewire/usb", True)):
            self.speaker_goes_away()
        self.assertNotEqual(self.daemon.mpv.paused, True)
        self.assertEqual(self.daemon._output_override, "usb")

    def test_a_fallback_output_that_is_not_there_pauses_as_before(self):
        self.daemon.cfg["AUDIO_FALLBACK_OUTPUT"] = "usb"
        with mock.patch.object(rukebox_daemon, "audio_env", return_value={}), \
                mock.patch.object(audio_output, "list_sinks", return_value=[]), \
                mock.patch.object(audio_output, "find", return_value=None):
            self.speaker_goes_away()
        self.assertTrue(self.daemon.mpv.paused)
        self.assertIsNone(self.daemon._output_override)

    def test_a_song_started_with_the_speaker_away_stays_paused(self):
        self.speaker_goes_away()
        self.daemon._play_track(self.track)
        self.assertTrue(self.daemon.mpv.paused, "kept off the air")
        self.assertTrue(self.daemon._paused)
        self.assertTrue(self.daemon._paused_for_speaker)

    def test_the_speaker_coming_back_resumes_it(self):
        self.speaker_goes_away()
        self.daemon._play_track(self.track)
        self.assertTrue(self.daemon.mpv.paused)
        self.daemon._on_speaker_back(MAC)
        self.assertFalse(self.daemon.mpv.paused)
        self.assertFalse(self.daemon._paused)
        self.assertFalse(self.daemon._paused_for_speaker)
        self.assertEqual(self.daemon.mpv.volume, 30)

    def test_a_song_started_with_the_speaker_there_plays(self):
        self.daemon._play_track(self.track)
        self.assertFalse(self.daemon.mpv.paused)
        self.assertFalse(self.daemon._paused_for_speaker)

    def test_nothing_is_held_when_the_pause_on_loss_is_off(self):
        self.daemon.cfg["SPEAKER_LOSS_PAUSE"] = False
        self.speaker_goes_away()
        self.daemon._play_track(self.track)
        self.assertFalse(self.daemon.mpv.paused)

    def test_nothing_is_held_for_a_wired_output(self):
        self.daemon.cfg["AUDIO_OUTPUT"] = "jack"
        self.daemon._apply_audio_output = lambda *args, **kwargs: None
        self.speaker_goes_away()
        self.daemon._play_track(self.track)
        self.assertFalse(self.daemon.mpv.paused)

    def test_a_speaker_alert_already_paused_stays_paused_once(self):
        self.speaker_goes_away()
        self.daemon._play_track(self.track)
        self.daemon._play_track(self.track)
        self.assertTrue(self.daemon.mpv.paused)
        self.assertTrue(self.daemon._paused_for_speaker)


if __name__ == "__main__":
    unittest.main()

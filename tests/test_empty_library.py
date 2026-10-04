"""A radio installed before its music: it waits instead of "playing" nothing,
starts when the music arrives, and its cutoff does not wait for a song that
never comes. mpv is faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import rukebox_daemon
from test_speaker_hold import FakeMpv


class DaemonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.music = os.path.join(self.dir, "music")
        os.makedirs(self.music)
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "START_FADE_SEC": 0,
            "SPEAKER_VOLUME_LINK": False,
        })
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(rukebox_daemon, "audio_env", return_value={}).start()
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class EmptyLibraryTest(DaemonCase):
    def boot_start(self):
        self.daemon._start_music_faded(self.daemon._play_next_track)

    def test_a_start_with_no_music_waits_instead_of_playing_nothing(self):
        self.boot_start()
        self.assertEqual(self.daemon.mode, "idle")
        self.assertEqual(self.daemon.mpv.files, [])

    def test_music_sent_afterwards_starts_at_the_rescan(self):
        self.boot_start()
        track = os.path.join(self.music, "a.mp3")
        with open(track, "wb") as f:
            f.write(b"x" * 10)
        self.assertEqual(self.daemon._rescan_music("web"), 1)
        self.assertEqual(self.daemon.mode, "music")
        self.assertIn(track, self.daemon.mpv.files)

    def test_a_rescan_does_not_start_music_nobody_asked_for(self):
        self.daemon.mode = "idle"
        with open(os.path.join(self.music, "a.mp3"), "wb") as f:
            f.write(b"x" * 10)
        self.daemon._rescan_music("web")
        self.assertEqual(self.daemon.mode, "idle")
        self.assertEqual(self.daemon.mpv.files, [])

    def test_the_cutoff_does_not_wait_for_a_song_that_is_not_there(self):
        self.daemon.mode = "music"
        self.daemon._current_track = None
        with mock.patch.object(self.daemon, "_schedule_tick"), \
                mock.patch.object(self.daemon, "_cutoff_due", return_value=True), \
                mock.patch.object(self.daemon, "_trigger_cutoff_from_idle") as from_idle, \
                mock.patch.object(self.daemon, "_arm_cutoff_end_of_track") as end_of_track:
            self.daemon._scheduler_tick()
        from_idle.assert_called_once()
        end_of_track.assert_not_called()


class PendingCutoffTest(DaemonCase):
    """A cutoff waiting for the end of a track does not outlive its evening."""

    def end_of_track(self, waited_sec):
        self.daemon.cfg["CUTOFF_MODE"] = "end_of_track"
        self.daemon.mode = "music"
        self.daemon.state.set_pending_cutoff(True)
        self.daemon.state.data["pending_cutoff_at"] -= waited_sec
        with mock.patch.object(self.daemon, "_start_cutoff_announce_now") as cutoff, \
                mock.patch.object(self.daemon, "_play_next_track") as following:
            self.daemon._on_mpv_event({"event": "end-file", "reason": "eof"})
        return cutoff.called, following.called

    def test_a_recent_cutoff_still_happens_at_the_end_of_the_track(self):
        self.assertEqual(self.end_of_track(5 * 60), (True, False))

    def test_a_cutoff_left_waiting_too_long_is_dropped(self):
        self.assertEqual(self.end_of_track(31 * 60), (False, True))
        self.assertFalse(self.daemon.state.is_pending_cutoff())


if __name__ == "__main__":
    unittest.main()

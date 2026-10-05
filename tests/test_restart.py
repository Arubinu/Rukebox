"""The planned restart: at the end of the song while music plays, at once when
nothing does - a pause is not a song to wait for. mpv is faked."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import rukebox_daemon


class FakeMpv:
    def set_volume(self, volume):
        pass

    def set_pause(self, paused):
        pass

    def loadfile(self, path):
        pass

    def stop_playback(self):
        pass

    def set_loop(self, mode="no"):
        pass


class RestartTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.dir,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            # No cue: where the sound is installed, the restart waits for its end.
            "RESTART_SOUND": "",
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.restart = mock.patch.object(self.daemon, "_restart_now").start()
        self.addCleanup(mock.patch.stopall)

    def test_music_playing_waits_for_the_end_of_the_song(self):
        self.daemon.mode = "music"
        self.daemon._schedule_restart(True)
        self.assertTrue(self.daemon._restart_pending)
        self.assertFalse(self.restart.called)

    def test_a_pause_is_not_a_song_to_wait_for(self):
        self.daemon.mode = "music"
        self.daemon._paused = True
        self.daemon._schedule_restart(True)
        self.assertEqual(self.daemon.mode, "restarting")
        self.assertTrue(self.restart.called)

    def test_nothing_playing_restarts_at_once(self):
        for mode in ("idle", "stopped"):
            self.restart.reset_mock()
            self.daemon.mode = mode
            self.daemon._paused = False
            self.daemon._schedule_restart(True)
            self.assertTrue(self.restart.called, mode)

    def test_the_status_says_which_one_it_is(self):
        self.daemon.mode = "music"
        self.daemon._paused = False
        self.assertFalse(self.daemon._build_status()["restart_direct"])
        self.daemon._paused = True
        self.assertTrue(self.daemon._build_status()["restart_direct"])
        self.daemon._paused = False
        self.daemon.mode = "idle"
        self.assertTrue(self.daemon._build_status()["restart_direct"])

    def test_turning_it_off_cancels_it(self):
        self.daemon.mode = "music"
        self.daemon._schedule_restart(True)
        self.daemon._schedule_restart(False)
        self.assertFalse(self.daemon._restart_pending)
        self.assertFalse(self.restart.called)

    def test_the_whole_device_restarts_at_the_end_of_the_song_when_asked(self):
        self.daemon.mode = "music"
        self.daemon._paused = False
        self.daemon._schedule_restart(True, "reboot")
        self.assertTrue(self.daemon._restart_pending)
        self.assertEqual(self.daemon._build_status()["restart_target"], "reboot")
        self.daemon._play_next_track()
        self.assertTrue(self.restart.called)

    def test_a_reboot_runs_systemctl_reboot_and_says_so(self):
        mock.patch.stopall()
        popen = mock.patch.object(rukebox_daemon.subprocess, "Popen").start()
        self.daemon._restart_target = "reboot"
        self.daemon._restart_now()
        self.assertEqual(popen.call_args[0][0], ["sudo", "systemctl", "reboot"])
        self.assertEqual(self.daemon._build_status()["powering_off"], "reboot")


if __name__ == "__main__":
    unittest.main()

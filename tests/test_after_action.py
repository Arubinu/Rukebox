"""What follows an announcement that started on its own, and the cutoff playing
one file like every other announcement."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
import announcements
from config_and_scan import load_config
import rukebox_daemon


class FakeMpv:
    def __init__(self):
        self.files = []
        self.paused = False

    def loadfile(self, path):
        self.files.append(path)

    def set_pause(self, paused):
        self.paused = paused

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def touch(folder, *names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"x")


class AfterActionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(mock.patch.stopall)
        music = os.path.join(self.dir, "music")
        self.sounds = os.path.join(self.dir, "morning")
        self.cutoff = os.path.join(self.dir, "cutoff")
        touch(music, "a.mp3", "b.mp3")
        touch(self.sounds, "one.mp3", "two.mp3")
        touch(self.cutoff, "bye1.mp3", "bye2.mp3", "bye3.mp3")
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "CUTOFF_ANNOUNCE_DIR": self.cutoff,
            "ANNOUNCE_ORDER_MODE": "ordered",
            "FADE_DURATION_SEC": 0, "INTERACTIVE_FADE_DURATION_SEC": 0,
            "LONGPRESS_FADE_DURATION_SEC": 0, "PAUSE_FADE_SEC": 0, "START_FADE_SEC": 0,
            "SPEAKER_VOLUME_LINK": False,
        })
        mock.patch.object(rukebox_daemon, "audio_env", return_value={}).start()
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.daemon.mode = "music"

    def item(self, action):
        return {"id": "morning", "name": "Morning", "folder": self.sounds,
                "trigger": "time", "after_action": action}

    def play(self, action, on_demand=False):
        self.daemon._trigger_custom_announcement(self.item(action), on_demand=on_demand)
        self.assertEqual(self.daemon.mode, "custom:morning")
        self.daemon._after_announce_finished(self.daemon.mode)

    def test_by_default_the_music_comes_back(self):
        self.play("none")
        self.assertEqual(self.daemon.mode, "music")
        self.assertFalse(self.daemon._paused)

    def test_pause_leaves_the_next_song_paused(self):
        self.play("pause")
        self.assertEqual(self.daemon.mode, "music")
        self.assertTrue(self.daemon._paused)
        self.assertTrue(self.daemon.mpv.paused)

    def test_a_play_started_by_hand_does_nothing_more(self):
        self.play("pause", on_demand=True)
        self.assertEqual(self.daemon.mode, "music")
        self.assertFalse(self.daemon._paused)

    def test_standby_waits_like_at_startup(self):
        self.play("standby")
        self.assertEqual(self.daemon.mode, "idle")

    def test_poweroff_switches_the_pi_off(self):
        with mock.patch.object(self.daemon, "_do_shutdown_sequence") as shutdown:
            self.play("poweroff")
        shutdown.assert_called_once_with(force=True, reason="announcement", tail=True)
        self.assertEqual(self.daemon.mode, "shutting_down")

    def test_another_action_runs_once_the_music_is_back(self):
        self.play("loop_track")
        self.assertEqual(self.daemon.mode, "music")
        self.assertEqual(self.daemon._loop_mode, "track")

    def test_the_action_does_not_leak_into_the_next_announcement(self):
        self.play("pause")
        self.daemon._set_pause(False, "web")
        self.play("none")
        self.assertFalse(self.daemon._paused)

    def test_the_cutoff_plays_one_file(self):
        with mock.patch.object(self.daemon, "_do_shutdown_sequence") as shutdown:
            self.daemon._trigger_cutoff_event_exact()
            self.assertEqual(self.daemon.mode, "cutoff_announce")
            self.assertEqual(self.daemon._announce_queue, [], "nothing queued behind it")
            self.assertEqual(os.path.basename(self.daemon.mpv.files[-1]), "bye1.mp3")
            self.daemon._after_announce_finished("cutoff_announce")
        shutdown.assert_called_once_with(tail=True)

    def test_the_speaker_plays_the_end_of_the_sound_before_the_pi_goes(self):
        self.daemon.cfg.update({"SHUTDOWN_AFTER_CUTOFF": True, "SPEAKER_MAC": "7C:E9:13:69:66:55"})
        steps = mock.Mock()
        mock.patch.object(rukebox_daemon.time, "sleep", steps.sleep).start()
        mock.patch.object(rukebox_daemon.subprocess, "run", steps.run).start()
        mock.patch.object(self.daemon, "_bluetoothctl", steps.bluetoothctl).start()
        self.daemon._do_shutdown_sequence(tail=True)
        self.assertEqual([call[0] for call in steps.mock_calls], ["sleep", "bluetoothctl", "run"],
                         "waited, then the speaker let go, then the power-off")
        self.assertEqual(steps.sleep.call_args[0][0], self.daemon.SHUTDOWN_TAIL_SEC)
        self.assertEqual(steps.run.call_args[0][0], ["sudo", "systemctl", "poweroff"])


class AfterActionFieldTest(unittest.TestCase):
    def base(self, **extra):
        return dict({"name": "Morning", "folder": "/home/pi/audio/morning"}, **extra)

    def test_an_announcement_without_the_field_does_nothing_more(self):
        self.assertEqual(announcements.validate(self.base())["after_action"], "none")

    def test_a_known_action_is_kept(self):
        self.assertEqual(announcements.validate(self.base(after_action="standby"))["after_action"], "standby")

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError) as refused:
            announcements.validate(self.base(after_action="next"))
        self.assertEqual(str(refused.exception), "announcement_bad_action")


if __name__ == "__main__":
    unittest.main()

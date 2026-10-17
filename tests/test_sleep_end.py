"""The sleep timer that lets the song end (src/rukebox_daemon.py): it waits for the song's
own last seconds, fades there, keeps the next song from starting early, and gives up waiting
for a song far too long to wait for."""
import os
from unittest import mock

import _path  # noqa: F401
from test_empty_library import DaemonCase


class SleepEndTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.mode = "music"
        self.daemon._paused = False
        self.daemon._play_kind = "music"
        self.daemon._last_music_track = os.path.join(self.music, "a.mp3")
        self.daemon.cfg["FADE_DURATION_SEC"] = 4
        self.daemon._duration = 200.0
        self.daemon._position = 80.0
        self.fades = mock.patch.object(self.daemon, "_fade_out_to_zero").start()
        # The timers run at once, as their thread would.
        mock.patch.object(self.daemon, "_set_timer",
                          side_effect=lambda name, delay, fn=None: fn() if fn and delay else None).start()

    def test_it_waits_for_the_end_of_the_song_then_fades_there(self):
        self.daemon._finish_track = True
        self.daemon._sleep_timer_over()
        self.assertFalse(self.daemon._paused, "the song goes on")
        self.assertTrue(self.daemon._build_status()["sleep_at_end"])
        self.assertFalse(self.daemon._crossfade_ok(), "the next song does not start early")
        self.daemon._position = 150.0
        self.daemon._near_end()
        self.assertFalse(self.daemon._paused)
        self.daemon._position = 196.0
        self.daemon._near_end()
        self.assertTrue(self.daemon._paused, "paused over the song's own last seconds")
        self.fades.assert_called_once_with(4.0)
        self.assertFalse(self.daemon._sleep_at_end)

    def test_without_the_choice_it_fades_at_once(self):
        self.daemon._finish_track = False
        self.daemon._sleep_timer_over()
        self.assertTrue(self.daemon._paused)

    def test_a_song_far_too_long_is_not_waited_for(self):
        self.daemon._finish_track = True
        self.daemon._duration = 3600.0
        self.daemon._sleep_timer_over()
        self.assertTrue(self.daemon._paused)
        self.assertFalse(self.daemon._sleep_at_end)

    def test_a_button_cancels_the_wait_and_a_new_timer_forgets_it(self):
        self.daemon._finish_track = True
        self.daemon._sleep_timer_over()
        self.assertEqual(self.daemon._perform_direct_action("sleep", "test"), "sleep_off")
        self.assertFalse(self.daemon._sleep_at_end)
        self.daemon._sleep_at_end = True
        with mock.patch.object(self.daemon, "_set_timer"):
            self.daemon._start_sleep_timer(30, "test")
        self.assertFalse(self.daemon._sleep_at_end)

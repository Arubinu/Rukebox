"""The night cap: between two times everything plays at a share of the volume chosen, which stays
the one shown (100% of a 60% cap plays at 60%)."""
import threading
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
from mpv_controller import MPVController
from test_empty_library import DaemonCase


class ControllerScaleTest(unittest.TestCase):
    def setUp(self):
        self.mpv = MPVController("/tmp/none.sock")
        self.sent = []
        self.mpv._send = self.sent.append

    def test_every_level_is_played_at_the_cap_and_a_new_cap_applies_at_once(self):
        self.mpv.set_volume(100)
        self.mpv.set_scale(0.6)
        self.mpv.set_volume(50)
        self.mpv.set_scale(1.0)
        self.assertEqual([command[2] for command in self.sent], [100, 60.0, 30.0, 50])

    def test_the_same_cap_writes_nothing_more(self):
        self.mpv.set_volume(80)
        self.mpv.set_scale(1.0)
        self.assertEqual(len(self.sent), 1)


class DaemonCapTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.cfg.update(VOLUME_CAP_ENABLED=True, VOLUME_CAP_PERCENT=60, VOLUME_CAP_FADE_SEC=0,
                               VOLUME_CAP_START_HOUR=21, VOLUME_CAP_START_MINUTE=30,
                               VOLUME_CAP_END_HOUR=7, VOLUME_CAP_END_MINUTE=0)

    def at(self, hour, minute=0):
        return datetime(2026, 10, 10, hour, minute)

    def test_the_hours_run_past_midnight_and_the_end_is_not_capped(self):
        cap = self.daemon._volume_cap
        self.assertEqual([cap(self.at(*t)) for t in ((21, 29), (21, 30), (0, 0), (6, 59), (7, 0), (12, 0))],
                         [None, 60, 60, 60, None, None])

    def test_the_hours_may_sit_inside_one_day_or_cover_all_of_it(self):
        self.daemon.cfg.update(VOLUME_CAP_START_HOUR=12, VOLUME_CAP_START_MINUTE=0,
                               VOLUME_CAP_END_HOUR=14, VOLUME_CAP_END_MINUTE=0)
        self.assertEqual([self.daemon._volume_cap(self.at(h)) for h in (11, 12, 13, 14)], [None, 60, 60, None])
        self.daemon.cfg.update(VOLUME_CAP_END_HOUR=12)
        self.assertEqual(self.daemon._volume_cap(self.at(3)), 60, "the same two times cap the whole day")

    def test_switched_off_there_is_no_cap(self):
        self.daemon.cfg["VOLUME_CAP_ENABLED"] = False
        self.assertIsNone(self.daemon._volume_cap(self.at(23)))

    def test_the_cap_scales_mpv_and_the_disposable_players_but_not_the_volume_shown(self):
        self.daemon._shown_volume = 100
        with mock.patch.object(self.daemon, "_bump_state") as bumped:
            self.daemon._apply_volume_cap(self.at(23))
            self.daemon._apply_volume_cap(self.at(23))
        self.assertEqual(self.daemon.mpv.scale, 0.6)
        self.assertEqual(bumped.call_count, 1, "only a change is told")
        self.assertEqual(self.daemon._build_status()["volume"], 100)
        self.assertEqual(self.daemon._capped(None), 60.0)
        self.assertEqual(self.daemon._capped(50), 30.0)
        self.daemon._apply_volume_cap(self.at(9))
        self.assertEqual((self.daemon.mpv.scale, self.daemon._capped(None)), (1.0, None))

    def test_the_hours_beginning_glide_down_and_a_save_cuts_the_glide_short(self):
        self.daemon.cfg["VOLUME_CAP_FADE_SEC"] = 1
        self.daemon._apply_volume_cap(self.at(23))
        self.assertEqual(self.daemon.mpv.scale, 1.0, "not at once")
        for _ in range(50):
            if self.daemon.mpv.scale == 0.6:
                break
            threading.Event().wait(0.05)
        self.assertAlmostEqual(self.daemon.mpv.scale, 0.6)
        self.daemon.cfg["VOLUME_CAP_FADE_SEC"] = 5
        self.daemon._apply_volume_cap(self.at(9))
        self.daemon._apply_volume_cap(self.at(23), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 0.6, "a save applies at once, whatever was gliding")

    def test_a_setting_saved_applies_at_once(self):
        with mock.patch.object(self.daemon, "_apply_volume_cap") as applied:
            fresh = dict(self.daemon.cfg, VOLUME_CAP_PERCENT=40)
            self.daemon._apply_config(fresh)
        applied.assert_called_once()


if __name__ == "__main__":
    unittest.main()

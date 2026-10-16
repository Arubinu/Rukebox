"""The daily volume curve: its maths, its block, and what the daemon does with it.

A curve is points on the clock; between two of them the level is a straight
line, two points at the same minute make a cut, and the line from the last
point back to the first crosses midnight.
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import config_document
import volume_curve
from test_empty_library import DaemonCase


class CurveMathsTest(unittest.TestCase):
    def points(self, *pairs):
        return volume_curve.clean_points([{"at": at, "level": level} for at, level in pairs])

    def test_a_single_point_holds_the_whole_day(self):
        flat = self.points((0, 40))
        self.assertEqual([volume_curve.level_at(flat, m) for m in (0, 600, 1439)], [40.0] * 3)

    def test_the_level_walks_from_one_point_to_the_next(self):
        day = self.points((13 * 60, 40), (15 * 60, 70), (20 * 60, 70))
        self.assertEqual(volume_curve.level_at(day, 13 * 60), 40.0)
        self.assertEqual(volume_curve.level_at(day, 14 * 60), 55.0, "half way, half the rise")
        self.assertEqual(volume_curve.level_at(day, 15 * 60), 70.0)
        self.assertEqual(volume_curve.level_at(day, 20 * 60), 70.0, "and it holds between two")
        # The last point leads back to the first: the day is a circle, so a
        # radio with two points ramps both ways and holds nowhere.
        two = self.points((13 * 60, 40), (15 * 60, 70))
        self.assertLess(volume_curve.level_at(two, 20 * 60), 70.0)

    def test_the_line_crosses_midnight(self):
        # 22:00 at 100%, 06:00 at 40%: the descent runs over those eight hours.
        night = self.points((6 * 60, 40), (22 * 60, 100))
        self.assertEqual(volume_curve.level_at(night, 22 * 60), 100.0)
        self.assertEqual(volume_curve.level_at(night, 2 * 60), 70.0, "half of the eight hours")
        self.assertEqual(volume_curve.level_at(night, 6 * 60), 40.0)
        self.assertEqual(volume_curve.level_at(night, 14 * 60), 70.0, "the rise the other way")

    def test_two_points_at_the_same_minute_make_a_cut(self):
        cut = self.points((6 * 60, 40), (6 * 60, 100), (22 * 60, 100), (22 * 60, 40))
        self.assertTrue(volume_curve.is_cut(cut, 6 * 60))
        self.assertFalse(volume_curve.is_cut(cut, 7 * 60))
        self.assertEqual(volume_curve.level_at(cut, 6 * 60), 100.0, "the second point wins")
        self.assertEqual(volume_curve.level_at(cut, 5 * 60 + 59), 40.0, "and the first holds before")
        self.assertEqual(volume_curve.level_at(cut, 22 * 60), 40.0, "the other cut, both ways")

    def test_a_change_is_a_rise_a_fall_or_a_cut(self):
        day = self.points((6 * 60, 40), (9 * 60, 100), (20 * 60, 100), (20 * 60, 20), (22 * 60, 20))
        kinds = [(one["at"], one["kind"], one["seconds"]) for one in volume_curve.describe(day)]
        # 06:00 is arrived at from the day before, then the rise to 09:00, then
        # the cut at 20:00: the levels that stand still are not lines at all.
        self.assertEqual(kinds, [(6 * 60, "rise", 8 * 3600), (9 * 60, "rise", 3 * 3600),
                                 (20 * 60, "cut", 0)])
        self.assertEqual(volume_curve.describe(day)[1]["from"], 40, "the level it left")
        self.assertEqual(volume_curve.describe(day)[1]["to"], 100)
        self.assertEqual(volume_curve.describe(day)[2]["from"], 100, "and the cut, both ways")
        self.assertEqual(volume_curve.describe(day)[2]["to"], 20)

    def test_the_next_point_is_the_next_one_on_the_clock(self):
        day = self.points((0, 70), (13 * 60, 40), (15 * 60, 70))
        self.assertEqual(volume_curve.next_point(day, 10 * 60), 3 * 60)
        self.assertEqual(volume_curve.next_point(day, 14 * 60), 60)
        self.assertEqual(volume_curve.next_point(day, 20 * 60), 4 * 60, "round the clock")
        self.assertIsNone(volume_curve.next_point([], 0))

    def test_what_makes_no_sense_is_left_out(self):
        raw = [{"at": -5, "level": 10}, {"at": 10, "level": 300}, "x", {},
               {"at": 10.4, "level": 55.6}, {"at": "600", "level": "40"}]
        self.assertEqual(volume_curve.clean_points(raw), [(10, 100), (10, 56), (600, 40)])
        self.assertEqual(volume_curve.clean_points(None), [])
        four = volume_curve.MINUTES_PER_DAY - 1
        self.assertIsNone(volume_curve.clean_points([{"at": four + 1, "level": 10}]) or None,
                          "a minute past the day is not a minute")


class CurveBlockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)

    def test_the_curve_is_a_block_of_the_document(self):
        volume_curve.save([(6 * 60, 40), (22 * 60, 100)], self.path)
        self.assertEqual(volume_curve.load(self.path), [(360, 40), (1320, 100)])
        self.assertEqual(config_document.read("volume_curve", self.path),
                         {"points": [{"at": 360, "level": 40}, {"at": 1320, "level": 100}]})
        with open(self.path, encoding="utf-8") as handle:
            self.assertIn("volume_curve:", handle.read())

    def test_a_document_we_cannot_read_is_not_a_curve(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("volume_curve:\n  points: [ oops\n")
        with self.assertLogs("config_document", level="ERROR"):
            self.assertIsNone(volume_curve.load(self.path))

    def test_no_curve_at_all_is_an_empty_one(self):
        self.assertEqual(volume_curve.load(self.path), [])


class DaemonCurveTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.cfg.update(VOLUME_SHAPE="curve", VOLUME_CURVE_MANUAL="scaled")
        volume_curve.save([(6 * 60, 40), (22 * 60, 100)])

    def at(self, hour, minute=0):
        return datetime(2026, 10, 10, hour, minute)

    def test_the_level_follows_the_clock(self):
        self.assertEqual(self.daemon._curve_level(self.at(22)), 100.0)
        self.assertEqual(self.daemon._curve_level(self.at(2)), 70.0)
        self.assertEqual(self.daemon._curve_level(self.at(6)), 40.0)

    def test_the_curve_scales_mpv_and_not_the_volume_shown(self):
        self.daemon._shown_volume = 100
        with mock.patch.object(self.daemon, "_bump_state") as bumped:
            # A ramp walks over a second, so what it lands on is read at once.
            self.daemon._apply_volume_shape(self.at(6), glide=False)
            self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 0.4)
        self.assertEqual(bumped.call_count, 1, "only a change is told")
        self.assertEqual(self.daemon._build_status()["volume"], 100)
        self.assertEqual(self.daemon._capped(None), 40.0)
        self.daemon._apply_volume_shape(self.at(22), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 1.0)

    def test_the_status_says_what_the_curve_gives(self):
        self.daemon.cfg["VOLUME_SHAPE"] = "curve"
        with mock.patch.object(self.daemon, "_curve_level", return_value=42.0):
            status = self.daemon._build_status()
        self.assertEqual(status["volume_curve"], 42.0)
        self.assertEqual(status["volume_shape"], "curve")

    def test_a_cut_lands_at_once_and_the_ramp_walks(self):
        volume_curve.save([(6 * 60, 40), (6 * 60, 100), (22 * 60, 100), (22 * 60, 40)])
        self.daemon._apply_volume_shape(self.at(6))
        self.assertEqual(self.daemon.mpv.scale, 1.0, "the cut is not a glide")
        self.daemon._apply_volume_shape(self.at(22))
        self.assertEqual(self.daemon.mpv.scale, 0.4, "nor is the other one")

    def test_a_shape_nobody_chose_still_reads_the_cap(self):
        # A radio that updated: no volume_shape in the file, the cap's switch on.
        self.daemon.cfg.update(VOLUME_SHAPE="", VOLUME_CAP_ENABLED=True, VOLUME_CAP_PERCENT=60,
                               VOLUME_CAP_FADE_SEC=0, VOLUME_CAP_START_HOUR=21,
                               VOLUME_CAP_START_MINUTE=0, VOLUME_CAP_END_HOUR=7,
                               VOLUME_CAP_END_MINUTE=0)
        self.assertEqual(self.daemon._volume_shape(), "cap")
        self.daemon._apply_volume_shape(self.at(23))
        self.assertEqual(self.daemon.mpv.scale, 0.6)

    def test_the_shape_decides_once_it_is_named(self):
        self.daemon.cfg.update(VOLUME_CAP_ENABLED=True, VOLUME_SHAPE="curve")
        self.assertEqual(self.daemon._volume_shape(), "curve", "the choice wins over the old switch")
        self.daemon.cfg["VOLUME_SHAPE"] = "off"
        self.assertEqual(self.daemon._volume_shape(), "off")
        self.daemon.cfg["VOLUME_SHAPE"] = "nonsense"
        self.assertEqual(self.daemon._volume_shape(), "cap", "and nonsense falls back to the switch")

    def test_going_back_to_the_cap_takes_its_own_scale_again(self):
        self.daemon.cfg.update(VOLUME_CAP_ENABLED=True, VOLUME_CAP_PERCENT=40,
                               VOLUME_CAP_FADE_SEC=0, VOLUME_CAP_START_HOUR=0,
                               VOLUME_CAP_END_HOUR=23, VOLUME_CAP_END_MINUTE=59)
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 0.4, "the curve")
        self.daemon.cfg["VOLUME_SHAPE"] = "cap"
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 0.4, "the cap, at the same share")

    def test_a_setting_saved_applies_at_once(self):
        with mock.patch.object(self.daemon, "_apply_volume_shape") as applied:
            self.daemon._apply_config(dict(self.daemon.cfg, VOLUME_SHAPE="off"))
        applied.assert_called_once()


if __name__ == "__main__":
    unittest.main()

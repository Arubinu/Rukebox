"""The daily volume curve: its maths, its block, and what the daemon does with it."""
import os
import shutil
import socket
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import config_document
import control_client
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


class CurveValuesTest(unittest.TestCase):
    def test_a_value_that_is_no_number_is_dropped(self):
        self.assertEqual(volume_curve.clean_points([{"at": float("inf"), "level": 50},
                                                    {"at": 60, "level": float("nan")},
                                                    {"at": 120, "level": float("-inf")},
                                                    {"at": 180, "level": 40}]),
                         [(180, 40)])


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


class DaemonCurveManualTest(DaemonCase):
    """scaled multiplies, force drives and refuses, shifted moves, paused steps aside."""

    def setUp(self):
        super().setUp()
        volume_curve.save([(6 * 60, 40), (22 * 60, 100)])
        self.daemon.cfg.update(VOLUME_SHAPE="curve", VOLUME_CHANGE="instant")

    def at(self, hour, minute=0):
        return datetime(2026, 10, 10, hour, minute)

    def shape(self, manual):
        self.daemon.cfg["VOLUME_CURVE_MANUAL"] = manual

    def test_scaled_lets_the_hand_choose_and_multiplies(self):
        self.shape("scaled")
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertTrue(self.daemon._set_user_volume(80, "web"))
        self.assertEqual(self.daemon._user_volume, 80)
        self.assertEqual(self.daemon.mpv.scale, 0.4, "and the curve still multiplies")

    def test_forced_the_curve_is_the_volume_and_the_hand_is_refused(self):
        self.shape("force")
        with mock.patch.object(self.daemon, "_bump_state"):
            self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon._user_volume, 40, "the curve's level is the volume")
        self.assertEqual(self.daemon.mpv.scale, 1.0, "and nothing multiplies it")
        self.assertFalse(self.daemon._set_user_volume(80, "web"), "a hand is told no")
        self.assertEqual(self.daemon._user_volume, 40, "and nothing moved")
        self.assertTrue(self.daemon._build_status()["volume_forced"])

    def test_shifted_moves_the_whole_curve_with_the_hand(self):
        self.shape("shifted")
        with mock.patch.object(self.daemon, "_bump_state"):
            self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon._user_volume, 40, "the curve's own level")
        # A hand arrives at whatever the clock says: the test says 06:00 for it.
        with mock.patch.object(self.daemon, "_curve_level", return_value=40.0):
            self.assertTrue(self.daemon._set_user_volume(60, "web"))
        self.assertAlmostEqual(self.daemon._curve_offset, 20.0, msg="raised by twenty")
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon._user_volume, 60, "the same stretch, twenty higher")
        self.daemon._apply_volume_shape(self.at(22), glide=False)
        self.assertEqual(self.daemon._user_volume, 100, "and the day's peak, capped at 100")

    def test_paused_steps_aside_until_the_next_dot(self):
        self.shape("paused")
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 0.4)
        self.daemon._set_user_volume(70, "web")
        self.assertTrue(self.daemon._curve_paused(), "the curve stepped aside")
        self.daemon._apply_volume_shape(self.at(7), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 1.0, "the volume chosen plays as it is")
        # The next dot is 22:00: from there the curve applies again.
        self.daemon._curve_paused_until = None
        self.daemon._apply_volume_shape(self.at(22), glide=False)
        self.assertEqual(self.daemon.mpv.scale, 1.0, "the peak is the full volume anyway")

    def test_a_curve_that_is_not_driving_leaves_the_hand_alone(self):
        self.shape("scaled")
        self.daemon._apply_volume_shape(self.at(6), glide=False)
        self.assertEqual(self.daemon._build_status()["volume_forced"], False)
        self.assertTrue(self.daemon._set_user_volume(55, "web"))


try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


@unittest.skipUnless(ws is not None, "Flask is not installed (run these on the Pi)")
@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
class VolumeNumberTest(DaemonCase):
    def test_a_volume_that_is_no_number_is_refused(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        for value in ("nan", "inf", "-inf"):
            answer = control_client.send_control_command(path, "set_volume", value=value)
            self.assertEqual(answer.get("error"), "invalid_value", value)


class CurveRouteTest(unittest.TestCase):
    """The route the strip talks to: the points, the words, the level now."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.document = _path.document(self.dir)
        self.client = ws.app.test_client()
        volume_curve.save([(360, 40), (1320, 100)], self.document)

    def test_it_gives_the_points_the_words_and_the_level_now(self):
        data = self.client.get("/api/volume_curve").get_json()["data"]
        self.assertEqual([one["at"] for one in data["points"]], [360, 1320])
        self.assertEqual([one["kind"] for one in data["summary"]], ["fall", "rise"])
        self.assertEqual(data["summary"][0]["seconds"], 28800, "eight hours down to 06:00")
        self.assertIn(data["shape"], ("off", "cap", "curve"))
        self.assertEqual(data["now"]["minute"], datetime.now().hour * 60 + datetime.now().minute)
        self.assertIsNotNone(data["now"]["level"])

    def test_it_saves_what_the_page_sends(self):
        answer = self.client.post("/api/volume_curve",
                                  json={"points": [{"at": 60, "level": 30},
                                                   {"at": 60, "level": 90}]})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(volume_curve.load(self.document), [(60, 30), (60, 90)])

    def test_the_socket_tells_a_hand_that_the_curve_is_in_charge(self):
        with mock.patch.object(ws, "control",
                               return_value={"ok": False, "error": "volume_forced"}):
            answer = self.client.post("/api/volume", json={"value": 80})
        self.assertEqual((answer.status_code, answer.get_json()["error"]), (400, "volume_forced"))

    def test_what_makes_no_sense_is_dropped_rather_than_stored(self):
        self.client.post("/api/volume_curve", json={"points": [{"at": -1, "level": 30},
                                                               {"at": 90, "level": 42}]})
        self.assertEqual(volume_curve.load(self.document), [(90, 42)])


if __name__ == "__main__":
    unittest.main()

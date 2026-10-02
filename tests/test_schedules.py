"""Schedules: when one runs, what it changes while it does, and what the
daemon does at its start and at its stop."""
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import announcements
from config_and_scan import load_config
import music_lists
import rukebox_daemon
import schedules


def at(day, hour, minute, second=0):
    """October 2026: the 5th is a Monday."""
    return datetime(2026, 10, day, hour, minute, second)


def make(**fields):
    base = {"name": "Morning", "start": "07:00", "stop": "09:00"}
    base.update(fields)
    return dict(schedules.validate(base), id=fields.get("id", "morning"))


class RulesTest(unittest.TestCase):
    def test_a_schedule_needs_a_name_and_a_time(self):
        for data, code in (
            ({"name": "", "start": "07:00"}, "schedule_name_required"),
            ({"name": "x" * 41, "start": "07:00"}, "schedule_name_too_long"),
            ({"name": "A"}, "schedule_no_time"),
            ({"name": "A", "start": "7h"}, "schedule_bad_time"),
            ({"name": "A", "start": "07:00", "stop": "07:00"}, "schedule_bad_time"),
            ({"name": "A", "start": "07:00", "days": [7]}, "schedule_bad_days"),
            ({"name": "A", "start": "07:00", "date": "2026-13-01"}, "schedule_bad_date"),
            ({"name": "A", "start": "07:00", "stop_action": "explode"}, "schedule_bad_action"),
            ({"name": "A", "start": "07:00", "settings": {"WEB_PORT": "81"}}, "schedule_bad_setting"),
            ({"name": "A", "start": "07:00", "settings": {"MUSIC_DIR": "/etc"}}, "schedule_bad_setting"),
            ({"name": "A", "start": "07:00", "settings": {"BASE_VOLUME": "loud"}}, "schedule_bad_setting"),
            ({"name": "A", "start": "07:00", "announcement": "../etc"}, "schedule_bad_announcement"),
        ):
            with self.assertRaises(ValueError, msg=str(data)) as refused:
                schedules.validate(data)
            self.assertEqual(str(refused.exception), code)

    def test_a_date_replaces_the_weekdays(self):
        clean = schedules.validate({"name": "Party", "start": "20:00", "days": [4, 5], "date": "2026-10-10"})
        self.assertEqual(clean["days"], [])
        self.assertEqual(clean["date"], "2026-10-10")

    def test_settings_are_kept_as_text_and_read_typed(self):
        clean = schedules.validate({"name": "A", "start": "07:00",
                                    "settings": {"BASE_VOLUME": 35, "MUSIC_LOOP": False}})
        self.assertEqual(clean["settings"], {"BASE_VOLUME": "35", "MUSIC_LOOP": "false"})
        self.assertEqual(schedules.overrides(clean), {"BASE_VOLUME": 35, "MUSIC_LOOP": False})

    def test_an_opening_announcement_needs_a_start(self):
        self.assertEqual(schedules.validate({"name": "A", "start": "07:00", "announcement": "hello"})
                         ["announcement"], "hello")
        self.assertIsNone(schedules.validate({"name": "A", "stop": "23:00", "announcement": "hello"})
                          ["announcement"], "a stop alone opens nothing")
        self.assertIsNone(schedules.validate({"name": "A", "start": "07:00"})["announcement"])

    def test_weekdays_no_day_at_all_and_one_date(self):
        week = make(days=[0, 1, 2, 3, 4])
        self.assertTrue(schedules.start_due(week, at(5, 7, 0, 20)))
        self.assertFalse(schedules.start_due(week, at(5, 7, 1)))
        self.assertFalse(schedules.start_due(week, at(10, 7, 0)), "a Saturday")
        self.assertTrue(schedules.start_due(make(), at(10, 7, 0)), "no day chosen is every day")
        once = make(date="2026-10-10")
        self.assertTrue(schedules.start_due(once, at(10, 7, 0)))
        self.assertFalse(schedules.start_due(once, at(11, 7, 0)))

    def test_a_schedule_runs_from_its_start_to_its_stop(self):
        item = make(days=[0])
        self.assertIsNone(schedules.window(item, at(5, 6, 59)))
        self.assertEqual(schedules.window(item, at(5, 7, 0)), (at(5, 7, 0), at(5, 9, 0)))
        self.assertIsNotNone(schedules.window(item, at(5, 8, 59, 59)))
        self.assertIsNone(schedules.window(item, at(5, 9, 0)))
        self.assertIsNone(schedules.window(item, at(6, 8, 0)), "a Tuesday")

    def test_a_stop_before_the_start_is_the_next_morning(self):
        night = make(start="22:00", stop="01:30", days=[4])
        self.assertIsNotNone(schedules.window(night, at(9, 23, 0)), "Friday night")
        self.assertIsNotNone(schedules.window(night, at(10, 1, 0)), "still Friday's run on Saturday")
        self.assertIsNone(schedules.window(night, at(10, 1, 30)))
        self.assertTrue(schedules.stop_due(night, at(10, 1, 30)))
        self.assertFalse(schedules.stop_due(night, at(9, 1, 30)), "Friday 01:30 ends Thursday's, which has none")
        self.assertIsNone(schedules.window(night, at(10, 23, 0)), "Saturday night is not Friday's")

    def test_without_a_stop_it_runs_to_the_end_of_the_day_and_a_stop_alone_runs_nothing(self):
        self.assertEqual(schedules.window(make(stop=None), at(5, 23, 59))[1], at(6, 0, 0))
        only_stop = make(start=None, stop="23:00", stop_action="standby")
        self.assertIsNone(schedules.window(only_stop, at(5, 12, 0)))
        self.assertTrue(schedules.stop_due(only_stop, at(5, 23, 0)))

    def test_a_date_beats_the_week_then_the_first_of_the_list(self):
        week = make(id="week", name="Week")
        other = make(id="other", name="Other")
        dated = make(id="party", name="Party", date="2026-10-05")
        off = make(id="off", name="Off", date="2026-10-05", enabled=False)
        self.assertEqual(schedules.active([week, other, off, dated], at(5, 8, 0))["id"], "party")
        self.assertEqual(schedules.active([week, other, off, dated], at(6, 8, 0))["id"], "week")
        self.assertIsNone(schedules.active([week], at(6, 10, 0)))

    def test_the_next_start_to_come(self):
        item, when = schedules.next_start([make(days=[0])], at(5, 9, 30))
        self.assertEqual(when, at(12, 7, 0))
        self.assertIsNone(schedules.next_start([make(date="2026-10-01")], at(5, 9, 30)))

    def test_the_file_keeps_ids_and_refuses_to_replace_what_it_cannot_read(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = os.path.join(folder, "schedules.json")
        first = schedules.add(path, {"name": "Le matin", "start": "07:00"})
        second = schedules.add(path, {"name": "Le matin", "start": "08:00"})
        self.assertEqual((first["id"], second["id"]), ("le-matin", "le-matin-2"))
        schedules.update(path, "le-matin", {"stop": "09:00", "enabled": False})
        self.assertEqual(schedules.load(path)[0]["stop"], "09:00")
        self.assertFalse(schedules.load(path)[0]["enabled"])
        third = schedules.add(path, {"name": "Le soir", "start": "20:00"})
        ordered = schedules.reorder(path, [third["id"], "gone", "le-matin-2", third["id"]])
        self.assertEqual([item["id"] for item in ordered], ["le-soir", "le-matin-2", "le-matin"],
                         "those named first, the others after, nothing lost")
        self.assertEqual([item["id"] for item in schedules.load(path)], ["le-soir", "le-matin-2", "le-matin"])
        with self.assertRaises(ValueError):
            schedules.reorder(path, "le-soir")
        schedules.delete(path, "le-matin-2")
        schedules.delete(path, "le-soir")
        self.assertEqual(len(schedules.load(path)), 1)
        with open(path, "w") as f:
            f.write("{ not json")
        with self.assertLogs("json_file", level="ERROR"), self.assertRaises(ValueError) as refused:
            schedules.add(path, {"name": "B", "start": "07:00"})
        self.assertEqual(str(refused.exception), "file_unreadable")


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


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(mock.patch.stopall)
        music = os.path.join(self.dir, "music")
        os.makedirs(os.path.join(music, "Calm"))
        for name in ("a.mp3", "b.mp3", os.path.join("Calm", "c.mp3")):
            with open(os.path.join(music, name), "wb") as f:
                f.write(b"x")
        self.path = os.path.join(self.dir, "schedules.json")
        self.lists = os.path.join(self.dir, "lists.json")
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "MUSIC_LISTS_FILE": self.lists,
            "SCHEDULES_FILE": self.path,
            "MUSIC_START_MODE": "action", "CUTOFF_HOUR": 3, "CUTOFF_MINUTE": 33,
            "BASE_VOLUME": 70, "VOLUME_CHANGE": "instant", "SPEAKER_VOLUME_LINK": False,
            "START_FADE_SEC": 0, "PAUSE_FADE_SEC": 0, "LONGPRESS_FADE_DURATION_SEC": 0,
            "FADE_DURATION_SEC": 0, "MUSIC_ORDER_MODE": "ordered", "KEEPALIVE_SOUND": "",
        })
        self.base = dict(cfg)
        mock.patch.object(rukebox_daemon, "load_config", lambda **kwargs: dict(self.base)).start()
        mock.patch.object(rukebox_daemon.subprocess, "run").start()
        mock.patch.object(rukebox_daemon.time, "sleep").start()
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.daemon.mode = "idle"

    def tick(self, when):
        with mock.patch.object(rukebox_daemon, "datetime") as clock:
            clock.now.return_value = when
            self.daemon._scheduler_tick()

    def add(self, **fields):
        base = {"name": "Morning", "start": "07:00", "stop": "09:00"}
        base.update(fields)
        return schedules.add(self.path, base)

    def test_the_start_starts_the_music_once(self):
        self.add()
        self.tick(at(5, 6, 59, 45))
        self.assertEqual(self.daemon.mode, "idle")
        self.tick(at(5, 7, 0, 0))
        self.assertEqual(self.daemon.mode, "music")
        self.assertEqual(self.daemon._build_status()["schedule"],
                         {"id": "morning", "name": "Morning", "until": "09:00", "stop_action": "pause"})
        played = len(self.daemon.mpv.files)
        self.tick(at(5, 7, 0, 15))
        self.tick(at(5, 7, 0, 30))
        self.assertEqual(len(self.daemon.mpv.files), played, "one start for the minute")

    def test_its_settings_hold_while_it_runs_and_no_longer(self):
        self.add(settings={"BASE_VOLUME": 30, "MUSIC_LOOP": False, "SINGLE_CLICK_ACTION": "pause"})
        self.tick(at(5, 6, 59))
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 70)
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 30)
        self.assertEqual(self.daemon._shown_volume, 30)
        self.assertIs(self.daemon.cfg["MUSIC_LOOP"], False)
        self.assertEqual(self.daemon.cfg["SINGLE_CLICK_ACTION"], "pause")

        self.base["FADE_DURATION_SEC"] = 4
        self.base["BASE_VOLUME"] = 55
        self.daemon._reload_config()
        self.assertEqual(self.daemon.cfg["FADE_DURATION_SEC"], 4, "a save still applies")
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 30, "but not over the running schedule")

        self.tick(at(5, 9, 0))
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 55)
        self.assertEqual(self.daemon._shown_volume, 55)
        self.assertEqual(self.daemon.cfg["SINGLE_CLICK_ACTION"], self.base["SINGLE_CLICK_ACTION"])
        self.assertIsNone(self.daemon._build_status()["schedule"])

    def test_a_restart_in_the_middle_finds_it_running_but_starts_nothing(self):
        self.add(settings={"BASE_VOLUME": 30})
        self.tick(at(5, 8, 0))
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 30)
        self.assertEqual(self.daemon.mode, "idle", "a start that is past is not replayed")

    def test_the_stop_pauses_goes_to_standby_or_switches_off(self):
        self.add(stop_action="pause")
        self.tick(at(5, 7, 0))
        self.tick(at(5, 9, 0))
        self.assertEqual(self.daemon.mode, "music")
        self.assertTrue(self.daemon._paused)

        schedules.update(self.path, "morning", {"stop_action": "standby", "stop": "09:30"})
        self.daemon._set_pause(False, "test")
        self.tick(at(5, 9, 30))
        self.assertEqual(self.daemon.mode, "idle")

        schedules.update(self.path, "morning", {"stop_action": "poweroff", "stop": "10:00"})
        self.tick(at(5, 10, 0))
        self.assertEqual(self.daemon.mode, "shutting_down")
        rukebox_daemon.subprocess.run.assert_called_with(["sudo", "systemctl", "poweroff"], check=False)

    def test_a_disabled_schedule_does_nothing(self):
        self.add(enabled=False, settings={"BASE_VOLUME": 30})
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.mode, "idle")
        self.assertEqual(self.daemon.cfg["BASE_VOLUME"], 70)

    def test_its_list_is_played_and_the_one_before_comes_back(self):
        calm = music_lists.add(self.lists, {"name": "Calm", "kind": "manual",
                                            "tracks": [os.path.join(self.base["MUSIC_DIR"], "Calm", "c.mp3")]})
        self.add(list=calm["id"])
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.state.active_list(), calm["id"])
        self.assertTrue(self.daemon.mpv.files[-1].endswith("c.mp3"))
        self.tick(at(5, 9, 0))
        self.assertIsNone(self.daemon.state.active_list())

    def announce(self, **fields):
        sounds = os.path.join(self.dir, "hello")
        os.makedirs(sounds, exist_ok=True)
        with open(os.path.join(sounds, "hello.mp3"), "wb") as f:
            f.write(b"x")
        item = dict(announcements.validate(dict({"name": "Hello", "folder": "/sounds"}, **fields)), id="hello")
        item["folder"] = sounds
        announcements.save_all(self.base["ANNOUNCEMENTS_FILE"], [item])

    def test_a_schedule_can_open_with_an_announcement_then_the_music(self):
        self.announce()
        self.add(announcement="hello")
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.mode, "custom:hello")
        self.assertTrue(self.daemon.mpv.files[-1].endswith("hello.mp3"))
        self.daemon._after_announce_finished(self.daemon.mode)
        self.assertEqual(self.daemon.mode, "music")
        self.assertTrue(self.daemon.state.already_triggered_today("last_music_start"))

    def test_with_the_music_already_on_the_announcement_still_plays_and_keeps_its_own_day(self):
        self.announce(trigger="time", hour=12, minute=0)
        self.add(announcement="hello")
        self.daemon._start_or_restart_playback()
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.mode, "custom:hello")
        self.assertFalse(self.daemon.state.already_triggered_today("custom_hello"),
                         "its own time at noon is still to come")

    def test_an_announcement_that_is_gone_or_off_does_not_hold_the_music_back(self):
        self.announce(enabled=False)
        self.add(announcement="hello")
        self.tick(at(5, 7, 0))
        self.assertEqual(self.daemon.mode, "music")
        schedules.update(self.path, "morning", {"announcement": "nobody", "start": "07:05"})
        self.daemon._go_standby("test")
        with self.assertLogs("radio", level="WARNING"):
            self.tick(at(5, 7, 5))
        self.assertEqual(self.daemon.mode, "music")

    def test_the_daily_cutoff_can_be_switched_off(self):
        self.daemon.cfg.update({"CUTOFF_HOUR": 23, "CUTOFF_MINUTE": 30})
        self.assertTrue(self.daemon._cutoff_due(at(5, 23, 30)))
        self.daemon.cfg["CUTOFF_ENABLED"] = False
        self.assertFalse(self.daemon._cutoff_due(at(5, 23, 30, 15)))
        self.assertFalse(self.daemon._build_status()["cutoff_enabled"])
        self.assertIn("CUTOFF_ENABLED", schedules.OVERRIDABLE, "an evening can do without it")

    def test_the_daily_cutoff_can_end_in_standby(self):
        self.daemon.cfg["SHUTDOWN_AFTER_CUTOFF"] = False
        self.daemon._start_or_restart_playback()
        self.daemon._do_shutdown_sequence()
        self.assertEqual(self.daemon.mode, "idle")
        rukebox_daemon.subprocess.run.assert_not_called()
        self.daemon._start_or_restart_playback()
        self.assertEqual(self.daemon.mode, "music", "and the music can start again")
        self.daemon._power_off_now("long_press")
        self.assertEqual(self.daemon.mode, "shutting_down", "a forced power off still powers off")


if __name__ == "__main__":
    unittest.main()

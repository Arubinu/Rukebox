"""Erasing the last run: what the radio did by itself goes back, and the run before stays."""
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from unittest import mock

import _path  # noqa: F401
import blind_test
import rukebox_daemon
import stats
from config_and_scan import load_config


class StatsEraseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.recorder = stats.StatsRecorder(os.path.join(self.dir, "stats.db"), enabled=True)
        self.recorder.open_session(clock_ok=True, clock_source="rtc")

    def tearDown(self):
        self.recorder.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def play(self, name, seconds):
        self.recorder.record(
            "track_played", label=name,
            detail={"kind": "music", "seconds": seconds},
            counters={"tracks_played": 1, "seconds_music": seconds},
            daily={"tracks_played": 1, "seconds_music": seconds},
            item=("music", name), seconds=seconds,
        )

    def test_the_run_holds_what_the_page_shows(self):
        self.play("a.mp3", 60)
        self.play("b.mp3", 120)
        run = self.recorder.run_to_erase()
        self.assertEqual(run["session"], self.recorder.session_id)
        self.assertEqual(run["events"], 3, "the two songs, and the session's own start")
        self.assertEqual(run["tracks"], 2)
        self.assertAlmostEqual(run["seconds"], 180, places=1)
        self.assertTrue(run["open"], "the run in progress is the one offered")
        self.assertEqual([e["label"] for e in self.recorder.run_events(run["session"])],
                         ["b.mp3", "a.mp3", "rtc"], "newest first, the session's start last")

    def test_erasing_gives_every_total_back(self):
        today = date.today()
        self.play("a.mp3", 60)
        self.recorder.record("click", label="single",
                             counters={"clicks_single": 1, "clicks": 1},
                             daily={"clicks_single": 1})
        self.play("b.mp3", 120)
        self.assertEqual(self.recorder.counters()["tracks_played"], 2)
        self.assertIsNotNone(self.recorder.recap(today, today)["morning"])

        self.recorder.erase_run(self.recorder.run_to_erase())

        counters = self.recorder.counters()
        self.assertEqual(counters["tracks_played"], 0)
        self.assertEqual(counters["seconds_music"], 0)
        self.assertEqual(counters["clicks_single"], 0)
        self.assertEqual(counters["clicks"], 0)
        today_values = self.recorder.daily_series(days=1)[0]
        self.assertEqual(today_values["tracks_played"], 0.0)
        self.assertEqual(today_values["seconds_music"], 0.0)
        self.assertEqual(today_values["clicks"], 0.0)
        self.assertEqual(today_values["sessions_started"], 1.0,
                         "the startup itself is not what was erased")
        self.assertEqual(self.recorder.summary()["top"]["music"], [],
                         "the most played songs forget it too")
        recap = self.recorder.recap(today, today)
        self.assertEqual(recap["tracks"], [])
        self.assertIsNone(recap["morning"], "nor is it the song that opened the day")

    def test_erasing_gives_the_tables_back_as_they_were(self):
        """A song played twice in the run, and totals it moved ten times: the
        tables must read exactly what they read before the first of them."""
        self.play("a.mp3", 60)
        self.recorder.record("click", label="single", counters={"clicks_single": 1})
        def tables():
            return {
                "counters": self.recorder.counters(),
                "items": self.recorder._rows("SELECT kind, name, count, seconds, last_at"
                                             " FROM items ORDER BY kind, name"),
                "daily": self.recorder._rows("SELECT day, key, value FROM daily ORDER BY day, key"),
                "monthly": self.recorder._rows("SELECT month, kind, name, count, seconds"
                                               " FROM monthly ORDER BY month, kind, name"),
            }

        self.recorder.open_session(clock_ok=True, clock_source="rtc")
        before = tables()
        self.play("a.mp3", 90)
        self.play("a.mp3", 30)
        self.play("b.mp3", 120)
        self.recorder.record("click", label="single", counters={"clicks_single": 1})
        self.assertNotEqual(tables(), before, "the run did move something")

        self.recorder.erase_run(self.recorder.run_to_erase())

        self.assertEqual(tables(), before)
        self.assertEqual(self.recorder._rows("SELECT name, last_at FROM items WHERE kind = 'music'"),
                         [{"name": "a.mp3", "last_at": before["items"][0]["last_at"]}],
                         "and the played song kept the date it had before the run")

    def test_a_note_from_the_first_shape_still_goes_back(self):
        """The undo note first kept a whole monthly line; a journal written then
        still reverses, the month being read from either shape."""
        import json

        self.play("a.mp3", 60)
        month = date.today().strftime("%Y-%m")
        self.recorder._conn.execute(
            "UPDATE events SET undo = ? WHERE id = (SELECT MAX(id) FROM events)",
            (json.dumps({
                "counters": {"tracks_played": 1.0, "seconds_music": 60.0},
                "daily": {date.today().isoformat(): {"tracks_played": 1.0, "seconds_music": 60.0}},
                "item": {"kind": "music", "name": "a.mp3", "seconds": 60.0, "last_at": None},
                "monthly": {"month": month, "name": "a.mp3", "count": 1.0,
                            "seconds": 60.0, "first": True},
            }),))

        self.recorder.erase_run(self.recorder.run_to_erase())

        recap = self.recorder.recap(date.today(), date.today())
        self.assertEqual(recap["tracks"], [])
        self.assertIsNone(recap["morning"])

    def test_nothing_is_left_to_erase_twice(self):
        self.play("a.mp3", 60)
        self.recorder.erase_run(self.recorder.run_to_erase())
        self.assertIsNone(self.recorder.run_to_erase())
        self.assertEqual(self.recorder.run_events(self.recorder.session_id), [])

    def test_the_run_before_keeps_its_own(self):
        self.play("a.mp3", 60)
        first = self.recorder.session_id
        self.recorder.open_session(clock_ok=True, clock_source="rtc")
        self.play("b.mp3", 120)

        run = self.recorder.run_to_erase()
        self.assertEqual(run["session"], self.recorder.session_id)
        self.recorder.erase_run(run)

        self.assertEqual(self.recorder.counters()["tracks_played"], 1)
        self.assertEqual([e["label"] for e in self.recorder.run_events(first)],
                         ["a.mp3", "rtc"], "and its own start, which moved nothing")

    def test_the_run_before_is_offered_after_a_new_start(self):
        self.play("a.mp3", 60)
        first = self.recorder.session_id
        self.recorder.open_session(clock_ok=True, clock_source="rtc")

        run = self.recorder.run_to_erase()
        self.assertEqual(run["session"], first,
                         "a start that has done nothing yet offers the run before it")
        self.assertFalse(run["open"])
        self.recorder.erase_run(run)
        self.assertEqual(self.recorder.counters()["tracks_played"], 0)

    def test_a_journal_from_before_gets_the_note_column(self):
        path = os.path.join(self.dir, "older.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER,"
            " ts REAL NOT NULL, clock_ok INTEGER NOT NULL DEFAULT 0, type TEXT NOT NULL,"
            " label TEXT, detail TEXT)")
        conn.commit()
        conn.close()

        recorder = stats.StatsRecorder(path, enabled=True)
        try:
            recorder.open_session(clock_ok=True, clock_source="rtc")
            recorder.record("click", label="single", counters={"clicks_single": 1})
            run = recorder.run_to_erase()
            self.assertIsNotNone(run, "the older table records like any other")
            recorder.erase_run(run)
            self.assertEqual(recorder.counters()["clicks_single"], 0)
        finally:
            recorder.close()


class WinsUndoTest(unittest.TestCase):
    def test_the_points_of_an_erased_run_go_back(self):
        path = os.path.join(tempfile.mkdtemp(), "game_wins.json")
        blind_test.record_wins(path, [{"person": "a", "name": "Al"}])
        blind_test.record_wins(path, [{"person": "a", "name": "Al"}, {"person": "b", "name": "Be"}])
        self.assertEqual(blind_test.wins(path)["a"]["wins"], 2)

        self.assertEqual(blind_test.remove_wins(path, [{"person": "a", "name": "Al"}]), 1)
        self.assertEqual(blind_test.wins(path)["a"]["wins"], 1)
        blind_test.remove_wins(path, [{"person": "a", "name": "Al"}, {"person": "a", "name": "Al"}])
        self.assertEqual(blind_test.wins(path)["a"]["wins"], 0)
        self.assertEqual(blind_test.wins(path)["a"]["name"], "Al", "the name is remembered")
        self.assertEqual(blind_test.wins(path)["b"]["wins"], 1, "another's wins are not touched")


class DaemonEraseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        cfg = load_config()
        cfg.update({
            "STATE_DIR": self.dir,
            "MUSIC_DIR": os.path.join(self.dir, "music"),
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "HIDDEN_FILE": os.path.join(self.dir, "hidden.json"),
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "STATS_ENABLED": True,
            "MUSIC_ORDER_MODE": "ordered",
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.stats.open_session(clock_ok=True, clock_source="rtc")

        # What a start takes and keeps (RadioDaemon.start()), then what the run
        # does by itself: it plays, it draws a sound, it says an announcement.
        self.daemon.state.data.update({
            "play_queue": ["x", "y", "z"], "last_track": "x",
            "recent": [{"path": "x", "at": 1}], "click_bags": {"a": ["s1"]},
            "resume_point": {"path": "x", "seconds": 30},
            "last_music_start": date.today().isoformat(),
            "last_cutoff_trigger": date.today().isoformat(),
        })
        self.before = self.daemon.state.run_memory()
        self.daemon._run_memory = self.before
        self.daemon._keep_run_memory()

        self.daemon.state.pop_next_track_or_none()
        self.daemon.state.add_recent("/music/played.mp3", 10)
        self.daemon.state.next_click_sound("a", lambda: ["s2"])
        self.daemon.state.mark_triggered_today("custom_new")
        self.daemon.state.set_resume_point("/music/played.mp3", 12)
        self.daemon.stats.record(
            "track_played", label="played.mp3", detail={"kind": "music", "seconds": 60},
            counters={"tracks_played": 1, "seconds_music": 60},
            daily={"tracks_played": 1, "seconds_music": 60},
            item=("music", "played.mp3"), seconds=60)

    def tearDown(self):
        self.daemon.stats.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def erase(self):
        with mock.patch.object(self.daemon, "_playable_tracks", return_value=["b", "a"]):
            return self.daemon._erase_run()

    def test_erasing_puts_the_memory_back_where_it_was(self):
        result = self.erase()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["tracks"], 1)

        self.assertEqual(self.daemon.state.data["play_queue"], ["x", "y", "z"],
                         "the pass starts again with the song the run took")
        self.assertEqual(self.daemon.state.run_memory(), self.before,
                         "and everything else is exactly as it was")
        self.assertEqual(self.daemon.state.resume_point(), ("x", 30.0),
                         "the song it found cut short is taken up again")
        self.assertTrue(self.daemon._resume_armed)
        self.assertFalse(self.daemon.state.already_triggered_today("custom_new"),
                         "an announcement the run said can be said again")
        self.assertTrue(self.daemon.state.already_triggered_today("last_cutoff_trigger"),
                         "the day's cutoff stays")
        self.assertEqual(self.daemon.stats.run_to_erase(), None)

    def test_the_run_before_is_put_back_after_a_restart(self):
        # A restart, then a run that does nothing yet: erasing offers the one
        # before, whose memory is the one the card kept.
        self.daemon.stats.open_session(clock_ok=True, clock_source="rtc")
        self.daemon._run_memory_before = self.daemon._read_run_memory()
        self.daemon._run_memory = self.daemon.state.run_memory()
        self.daemon._keep_run_memory()

        result = self.erase()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["session"], 1, "the run before this one")
        self.assertEqual(self.daemon.state.data["play_queue"], ["x", "y", "z"])
        self.assertEqual(self.daemon.state.resume_point(), ("x", 30.0))

    def test_a_run_with_no_memory_kept_starts_the_pass_over(self):
        self.daemon._run_memory = None
        self.daemon._run_memory_before = None

        result = self.erase()

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.daemon.state.data["play_queue"], ["a", "b"],
                         "the whole library again, in the order mode's order")
        self.assertIsNone(self.daemon.state.resume_point())
        self.assertEqual(self.daemon.state.recent_paths(), [])

    def test_a_start_that_finds_the_same_memory_leaves_the_file_alone(self):
        self.daemon._run_memory_before = self.daemon._run_memory
        with mock.patch.object(rukebox_daemon.json_file, "write") as write:
            self.daemon._keep_run_memory()
        self.assertFalse(write.called, "the same memory is already kept")

    def test_what_plays_now_is_counted_from_the_erase(self):
        self.daemon._play_kind = "music"
        self.daemon._play_since = time.monotonic() - 300
        before = time.monotonic()

        self.erase()

        self.assertGreaterEqual(self.daemon._play_since, before,
                                "the song playing now belongs to the run that starts here")

    def test_a_second_erase_finds_nothing(self):
        self.erase()
        self.assertEqual(self.erase(), {"ok": False, "error": "nothing_to_erase"})

    def test_a_blind_test_is_never_cut_short(self):
        self.daemon.mode = "game"
        self.assertEqual(self.daemon._erase_run(), {"ok": False, "error": "game_running"})
        self.assertEqual(self.daemon.state.data["play_queue"], ["y", "z"],
                         "and nothing was touched")


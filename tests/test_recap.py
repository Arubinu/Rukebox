"""The recap: songs counted month by month for good, the first song of each
day, and a period read back in figures."""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import date

import _path  # noqa: F401
import stats

try:
    import web_server
except ImportError:  # no Flask on this machine
    web_server = None


class RecapTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "stats.db")
        self.rec = stats.StatsRecorder(self.path, enabled=True)
        self.rec.open_session(clock_ok=True, clock_source="rtc")

    def tearDown(self):
        self.rec.close()

    def play(self, name, seconds=180.0):
        self.rec.record("track_played", label=name, detail={"seconds": seconds},
                        counters={"tracks_played": 1, "seconds_music": seconds},
                        daily={"tracks_played": 1, "seconds_music": seconds},
                        item=("music", name), seconds=seconds)

    def test_a_period_in_figures(self):
        for name in ("a.mp3", "b.mp3", "a.mp3"):
            self.play(name)
        today = date.today()
        recap = self.rec.recap(today.replace(day=1), today)
        self.assertEqual(recap["tracks_played"], 3)
        self.assertEqual(recap["seconds_music"], 540)
        self.assertEqual(recap["days"], 1)
        self.assertEqual(recap["best_day"]["day"], today.isoformat())
        self.assertEqual([t["name"] for t in recap["tracks"]], ["a.mp3", "b.mp3"])
        self.assertEqual(recap["morning"], {"name": "a.mp3", "count": 1}, "only the first song of the day")

    def test_a_reset_takes_the_months_too(self):
        self.play("a.mp3")
        self.rec.reset("all")
        today = date.today()
        self.assertEqual(self.rec.recap(today.replace(day=1), today)["tracks"], [])

    def test_an_older_database_is_filled_from_its_events_once(self):
        self.rec.close()
        db = sqlite3.connect(self.path)
        db.execute("DELETE FROM monthly")
        db.execute("DELETE FROM schema_info WHERE key = 'monthly_filled'")
        db.execute("INSERT INTO events(ts, clock_ok, type, label, detail) VALUES(?, 1, 'track_played', 'old.mp3', ?)",
                   (time.time(), json.dumps({"seconds": 100})))
        db.commit()
        db.close()
        self.rec = stats.StatsRecorder(self.path, enabled=True)
        rows = sqlite3.connect(self.path).execute("SELECT kind, name, count FROM monthly ORDER BY kind").fetchall()
        self.assertEqual(rows, [("first", "old.mp3", 1.0), ("track", "old.mp3", 1.0)])


@unittest.skipIf(web_server is None, "Flask is not installed")
class RangeTest(unittest.TestCase):
    def test_the_periods(self):
        today = date(2026, 3, 15)
        self.assertEqual(web_server.recap_range("month", today), (date(2026, 3, 1), today))
        self.assertEqual(web_server.recap_range("last_month", today), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(web_server.recap_range("year", today), (date(2026, 1, 1), today))
        self.assertEqual(web_server.recap_range("last_year", today), (date(2025, 1, 1), date(2025, 12, 31)))


if __name__ == "__main__":
    unittest.main()

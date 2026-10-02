"""The background loops: a turn that fails costs that turn, and a cutoff minute
the scheduler was held past is still a cutoff."""
import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime

import _path  # noqa: F401
from config_and_scan import load_config
import rukebox_daemon


class SchedulerTest(unittest.TestCase):
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
            "CUTOFF_HOUR": 23, "CUTOFF_MINUTE": 30,
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon._clock_ready = threading.Event()

    def at(self, hour, minute, second=0, day=2):
        return datetime(2026, 10, day, hour, minute, second)

    def test_the_cutoff_minute_is_due(self):
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 29, 50)))
        self.assertTrue(self.daemon._cutoff_due(self.at(23, 30, 5)))

    def test_a_tick_held_past_the_minute_still_cuts_off(self):
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 29, 50)))
        self.assertTrue(self.daemon._cutoff_due(self.at(23, 31, 10)))

    def test_a_start_after_the_cutoff_is_not_one(self):
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 45)))
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 45, 15)))

    def test_a_clock_set_forward_is_not_a_cutoff(self):
        self.assertFalse(self.daemon._cutoff_due(self.at(20, 0)))
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 50)))

    def test_a_clock_set_back_is_not_a_cutoff(self):
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 40)))
        self.assertFalse(self.daemon._cutoff_due(self.at(23, 20)))

    def test_a_failing_turn_does_not_end_the_loop(self):
        def broken():
            raise RuntimeError("boom")

        with self.assertLogs("radio", level="ERROR"):
            self.assertIsNone(self.daemon._guarded("Test", broken))
        self.assertEqual(self.daemon._guarded("Test", lambda: 7), 7)


if __name__ == "__main__":
    unittest.main()

"""The small SQLite stores and the password hash: statistics (lifetime
counters survive row deletion, today's summary), the suggestion box
(lookalike names, bans, credit exemption), web_auth."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import stats
import suggestions
import web_auth


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.rec = stats.StatsRecorder(os.path.join(self.dir, "stats.db"))
        self.rec.open_session(clock_ok=True, clock_source="test")

    def tearDown(self):
        self.rec.close() if hasattr(self.rec, "close") else None
        shutil.rmtree(self.dir, ignore_errors=True)

    def played(self, name, seconds=60):
        self.rec.record("track_played", label=name, counters={"tracks_played": 1, "seconds_music": seconds},
                        daily={"tracks_played": 1, "seconds_music": seconds}, item=("music", name), seconds=seconds)

    def test_today_summary(self):
        for name in ("a.mp3", "b.mp3", "a.mp3"):
            self.played(name)
        today = self.rec.today_summary()
        self.assertEqual(today["tracks_played"], 3)
        self.assertEqual(today["seconds_music"], 180)
        self.assertEqual(today["top"][0], {"name": "a.mp3", "count": 2})

    def test_a_clock_that_is_the_machines_own_is_not_a_doubt(self):
        # A container cannot set its clock and does not need to: the host keeps
        # it, so the time is established rather than unreliable (asked for as:
        # "is it normal that the journal says no reliable time source?").
        self.rec.set_clock("host")
        counters = self.rec.counters()
        self.assertEqual(counters["clock_host"], 1)
        self.assertEqual(counters["clock_unreliable"], 0)
        kinds = [row["type"] for row in self.rec._rows("SELECT type FROM events ORDER BY id")]
        self.assertIn("clock_ready", kinds)
        self.assertNotIn("clock_unreliable", kinds)

    def test_counters_survive_deleting_rows(self):
        self.played("a.mp3")
        ids = [r["id"] for r in self.rec._rows("SELECT id FROM events WHERE type = 'track_played'")]
        self.rec.delete_rows("events", ids)
        self.assertEqual(self.rec.counters()["tracks_played"], 1)


class SuggestionsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.box = suggestions.SuggestionBox(os.path.join(self.dir, "s.db"))

    def tearDown(self):
        self.box._db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_lookalike_names(self):
        self.assertEqual(suggestions.name_key("Renard Bleu"), suggestions.name_key("rénard  bleu!"))

    def test_free_credits_and_ban(self):
        device = self.box.resolve_device(None, None, "10.42.0.9")[0]
        self.assertFalse(self.box.has_free_credits(device["id"]))
        self.box.set_free_credits(device["id"], True)
        self.assertTrue(self.box.has_free_credits(device["id"]))
        self.assertTrue(self.box.device_summary(device)["free_credits"])
        self.box.set_ban(device["id"], -1)
        self.assertEqual(self.box.ban_until(device["id"]), -1)


class WebAuthTest(unittest.TestCase):
    def test_hash_roundtrip(self):
        stored = web_auth.hash_password("pa$$ `word`")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertTrue(web_auth.verify_password("pa$$ `word`", stored))
        self.assertFalse(web_auth.verify_password("other", stored))
        self.assertFalse(web_auth.verify_password("x", ""))


if __name__ == "__main__":
    unittest.main()

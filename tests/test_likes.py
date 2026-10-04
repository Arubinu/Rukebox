"""Liked tracks (src/likes.py): the file, the dates, and the heart's answer."""
import datetime
import os
import shutil
import tempfile
import time
import unittest

import _path  # noqa: F401
import likes


class LikesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "likes.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nothing_liked_yet(self):
        self.assertEqual(likes.load(self.path), [])
        self.assertEqual(likes.keys(self.path), set())

    def test_like_then_unlike(self):
        self.assertTrue(likes.toggle(self.path, "abc", "Title", "Artist"))
        self.assertEqual(likes.keys(self.path), {"abc"})
        item = likes.load(self.path)[0]
        self.assertEqual(item["title"], "Title")
        self.assertEqual(item["artist"], "Artist")
        self.assertGreater(item["liked_at"], 0, "the list shows the date, so it is kept")
        self.assertFalse(likes.toggle(self.path, "abc"))
        self.assertEqual(likes.load(self.path), [])

    def test_most_recently_liked_first(self):
        likes.toggle(self.path, "one", "One")
        time.sleep(0.01)
        likes.toggle(self.path, "two", "Two")
        self.assertEqual([item["key"] for item in likes.load(self.path)], ["two", "one"])

    def test_liking_the_same_track_twice_leaves_one_entry(self):
        likes.toggle(self.path, "one")
        likes.toggle(self.path, "one")  # takes the like back
        likes.toggle(self.path, "one")  # and puts it back
        self.assertEqual(len(likes.load(self.path)), 1)
        self.assertEqual(likes.keys(self.path), {"one"})

    def test_a_key_is_required(self):
        for empty in ("", "   ", None):
            with self.assertRaises(ValueError):
                likes.toggle(self.path, empty)

    def test_an_unreadable_file_is_no_like_rather_than_a_crash(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(likes.load(self.path), [])



class MemoriesTest(unittest.TestCase):
    @staticmethod
    def liked(key, day):
        return {"key": key, "title": key.upper(), "artist": "A",
                "liked_at": time.mktime(day.timetuple()) + 3600}

    def test_a_year_ago_and_a_month_ago(self):
        today = datetime.date(2027, 10, 4)
        items = [self.liked("year", datetime.date(2026, 10, 5)),
                 self.liked("two", datetime.date(2025, 10, 3)),
                 self.liked("month", datetime.date(2027, 9, 4)),
                 self.liked("never", datetime.date(2027, 6, 1))]
        found = likes.memories(items, today)
        self.assertEqual([(m["key"], m.get("years"), m.get("months")) for m in found],
                         [("two", 2, None), ("year", 1, None), ("month", None, 1)])

    def test_the_end_of_a_month_is_clamped(self):
        found = likes.memories([self.liked("feb", datetime.date(2027, 2, 28))], datetime.date(2027, 3, 31))
        self.assertEqual([m["key"] for m in found], ["feb"])


if __name__ == "__main__":
    unittest.main()

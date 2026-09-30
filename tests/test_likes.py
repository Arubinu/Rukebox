"""Liked tracks (src/likes.py): the file, the dates, and the heart's answer."""
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
        self.assertFalse(likes.is_liked(self.path, "abc"))

    def test_like_then_unlike(self):
        self.assertTrue(likes.toggle(self.path, "abc", "Title", "Artist"))
        self.assertTrue(likes.is_liked(self.path, "abc"))
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
        self.assertTrue(likes.is_liked(self.path, "one"))

    def test_a_key_is_required(self):
        for empty in ("", "   ", None):
            with self.assertRaises(ValueError):
                likes.toggle(self.path, empty)

    def test_an_unreadable_file_is_no_like_rather_than_a_crash(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(likes.load(self.path), [])


if __name__ == "__main__":
    unittest.main()

"""Hidden tracks (src/hidden_tracks.py): what a duplicate check set aside."""
import os
import shutil
import tempfile
import time
import unittest

import _path  # noqa: F401
import hidden_tracks


class HiddenTracksTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "hidden.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nothing_set_aside_yet(self):
        self.assertEqual(hidden_tracks.load(self.path), [])
        self.assertEqual(hidden_tracks.keys(self.path), set())
        self.assertEqual(hidden_tracks.paths(self.path), set())

    def test_hide_then_give_back(self):
        self.assertTrue(hidden_tracks.set_hidden(
            self.path, "abc", True, "/m/A/Song.opus", "Song", "A"))
        self.assertEqual(hidden_tracks.keys(self.path), {"abc"})
        self.assertEqual(hidden_tracks.paths(self.path), {"/m/A/Song.opus"})
        item = hidden_tracks.load(self.path)[0]
        self.assertEqual(item["title"], "Song")
        self.assertGreater(item["hidden_at"], 0, "the file keeps when it happened")
        self.assertFalse(hidden_tracks.set_hidden(self.path, "abc", False))
        self.assertEqual(hidden_tracks.load(self.path), [])

    def test_hiding_the_same_track_twice_leaves_one_entry(self):
        hidden_tracks.set_hidden(self.path, "one", True)
        hidden_tracks.set_hidden(self.path, "one", True)
        self.assertEqual(len(hidden_tracks.load(self.path)), 1)

    def test_most_recently_set_aside_first(self):
        hidden_tracks.set_hidden(self.path, "one", True)
        time.sleep(0.01)
        hidden_tracks.set_hidden(self.path, "two", True)
        self.assertEqual([item["key"] for item in hidden_tracks.load(self.path)],
                         ["two", "one"])

    def test_a_key_is_required(self):
        for empty in ("", "   ", None):
            with self.assertRaises(ValueError):
                hidden_tracks.set_hidden(self.path, empty, True)

    def test_everything_can_be_given_back_at_once(self):
        hidden_tracks.set_hidden(self.path, "one", True)
        hidden_tracks.set_hidden(self.path, "two", True)
        hidden_tracks.clear(self.path)
        self.assertEqual(hidden_tracks.keys(self.path), set())

    def test_an_unreadable_file_is_nothing_hidden_rather_than_a_crash(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(hidden_tracks.load(self.path), [])
        self.assertEqual(hidden_tracks.paths(self.path), set())


if __name__ == "__main__":
    unittest.main()

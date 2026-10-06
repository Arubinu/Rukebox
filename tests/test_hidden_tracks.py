"""Excluded tracks (src/hidden_tracks.py): what the radio must not pick by
itself, whether a duplicate check put it there or the excluded page did."""
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

    def test_where_an_entry_came_from_is_kept(self):
        hidden_tracks.set_hidden(self.path, "one", True, origin="duplicate")
        hidden_tracks.set_hidden(self.path, "two", True)
        by_key = {item["key"]: item for item in hidden_tracks.load(self.path)}
        self.assertEqual(by_key["one"]["origin"], "duplicate")
        self.assertEqual(by_key["two"]["origin"], "manual", "nothing said: by hand")
        hidden_tracks.set_hidden(self.path, "three", True, origin="pony")
        by_key = {item["key"]: item for item in hidden_tracks.load(self.path)}
        self.assertEqual(by_key["three"]["origin"], "manual",
                         "an origin the page cannot name is not stored")

    def test_several_tracks_at_once_in_one_write(self):
        tracks = [{"key": "a", "path": "/m/a.mp3", "title": "A", "artist": "X"},
                  {"key": "b", "path": "/m/b.mp3", "title": "B", "artist": "Y"},
                  {"key": "", "path": "/m/gone.mp3"}]
        self.assertEqual(hidden_tracks.set_many(self.path, tracks, True, "filter"), 2)
        self.assertEqual(hidden_tracks.keys(self.path), {"a", "b"})
        self.assertEqual(hidden_tracks.paths(self.path), {"/m/a.mp3", "/m/b.mp3"})
        self.assertEqual({item["origin"] for item in hidden_tracks.load(self.path)}, {"filter"})

    def test_the_same_key_twice_is_one_entry(self):
        hidden_tracks.set_many(self.path, [{"key": "a"}, {"key": "a", "title": "A"}])
        self.assertEqual(len(hidden_tracks.load(self.path)), 1)
        self.assertEqual(hidden_tracks.load(self.path)[0]["title"], "A")

    def test_giving_several_back_at_once(self):
        hidden_tracks.set_many(self.path, [{"key": "a"}, {"key": "b"}, {"key": "c"}])
        self.assertEqual(hidden_tracks.set_many(self.path, [{"key": "a"}, {"key": "c"}], False), 2)
        self.assertEqual(hidden_tracks.keys(self.path), {"b"})

    def test_nothing_to_do_writes_nothing(self):
        self.assertEqual(hidden_tracks.set_many(self.path, []), 0)
        self.assertEqual(hidden_tracks.set_many(self.path, [{"key": " "}], False), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_the_keys_and_the_paths_are_two_answers_about_one_file(self):
        hidden_tracks.set_hidden(self.path, "abc", True, "/m/Song.opus")
        self.assertEqual(hidden_tracks.keys(self.path), {"abc"})
        self.assertEqual(hidden_tracks.paths(self.path), {"/m/Song.opus"})
        self.assertEqual(hidden_tracks.keys(self.path), {"abc"}, "and again, from the cache")

    def test_a_file_that_changed_is_read_again(self):
        hidden_tracks.set_hidden(self.path, "abc", True)
        self.assertEqual(hidden_tracks.keys(self.path), {"abc"})
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"tracks": [{"key": "xyz", "hidden_at": 1}]}')
        self.assertEqual(hidden_tracks.keys(self.path), {"xyz"})


if __name__ == "__main__":
    unittest.main()

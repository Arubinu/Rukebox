"""The duplicate check (src/duplicates.py): the key, the groups, the evidence.

The library database is built here rather than copied from a Pi: what matters
is that two spellings of one song end up together, that a pair of identical
files is called out, and that nothing is grouped on its own.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import _path  # noqa: F401
import duplicates
import library


def make_db(path, rows, root=None):
    """A catalogue with the files really there - a track key is a stat()."""
    root = root or path + ".files"
    connection = sqlite3.connect(path)
    connection.executescript(library.SCHEMA)
    for row in rows:
        full = os.path.join(root, row["path"])
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(b"x" * min(row["size"], 64))
        connection.execute(
            "INSERT INTO tracks (path, size, mtime, key, title, artist, album,"
            " duration, probed) VALUES (?,?,?,?,?,?,?,?,1)",
            (full, row["size"], 1, full, row.get("title"),
             row.get("artist"), row.get("album"), row["duration"]))
    connection.commit()
    connection.close()


class SongKeyTest(unittest.TestCase):
    def test_the_same_song_written_differently(self):
        pairs = [
            ("05 - La Seine", "13 - La Seine (Extrait de la bande originale)"),
            ("Boogie Wonderland", "Boogie Wonderland (feat. Earth, Wind & Fire)"),
            ("Silhouettes (Original Radio Edit)",
             "Silhouettes (Original Radio Edit) (feat. Salem Al Fakir)"),
            ("ete", "\u00c9t\u00e9"),
            ("DON'T CARE", "Don't Care"),
        ]
        for one, other in pairs:
            self.assertEqual(duplicates.song_key(one, "Artist"),
                             duplicates.song_key(other, "Artist"), one)

    def test_two_songs_are_not_one(self):
        self.assertNotEqual(duplicates.song_key("Alone", "Alan Walker"),
                            duplicates.song_key("Alone", "Marshmello"))
        self.assertNotEqual(duplicates.song_key("Better Off Alone", "Alice DJ"),
                            duplicates.song_key("Alone", "Alice DJ"))


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "library.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_very_same_file_twice(self):
        make_db(self.db, [
            {"path": "A/01 - Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "B/Song (feat. A).opus", "size": 4000000, "duration": 200.0,
             "title": "Song (feat. A)", "artist": "A", "album": "Two"},
        ], self.dir)
        found = duplicates.groups(self.db)
        self.assertEqual(len(found), 1, "one spelling of one song")
        self.assertTrue(found[0]["same_file"], "same bytes, same second")
        self.assertEqual([one["same_file"] for one in found[0]["tracks"]], [True, True])
        self.assertEqual(found[0]["reclaimable"], 4000000)

    def test_the_same_song_in_two_qualities(self):
        make_db(self.db, [
            {"path": "A/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "B/Song.opus", "size": 2000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "Two"},
        ], self.dir)
        found = duplicates.groups(self.db)
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["same_file"], "different weights, different files")
        self.assertEqual(found[0]["reclaimable"], 2000000)
        self.assertTrue(found[0]["tracks"][0]["kbps"] > found[0]["tracks"][1]["kbps"])

    def test_a_song_on_its_own_is_not_a_duplicate(self):
        make_db(self.db, [
            {"path": "A/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "A/Other.opus", "size": 3000000, "duration": 150.0,
             "title": "Other", "artist": "A", "album": "One"},
        ], self.dir)
        self.assertEqual(duplicates.groups(self.db), [])

    def test_a_short_file_is_not_worth_the_noise(self):
        make_db(self.db, [
            {"path": "A/ding.opus", "size": 40000, "duration": 2.0,
             "title": "Ding", "artist": "A", "album": "One"},
            {"path": "B/ding.opus", "size": 40000, "duration": 2.0,
             "title": "Ding", "artist": "A", "album": "Two"},
        ], self.dir)
        self.assertEqual(duplicates.groups(self.db), [])

    def test_what_a_check_set_aside_is_reported(self):
        make_db(self.db, [
            {"path": "A/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "B/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "Two"},
        ], self.dir)
        key = duplicates.groups(self.db)[0]["tracks"][1]["key"]
        found = duplicates.groups(self.db, hidden={key})
        self.assertFalse(found[0]["tracks"][0]["hidden"])
        self.assertTrue(found[0]["tracks"][1]["hidden"])

    def test_the_copies_of_one_file_come_first(self):
        make_db(self.db, [
            {"path": "A/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "B/Song.opus", "size": 2000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "Two"},
            {"path": "A/Twin.opus", "size": 5000000, "duration": 300.0,
             "title": "Twin", "artist": "A", "album": "One"},
            {"path": "B/Twin.opus", "size": 5000000, "duration": 300.0,
             "title": "Twin", "artist": "A", "album": "Two"},
        ], self.dir)
        found = duplicates.groups(self.db)
        self.assertEqual([group["same_file"] for group in found], [True, False],
                         "the same file twice is what a reader acts on")

    def test_the_report_counts_what_is_held_twice(self):
        make_db(self.db, [
            {"path": "A/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "One"},
            {"path": "B/Song.opus", "size": 4000000, "duration": 200.0,
             "title": "Song", "artist": "A", "album": "Two"},
        ], self.dir)
        found = duplicates.groups(self.db)
        self.assertEqual(duplicates.summary(found),
                         {"groups": 1, "tracks": 2, "reclaimable": 4000000,
                          "same_file": 1})
        self.assertIn("1 group(s) of duplicates", duplicates.report(self.db))

    def test_an_empty_database_is_no_duplicate(self):
        make_db(self.db, [], self.dir)
        self.assertEqual(duplicates.groups(self.db), [])


if __name__ == "__main__":
    unittest.main()

"""The music library catalogue (src/library.py): sync, search, facets and
the matching of a suggestion against what is already there."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import library


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.music = os.path.join(self.dir, "music")
        self.files = []
        for rel in ("Daft Punk/Discovery/01 - One More Time.mp3",
                    "Daft Punk/Discovery/02 - Aerodynamic.mp3",
                    "Beyoncé/I Am/03 - Halo.opus",
                    "loose_track.mp3"):
            path = os.path.join(self.music, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x" * 10)
            self.files.append(path)
        self.lib = library.Library(os.path.join(self.dir, "lib.db"), lambda p: "k" + os.path.basename(p))
        self.lib.sync(self.files, self.music)

    def tearDown(self):
        self.lib._db.close()
        shutil.rmtree(self.dir)

    def test_fold(self):
        self.assertEqual(library.fold("Beyoncé - Halo!"), "beyonce halo")

    def test_names_from_path_until_read(self):
        item = self.lib.item_for_path(self.files[0])
        self.assertEqual((item["title"], item["artist"], item["album"]), ("One More Time", "Daft Punk", "Discovery"))
        self.assertEqual(self.lib.status(), {"total": 4, "read": 0})

    def test_sync_is_incremental(self):
        self.assertEqual(self.lib.sync(self.files, self.music), (0, 0))
        self.assertEqual(self.lib.sync(self.files[1:], self.music), (0, 1))

    def test_store_tags(self):
        self.lib.store(self.files[2], {"title": "Halo", "artist": "Beyoncé", "genre": "Pop", "duration": 261.0},
                       self.music)
        self.assertEqual(self.lib.status()["read"], 1)
        self.assertEqual(self.lib.search("", genre="Pop")["total"], 1)

    def test_search_and_facets(self):
        self.assertEqual(self.lib.search("discovery")["total"], 2)
        self.assertEqual(self.lib.search("", artist="Daft Punk")["total"], 2)
        facets = self.lib.facets("Daft Punk")
        self.assertIn({"name": "Discovery", "count": 2}, facets["albums"])

    def test_match(self):
        self.assertEqual(self.lib.match("Daft Punk - One More Time")["title"], "One More Time")
        self.assertEqual(self.lib.match("One More Time - Daft Punk")["title"], "One More Time")
        self.assertEqual(self.lib.match("beyonce halo")["title"], "Halo")
        self.assertIsNone(self.lib.match("Halo"), "one word matches too much")
        self.assertIsNone(self.lib.match("nothing like this"))

    def test_keys_and_basenames(self):
        self.assertEqual(self.lib.path_for_key("k02 - Aerodynamic.mp3"), self.files[1])
        self.assertEqual(self.lib.item_for_basename("loose_track.mp3")["title"], "loose_track")
        self.assertIsNone(self.lib.item_for_basename("loose%track.mp3"), "LIKE wildcards are escaped")


if __name__ == "__main__":
    unittest.main()

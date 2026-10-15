"""The music lists (src/music_lists.py): what the radio plays instead of the
whole library. A manual list keeps the order it was built in, a genre list
follows the library's tags, and nothing but a manual list takes tracks."""
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import music_lists


class MusicListsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.path = _path.document(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_create_and_read(self):
        entry = music_lists.add({"name": "  Le soir ", "kind": "manual"}, self.path)
        self.assertEqual(entry["id"], "le-soir")
        self.assertEqual(entry["tracks"], [])
        self.assertEqual(entry["genres"], [])
        self.assertEqual([e["name"] for e in music_lists.load(self.path)], ["Le soir"])

    def test_ids_do_not_collide(self):
        self.assertEqual(music_lists.add({"name": "Jazz"}, self.path)["id"], "jazz")
        self.assertEqual(music_lists.add({"name": "Jazz!"}, self.path)["id"], "jazz-2")

    def test_names_and_kinds_are_checked(self):
        for data, code in (({"name": "  "}, "list_name_required"),
                           ({"name": "x" * (music_lists.NAME_MAX + 1)}, "list_name_too_long"),
                           ({"name": "ok", "kind": "smart"}, "list_kind_unknown"),
                           ({"name": "ok", "kind": "genre"}, "list_genres_required")):
            with self.assertRaises(ValueError) as caught:
                music_lists.add(data, self.path)
            self.assertEqual(str(caught.exception), code, data)

    def test_genres_are_deduplicated_and_trimmed(self):
        entry = music_lists.add({"name": "Calme", "kind": "genre",
                                            "genres": [" Jazz ", "jazz", "", "Classique"]}, self.path)
        self.assertEqual(entry["genres"], ["Jazz", "Classique"])
        self.assertEqual(entry["tracks"], [], "a genre list holds no track")

    def test_tracks_of_a_manual_list_keep_their_order(self):
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.path)
        music_lists.add_track(entry["id"], "/m/b.mp3", self.path)
        music_lists.add_track(entry["id"], "/m/a.mp3", self.path)
        music_lists.add_track(entry["id"], "/m/b.mp3", self.path)
        got = music_lists.get(entry["id"], self.path)
        self.assertEqual(got["tracks"], ["/m/b.mp3", "/m/a.mp3"], "added twice is added once")
        self.assertEqual(music_lists.custom_order(got), ["b.mp3", "a.mp3"])
        music_lists.remove_track(entry["id"], "/m/b.mp3", self.path)
        self.assertEqual(music_lists.get(entry["id"], self.path)["tracks"], ["/m/a.mp3"])
        self.assertIsNone(music_lists.custom_order({"kind": "genre"}))

    def test_only_a_manual_list_takes_tracks(self):
        entry = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.path)
        for call, code in ((lambda: music_lists.add_track(entry["id"], "/m/a.mp3", self.path),
                            "list_not_manual"),
                           (lambda: music_lists.remove_track(entry["id"], "/m/a.mp3", self.path),
                            "list_not_manual"),
                           (lambda: music_lists.add_track(entry["id"], "", self.path),
                            "list_track_required")):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(str(caught.exception), code)

    def test_resolved_follows_the_library(self):
        manual = music_lists.add({"name": "Soir", "kind": "manual"}, self.path)
        music_lists.add_track(manual["id"], "/m/kept.mp3", self.path)
        music_lists.add_track(manual["id"], "/m/gone.mp3", self.path)
        self.assertEqual(
            music_lists.resolved(music_lists.get(manual["id"], self.path),
                                 ["/m/kept.mp3", "/m/other.mp3"], lambda g: []),
            ["/m/kept.mp3"], "a file deleted from the library leaves the list")
        genre = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.path)
        self.assertEqual(
            music_lists.resolved(genre, ["/m/x.mp3"],
                                 lambda genres: ["/m/x.mp3"] if genres == ["Jazz"] else []),
            ["/m/x.mp3"])
        self.assertEqual(music_lists.resolved(None, ["/m/x.mp3"], lambda g: []), [])

    def test_update_and_delete(self):
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.path)
        music_lists.add_track(entry["id"], "/m/a.mp3", self.path)
        renamed = music_lists.update(entry["id"], {"name": "Nuit"}, self.path)
        self.assertEqual((renamed["name"], renamed["tracks"]), ("Nuit", ["/m/a.mp3"]))
        self.assertEqual(renamed["id"], entry["id"], "the id does not move with the name")
        music_lists.delete(entry["id"], self.path)
        self.assertEqual(music_lists.load(self.path), [])
        with self.assertRaises(KeyError):
            music_lists.get(entry["id"], self.path)

    def test_an_unreadable_document_reads_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("music_lists:\n  lists: [ oops\n")
        self.assertEqual(music_lists.load(self.path), [])


if __name__ == "__main__":
    unittest.main()

"""The prepared folders (src/prepared_media.py): what the interface may browse
and write in the covers folder and the introductions folder, and - the point of
the module - what it may NOT touch."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import prepared_media


class PreparedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.covers = os.path.join(self.dir, "covers")
        self.intros = os.path.join(self.dir, "dj_announcements")
        self.music = os.path.join(self.dir, "music")
        self.cfg = {"COVER_DIR": self.covers, "DJ_ANNOUNCE_DIR": self.intros}
        for folder in (self.covers, self.intros):
            os.makedirs(folder)

    def write(self, *parts, data=b"x"):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_the_two_kinds_and_the_formats_they_take(self):
        self.assertEqual(sorted(prepared_media.KINDS), ["covers", "intros"])
        self.assertIsNotNone(prepared_media.file_name("covers", "cover.JPG"))
        self.assertIsNotNone(prepared_media.file_name("covers", "_any.png"))
        self.assertIsNone(prepared_media.file_name("covers", "track.mp3"))
        self.assertIsNotNone(prepared_media.file_name("intros", "A song.opus"))
        self.assertIsNone(prepared_media.file_name("intros", "cover.jpg"),
                          "a picture is not an introduction")
        self.assertIsNone(prepared_media.file_name("nonsense", "cover.jpg"))
        self.assertIsNone(prepared_media.spec("nonsense"))
        self.assertIsNone(prepared_media.root("covers", {}), "no folder configured")

    def test_a_relative_path_that_climbs_out_is_refused(self):
        for attempt in ("..", "../music", "Album/../../etc", "/etc/passwd", "a/../../b",
                        ".hidden", "Album/.git", "a//../b", "\x00"):
            with self.subTest(attempt=attempt):
                self.assertIsNone(prepared_media.relative_path(attempt), attempt)
                top, folder = prepared_media.inside("covers", self.cfg, attempt)
                self.assertEqual(top, os.path.realpath(self.covers))
                self.assertIsNone(folder)

    def test_an_ordinary_relative_path_is_kept(self):
        self.assertEqual(prepared_media.relative_path("LMFAO/Album"), "LMFAO/Album")
        self.assertEqual(prepared_media.relative_path("LMFAO//Album/"), "LMFAO/Album")
        self.assertEqual(prepared_media.relative_path(""), "")
        self.assertEqual(prepared_media.relative_path(None), "")
        top, folder = prepared_media.inside("covers", self.cfg, "LMFAO/Album")
        self.assertEqual(folder, os.path.join(os.path.realpath(self.covers), "LMFAO", "Album"))

    def test_a_symlink_out_of_the_root_is_refused(self):
        outside = os.path.join(self.dir, "elsewhere")
        os.makedirs(outside)
        link = os.path.join(self.covers, "linked")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need a privilege this machine does not give")
        _top, folder = prepared_media.inside("covers", self.cfg, "linked")
        self.assertIsNone(folder, "resolved through the link, it is outside the root")

    def test_the_names_a_file_may_be_given(self):
        self.assertEqual(prepared_media.visible_name("cover.jpg"), "cover.jpg")
        for bad in ("", ".", "..", ".hidden", "LMFAO/cover.jpg", "a\\b.txt", None):
            self.assertIsNone(prepared_media.visible_name(bad), bad)
        self.assertEqual(prepared_media.file_name("covers", "C:\\Users\\me\\cover.jpg"),
                         "cover.jpg", "a browser may send a whole path")
        self.assertEqual(prepared_media.folder_name("2011 - Sorry"), "2011 - Sorry")
        for bad in ("..", "a/b", "a\\b", ".git", " ", ""):
            self.assertIsNone(prepared_media.folder_name(bad), bad)

    def test_a_listing_shows_the_folders_and_what_is_used(self):
        self.write(self.covers, "LMFAO", "cover.jpg")
        self.write(self.covers, "LMFAO", "notes.txt")
        self.write(self.covers, "_any.png")
        self.write(self.covers, "desktop.ini")
        os.makedirs(os.path.join(self.covers, "Zz Top"))
        data = prepared_media.listing("covers", self.cfg)
        self.assertEqual(data["path"], "")
        self.assertIsNone(data["parent"])
        self.assertFalse(data["missing"])
        self.assertEqual([d["name"] for d in data["dirs"]], ["LMFAO", "Zz Top"])
        self.assertEqual([(f["name"], f["used"]) for f in data["files"]],
                         [("_any.png", True), ("desktop.ini", False)])

        inside = prepared_media.listing("covers", self.cfg, "LMFAO")
        self.assertEqual(inside["path"], "LMFAO")
        self.assertEqual(inside["parent"], "")
        self.assertEqual([(f["name"], f["used"]) for f in inside["files"]],
                         [("cover.jpg", True), ("notes.txt", False)],
                         "a file that is there but never used is shown as such")
        self.assertEqual([f["size"] for f in inside["files"]], [1, 1])

    def test_a_folder_that_is_not_there(self):
        missing = prepared_media.listing("covers", self.cfg, "Nope")
        self.assertIsNone(missing, "a subfolder the client was never told about")
        shutil.rmtree(self.covers)
        fresh = prepared_media.listing("covers", self.cfg)
        self.assertTrue(fresh["missing"], "the root is created by the first upload")
        self.assertEqual(fresh["files"], [])
        self.assertEqual(fresh["dirs"], [])

    def test_the_two_kinds_never_see_each_other(self):
        self.write(self.covers, "cover.jpg")
        self.write(self.intros, "A song.opus")
        self.assertEqual([f["name"] for f in
                          prepared_media.listing("covers", self.cfg)["files"]], ["cover.jpg"])
        self.assertEqual([f["name"] for f in
                          prepared_media.listing("intros", self.cfg)["files"]], ["A song.opus"])
        self.assertEqual(prepared_media.listing("intros", self.cfg)["extensions"],
                         list(__import__("dj_intro").EXTENSIONS))

    def test_an_empty_setting_answers_nothing(self):
        self.assertIsNone(prepared_media.listing("covers", {}, ""))
        self.assertEqual(prepared_media.inside("covers", {}, ""), (None, None))


if __name__ == "__main__":
    unittest.main()

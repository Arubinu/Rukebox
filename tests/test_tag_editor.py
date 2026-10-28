"""Tags and covers written into the music folder, the old covers folder emptied, AcoustID read."""
import base64
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

import _path  # noqa: F401
import acoustid
import covers_move
import tag_editor
import track_media

JPEG = b"\xff\xd8\xff" + b"j" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 64
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def write(*parts, data=b"x"):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class ChangesTest(unittest.TestCase):
    def test_only_the_known_fields_and_sane_values_go_through(self):
        self.assertEqual(tag_editor.clean_changes({"title": "  Été ", "nonsense": "x", "track": "3/12"}),
                         {"title": "Été", "track": "3/12"})
        self.assertEqual(tag_editor.clean_changes({"genre": None}), {"genre": ""}, "an empty one clears it")
        for changes, code in (({"title": "a\nb"}, "bad_tag"), ({"title": "x" * 201}, "bad_tag"),
                              ({"year": "99"}, "bad_year"), ({"track": "three"}, "bad_track")):
            with self.assertRaises(tag_editor.TagError) as caught:
                tag_editor.clean_changes(changes)
            self.assertEqual(str(caught.exception), code)
        self.assertEqual(tag_editor.clean_changes({"year": "2011-05-20"}), {"year": "2011-05-20"})

    def test_ogg_carries_its_picture_as_a_flac_picture_block(self):
        block = base64.b64decode(tag_editor.picture_block(JPEG))
        kind, length = struct.unpack(">II", block[:8])
        self.assertEqual((kind, block[8:8 + length]), (3, b"image/jpeg"), "a front cover")
        self.assertTrue(block.endswith(JPEG))
        self.assertEqual(struct.unpack(">I", block[-len(JPEG) - 4:-len(JPEG)])[0], len(JPEG))

    def test_only_the_formats_that_carry_tags_are_written(self):
        for name in ("a.mp3", "a.FLAC", "a.m4a", "a.ogg", "a.opus"):
            self.assertTrue(tag_editor.writable(name), name)
        for name in ("a.wav", "a.aac", "a.txt"):
            self.assertFalse(tag_editor.writable(name), name)
        with self.assertRaises(tag_editor.TagError) as caught:
            tag_editor.write("a.wav", {"title": "x"})
        self.assertEqual(str(caught.exception), "format_not_writable")


class FolderPictureTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.track = write(self.music, "LMFAO", "Sorry", "01 - Party.opus")

    def test_the_album_and_the_artist_are_the_two_folders_above_the_song(self):
        levels = tag_editor.levels(self.track, self.music)
        self.assertEqual(levels["album"], os.path.join(self.music, "LMFAO", "Sorry"))
        self.assertEqual(levels["artist"], os.path.join(self.music, "LMFAO"))
        loose = write(self.music, "LMFAO", "Single.opus")
        self.assertEqual(tag_editor.levels(loose, self.music),
                         {"album": os.path.join(self.music, "LMFAO"), "artist": None},
                         "a song straight in an artist's folder: that folder is the album's")
        top = write(self.music, "Alone.opus")
        self.assertEqual(tag_editor.levels(top, self.music)["artist"], None)

    def test_a_folder_picture_is_its_cover_jpg_and_wins_there(self):
        album = os.path.dirname(self.track)
        write(album, "folder.png", data=PNG)
        write(album, "cover.png", data=PNG)
        tag_editor.set_folder_picture(album, JPEG)
        self.assertEqual(sorted(os.listdir(album)), ["01 - Party.opus", "cover.jpg", "folder.png"],
                         "another cover.* would compete with it; folder.png stays")
        self.assertEqual(os.path.basename(tag_editor.folder_picture(album)), "cover.jpg")
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(track_media.cover(self.track, self.music), (JPEG, "image/jpeg"))
        tag_editor.remove_folder_picture(album)
        self.assertEqual(os.path.basename(tag_editor.folder_picture(album)), "folder.png",
                         "the next one shows once it is gone")


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.covers = os.path.join(self.dir, "covers")
        write(self.music, "LMFAO", "Sorry", "01 - Party.opus")

    def test_every_picture_lands_where_it_was_meant_and_the_folder_is_kept_aside(self):
        write(self.covers, "LMFAO", "Sorry", "01 - Party.jpg", data=JPEG)
        write(self.covers, "LMFAO", "Sorry", "_any.png", data=PNG)
        write(self.covers, "LMFAO", "folder.jpg", data=JPEG)
        write(self.covers, "_any.jpg", data=JPEG)
        write(self.covers, "Gone", "_any.jpg", data=JPEG)
        write(self.covers, "LMFAO", "notes.txt")
        write(self.covers, "LMFAO", "Thumbs.db")
        summary = covers_move.move(self.covers, self.music)
        self.assertEqual((summary["copied"], summary["kept"], summary["no_folder"]), (4, 0, 1))
        album = os.path.join(self.music, "LMFAO", "Sorry")
        self.assertEqual(sorted(os.listdir(album)), ["01 - Party.jpg", "01 - Party.opus", "cover.png"])
        self.assertTrue(os.path.isfile(os.path.join(self.music, "LMFAO", "folder.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(self.music, "cover.jpg")), "_any of everything")
        self.assertFalse(os.path.exists(self.covers), "no longer read")
        self.assertTrue(os.path.isfile(os.path.join(summary["kept_in"], "Gone", "_any.jpg")),
                        "the folder is kept whole as the way back")
        self.assertIsNone(covers_move.move(self.covers, self.music), "once")

    def test_a_picture_the_music_folder_already_has_is_left_alone(self):
        album = os.path.join(self.music, "LMFAO", "Sorry")
        write(album, "Cover.JPG", data=PNG)
        write(self.covers, "LMFAO", "Sorry", "_any.jpg", data=JPEG)
        summary = covers_move.move(self.covers, self.music)
        self.assertEqual((summary["copied"], summary["kept"]), (0, 1))
        with open(os.path.join(album, "Cover.JPG"), "rb") as f:
            self.assertEqual(f.read(), PNG)

    def test_nothing_to_do(self):
        self.assertIsNone(covers_move.move("", self.music))
        self.assertIsNone(covers_move.move(self.covers, self.music), "no such folder")


ANSWER = {"status": "ok", "results": [
    {"score": 0.97, "recordings": [{
        "title": "Party Rock Anthem", "artists": [{"name": "LMFAO", "joinphrase": " feat. "},
                                                  {"name": "Lauren Bennett"}],
        "releasegroups": [
            {"id": "comp-1", "title": "Now 80", "type": "Album", "secondarytypes": ["Compilation"],
             "releases": [{"date": {"year": 2011}}]},
            {"id": "rg-1", "title": "Sorry for Party Rocking", "type": "Album",
             "releases": [{"date": {"year": 2012}}, {"date": {"year": 2011}}]},
            {"id": "rg-2", "title": "Party Rock Anthem", "type": "Single",
             "releases": [{"date": {"year": 2011}}]},
        ]}]},
    {"score": 0.5, "recordings": [{"title": "Party Rock Anthem", "artists": [{"name": "LMFAO"}],
                                   "releasegroups": [{"id": "rg-1", "title": "Sorry for Party Rocking"}]}]},
]}


class AcoustidTest(unittest.TestCase):
    def test_the_answer_is_read_the_studio_album_first(self):
        found = acoustid.candidates(ANSWER)
        self.assertEqual(found[0], {"score": 0.97, "title": "Party Rock Anthem",
                                    "artist": "LMFAO feat. Lauren Bennett",
                                    "album": "Sorry for Party Rocking", "year": "2011",
                                    "release_group": "rg-1"})
        self.assertEqual([one["album"] for one in found][:3],
                         ["Sorry for Party Rocking", "Party Rock Anthem", "Now 80"])

    def test_a_lookup_without_a_key_or_a_connection_says_which(self):
        with self.assertRaises(acoustid.AcoustidError) as caught:
            acoustid.lookup("", 200, "AQAA")
        self.assertEqual(str(caught.exception), "acoustid_no_key")
        with mock.patch.object(acoustid.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            with self.assertRaises(acoustid.AcoustidError) as caught:
                acoustid.lookup("key", 200, "AQAA")
        self.assertEqual(str(caught.exception), "acoustid_offline")
        refused = urllib.error.HTTPError(acoustid.LOOKUP_URL, 400, "bad", {},
                                         io.BytesIO(b'{"status": "error", "error": {"message": "invalid API key"}}'))
        with mock.patch.object(acoustid.urllib.request, "urlopen", side_effect=refused):
            with self.assertRaises(acoustid.AcoustidError) as caught:
                acoustid.lookup("key", 200, "AQAA")
        self.assertEqual(str(caught.exception), "acoustid_refused")

    def test_a_lookup_sends_the_fingerprint_and_reads_the_candidates(self):
        sent = {}

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake(request, timeout):
            sent["body"] = request.data.decode("ascii")
            return Reply(json.dumps(ANSWER).encode("utf-8"))

        with mock.patch.object(acoustid.urllib.request, "urlopen", side_effect=fake):
            found = acoustid.lookup("key", 213, "AQAA")
        self.assertIn("client=key", sent["body"])
        self.assertIn("duration=213", sent["body"])
        self.assertEqual(found[0]["release_group"], "rg-1")

    def test_a_cover_is_only_asked_for_a_release_group_id(self):
        with self.assertRaises(acoustid.AcoustidError) as caught:
            acoustid.cover("../../etc")
        self.assertEqual(str(caught.exception), "bad_release_group")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is not installed here")
class RewriteTest(unittest.TestCase):
    """The real thing: a file of each format rewritten, its sound untouched."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        picture = os.path.join(self.dir, "c.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64",
                        "-frames:v", "1", picture], check=True)
        with open(picture, "rb") as f:
            self.picture = tag_editor.picture_jpeg(f.read())

    def make(self, ext, codec):
        path = os.path.join(self.dir, "song" + ext)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2"]
                       + codec + [path], check=True)
        return path

    def test_each_format_takes_tags_and_a_picture_and_gives_them_back(self):
        for ext, codec in ((".opus", ["-c:a", "libopus"]), (".mp3", ["-c:a", "libmp3lame"]),
                           (".flac", []), (".m4a", ["-c:a", "aac"])):
            with self.subTest(ext=ext):
                path = self.make(ext, codec)
                written = tag_editor.write(path, {"title": "Été", "artist": "Moi", "track": "3/12"},
                                           picture=self.picture)
                self.assertEqual((written["title"], written["artist"], written["picture"]),
                                 ("Été", "Moi", True))
                kept = tag_editor.write(path, {"album": "Album"})
                self.assertTrue(kept["picture"], "a tag change keeps the picture")
                self.assertEqual((kept["title"], kept["album"]), ("Été", "Album"))
                cleared = tag_editor.write(path, {"title": ""}, picture=None)
                self.assertEqual((cleared["title"], cleared["picture"]), ("", False))
                self.assertEqual([n for n in os.listdir(self.dir) if tag_editor.TMP_MARK in n], [])


if __name__ == "__main__":
    unittest.main()


try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class RouteTest(unittest.TestCase):
    """The tag page's routes, around a song whose file is faked."""

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.album = os.path.join(self.music, "LMFAO", "Sorry")
        self.track = write(self.album, "01 - Party.opus")
        self.other = write(self.album, "02 - Sexy.mp3")
        write(self.album, "notes.txt")
        mock.patch.object(ws, "_tag_target", side_effect=lambda key: (self.track, self.music)
                          if key == "k1" else (None, "not_found")).start()
        mock.patch.object(ws, "_tags_changed").start()
        self.tags = {"title": "Party", "artist": "LMFAO", "album": "", "album_artist": "",
                     "year": "", "genre": "", "track": "", "duration": 200.0, "picture": False,
                     "writable": True}
        mock.patch.object(tag_editor, "read", side_effect=lambda path: dict(self.tags)).start()
        self.write = mock.patch.object(tag_editor, "write", return_value={}).start()
        self.client = ws.app.test_client()

    def test_a_song_is_answered_with_its_tags_and_where_its_pictures_go(self):
        write(self.music, "LMFAO", "folder.jpg", data=JPEG)
        data = self.client.get("/api/tags?key=k1").get_json()["data"]
        self.assertEqual(data["path"], "LMFAO/Sorry/01 - Party.opus")
        self.assertEqual(data["tags"]["title"], "Party")
        self.assertEqual(data["folders"]["album"]["folder"], "LMFAO/Sorry")
        self.assertIsNone(data["folders"]["album"]["picture"])
        self.assertEqual(data["folders"]["artist"]["picture"], "folder.jpg")
        self.assertEqual(self.client.get("/api/tags?key=nope").status_code, 404)

    def test_the_album_fields_go_into_every_song_of_its_folder(self):
        answer = self.client.post("/api/tags", json={"key": "k1", "album": True,
                                                     "tags": {"title": "Party!", "album": "Sorry"}})
        self.assertTrue(answer.get_json()["ok"], answer.get_json())
        calls = [(os.path.basename(c.args[0]), c.args[1]) for c in self.write.call_args_list]
        self.assertEqual(calls, [("01 - Party.opus", {"title": "Party!", "album": "Sorry"}),
                                 ("02 - Sexy.mp3", {"album": "Sorry"})],
                         "the title stays the song's own; a text file is no song")
        self.assertEqual(answer.get_json()["data"]["album_done"], 1)

    def test_an_albums_cover_comes_from_the_archive_into_its_folder(self):
        with mock.patch.object(acoustid, "cover", return_value=JPEG) as fetched, \
                mock.patch.object(tag_editor, "picture_jpeg", side_effect=lambda raw: raw):
            answer = self.client.post("/api/tags/picture", data={
                "key": "k1", "level": "album", "release_group": "rg-1"})
        self.assertTrue(answer.get_json()["ok"], answer.get_json())
        fetched.assert_called_once_with("rg-1")
        with open(os.path.join(self.album, "cover.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPEG)

    def test_a_picture_named_after_the_song_can_be_taken_away(self):
        beside = write(self.album, "01 - Party.jpg", data=JPEG)
        answer = self.client.delete("/api/tags/picture?key=k1&level=beside")
        self.assertTrue(answer.get_json()["ok"], answer.get_json())
        self.assertFalse(os.path.exists(beside))

    def test_recognising_needs_the_tool_and_says_so(self):
        with mock.patch.object(acoustid, "available", return_value=False):
            answer = self.client.post("/api/tags/identify", json={"key": "k1"})
        self.assertEqual((answer.status_code, answer.get_json()["error"]), (501, "fingerprint_missing"))


class WaitingTest(unittest.TestCase):
    def test_a_folder_left_with_pictures_is_said_to_be_waiting(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.assertFalse(covers_move.waiting(folder), "empty")
        write(folder, "LMFAO", "Thumbs.db")
        write(folder, "LMFAO", "notes.txt")
        self.assertFalse(covers_move.waiting(folder), "nothing a cover is made of")
        write(folder, "LMFAO", "_any.jpg", data=JPEG)
        self.assertTrue(covers_move.waiting(folder))
        self.assertFalse(covers_move.waiting(os.path.join(folder, "gone")))
        self.assertFalse(covers_move.waiting(""))


class ReviewGuardsTest(unittest.TestCase):
    def test_music_inside_the_old_covers_folder_is_never_moved_away(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        music = os.path.join(folder, "music")
        os.makedirs(music)
        self.assertIsNone(covers_move.move(folder, music))
        self.assertTrue(os.path.isdir(music))

    def test_a_release_group_is_plain_letters_digits_and_dashes(self):
        for bad in ("", "caf\u00e9", "../x", "a/b", "x" * 65):
            with self.assertRaises(acoustid.AcoustidError):
                acoustid.cover(bad)

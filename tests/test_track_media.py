"""Cover art (src/track_media.py): the two sides a picture comes from - the
prepared covers folder, and what the music folders and the tags carry - which
one wins, and what a picture that cannot be read does."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import track_media

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 64
JPEG = b"\xff\xd8\xff" + b"j" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"w" * 64
TAG = b"\xff\xd8\xff" + b"t" * 64


class CoverTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.covers = os.path.join(self.dir, "covers")
        self.track = self.write(self.music, "LMFAO", "Sorry For Party Rocking",
                                "03 - Party Rock Anthem.opus", data=b"x")

    def write(self, *parts, data=PNG):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def song(self, *parts, data=PNG):
        return self.write(self.music, *parts, data=data)

    def cover(self, priority=None, cover_dir=None, music_dir=None):
        return track_media.cover(self.track,
                                 self.covers if cover_dir is None else cover_dir,
                                 self.music if music_dir is None else music_dir,
                                 priority)

    def test_the_prepared_folder_is_a_mirror_of_the_music_one(self):
        own = self.write(self.covers, "LMFAO", "Sorry For Party Rocking",
                         "03 - Party Rock Anthem.jpg", data=JPEG)
        album = self.write(self.covers, "LMFAO", "Sorry For Party Rocking", "_any.png")
        artist = self.write(self.covers, "LMFAO", "_any.webp", data=WEBP)
        everything = self.write(self.covers, "_any.png", data=WEBP)
        self.assertEqual(self.cover(), (JPEG, "image/jpeg"), "the song's own picture")
        os.remove(own)
        self.assertEqual(self.cover(), (PNG, "image/png"), "then its album's")
        os.remove(album)
        self.assertEqual(self.cover(), (WEBP, "image/webp"), "then its artist's")
        os.remove(artist)
        self.assertEqual(self.cover(), (WEBP, "image/webp"), "then the whole library's")
        os.remove(everything)
        self.assertIsNone(self.cover())

    def test_the_jpg_comes_first_whatever_the_disk_lists(self):
        self.write(self.covers, "_any.png")
        self.write(self.covers, "_any.jpeg", data=WEBP)
        jpg = self.write(self.covers, "_any.jpg", data=JPEG)
        self.assertEqual(self.cover(), (JPEG, "image/jpeg"))
        os.remove(jpg)
        self.assertEqual(self.cover(), (WEBP, "image/webp"), "jpeg before png")

    def test_the_music_side_wins_when_the_priority_asks_for_it(self):
        self.write(self.covers, "LMFAO", "_any.png")
        sidecar = self.write(self.music, "LMFAO", "Sorry For Party Rocking",
                             "03 - Party Rock Anthem.jpg", data=JPEG)
        self.assertEqual(self.cover("files"), (PNG, "image/png"), "the default")
        self.assertEqual(self.cover("id3"), (JPEG, "image/jpeg"))
        os.remove(sidecar)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover("id3"), (PNG, "image/png"),
                             "the other side is only a fallback")

    def test_the_sidecar_comes_before_the_tag_and_the_folder_picture(self):
        sidecar = self.song("LMFAO", "Sorry For Party Rocking", "03 - Party Rock Anthem.png")
        self.song("LMFAO", "Sorry For Party Rocking", "cover.jpg", data=JPEG)
        with mock.patch.object(track_media, "_embedded_picture", return_value=TAG):
            self.assertEqual(self.cover("id3")[0], PNG, "the picture named after the track")
            os.remove(sidecar)
            self.assertEqual(self.cover("id3")[0], TAG, "then the embedded one, not the folder's")

    def test_the_folder_picture_goes_up_to_the_artist_then_the_library(self):
        album = self.song("LMFAO", "Sorry For Party Rocking", "cover.jpg", data=JPEG)
        artist = self.song("LMFAO", "front.png")
        everything = self.song("cover.webp", data=WEBP)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover("id3")[0], JPEG, "the album folder")
            os.remove(album)
            self.assertEqual(self.cover("id3")[0], PNG, "the artist folder")
            os.remove(artist)
            self.assertEqual(self.cover("id3")[0], WEBP, "the music folder itself")
            os.remove(everything)
            self.assertIsNone(self.cover("id3"))

    def test_the_artist_name_is_a_picture_of_the_artist_folder(self):
        self.song("LMFAO", "artist.jpg", data=JPEG)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover("id3"), (JPEG, "image/jpeg"))

    def test_a_picture_that_cannot_be_read_gives_way_to_the_next_one(self):
        self.write(self.covers, "LMFAO", "Sorry For Party Rocking", "_any.png", data=b"nope")
        self.song("LMFAO", "Sorry For Party Rocking", "03 - Party Rock Anthem.png", data=b"nope")
        self.song("LMFAO", "Sorry For Party Rocking", "cover.jpg", data=JPEG)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover("files"), (JPEG, "image/jpeg"),
                             "the prepared folder first, then the music one")

    def test_the_capital_letters_do_not_matter(self):
        self.write(self.covers, "lmfao", "SORRY FOR PARTY ROCKING", "_ANY.PNG")
        self.song("LMFAO", "Sorry For Party Rocking", "03 - PARTY ROCK ANTHEM.JPG", data=JPEG)
        self.assertEqual(self.cover("files"), (PNG, "image/png"))
        self.assertEqual(self.cover("id3"), (JPEG, "image/jpeg"))

    def test_a_picture_added_later_is_seen_without_a_restart(self):
        self.assertIsNone(self.cover(), "nothing yet, and it is remembered that way")
        self.write(self.covers, "_any.png")
        self.assertEqual(self.cover(), (PNG, "image/png"))
        self.write(self.music, "LMFAO", "Sorry For Party Rocking", "cover.jpg", data=JPEG)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover("id3"), (JPEG, "image/jpeg"))

    def test_a_track_outside_the_music_folder_still_gets_the_library_one(self):
        other = self.write(self.dir, "memes", "a jingle.opus", data=b"x")
        self.assertIsNone(track_media.cover(other, self.covers, self.music))
        self.write(self.covers, "_any.png")
        self.assertEqual(track_media.cover(other, self.covers, self.music), (PNG, "image/png"))

    def test_an_empty_covers_folder_only_looks_beside_the_music(self):
        self.write(self.covers, "_any.png")
        self.song("LMFAO", "Sorry For Party Rocking", "cover.jpg", data=JPEG)
        with mock.patch.object(track_media, "_embedded_picture", return_value=None):
            self.assertEqual(self.cover(cover_dir=""), (JPEG, "image/jpeg"))

    def test_an_unknown_priority_is_the_default_one(self):
        self.write(self.covers, "_any.png")
        self.assertEqual(self.cover("spoken"), (PNG, "image/png"))

    def test_the_old_call_still_reads_beside_the_track(self):
        """cover(path) without the settings: what the API always did."""
        self.song("LMFAO", "Sorry For Party Rocking", "03 - Party Rock Anthem.jpg", data=JPEG)
        self.assertEqual(track_media.cover(self.track), (JPEG, "image/jpeg"))
        self.assertIsNone(track_media.cover(os.path.join(self.dir, "nothing.mp3")))
        self.assertIsNone(track_media.cover(""))


class LyricsStillWorkTest(unittest.TestCase):
    """The cover lookup was rewritten around them; these must not have moved."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.track = os.path.join(self.dir, "A song.opus")
        with open(self.track, "wb") as handle:
            handle.write(b"x")

    def test_a_sidecar_lyrics_file_is_found(self):
        with open(os.path.join(self.dir, "A song.lrc"), "w", encoding="utf-8") as handle:
            handle.write("[00:01.00]hello\n")
        found = track_media.lyrics(self.track)
        self.assertTrue(found["synced"])
        self.assertEqual(found["lines"][0]["text"], "hello")
        self.assertEqual(found["source"], "lrc")

    def test_a_picture_is_a_companion_file(self):
        self.assertTrue(track_media.is_companion_file("cover.jpg"))
        self.assertTrue(track_media.is_companion_file("A song.lrc"))
        self.assertFalse(track_media.is_companion_file("A song.opus"))


if __name__ == "__main__":
    unittest.main()

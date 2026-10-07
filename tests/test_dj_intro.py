"""The prepared introductions (src/dj_intro.py): which file introduces a song,
and how many of the library's songs have one."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import dj_intro


class PreparedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.intros = os.path.join(self.dir, "dj_announcements")

    def song(self, *parts):
        path = os.path.join(self.music, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def intro(self, *parts):
        path = os.path.join(self.intros, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def find(self, track):
        return dj_intro.find(track, self.intros, self.music)

    def test_the_song_own_file_comes_first(self):
        track = self.song("LMFAO", "Sorry", "03 - Party Rock Anthem.opus")
        self.assertIsNone(self.find(track), "nothing prepared yet")
        own = self.intro("LMFAO", "Sorry", "03 - Party Rock Anthem.wav")
        self.intro("LMFAO", "Sorry", "_any.mp3")
        self.intro("LMFAO", "_any.mp3")
        self.intro("_any.mp3")
        self.assertEqual(self.find(track), own)

    def test_any_beside_the_song_covers_its_album(self):
        track = self.song("LMFAO", "Sorry", "03 - Party Rock Anthem.opus")
        album = self.intro("LMFAO", "Sorry", "_any.wav")
        self.intro("LMFAO", "_any.wav")
        self.assertEqual(self.find(track), album,
                         "and it does not matter what the song is called")

    def test_any_beside_the_artist_covers_all_of_it(self):
        track = self.song("LMFAO", "Sorry", "03 - Party Rock Anthem.opus")
        artist = self.intro("LMFAO", "_any.wav")
        self.intro("_any.wav")
        self.assertEqual(self.find(track), artist)

    def test_any_in_the_folder_covers_the_whole_library(self):
        track = self.song("Someone", "Something", "A song.opus")
        everything = self.intro("_any.wav")
        self.assertEqual(self.find(track), everything)

    def test_a_song_in_the_music_folder_itself(self):
        track = self.song("A song.opus")
        own = self.intro("A song.wav")
        self.intro("_any.wav")
        self.assertEqual(self.find(track), own)

    def test_the_name_is_read_whatever_its_case(self):
        track = self.song("LMFAO", "Sorry For Party Rocking", "03 - Party Rock Anthem.opus")
        prepared = self.intro("lmfao", "sorry for party rocking", "03 - PARTY ROCK ANTHEM.WAV")
        self.assertEqual(self.find(track), prepared)

    def test_the_extension_has_a_fixed_order(self):
        track = self.song("A song.opus")
        self.intro("A song.mp3")
        wav = self.intro("A song.wav")
        self.assertEqual(self.find(track), wav, "the same one every time, not whichever the disk lists first")

    def test_the_folder_holds_other_things_too(self):
        track = self.song("A song.opus")
        self.intro("A song.txt")
        self.intro("notes.txt")
        self.assertIsNone(self.find(track), "only what mpv can play counts")

    def test_a_song_that_is_not_under_the_music_folder_gets_the_jingle(self):
        other = os.path.join(self.dir, "elsewhere", "A song.opus")
        os.makedirs(os.path.dirname(other), exist_ok=True)
        with open(other, "wb") as handle:
            handle.write(b"x")
        self.assertIsNone(self.find(other))
        everything = self.intro("_any.wav")
        self.assertEqual(self.find(other), everything)

    def test_nothing_prepared_at_all(self):
        track = self.song("A song.opus")
        self.assertIsNone(self.find(track))
        self.assertIsNone(dj_intro.find(track, "", self.music), "no folder configured")
        self.assertIsNone(dj_intro.find("", self.intros, self.music))
        self.assertIsNone(dj_intro.find(track, self.intros, ""))


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.intros = os.path.join(self.dir, "dj_announcements")

    def write(self, folder, *names):
        os.makedirs(folder, exist_ok=True)
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            del path

    def test_it_counts_the_files_and_the_songs_they_cover(self):
        self.write(self.music, "a.mp3", "b.mp3", "c.mp3")
        self.write(os.path.join(self.intros), "a.wav", "_any.mp3", "notes.txt")
        tracks = [os.path.join(self.music, name) for name in ("a.mp3", "b.mp3", "c.mp3")]
        self.assertEqual(dj_intro.scan(tracks, self.intros, self.music), (2, 3),
                         "two files, and the jingle covers all three songs")

    def test_a_folder_that_is_not_there_counts_nothing(self):
        tracks = [os.path.join(self.music, "a.mp3")]
        self.assertEqual(dj_intro.scan(tracks, self.intros, self.music), (0, 0))


if __name__ == "__main__":
    unittest.main()

"""M3U playlists (src/m3u.py): a list written out for another player, and one
read back in and matched against the library."""
import io
import unittest
from unittest import mock

import _path  # noqa: F401
import m3u

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None

ROOT = "/home/pi/audio/music"
SONGS = {
    ROOT + "/LMFAO/Sorry/03 - Party.opus": {"key": "k1", "title": "Party Rock Anthem", "artist": "LMFAO"},
    ROOT + "/a-ha/Hunting/01 - Take On Me.mp3": {"key": "k2", "title": "Take On Me", "artist": "a-ha"},
    ROOT + "/Queen/Night/11 - Bohemian.flac": {"key": "k3", "title": "Bohemian Rhapsody", "artist": "Queen"},
}


class FakeLibrary:
    def item_for_path(self, path):
        return SONGS.get(path)

    def item_for_basename(self, name):
        return next((item for path, item in SONGS.items() if path.endswith("/" + name)), None)

    def match(self, text):
        artist, _, title = text.partition(" - ")
        return next((item for item in SONGS.values()
                     if item["artist"] == artist and item["title"] == title), None)

    def path_for_key(self, key):
        return next(path for path, item in SONGS.items() if item["key"] == key)


class M3uTest(unittest.TestCase):
    def test_a_list_is_written_with_paths_inside_the_music_folder(self):
        text = m3u.export([{"path": ROOT + "/LMFAO/Sorry/03 - Party.opus", "title": "Party Rock Anthem",
                            "artist": "LMFAO", "duration": 262.4},
                           {"path": "/elsewhere/x.mp3"}], [ROOT])
        self.assertEqual(text, "#EXTM3U\n#EXTINF:262,LMFAO - Party Rock Anthem\nLMFAO/Sorry/03 - Party.opus\n"
                               "#EXTINF:-1,x\n/elsewhere/x.mp3\n")

    def test_every_way_a_player_names_a_song_is_matched(self):
        text = ("#EXTM3U\n"
                "#EXTINF:262,LMFAO - Party Rock Anthem\nLMFAO/Sorry/03 - Party.opus\n"
                "C:\\Users\\me\\Music\\01 - Take On Me.mp3\n"
                "#EXTINF:355,Queen - Bohemian Rhapsody\n/media/old/bohemian.flac\n"
                "http://radio.example/stream\n"
                "#EXTINF:200,Nobody - Nothing\nnothing.mp3\n"
                "LMFAO/Sorry/03 - Party.opus\n")
        found, missing = m3u.match(m3u.parse(text), FakeLibrary(), [ROOT])
        self.assertEqual(found, [ROOT + "/LMFAO/Sorry/03 - Party.opus", ROOT + "/a-ha/Hunting/01 - Take On Me.mp3",
                                 ROOT + "/Queen/Night/11 - Bohemian.flac"],
                         "inside the music folder, by file name, by Artist - Title; each once")
        self.assertEqual(missing, ["http://radio.example/stream", "Nobody - Nothing"])

    def test_a_file_from_windows_is_read_too(self):
        self.assertEqual(m3u.decode("\ufeffcaf\u00e9.mp3".encode("utf-8")), "caf\u00e9.mp3")
        self.assertEqual(m3u.decode("caf\u00e9.mp3".encode("latin-1")), "caf\u00e9.mp3")


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class ImportRouteTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ws, "_get_library", return_value=FakeLibrary()).start()
        mock.patch.object(ws, "_library_roots", return_value=(ROOT, None)).start()
        mock.patch.object(ws, "notify_daemon").start()
        self.added = mock.patch.object(ws.music_lists, "add", side_effect=lambda data: dict(
            data, id="road-trip")).start()
        mock.patch.object(ws, "_with_counts", side_effect=lambda items: items).start()
        self.client = ws.app.test_client()

    def send(self, text, name="Road trip.m3u"):
        return self.client.post("/api/lists/import", content_type="multipart/form-data",
                                data={"file": (io.BytesIO(text.encode("utf-8")), name)})

    def test_an_imported_file_becomes_a_list_of_what_the_library_has(self):
        answer = self.send("LMFAO/Sorry/03 - Party.opus\nnothing.mp3\n").get_json()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual((answer["data"]["found"], answer["data"]["missing_count"]), (1, 1))
        self.assertEqual(self.added.call_args[0][0],
                         {"name": "Road trip", "kind": "manual", "tracks": [ROOT + "/LMFAO/Sorry/03 - Party.opus"]})

    def test_a_file_with_nothing_known_makes_no_list(self):
        answer = self.send("nothing.mp3\n")
        self.assertEqual((answer.status_code, answer.get_json()["error"]), (400, "m3u_nothing_found"))
        self.added.assert_not_called()


if __name__ == "__main__":
    unittest.main()

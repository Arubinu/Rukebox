"""Web radios (src/stations.py): only web addresses kept, played as a cast that gives way
to the radio's music, the song the station says it plays shown, and a schedule that opens with one."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path
import schedules
import stations
from test_empty_library import DaemonCase

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None

FIP = "https://icecast.radiofrance.fr/fip-midfi.mp3"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = _path.document(self.dir)
        self.addCleanup(_path.document, None)

    def test_only_a_web_stream_is_kept(self):
        first = stations.add({"name": "  FIP  ", "url": FIP}, self.path)
        self.assertEqual((first["id"], first["name"]), ("fip", "FIP"))
        self.assertEqual(stations.add({"name": "FIP", "url": FIP}, self.path)["id"], "fip-2")
        for data, code in (({"name": "", "url": FIP}, "station_name_required"),
                           ({"name": "x", "url": "file:///etc/passwd"}, "station_bad_url"),
                           ({"name": "x", "url": "/home/pi/a.mp3"}, "station_bad_url"),
                           ({"name": "x", "url": "http://a b/c"}, "station_bad_url")):
            with self.assertRaisesRegex(ValueError, code):
                stations.add(data, self.path)
        stations.update("fip", {"name": "Fip Radio"}, self.path)
        self.assertEqual(stations.get("fip", self.path)["name"], "Fip Radio")
        stations.delete("fip", self.path)
        with self.assertRaises(KeyError):
            stations.get("fip", self.path)


class DaemonStationTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.daemon.mode = "music"
        self.daemon._paused = False
        self.daemon._last_music_track = os.path.join(self.music, "a.mp3")
        stations.add({"name": "FIP", "url": FIP})

    def test_a_station_plays_as_a_cast_and_says_its_song(self):
        self.assertEqual(self.daemon._play_station("nope", "test")["error"], "not_found")
        self.assertTrue(self.daemon._play_station("fip", "test")["ok"])
        self.assertEqual((self.daemon.mode, self.daemon.mpv.files[-1]), ("cast", FIP))
        self.assertEqual(self.daemon._cast["origin"], "station")
        self.daemon._on_mpv_event({"event": "property-change", "name": "metadata/by-key/icy-title",
                                   "data": "Nina Simone - Feeling Good"})
        self.assertEqual(self.daemon._build_status()["cast"]["now"], "Nina Simone - Feeling Good")
        with mock.patch.object(self.daemon, "_resume_after_announce"):
            self.daemon._end_cast()
        self.assertIsNone(self.daemon._cast, "the radio's own music comes back")

    def test_a_schedule_opens_with_its_station(self):
        self.daemon.mode = "idle"
        item = schedules.validate({"name": "News", "start": "08:00", "station": "fip"})
        with mock.patch.object(self.daemon, "_start_or_restart_playback") as music:
            self.daemon._schedule_start(dict(item, id="news"))
        self.assertEqual(self.daemon.mode, "cast")
        music.assert_not_called()
        self.daemon.mode = "idle"
        self.daemon._cast = None
        with mock.patch.object(self.daemon, "_start_or_restart_playback") as music:
            self.daemon._schedule_start(dict(item, id="news", station="gone"))
        music.assert_called_once()


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class RouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        _path.document(self.dir)
        self.addCleanup(_path.document, None)
        self.client = ws.app.test_client()

    def test_added_listed_played_and_removed(self):
        answer = self.client.post("/api/stations", json={"name": "FIP", "url": FIP}).get_json()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.client.post("/api/stations", json={"name": "x", "url": "ftp://x"}).get_json()["error"],
                         "station_bad_url")
        self.assertEqual([one["id"] for one in self.client.get("/api/stations").get_json()["data"]["stations"]],
                         ["fip"])
        with mock.patch.object(ws, "control", return_value={"ok": True}) as control:
            self.client.post("/api/stations/fip/play")
        control.assert_called_once_with("play_station", id="fip")
        self.assertTrue(self.client.delete("/api/stations/fip").get_json()["ok"])
        self.assertEqual(self.client.delete("/api/stations/fip").status_code, 404)

    def test_the_player_shows_the_song_under_the_stations_name(self):
        status = {"mode": "cast", "current_track_path": None,
                  "cast": {"origin": "station", "title": "FIP", "now": "Nina Simone - Feeling Good", "url": FIP}}
        with mock.patch.object(ws, "control", return_value={"ok": True, "data": status}),                 mock.patch.object(ws, "_audio_output_state", return_value={}),                 mock.patch.object(ws, "_bt_controllers", return_value=[]):
            data = self.client.get("/api/status").get_json()["data"]
        self.assertEqual((data["track_title"], data["track_artist"]), ("Nina Simone - Feeling Good", "FIP"))

"""What the daemon plays (src/rukebox_daemon.py): the whole library minus the
excluded tracks, or the active music list - a manual one in the order it was
built, a genre one from the library's tags, and the tracks excluded from the
radio's own passes it keeps - and the loudness filter pushed to mpv. mpv
itself is faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
import bt_link
from config_and_scan import load_config
import hidden_tracks
import library
import music_lists
import rukebox_daemon
import track_media


class FakeMpv:
    """Records what the daemon asked mpv to do."""

    def __init__(self):
        self.files = []
        self.filters = []
        self.paused = None
        self.volume = None

    def loadfile(self, path):
        self.files.append(path)

    def set_pause(self, paused):
        self.paused = paused

    def set_volume(self, volume):
        self.volume = volume

    def set_audio_filter(self, chain):
        self.filters.append(chain)
        return True

    def stop(self):
        self.files.append(None)

    def set_loop(self, mode="no"):
        pass

    def seek(self, seconds):
        pass

    def set_replaygain(self, mode):
        pass

    def set_mute(self, muted):
        pass

    def set_audio_device(self, device):
        pass

    def observe(self, prop_id, name):
        pass

    def on_event(self, callback):
        pass


class DaemonListsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.music = os.path.join(self.dir, "music")
        self.paths = {}
        for name in ("jazz1.mp3", "rock1.mp3", "metal.mp3", "Daft Punk/album1.mp3"):
            path = os.path.join(self.music, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x" * 10)
            self.paths[name] = path

        self.lists_file = os.path.join(self.dir, "music_lists.json")
        lib = library.Library(os.path.join(self.dir, "library.db"), track_media.track_key)
        lib.sync(list(self.paths.values()), self.music)
        for name, genre in (("jazz1.mp3", "Jazz"), ("rock1.mp3", "Rock"),
                            ("metal.mp3", "Alternative Metal;Heavy Metal;Kawaii Metal"),
                            ("Daft Punk/album1.mp3", "Jazz")):
            lib.store(self.paths[name], {"genre": genre}, self.music)
        lib._db.close()

        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "LIST_LIBRARY_DB": None, "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "MUSIC_LISTS_FILE": self.lists_file,
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "HIDDEN_FILE": os.path.join(self.dir, "hidden.json"),
            "INTERACTIVE_FADE_DURATION_SEC": 0,
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()

    def tearDown(self):
        if self.daemon._list_library is not None:
            self.daemon._list_library._db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def jazz(self):
        return [self.paths["jazz1.mp3"], self.paths["Daft Punk/album1.mp3"]]

    def test_everything_plays_when_no_list_is_active(self):
        self.assertIsNone(self.daemon.state.active_list())
        self.assertEqual(sorted(self.daemon._playable_tracks()),
                         sorted(self.paths.values()))
        self.assertIsNone(self.daemon._active_list_status())
        self.assertIsNone(self.daemon._build_status()["active_list"])

    def test_a_genre_list_limits_what_plays(self):
        entry = music_lists.add(self.lists_file,
                                {"name": "Jazz", "kind": "genre", "genres": ["jazz"]})
        result = self.daemon._set_active_list(entry["id"], "test")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["tracks"], 2)
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted(self.jazz()))
        self.assertEqual(sorted(self.daemon.state.data["play_queue"]), sorted(self.jazz()))
        self.assertEqual(self.daemon._active_list_status(),
                         {"id": entry["id"], "name": "Jazz", "kind": "genre",
                          "genres": ["jazz"], "tracks": 2})
        self.assertEqual(self.daemon._build_status()["active_list"]["name"], "Jazz")

    def test_a_genre_list_finds_a_genre_among_several(self):
        entry = music_lists.add(self.lists_file,
                                {"name": "Heavy", "kind": "genre", "genres": ["Heavy Metal"]})
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon._playable_tracks(), [self.paths["metal.mp3"]])

    def test_a_manual_list_keeps_the_order_it_was_built_in(self):
        entry = music_lists.add(self.lists_file, {"name": "Soir", "kind": "manual"})
        music_lists.add_track(self.lists_file, entry["id"], self.paths["rock1.mp3"])
        music_lists.add_track(self.lists_file, entry["id"], self.paths["jazz1.mp3"])
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon.state.data["play_queue"],
                         [self.paths["rock1.mp3"], self.paths["jazz1.mp3"]])

    def test_playing_a_list_starts_it_now(self):
        entry = music_lists.add(self.lists_file, {"name": "Soir", "kind": "manual"})
        music_lists.add_track(self.lists_file, entry["id"], self.paths["rock1.mp3"])
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon.mode = "music"
        self.daemon._set_active_list(entry["id"], "test", start=True)
        self.assertEqual(self.daemon.mpv.files[-1], self.paths["rock1.mp3"])
        self.assertEqual(self.daemon.state.active_list(), entry["id"])

    def test_going_back_to_everything(self):
        entry = music_lists.add(self.lists_file, {"name": "Jazz", "kind": "genre",
                                                  "genres": ["Jazz"]})
        self.daemon._set_active_list(entry["id"], "test")
        self.assertTrue(self.daemon._set_active_list(None, "test")["ok"])
        self.assertIsNone(self.daemon.state.active_list())
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted(self.paths.values()))

    def test_an_unknown_list_is_refused(self):
        result = self.daemon._set_active_list("nope", "test")
        self.assertEqual((result["ok"], result["error"]), (False, "list_not_found"))
        self.assertIsNone(self.daemon.state.active_list())

    def test_a_deleted_list_goes_back_to_everything(self):
        entry = music_lists.add(self.lists_file, {"name": "Jazz", "kind": "genre",
                                                  "genres": ["Jazz"]})
        self.daemon._set_active_list(entry["id"], "test")
        before = os.stat(self.lists_file).st_mtime_ns
        music_lists.delete(self.lists_file, entry["id"])
        # A filesystem with coarse timestamps leaves the date alone when two
        # writes land in the same tick: the daemon then kept playing the list
        # the page had just deleted, and only its size told them apart.
        os.utime(self.lists_file, ns=(before, before))
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted(self.paths.values()))
        self.assertIsNone(self.daemon.state.active_list())

    def test_a_manual_list_forgets_a_file_the_library_lost(self):
        entry = music_lists.add(self.lists_file, {"name": "Soir", "kind": "manual"})
        music_lists.add_track(self.lists_file, entry["id"], self.paths["jazz1.mp3"])
        music_lists.add_track(self.lists_file, entry["id"], "/nowhere/gone.mp3")
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon._playable_tracks(), [self.paths["jazz1.mp3"]])

    def test_the_compression_filter_follows_the_setting(self):
        self.daemon.cfg["AUDIO_COMPRESSION"] = "soft"
        self.assertTrue(self.daemon._apply_compression())
        self.assertIn("acompressor=", self.daemon.mpv.filters[-1])
        self.daemon.cfg["AUDIO_COMPRESSION"] = "nonsense"
        self.assertTrue(self.daemon._apply_compression())
        self.assertEqual(self.daemon.mpv.filters[-1], "", "unknown means off")
        self.daemon.cfg["AUDIO_COMPRESSION"] = "strong"
        self.assertTrue(self.daemon._apply_compression())
        self.assertIn("volume=9dB", self.daemon.mpv.filters[-1])

    def test_a_build_without_the_filter_still_plays(self):
        self.daemon.mpv.set_audio_filter = lambda chain: False
        self.daemon.cfg["AUDIO_COMPRESSION"] = "soft"
        self.assertFalse(self.daemon._apply_compression())

    def test_the_speaker_is_looked_for_on_every_controller(self):
        original = bt_link.locate
        seen = []
        bt_link.locate = lambda mac, adapter="": seen.append((mac, adapter)) or {
            "mac": mac, "connected": True, "controller": "B8:27:EB:62:82:CB",
            "expected": adapter, "paired_here": False, "known_here": False,
            "name": "", "unknown": False}
        try:
            self.daemon.cfg["SPEAKER_MAC"] = "7C:E9:13:69:66:55"
            self.daemon.cfg["SPEAKER_BT_ADAPTER"] = "00:A7:50:72:14:C4"
            state = self.daemon._speaker_link()
        finally:
            bt_link.locate = original
        self.assertTrue(state["connected"])
        self.assertEqual(seen, [("7C:E9:13:69:66:55", "00:A7:50:72:14:C4")],
                         "the configured controller is the one named to bt_link")

    def test_the_speaker_is_moved_to_the_controller_set_for_it(self):
        original_locate, original_connect = bt_link.locate, bt_link.connect_here
        calls = []
        bt_link.locate = lambda mac, adapter="": {
            "mac": mac, "connected": True, "controller": "B8:27:EB:62:82:CB",
            "expected": "00:A7:50:72:14:C4", "paired_here": True, "known_here": True,
            "name": "", "unknown": False}
        bt_link.connect_here = lambda mac, adapter: calls.append((mac, adapter)) or True
        try:
            self.daemon.cfg["SPEAKER_MAC"] = "7C:E9:13:69:66:55"
            state = self.daemon._speaker_link()
            # A machine started a minute ago: its uptime clock is still under the retry delay.
            with mock.patch.object(rukebox_daemon.time, "monotonic", return_value=60.0):
                self.daemon._move_speaker_to_its_controller(state)
            self.assertEqual(calls, [("7C:E9:13:69:66:55", "00:A7:50:72:14:C4")])
            self.daemon._speaker_move_at = rukebox_daemon.time.monotonic()
            self.daemon._move_speaker_to_its_controller(state)
            self.assertEqual(len(calls), 1, "not again before the retry delay")
        finally:
            bt_link.locate, bt_link.connect_here = original_locate, original_connect

    def test_a_controller_that_does_not_know_the_speaker_is_left_alone(self):
        original_locate, original_connect = bt_link.locate, bt_link.connect_here
        calls = []
        bt_link.locate = lambda mac, adapter="": {
            "mac": mac, "connected": True, "controller": "B8:27:EB:62:82:CB",
            "expected": "00:A7:50:72:14:C4", "paired_here": False, "known_here": False,
            "name": "", "unknown": False}
        bt_link.connect_here = lambda mac, adapter: calls.append(adapter) or True
        try:
            self.daemon.cfg["SPEAKER_MAC"] = "7C:E9:13:69:66:55"
            self.daemon._move_speaker_to_its_controller(self.daemon._speaker_link())
        finally:
            bt_link.locate, bt_link.connect_here = original_locate, original_connect
        self.assertEqual(calls, [], "a controller that never saw it cannot take it back")

    def test_an_excluded_track_is_not_played_by_the_radio(self):
        hidden_tracks.set_hidden(self.daemon.cfg["HIDDEN_FILE"], "k1", True,
                                 self.paths["jazz1.mp3"], "Jazz 1", "Someone")
        self.assertEqual(sorted(self.daemon._playable_tracks()),
                         sorted(set(self.paths.values()) - {self.paths["jazz1.mp3"]}))
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._rebuild_queue()
        self.assertNotIn(self.paths["jazz1.mp3"], self.daemon.state.data["play_queue"])

    def test_a_list_keeps_what_the_radio_must_not_pick_itself(self):
        entry = music_lists.add(self.lists_file, {"name": "Soir", "kind": "manual"})
        music_lists.add_track(self.lists_file, entry["id"], self.paths["jazz1.mp3"])
        music_lists.add_track(self.lists_file, entry["id"], self.paths["rock1.mp3"])
        hidden_tracks.set_hidden(self.daemon.cfg["HIDDEN_FILE"], "k1", True,
                                 self.paths["jazz1.mp3"])
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon._playable_tracks(),
                         [self.paths["jazz1.mp3"], self.paths["rock1.mp3"]],
                         "a list is an explicit choice: the page marks the track instead")
        self.assertEqual(self.daemon._active_list_status()["tracks"], 2)
        self.daemon._set_active_list(None, "test")
        self.assertNotIn(self.paths["jazz1.mp3"], self.daemon._playable_tracks())

    def test_a_folder_of_a_card_still_skips_them(self):
        hidden_tracks.set_hidden(self.daemon.cfg["HIDDEN_FILE"], "k1", True,
                                 self.paths["Daft Punk/album1.mp3"])
        self.assertEqual(self.daemon._play_folder("Daft Punk", "web"), "not_found",
                         "the radio picks inside that folder, so the exclusion holds")

    def test_excluding_takes_the_track_out_of_the_pass_under_way(self):
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._rebuild_queue()
        asked = self.paths["metal.mp3"]
        self.daemon.state.enqueue_request(asked)
        queue = list(self.daemon.state.data["play_queue"])
        hidden_tracks.set_hidden(self.daemon.cfg["HIDDEN_FILE"], "k1", True,
                                 self.paths["rock1.mp3"])
        answer = self.daemon._reload_hidden()
        self.assertEqual((answer["hidden"], answer["removed"]), (1, 1))
        self.assertEqual(self.daemon.state.data["play_queue"],
                         [p for p in queue if p != self.paths["rock1.mp3"]],
                         "the rest of the pass keeps its order")
        self.assertEqual(self.daemon.state.requested_paths(), [asked],
                         "the songs asked for are still waiting")

    def test_a_duration_list_is_cleaned_and_never_left_empty(self):
        self.assertEqual(self.daemon._pause_durations(), [5, 15, 30, 60])
        self.assertEqual(self.daemon._sleep_durations(), [30, 60, 90, 120])
        self.daemon.cfg["SLEEP_DURATIONS"] = "60; 30,30,15, 900,abc"
        self.assertEqual(self.daemon._sleep_durations(), [15, 30, 60],
                         "sorted, no duplicate, nothing outside 1-600")
        for empty in ("", None, "pony"):
            self.daemon.cfg["SLEEP_DURATIONS"] = empty
            self.assertEqual(self.daemon._sleep_durations(), [30, 60, 90, 120],
                             "an unusable setting falls back to the default")

    def test_the_sleep_timer_offers_what_the_web_dialog_does(self):
        self.daemon.cfg["SLEEP_DURATIONS"] = "45,20"
        self.assertEqual(self.daemon._build_status()["sleep_durations"], [20, 45])
        seen = []
        original = self.daemon._set_timer
        self.daemon._set_timer = lambda name, delay, fn=None: seen.append((name, delay))
        try:
            # No minutes named: the click action sleeps for the shortest one.
            self.daemon._start_sleep_timer(None, "test")
        finally:
            self.daemon._set_timer = original
        self.assertEqual(seen, [("sleep", 20 * 60)])


if __name__ == "__main__":
    unittest.main()

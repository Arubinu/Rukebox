"""What the daemon plays (src/rukebox_daemon.py): the whole library minus the
excluded tracks, or the lists played together, minus the lists left out - a manual one in the order it was
built, a genre one from the library's tags, and the tracks excluded from the
radio's own passes it keeps - and the loudness filter pushed to mpv. mpv
itself is faked, so this runs off-hardware."""
import datetime
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import _path  # noqa: F401
import bt_link
from config_and_scan import load_config
import control_client
import hidden_tracks
import library
import music_lists
import rukebox_daemon
import track_media


class FakeMpv:
    """Records what the daemon asked mpv to do."""
    def set_volume_gain(self, db):
        self.volume_gain = db


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
        _path.document(self.dir)
        self.music = os.path.join(self.dir, "music")
        self.paths = {}
        for name in ("jazz1.mp3", "rock1.mp3", "metal.mp3", "Daft Punk/album1.mp3"):
            path = os.path.join(self.music, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x" * 10)
            self.paths[name] = path

        self.lists_file = _path.document(self.dir)
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
        entry = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["jazz"]}, self.lists_file)
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
        entry = music_lists.add({"name": "Heavy", "kind": "genre", "genres": ["Heavy Metal"]}, self.lists_file)
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon._playable_tracks(), [self.paths["metal.mp3"]])

    def test_a_manual_list_keeps_the_order_it_was_built_in(self):
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.lists_file)
        music_lists.add_track(entry["id"], self.paths["rock1.mp3"], self.lists_file)
        music_lists.add_track(entry["id"], self.paths["jazz1.mp3"], self.lists_file)
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._set_active_list(entry["id"], "test")
        self.assertEqual(self.daemon.state.data["play_queue"],
                         [self.paths["rock1.mp3"], self.paths["jazz1.mp3"]])

    def test_playing_a_list_starts_it_now(self):
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.lists_file)
        music_lists.add_track(entry["id"], self.paths["rock1.mp3"], self.lists_file)
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon.mode = "music"
        self.daemon._set_active_list(entry["id"], "test", start=True)
        self.assertEqual(self.daemon.mpv.files[-1], self.paths["rock1.mp3"])
        self.assertEqual(self.daemon.state.active_list(), entry["id"])

    def test_going_back_to_everything(self):
        entry = music_lists.add({"name": "Jazz", "kind": "genre",
                                                  "genres": ["Jazz"]}, self.lists_file)
        self.daemon._set_active_list(entry["id"], "test")
        self.assertTrue(self.daemon._set_active_list(None, "test")["ok"])
        self.assertIsNone(self.daemon.state.active_list())
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted(self.paths.values()))

    def test_an_unknown_list_is_refused(self):
        result = self.daemon._set_active_list("nope", "test")
        self.assertEqual((result["ok"], result["error"]), (False, "list_not_found"))
        self.assertIsNone(self.daemon.state.active_list())

    def test_a_deleted_list_goes_back_to_everything(self):
        entry = music_lists.add({"name": "Jazz", "kind": "genre",
                                                  "genres": ["Jazz"]}, self.lists_file)
        self.daemon._set_active_list(entry["id"], "test")
        before = os.stat(self.lists_file).st_mtime_ns
        music_lists.delete(entry["id"], self.lists_file)
        # A filesystem with coarse timestamps leaves the date alone when two
        # writes land in the same tick: the daemon then kept playing the list
        # the page had just deleted, and only its size told them apart.
        os.utime(self.lists_file, ns=(before, before))
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted(self.paths.values()))
        self.assertIsNone(self.daemon.state.active_list())

    def test_a_manual_list_forgets_a_file_the_library_lost(self):
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.lists_file)
        music_lists.add_track(entry["id"], self.paths["jazz1.mp3"], self.lists_file)
        music_lists.add_track(entry["id"], "/nowhere/gone.mp3", self.lists_file)
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
        entry = music_lists.add({"name": "Soir", "kind": "manual"}, self.lists_file)
        music_lists.add_track(entry["id"], self.paths["jazz1.mp3"], self.lists_file)
        music_lists.add_track(entry["id"], self.paths["rock1.mp3"], self.lists_file)
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

    def manual(self, name, *keys):
        entry = music_lists.add({"name": name, "kind": "manual"}, self.lists_file)
        for key in keys:
            music_lists.add_track(entry["id"], self.paths[key], self.lists_file)
        return entry

    def test_several_lists_play_together(self):
        evening = self.manual("Soir", "rock1.mp3")
        jazz = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.lists_file)
        self.assertTrue(self.daemon._set_selection([evening["id"], jazz["id"]], None, "test")["ok"])
        self.assertEqual(sorted(self.daemon._playable_tracks()), sorted([self.paths["rock1.mp3"]] + self.jazz()))
        status = self.daemon._selection_status()
        self.assertEqual([one["id"] for one in status["play"]], [evening["id"], jazz["id"]])
        self.assertEqual(status["tracks"], 3)
        self.assertIsNone(self.daemon._active_list_entry(), "no single list: no custom order")

    def test_an_excluded_list_leaves_its_songs_out_of_everything(self):
        jazz = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.lists_file)
        mixed = self.manual("Mix", "jazz1.mp3", "rock1.mp3")
        self.daemon._set_selection(None, [jazz["id"]], "test")
        self.assertEqual(sorted(self.daemon._playable_tracks()),
                         sorted([self.paths["rock1.mp3"], self.paths["metal.mp3"]]), "the library without the jazz")
        self.daemon._set_active_list(mixed["id"], "test")
        self.assertEqual(self.daemon._playable_tracks(), [self.paths["rock1.mp3"]],
                         "a list played loses the songs an excluded list holds, and the exclusion stays")
        self.assertEqual(self.daemon.state.selection(), {"play": [mixed["id"]], "exclude": [jazz["id"]]})

    def test_excluding_a_list_leaves_the_pass_under_way_in_its_order(self):
        jazz = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.lists_file)
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._rebuild_queue()
        queue = list(self.daemon.state.data["play_queue"])
        self.daemon._set_selection(None, [jazz["id"]], "test")
        self.assertEqual(self.daemon.state.data["play_queue"], [p for p in queue if p not in self.jazz()],
                         "not reshuffled: the excluded songs just leave it")

    def test_a_schedule_sets_its_lists_and_gives_the_old_ones_back(self):
        evening = self.manual("Soir", "rock1.mp3")
        jazz = music_lists.add({"name": "Jazz", "kind": "genre", "genres": ["Jazz"]}, self.lists_file)
        self.daemon._set_selection([evening["id"]], [], "test")
        running = {"id": "night", "name": "Night", "settings": {}, "lists": None, "exclude": [jazz["id"]],
                   "end": datetime.datetime(2026, 1, 1, 23, 0)}
        with mock.patch.object(rukebox_daemon, "load_config", return_value=dict(self.daemon.cfg)):
            self.daemon._follow_schedule(running)
            self.assertEqual(self.daemon.state.selection(), {"play": [evening["id"]], "exclude": [jazz["id"]]},
                             "lists: None leaves what plays alone")
            self.daemon._follow_schedule(None)
        self.assertEqual(self.daemon.state.selection(), {"play": [evening["id"]], "exclude": []})

    def test_a_list_played_next_then_the_pass_goes_on_where_it_was(self):
        self.daemon.cfg["MUSIC_ORDER_MODE"] = "ordered"
        self.daemon._rebuild_queue()
        asked = self.paths["metal.mp3"]
        self.daemon.state.enqueue_request(asked)
        evening = self.manual("Soir", "rock1.mp3", "jazz1.mp3")
        out = self.manual("Out", "jazz1.mp3")
        self.daemon._set_selection(None, [out["id"]], "test")
        pass_before = [p for p in self.daemon.state.data["play_queue"] if p != asked]
        self.assertTrue(pass_before)
        self.daemon.mode = "music"
        answer = self.daemon._queue_list(evening["id"], None, "test")
        self.assertEqual(answer["data"], {"added": 2, "of": 2, "already": 0, "started": False})
        queue = self.daemon.state.data["play_queue"]
        self.assertEqual(queue[:3], [asked, self.paths["rock1.mp3"], self.paths["jazz1.mp3"]],
                         "after what was asked before, in the list's order, the excluded song too: asked for")
        self.assertEqual(self.daemon.state.requested_paths(), queue[:3])
        self.assertEqual(queue[3:], [p for p in pass_before if p not in queue[:3]],
                         "then the pass, in the order it had")
        again = self.daemon._queue_list(evening["id"], None, "test")
        self.assertEqual((again["data"]["added"], again["data"]["already"]), (0, 2),
                         "a song already waiting is not asked twice")
        while self.daemon.state.requested_paths():
            self.daemon.state.pop_next_track_or_none()
        self.daemon.state.data["play_queue"].append(self.paths["rock1.mp3"])
        later = self.daemon._queue_list(evening["id"], None, "test")
        self.assertEqual(later["data"]["added"], 2, "once played, the list can be asked for again")

    def test_a_list_played_next_is_capped_and_starts_a_silent_radio(self):
        entry = self.manual("Soir", "rock1.mp3", "jazz1.mp3", "metal.mp3")
        self.daemon.LIST_QUEUE_MAX = 2
        self.daemon.mode = "idle"
        with mock.patch.object(self.daemon, "_start_or_restart_playback") as start:
            answer = self.daemon._queue_list(entry["id"], "p1", "test")
        self.assertEqual((answer["data"]["added"], answer["data"]["of"], answer["data"]["started"]), (2, 3, True))
        start.assert_called_once()
        self.assertEqual(self.daemon._queue_list("nope", None, "test")["error"], "list_not_found")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
    def test_up_next_moved_by_hand_through_the_socket(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        a, b, c = self.paths["rock1.mp3"], self.paths["jazz1.mp3"], self.paths["metal.mp3"]
        self.daemon.state.data["play_queue"] = [a, b, c]
        answer = control_client.send_control_command(path, "get_queue", n=5)["data"]
        self.assertEqual((answer["paths"], answer["movable"], answer["fixed"]), ([a, b, c], True, 0))
        moved = control_client.send_control_command(path, "move_queue", path=c, after="")
        self.assertEqual(moved, {"ok": True, "data": {"position": 0}})
        self.assertEqual(self.daemon.state.data["play_queue"], [c, a, b])
        self.daemon.state.pop_next_track_or_none()
        late = control_client.send_control_command(path, "move_queue", path=c, after=b)
        self.assertEqual(late.get("error"), "queue_changed", "the song started in between")
        self.daemon._forced_next = c
        answer = control_client.send_control_command(path, "get_queue", n=5)["data"]
        self.assertEqual((answer["paths"][0], answer["fixed"]), (c, 1), "a song forced next stays first")
        self.assertTrue(control_client.send_control_command(path, "move_queue", path=b, after=c)["ok"],
                        "dropped after it: first of the queue")
        self.assertEqual(self.daemon.state.data["play_queue"], [b, a])
        self.assertTrue(control_client.send_control_command(path, "drop_from_queue", path=c)["ok"],
                        "a song forced next can be taken out too")
        self.assertIsNone(self.daemon._forced_next)
        self.daemon.state.data["dedications"] = {b: {"from": "Luc", "text": "Pour toi"}}
        self.assertTrue(control_client.send_control_command(path, "drop_from_queue", path=b)["ok"])
        self.assertEqual((self.daemon.state.data["play_queue"], self.daemon.state.dedications()), ([a], {}),
                         "out of the queue, its dedication with it")
        self.assertEqual(control_client.send_control_command(path, "drop_from_queue", path=b).get("error"),
                         "queue_changed", "already gone: it started, or someone else took it out")
        self.daemon._loop_mode = "album"
        self.assertEqual(control_client.send_control_command(path, "move_queue", path=a, after="").get("error"),
                         "queue_not_movable")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
    def test_the_voice_test_answers_before_the_sentence_is_made(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        self.daemon.mode = "idle"
        rendering = threading.Event()
        said = threading.Event()
        with mock.patch.object(rukebox_daemon.speech, "engine", return_value="piper:test"),                 mock.patch.object(self.daemon, "_speech_file", side_effect=lambda kind: rendering.wait(5) and "/x.wav"),                 mock.patch.object(self.daemon, "_say_path", side_effect=lambda p: said.set()):
            answer = control_client.send_control_command(path, "speak", kind="time_date", timeout=2)
            self.assertEqual(answer, {"ok": True}, "piper may take longer than the page waits")
            self.assertFalse(said.is_set())
            rendering.set()
            self.assertTrue(said.wait(5), "said once made")
        with mock.patch.object(rukebox_daemon.speech, "engine", return_value=""):
            self.assertEqual(control_client.send_control_command(path, "speak", kind="time").get("error"),
                             "speech_unavailable", "no voice at all: said at once, as before")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
    def test_the_press_that_wakes_the_flic_button_is_ignored_when_asked(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        sound = os.path.join(folder, "flic.wav")
        open(sound, "wb").close()
        self.daemon.cfg["FLIC_CONNECT_SOUND"] = sound
        with mock.patch.object(self.daemon, "_play_cue_sound") as cue:
            self.assertTrue(control_client.send_control_command(path, "flic_connected", source="flic")["ok"])
        self.assertEqual(cue.call_args[0], (sound, "FLIC_CONNECT_SOUND"))
        with mock.patch.object(self.daemon, "_handle_single_click") as click:
            self.daemon.cfg["FLIC_IGNORE_WAKING_PRESS"] = True
            answer = control_client.send_control_command(path, "single_click", source="flic", waking=True)
            self.assertEqual(answer["data"], {"ignored": "waking"})
            click.assert_not_called()
            self.daemon.cfg["FLIC_IGNORE_WAKING_PRESS"] = False
            control_client.send_control_command(path, "single_click", source="flic", waking=True)
            click.assert_called_once()

    def test_the_signature_opens_every_system_sound_but_the_keep_alive(self):
        signature = os.path.join(self.dir, "signature.wav")
        sound = os.path.join(self.dir, "wifi.wav")
        for path in (signature, sound):
            open(path, "wb").close()
        self.daemon.cfg["SIGNATURE_SOUND"] = signature
        with mock.patch.object(rukebox_daemon.subprocess, "Popen") as popen,                 mock.patch.object(self.daemon, "_capped", return_value=None):
            self.daemon._play_cue_sound(sound, "AP_CONNECT_SOUND")
            self.assertEqual(popen.call_args[0][0][-2:], [signature, sound])
            self.daemon._play_cue_sound(sound, "KEEPALIVE_SOUND")
            self.assertEqual(popen.call_args[0][0][-2:][-1], sound)
            self.assertNotIn(signature, popen.call_args[0][0], "the keep-alive stays discreet")
            self.daemon._play_cue_sound(sound)
            self.assertNotIn(signature, popen.call_args[0][0], "a sound that is not a system sound")
            self.daemon.cfg["SIGNATURE_SOUND"] = ""
            self.daemon._play_cue_sound(sound, "AP_CONNECT_SOUND")
            self.assertNotIn(signature, popen.call_args[0][0], "turned off")

    def test_a_restart_waits_for_the_signature_before_its_sound(self):
        signature = os.path.join(self.dir, "signature.wav")
        sound = os.path.join(self.dir, "restart.wav")
        open(sound, "wb").close()
        import wave
        with wave.open(signature, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(1000)
            w.writeframes(bytes(500))
        self.daemon.cfg.update({"SIGNATURE_SOUND": signature, "RESTART_SOUND": sound})
        with mock.patch.object(self.daemon, "_play_cue_sound") as cue,                 mock.patch.object(self.daemon, "_set_timer") as timer,                 mock.patch.object(self.daemon, "_begin_play") as begin:
            self.daemon._do_planned_restart()
            cue.assert_called_once_with(signature, "SIGNATURE_SOUND")
            self.assertEqual(timer.call_args[0][:2], ("restart_cue", 0.25))
            begin.assert_not_called()
            timer.call_args[0][2]()
            begin.assert_called_once_with("restart_cue", sound)

    def test_a_restart_while_asleep_sleeps_on_until_someone_starts_the_music(self):
        self.daemon.cfg["MUSIC_START_MODE"] = "boot"
        self.daemon.mode = "idle"
        with mock.patch.object(rukebox_daemon, "_boot_id", return_value="b1"):
            self.daemon.state.note_asleep("b1")
            with mock.patch.object(self.daemon, "_start_music_faded") as start,                     mock.patch.object(self.daemon, "_start_keepalive") as wait:
                self.daemon._begin_run()
            start.assert_not_called()
            wait.assert_called_once()
            self.assertTrue(self.daemon._asleep_since_restart)
            self.daemon.cfg.update({"MUSIC_START_MODE": "bluetooth", "AUDIO_OUTPUT": "bluetooth"})
            with mock.patch.object(self.daemon, "_start_or_restart_playback") as play:
                self.daemon._start_music_on_speaker_connect("AA")
            play.assert_not_called()
            with mock.patch.object(self.daemon, "_start_music_faded"):
                self.daemon._prepare_music_start("click")
            self.assertFalse(self.daemon._asleep_since_restart, "a start by someone ends it")
            self.daemon.cfg["MUSIC_START_MODE"] = "boot"
            with mock.patch.object(self.daemon, "_start_music_faded") as start:
                self.daemon._begin_run()
            start.assert_called_once()

    def test_the_boot_start_waits_for_a_wired_output_still_coming(self):
        self.daemon.cfg.update({"MUSIC_START_MODE": "boot", "AUDIO_OUTPUT": "usb"})
        self.daemon.mode = "idle"
        self.daemon.OUTPUT_WAIT_SEC = 5
        self.daemon._audio_output_missing = "usb"
        found = threading.Event()

        def check(force=False):
            if found.is_set():
                self.daemon._audio_output_missing = None
        with mock.patch.object(self.daemon, "_apply_audio_output", side_effect=check),                 mock.patch.object(self.daemon, "_start_music_faded") as start:
            self.daemon._start_when_output_ready(poll=0.05)
            time.sleep(0.2)
            start.assert_not_called()
            found.set()
            for _ in range(50):
                if start.called:
                    break
                time.sleep(0.05)
            start.assert_called_once()
        self.daemon._audio_output_missing = None
        with mock.patch.object(self.daemon, "_start_music_faded") as start:
            self.daemon._start_when_output_ready()
        start.assert_called_once()

    def test_a_sound_left_empty_is_silent_and_says_nothing(self):
        with mock.patch.object(rukebox_daemon.log, "warning") as warning:
            self.daemon._play_cue_sound("", "AP_CONNECT_SOUND")
            warning.assert_not_called()
            self.daemon._play_cue_sound(os.path.join(self.dir, "gone.mp3"), "AP_CONNECT_SOUND")
            warning.assert_called_once()

    def test_the_next_pass_is_drawn_as_the_last_song_starts(self):
        self.daemon.cfg["MUSIC_LOOP"] = True
        self.daemon.mode = "music"
        a, b = self.paths["rock1.mp3"], self.paths["jazz1.mp3"]
        self.daemon.state.data["play_queue"] = [a]
        with mock.patch.object(self.daemon, "_playable_tracks", return_value=[a, b]),                 mock.patch.object(self.daemon, "_play_with_intro") as play:
            self.daemon._play_next_track()
        self.assertEqual(play.call_args[0][0], a)
        queue = self.daemon.state.data["play_queue"]
        self.assertEqual(sorted(queue), sorted([a, b]), "Up next shows the next pass during the last song")
        self.assertEqual(queue[0], b, "the song playing does not open it")

    def test_a_radio_that_played_one_list_keeps_it(self):
        entry = self.manual("Soir", "rock1.mp3")
        self.daemon.state.data.pop("selection", None)
        self.daemon.state.data["active_list"] = entry["id"]
        self.assertEqual(self.daemon.state.selection(), {"play": [entry["id"]], "exclude": []})
        self.assertEqual(self.daemon._playable_tracks(), [self.paths["rock1.mp3"]])

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

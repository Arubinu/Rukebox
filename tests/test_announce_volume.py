"""The volume an announcement or a System sound plays at: one per source, and
"off" meaning it follows the music, as it always did. src/announcements.py
holds the store (in the announcements file, next to the announcements), the
daemon applies it. mpv is faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import time
import unittest

import _path  # noqa: F401
import announcements
from config_and_scan import load_config
import config_document
import rukebox_daemon


class FakeMpv:
    """Records what the daemon asked mpv to do."""
    def set_volume_gain(self, db):
        self.volume_gain = db


    def __init__(self):
        self.files = []
        self.volume = None
        self.paused = None

    def loadfile(self, path):
        self.files.append(path)

    def set_volume(self, volume):
        self.volume = volume

    def set_pause(self, paused):
        self.paused = paused

    def set_loop(self, mode="no"):
        pass

    def stop(self):
        self.files.append(None)

    def stop_playback(self):
        self.files.append(None)

    def set_audio_filter(self, chain):
        return True

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


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.path = _path.document(self.dir)
        self.folder = os.path.join(self.dir, "matin")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nothing_has_a_volume_of_its_own_to_start_with(self):
        announcements.add({"name": "Matin", "folder": self.folder,
                                      "trigger": "manual"}, self.path)
        self.assertEqual(announcements.volumes(self.path), {})
        self.assertIsNone(announcements.source_volume({}, "custom:matin"))

    def test_a_volume_round_trips(self):
        announcements.set_volume("meme", {"on": True, "volume": 20}, self.path)
        self.assertEqual(announcements.volumes(self.path)["meme"], {"on": True, "volume": 20})
        self.assertEqual(announcements.source_volume(announcements.volumes(self.path), "meme"),
                         20)

    def test_off_keeps_the_value_but_follows_the_music(self):
        announcements.set_volume("cutoff", {"on": False, "volume": 10}, self.path)
        entries = announcements.volumes(self.path)
        self.assertEqual(entries["cutoff"], {"on": False, "volume": 10})
        self.assertIsNone(announcements.source_volume(entries, "cutoff"))

    def test_a_volume_out_of_range_is_refused(self):
        for value in (-1, 101, "loud", "1e999"):
            with self.assertRaises(ValueError) as caught:
                announcements.set_volume("meme", {"on": True, "volume": value}, self.path)
            self.assertEqual(str(caught.exception), "announcement_bad_volume")
        with self.assertRaises(ValueError):
            announcements.set_volume("  ", {"on": True, "volume": 10}, self.path)

    def test_an_entry_that_makes_no_sense_is_ignored(self):
        announcements.save_all([], self.path)
        config_document.write("announcements", {
            "items": [],
            "volumes": {"meme": {"on": True, "volume": 900}, "cutoff": "yes"},
        }, self.path)
        self.assertEqual(announcements.volumes(self.path), {})

    def test_writing_a_volume_keeps_the_announcements(self):
        item = announcements.add({"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"}, self.path)
        announcements.set_volume("custom:" + item["id"], {"on": True, "volume": 42}, self.path)
        self.assertEqual([i["id"] for i in announcements.load(self.path)], [item["id"]])
        self.assertEqual(announcements.volumes(self.path)["custom:" + item["id"]]["volume"], 42)

    def test_deleting_an_announcement_drops_its_volume(self):
        item = announcements.add({"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"}, self.path)
        announcements.set_volume("custom:" + item["id"], {"on": True, "volume": 42}, self.path)
        announcements.delete(item["id"], self.path)
        self.assertEqual(announcements.volumes(self.path), {})

    def test_writers_at_once_do_not_lose_the_announcements(self):
        # Two saves at once must leave the document whole: one writer at a time.
        announcements.save_all([
            {"id": "morning", "name": "Matin", "folder": self.folder, "hour": 7,
             "minute": 0, "trigger": "time", "delay_min": 30, "repeat_times": 1,
             "enabled": True},
            {"id": "doubleclick", "name": "Double", "folder": self.folder, "hour": 12,
             "minute": 0, "trigger": "manual", "delay_min": 30, "repeat_times": 1,
             "enabled": True},
        ], self.path)
        seen = []
        errors = []

        def hammer(key):
            for value in range(40):
                # Windows refuses the rename while a reader holds the file open (no FILE_SHARE_DELETE); the Pi does not.
                for attempt in range(20):
                    try:
                        announcements.set_volume(key, {"on": True, "volume": value}, self.path)
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(0.01)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(repr(exc))
                        break
                block = config_document.read("announcements", self.path) or {}
                seen.append(len(block.get("items") or []))

        threads = [threading.Thread(target=hammer, args=("key%d" % i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(seen), {2}, "every write left both announcements in place")
        self.assertEqual([i["id"] for i in announcements.load(self.path)],
                         ["morning", "doubleclick"])

    def test_a_document_we_cannot_read_is_never_replaced(self):
        broken = 'announcements:\n  items: [ {"id": "morning"}\n  other: a second document\n'
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(broken)
        with self.assertRaises(ValueError) as caught:
            announcements.set_volume("meme", {"on": True, "volume": 20}, self.path)
        self.assertEqual(str(caught.exception), "file_unreadable")
        with self.assertRaises(ValueError):
            announcements.add({"name": "Matin", "folder": self.folder,
                                          "trigger": "manual"}, self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), broken, "the document was left alone")


class PlayTargetTest(unittest.TestCase):
    """What a play_announcement command names: the row's "Jouer" names its
    announcement by id, but the web server adds source="web" to every command
    it sends - which the daemon read as an announcement source, and answered
    "unknown source" (reported as "Cette annonce n'existe plus")."""

    def test_the_provenance_of_a_command_is_not_an_announcement_source(self):
        target = rukebox_daemon.announcement_target
        self.assertEqual(target({"id": "morning", "source": "web"}), (None, "morning", False))
        self.assertEqual(target({"id": "morning", "source": "flic"}), (None, "morning", False))
        self.assertEqual(target({"id": "", "source": "gpio"}), (None, "", False))

    def test_a_built_in_list_is_played_by_name(self):
        target = rukebox_daemon.announcement_target
        self.assertEqual(target({"source": "cutoff"}), ("cutoff", None, True))
        self.assertEqual(target({"source": "meme"}), ("meme", None, True))

    def test_a_custom_source_is_a_chooser(self):
        self.assertEqual(rukebox_daemon.announcement_target({"source": "custom:morning"}),
                         (None, "morning", True))


class DaemonVolumeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.music = os.path.join(self.dir, "music")
        self.track = self._audio(self.music, "a.mp3")
        self.folder = os.path.join(self.dir, "matin")
        self.memes = os.path.join(self.dir, "memes")
        self._audio(self.folder, "un.mp3")
        self._audio(self.memes, "clic.mp3")

        self.path = _path.document(self.dir)
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "MEME_DIR": self.memes,
            "INTERACTIVE_FADE_DURATION_SEC": 0,
            "FADE_DURATION_SEC": 0,
            "BASE_VOLUME": 30,
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _audio(self, folder, name):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(b"x" * 10)
        return path

    def announcement(self, volume=None):
        item = announcements.add({"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"}, self.path)
        if volume is not None:
            announcements.set_volume("custom:" + item["id"], {"on": True, "volume": volume}, self.path)
        self.daemon._custom_announcements = announcements.load(self.path)
        self.daemon._announce_volumes = announcements.volumes(self.path)
        return item

    def test_an_announcement_with_its_own_volume_plays_at_it(self):
        item = self.announcement(volume=20)
        self.daemon.mode = "music"
        self.daemon._trigger_custom_announcement(item, on_demand=True)
        self.assertEqual(self.daemon.mpv.volume, 20)
        self.assertEqual(self.daemon.mpv.files[-1], os.path.join(self.folder, "un.mp3"))

    def test_the_music_is_not_stuck_at_the_announcement_volume(self):
        item = self.announcement(volume=5)
        self.daemon.mode = "music"
        self.daemon._trigger_custom_announcement(item, on_demand=True)
        self.assertEqual(self.daemon.mpv.volume, 5)
        self.daemon._play_track(self.track)
        self.assertEqual(self.daemon.mpv.volume, 30)

    def test_without_a_volume_of_its_own_it_plays_at_the_music_volume(self):
        item = self.announcement()
        self.daemon.mode = "music"
        self.daemon._trigger_custom_announcement(item, on_demand=True)
        self.assertEqual(self.daemon.mpv.volume, 30, "the music's own volume, as before")

    def test_a_click_sound_plays_at_the_volume_of_its_list(self):
        announcements.set_volume("meme", {"on": True, "volume": 15}, self.path)
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.daemon.mode = "music"
        self.daemon.cfg["SINGLE_CLICK_ACTION"] = "next"
        self.daemon.cfg["SINGLE_CLICK_SOURCE"] = "meme"
        self.daemon._perform_click_action("single", "test", "next", "meme")
        self.assertEqual(self.daemon.mpv.volume, 15)

    def test_a_keepalive_sound_with_its_own_volume_is_applied(self):
        self.daemon.cfg["KEEPALIVE_SOUND"] = os.path.join(self.memes, "clic.mp3")
        announcements.set_volume("KEEPALIVE_SOUND", {"on": True, "volume": 7}, self.path)
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.daemon._start_keepalive()
        self.assertEqual(self.daemon.mpv.volume, 7)

    def test_the_volume_of_a_source_is_read_from_the_store(self):
        announcements.set_volume("cutoff", {"on": True, "volume": 8}, self.path)
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.assertEqual(self.daemon._source_volume("cutoff"), 8)
        self.assertIsNone(self.daemon._source_volume("meme"))

    def test_the_daemon_rereads_the_file_when_it_changed(self):
        item = self.announcement(volume=20)
        self.daemon._announcements()  # as the first command would
        self.assertEqual([i["id"] for i in self.daemon._custom_announcements], [item["id"]])
        time.sleep(0.02)
        announcements.set_volume("meme", {"on": True, "volume": 33}, self.path)
        self.daemon._announcements()
        self.assertEqual(self.daemon._source_volume("meme"), 33)
        time.sleep(0.02)
        announcements.delete(item["id"], self.path)
        self.assertEqual(self.daemon._announcements(), [])

    def test_a_broken_document_keeps_the_announcements_already_loaded(self):
        item = self.announcement(volume=20)
        self.daemon._announcements()
        time.sleep(0.02)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('announcements:\n  items: [ {"id": "morning"}\n  other: broken\n')
        self.assertEqual([i["id"] for i in self.daemon._announcements()], [item["id"]],
                         "a document we cannot read does not empty the list")

"""The volume an announcement or a System sound plays at: one per source, and
"off" meaning it follows the music, as it always did. src/announcements.py
holds the store (in the announcements file, next to the announcements), the
daemon applies it. mpv is faked, so this runs off-hardware."""
import json
import os
import shutil
import tempfile
import threading
import unittest

import _path  # noqa: F401
import announcements
from config_and_scan import load_config
import rukebox_daemon


class FakeMpv:
    """Records what the daemon asked mpv to do."""

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
        self.path = os.path.join(self.dir, "announcements.json")
        self.folder = os.path.join(self.dir, "matin")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nothing_has_a_volume_of_its_own_to_start_with(self):
        announcements.add(self.path, {"name": "Matin", "folder": self.folder,
                                      "trigger": "manual"})
        self.assertEqual(announcements.volumes(self.path), {})
        self.assertIsNone(announcements.source_volume({}, "custom:matin"))

    def test_a_volume_round_trips(self):
        announcements.set_volume(self.path, "meme", {"on": True, "volume": 20})
        self.assertEqual(announcements.volumes(self.path)["meme"], {"on": True, "volume": 20})
        self.assertEqual(announcements.source_volume(announcements.volumes(self.path), "meme"),
                         20)

    def test_off_keeps_the_value_but_follows_the_music(self):
        announcements.set_volume(self.path, "cutoff", {"on": False, "volume": 10})
        entries = announcements.volumes(self.path)
        self.assertEqual(entries["cutoff"], {"on": False, "volume": 10})
        self.assertIsNone(announcements.source_volume(entries, "cutoff"))

    def test_a_volume_out_of_range_is_refused(self):
        for value in (-1, 101, "loud", "1e999"):
            with self.assertRaises(ValueError) as caught:
                announcements.set_volume(self.path, "meme", {"on": True, "volume": value})
            self.assertEqual(str(caught.exception), "announcement_bad_volume")
        with self.assertRaises(ValueError):
            announcements.set_volume(self.path, "  ", {"on": True, "volume": 10})

    def test_an_entry_that_makes_no_sense_is_ignored(self):
        announcements.save_all(self.path, [])
        with open(self.path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        doc["volumes"]["meme"] = {"on": True, "volume": 900}
        doc["volumes"]["cutoff"] = "yes"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.assertEqual(announcements.volumes(self.path), {})

    def test_writing_a_volume_keeps_the_announcements(self):
        item = announcements.add(self.path, {"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"})
        announcements.set_volume(self.path, "custom:" + item["id"], {"on": True, "volume": 42})
        self.assertEqual([i["id"] for i in announcements.load(self.path)], [item["id"]])
        self.assertEqual(announcements.volumes(self.path)["custom:" + item["id"]]["volume"], 42)

    def test_deleting_an_announcement_drops_its_volume(self):
        item = announcements.add(self.path, {"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"})
        announcements.set_volume(self.path, "custom:" + item["id"], {"on": True, "volume": 42})
        announcements.delete(self.path, item["id"])
        self.assertEqual(announcements.volumes(self.path), {})


class DaemonVolumeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.music = os.path.join(self.dir, "music")
        self.track = self._audio(self.music, "a.mp3")
        self.folder = os.path.join(self.dir, "matin")
        self.memes = os.path.join(self.dir, "memes")
        self._audio(self.folder, "un.mp3")
        self._audio(self.memes, "clic.mp3")

        self.path = os.path.join(self.dir, "announcements.json")
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": self.path,
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
        item = announcements.add(self.path, {"name": "Matin", "folder": self.folder,
                                             "trigger": "manual"})
        if volume is not None:
            announcements.set_volume(self.path, "custom:" + item["id"],
                                     {"on": True, "volume": volume})
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
        announcements.set_volume(self.path, "meme", {"on": True, "volume": 15})
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.daemon.mode = "music"
        self.daemon.cfg["SINGLE_CLICK_ACTION"] = "next"
        self.daemon.cfg["SINGLE_CLICK_SOURCE"] = "meme"
        self.daemon._perform_click_action("single", "test", "next", "meme")
        self.assertEqual(self.daemon.mpv.volume, 15)

    def test_a_keepalive_sound_with_its_own_volume_is_applied(self):
        self.daemon.cfg["KEEPALIVE_SOUND"] = os.path.join(self.memes, "clic.mp3")
        announcements.set_volume(self.path, "KEEPALIVE_SOUND", {"on": True, "volume": 7})
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.daemon._start_keepalive()
        self.assertEqual(self.daemon.mpv.volume, 7)

    def test_the_volume_of_a_source_is_read_from_the_store(self):
        announcements.set_volume(self.path, "cutoff", {"on": True, "volume": 8})
        self.daemon._announce_volumes = announcements.volumes(self.path)
        self.assertEqual(self.daemon._source_volume("cutoff"), 8)
        self.assertIsNone(self.daemon._source_volume("meme"))

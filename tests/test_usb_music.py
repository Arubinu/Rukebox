"""Music on a USB key (the daemon side): the key is read from, the library
follows it, and it goes back to the Pi's own folder as soon as the key is gone.
`lsblk`, the mount helper and mpv are all faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import rukebox_daemon
import system_actions
import usb_storage

KEY = {"device": "/dev/sda1", "label": "MUSIQUE", "fstype": "exfat", "size": "29.5G",
       "uuid": "1A2B-3C4D", "mountpoint": "", "removable": True}
OTHER = dict(KEY, device="/dev/sdb1", label="AUTRE", uuid="9Z8Y-7X6W")


class FakeMpv:
    def __init__(self):
        self.files = []

    def loadfile(self, path):
        self.files.append(path)

    def set_pause(self, paused):
        pass

    def set_volume(self, volume):
        pass

    def set_loop(self, mode="no"):
        pass

    def stop_playback(self):
        self.files.append(None)

    def set_audio_filter(self, chain):
        return True

    def seek(self, seconds):
        pass

    def set_replaygain(self, mode):
        pass

    def on_event(self, callback):
        pass


class UsbMusicTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        os.makedirs(self.music)
        with open(os.path.join(self.music, "a.mp3"), "wb") as f:
            f.write(b"x" * 10)
        # Where the key would be mounted: an empty folder here, which is what
        # a real mount point looks like to is_mounted() once patched.
        self.mount = os.path.join(self.dir, "usb")
        os.makedirs(self.mount)
        patcher = mock.patch.dict(os.environ, {"RUKEBOX_USB_MOUNT": self.mount})
        patcher.start()
        self.addCleanup(patcher.stop)

        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "USB_PORT_MODE": "host",
        })
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()

    def plugged(self, devices, mounted=True):
        """What the machine reports: these keys, and a key mounted or not."""
        patchers = [
            mock.patch.object(usb_storage, "devices", return_value=list(devices)),
            mock.patch.object(usb_storage, "is_mounted", return_value=mounted),
            mock.patch.object(usb_storage, "count_music", return_value=(7, 1024)),
            mock.patch.object(system_actions, "usb_mount", return_value=(True, "")),
            mock.patch.object(system_actions, "usb_umount", return_value=(True, "")),
        ]
        for one in patchers:
            one.start()
            self.addCleanup(one.stop)

    def test_a_key_taken_reads_the_library_from_it(self):
        self.plugged([KEY])
        self.assertEqual(self.daemon._music_dir(), self.music, "the Pi's own folder first")
        answer = self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.daemon._music_dir(), self.mount)
        self.assertTrue(answer["data"]["active"])
        self.assertEqual(answer["data"]["label"], "MUSIQUE")
        self.assertEqual(answer["data"]["tracks"], 7)
        self.assertEqual(answer["data"]["internal"], self.music)
        self.assertEqual(answer["data"]["remembered"]["key"], "uuid:1A2B-3C4D")
        self.assertEqual([d["active"] for d in answer["data"]["devices"]], [True])

    def test_the_key_is_taken_again_by_itself_once_it_is_known(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon._usb_music_command({"forget": True})
        self.assertEqual(self.daemon._music_dir(), self.music, "given back")
        self.assertIsNone(self.daemon._usb_music_status()["remembered"],
                          "forgotten: it is only offered again")

        # Unplugged, then plugged again: the tick takes it without being asked.
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        with mock.patch.object(usb_storage, "devices", return_value=[]):
            self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music)
        self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.mount, "taken again on the next plug")

    def test_a_key_pulled_out_gives_the_internal_folder_back(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.assertEqual(self.daemon._music_dir(), self.mount)
        with mock.patch.object(usb_storage, "devices", return_value=[]), \
                mock.patch.object(usb_storage, "is_mounted", return_value=False), \
                mock.patch.object(system_actions, "usb_umount", return_value=(True, "")) as unmount:
            self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music, "back to the internal folder")
        self.assertFalse(self.daemon._usb_music_status()["active"])
        self.assertEqual(self.daemon._usb_music_status()["remembered"]["key"], "uuid:1A2B-3C4D",
                         "and the key is not forgotten: only put aside")

    def test_a_key_that_leaves_while_its_music_plays_moves_on(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.mount, "a.mp3")
        self.daemon._play_next_track = mock.Mock()
        with mock.patch.object(usb_storage, "devices", return_value=[]), \
                mock.patch.object(usb_storage, "is_mounted", return_value=False):
            self.daemon._check_usb_music()
        self.daemon._play_next_track.assert_called_once_with()

    def test_a_key_that_cannot_be_mounted_says_why_and_changes_nothing(self):
        self.plugged([KEY])
        with mock.patch.object(system_actions, "usb_mount",
                               return_value=(False, "unknown filesystem type 'exfat'")):
            answer = self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error"], "mount_failed")
        self.assertEqual(self.daemon._music_dir(), self.music, "the library did not move")
        self.assertIn("exfat", self.daemon._usb_music_status()["error"])

    def test_asking_for_a_key_that_is_not_there(self):
        self.plugged([KEY])
        answer = self.daemon._usb_music_command({"device": "/dev/sdz9"}, source="web")
        self.assertEqual((answer["ok"], answer["error"]), (False, "no_such_device"))
        self.assertEqual(self.daemon._music_dir(), self.music)

    def test_forgetting_the_key(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        answer = self.daemon._usb_music_command({"forget": True}, source="web")
        self.assertTrue(answer["ok"])
        self.assertIsNone(answer["data"]["remembered"])
        self.assertEqual(self.daemon._music_dir(), self.music)
        self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music, "nothing is taken again")

    def test_another_key_can_replace_the_one_in_use(self):
        self.plugged([KEY, OTHER])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        with mock.patch.object(system_actions, "usb_umount", return_value=(True, "")) as unmount:
            answer = self.daemon._usb_music_command({"key": usb_storage.key_of(OTHER)}, source="web")
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(unmount.called, "the first key is unmounted before the second is taken")
        self.assertEqual(answer["data"]["device"], "/dev/sdb1")

    def test_the_status_says_the_port_mode_and_the_devices(self):
        self.plugged([KEY, OTHER])
        self.daemon._check_usb_music()
        status = self.daemon._usb_music_status()
        self.assertEqual(status["port_mode"], "host")
        self.assertEqual([d["key"] for d in status["devices"]],
                         ["uuid:1A2B-3C4D", "uuid:9Z8Y-7X6W"])
        self.assertFalse(status["active"])
        self.assertEqual(status["internal"], self.music)


if __name__ == "__main__":
    unittest.main()

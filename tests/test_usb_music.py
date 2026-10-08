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

    def write(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * 10)
        return path

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
                mock.patch.object(system_actions, "usb_umount", return_value=(True, "")):
            self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music, "back to the internal folder")
        self.assertFalse(self.daemon._usb_music_status()["active"])
        self.assertEqual(self.daemon._usb_music_status()["remembered"]["key"], "uuid:1A2B-3C4D",
                         "and the key is not forgotten: only put aside")

    def test_giving_the_folder_back_fades_the_song_that_was_on_the_key(self):
        """Asked for as: a fade, as the fades section gives one, when the music
        comes back to the radio's own folder."""
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.mount, "Artiste", "b1.mp3")
        self.daemon.cfg["INTERACTIVE_FADE_DURATION_SEC"] = 2.5
        self.daemon._fade_out_and_pause = mock.Mock()
        self.daemon._play_next_track = mock.Mock()
        self.daemon._usb_music_command({"forget": True}, source="web")
        self.daemon._fade_out_and_pause.assert_called_once_with(2.5)
        self.daemon._play_next_track.assert_called_once_with(user=True)

    def test_a_key_that_leaves_while_its_music_plays_moves_on(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.mount, "a.mp3")
        self.daemon._play_next_track = mock.Mock()
        self.daemon._fade_out_and_pause = mock.Mock()
        with mock.patch.object(usb_storage, "devices", return_value=[]), \
                mock.patch.object(usb_storage, "is_mounted", return_value=False):
            self.daemon._check_usb_music()
        self.daemon._play_next_track.assert_called_once_with()
        self.daemon._fade_out_and_pause.assert_not_called()

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

    def test_a_device_with_no_volume_label_keeps_the_disks_name(self):
        """What the page shows instead of /dev/sdc1, and what comes back on the
        row once the device is unplugged and only remembered."""
        unlabelled = {"device": "/dev/sdc1", "label": "", "model": "Elements 25A2",
                      "fstype": "exfat", "size": "1.8T", "uuid": "", "mountpoint": "",
                      "removable": True}
        self.plugged([unlabelled])
        answer = self.daemon._usb_music_command({"device": "/dev/sdc1"}, source="web")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["model"], "Elements 25A2")
        self.assertEqual(answer["data"]["remembered"]["model"], "Elements 25A2")
        self.assertEqual(answer["data"]["devices"][0]["model"], "Elements 25A2")

    def test_the_queue_follows_the_key(self):
        """Checked because the owner asked: once a key is taken, the next songs
        are the key's, not the folder that was playing before."""
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.write(os.path.join(self.mount, "Artiste", "b2.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        queue = self.daemon.state.data.get("play_queue") or []
        self.assertEqual(len(queue), 2, queue)
        self.assertTrue(all(path.startswith(self.mount) for path in queue), queue)

    def test_the_song_playing_is_left_alone_unless_it_is_asked_for(self):
        """The switch happens at the end of the song by itself: `switch: "now"`
        is what replaces it."""
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._play_next_track = mock.Mock()
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon._play_next_track.assert_not_called()
        self.assertEqual(self.daemon._current_track, os.path.join(self.music, "a.mp3"))

    def test_switch_now_replaces_the_song_playing(self):
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._fade_out_and_pause = mock.Mock()
        self.daemon._play_next_track = mock.Mock()
        answer = self.daemon._usb_music_command({"device": "/dev/sda1", "switch": "now"},
                                                source="web")
        self.assertTrue(answer["ok"], answer)
        self.daemon._play_next_track.assert_called_once_with(user=True)

    def test_switch_now_on_the_key_already_in_use(self):
        """The button is offered while the song comes from the old folder: the
        key is the library already, so only the song changes."""
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon.mode = "music"
        self.daemon._fade_out_and_pause = mock.Mock()
        self.daemon._play_next_track = mock.Mock()
        answer = self.daemon._usb_music_command({"key": usb_storage.key_of(KEY), "switch": "now"},
                                                source="web")
        self.assertTrue(answer["ok"], answer)
        self.daemon._play_next_track.assert_called_once_with(user=True)

    def test_the_card_knows_the_song_comes_from_the_folder_before(self):
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.assertTrue(self.daemon._usb_music_status()["playing_from_other"])
        self.daemon._current_track = os.path.join(self.mount, "Artiste", "b1.mp3")
        self.assertFalse(self.daemon._usb_music_status()["playing_from_other"],
                         "a song of the key is not from the previous folder")

    def test_the_setting_decides_when_no_switch_is_asked_for(self):
        """USB_MUSIC_SWITCH is the choice made once for every take, the card's
        button being the one-off."""
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._play_next_track = mock.Mock()
        self.daemon.cfg["USB_MUSIC_SWITCH"] = "now"
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon._play_next_track.assert_called_once_with(user=True)

    def test_the_setting_is_read_when_the_daemon_takes_the_device_itself(self):
        """A remembered device taken on the next plug follows the same choice."""
        self.plugged([KEY])
        self.write(os.path.join(self.mount, "Artiste", "b1.mp3"))
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        self.daemon._release_usb_music("test")
        self.daemon.cfg["USB_MUSIC_SWITCH"] = "now"
        self.daemon.mode = "music"
        self.daemon._current_track = os.path.join(self.music, "a.mp3")
        self.daemon._play_next_track = mock.Mock()
        self.daemon._check_usb_music()
        self.daemon._play_next_track.assert_called_once_with(user=True)

    def test_a_device_plugged_in_is_only_offered_by_default(self):
        self.plugged([])
        self.daemon._check_usb_music()
        with mock.patch.object(usb_storage, "devices", return_value=[KEY]):
            self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music)

    def test_a_device_plugged_in_is_played_from_when_the_setting_says_so(self):
        self.plugged([])
        self.daemon.cfg["USB_MUSIC_ON_PLUG"] = "use"
        self.daemon._check_usb_music()
        with mock.patch.object(usb_storage, "devices", return_value=[KEY]):
            self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.mount)

    def test_only_a_device_that_just_arrived_is_taken(self):
        """Given back by hand, a device still plugged in stays given back."""
        self.plugged([KEY])
        self.daemon.cfg["USB_MUSIC_ON_PLUG"] = "use"
        self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.mount)
        self.daemon._usb_music_command({"forget": True})
        self.daemon._check_usb_music()
        self.assertEqual(self.daemon._music_dir(), self.music)

    def test_the_switch_never_takes_the_command_lock_twice(self):
        """The control socket holds `_command_lock` for every command that is
        not a read, so a non-reentrant lock taken again inside (the USB switch
        did) waits on itself for ever: the radio then answers `get_status` and
        nothing else until it is restarted. This is what a real switch did."""
        self.plugged([KEY])
        answer = []

        def command():
            with self.daemon._command_lock:
                answer.append(self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web"))

        thread = threading.Thread(target=command, daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive(), "the USB switch took the command lock twice")
        self.assertTrue(answer and answer[0]["ok"], answer)
        self.assertEqual(self.daemon._music_dir(), self.mount)

    def test_the_status_says_the_port_mode_and_the_devices(self):
        self.plugged([KEY, OTHER])
        self.daemon._check_usb_music()
        status = self.daemon._usb_music_status()
        self.assertEqual(status["port_mode"], "host")
        self.assertEqual([d["key"] for d in status["devices"]],
                         ["uuid:1A2B-3C4D", "uuid:9Z8Y-7X6W"])
        self.assertFalse(status["active"])
        self.assertEqual(status["internal"], self.music)

    def test_the_status_says_how_full_the_storage_that_is_playing_is(self):
        self.plugged([KEY])
        self.daemon._usb_music_command({"device": "/dev/sda1"}, source="web")
        full = {"total": 1000, "used": 620, "free": 380, "percent": 62}
        with mock.patch.object(usb_storage, "space", return_value=full) as space:
            status = self.daemon._usb_music_status()
        self.assertEqual(status["space"], full)
        space.assert_called_with(self.mount)

    def test_the_space_of_the_folder_on_the_pi_is_reported_too(self):
        self.plugged([])
        with mock.patch.object(usb_storage, "space", return_value=None) as space:
            status = self.daemon._usb_music_status()
        self.assertIsNone(status["space"])
        space.assert_called_with(self.music)


if __name__ == "__main__":
    unittest.main()

"""Music on a USB key (src/usb_storage.py): which plugged devices are keys,
which one is mounted, and what it holds. The listing is exercised against the
real `lsblk` tree of the owner's Pi (read over SSH) with a key added to it."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import usb_storage

# The Pi's own tree, taken from `lsblk -J` on the radio, plus a key.
SD_CARD = {
    "name": "mmcblk0", "path": "/dev/mmcblk0", "label": None, "fstype": None,
    "size": "29.7G", "rm": False, "type": "disk", "uuid": None, "tran": "mmc",
    "mountpoint": None,
    "children": [
        {"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "label": "bootfs",
         "fstype": "vfat", "size": "512M", "rm": False, "type": "part",
         "uuid": "D8AB-6612", "mountpoint": "/boot/firmware"},
        {"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "label": "rootfs",
         "fstype": "ext4", "size": "29.2G", "rm": False, "type": "part",
         "uuid": "a7857f95-5305-411f-917d-93d06ca53dea", "mountpoint": "/"},
    ],
}

SWAP = {"name": "zram0", "path": "/dev/zram0", "label": "zram0", "fstype": "swap",
        "size": "463M", "rm": False, "type": "disk", "uuid": "a02dc010",
        "mountpoint": "[SWAP]"}

KEY = {
    "name": "sda", "path": "/dev/sda", "label": None, "fstype": None,
    "size": "29.5G", "rm": True, "type": "disk", "uuid": None, "tran": "usb",
    "mountpoint": None,
    "children": [
        {"name": "sda1", "path": "/dev/sda1", "label": "MUSIQUE", "fstype": "exfat",
         "size": "29.5G", "rm": True, "type": "part", "uuid": "1A2B-3C4D",
         "mountpoint": None},
    ],
}


class DevicesTest(unittest.TestCase):
    def test_only_a_key_is_offered(self):
        found = usb_storage.devices([SD_CARD, SWAP, KEY])
        self.assertEqual([d["device"] for d in found], ["/dev/sda1"])
        self.assertEqual(found[0]["label"], "MUSIQUE")
        self.assertEqual(found[0]["fstype"], "exfat")
        self.assertEqual(found[0]["size"], "29.5G")
        self.assertTrue(found[0]["removable"])

    def test_the_sd_card_is_never_one(self):
        self.assertEqual(usb_storage.devices([SD_CARD]), [])

    def test_an_ssd_in_a_usb_caddy_counts_too(self):
        """It reports `rm` false but sits on the USB bus - the owner's own
        enclosure behaved that way."""
        caddy = dict(KEY, rm=False, children=[dict(KEY["children"][0], rm=False)])
        self.assertEqual([d["device"] for d in usb_storage.devices([caddy])], ["/dev/sda1"])

    def test_a_key_with_no_partition_table(self):
        flat = {"name": "sdb", "path": "/dev/sdb", "label": "CLES", "fstype": "vfat",
                "size": "7.4G", "rm": True, "type": "disk", "uuid": "1234-ABCD",
                "tran": "usb", "mountpoint": None}
        self.assertEqual([d["device"] for d in usb_storage.devices([flat])], ["/dev/sdb"])

    def test_a_partition_with_nothing_on_it(self):
        blank = dict(KEY, children=[dict(KEY["children"][0], fstype=None, label=None)])
        self.assertEqual(usb_storage.devices([blank]), [])
        swap = dict(KEY, children=[dict(KEY["children"][0], fstype="swap")])
        self.assertEqual(usb_storage.devices([swap]), [])

    def test_the_key_is_recognised_from_one_plug_to_the_next(self):
        entry = usb_storage.devices([KEY])[0]
        self.assertEqual(usb_storage.key_of(entry), "uuid:1A2B-3C4D")
        self.assertEqual(usb_storage.find(usb_storage.devices([KEY]),
                                          "uuid:1A2B-3C4D")["device"], "/dev/sda1")
        self.assertIsNone(usb_storage.find(usb_storage.devices([KEY]), "uuid:OTHER"))
        self.assertIsNone(usb_storage.find(usb_storage.devices([KEY]), None))
        self.assertEqual(usb_storage.key_of({"label": "MUSIQUE"}), "label:MUSIQUE")
        self.assertEqual(usb_storage.key_of({"device": "/dev/sdc1"}), "path:/dev/sdc1")

    def test_a_machine_without_lsblk_offers_nothing(self):
        with mock.patch.object(usb_storage, "_lsblk", return_value={}):
            self.assertEqual(usb_storage.devices(), [])
        with mock.patch("subprocess.run", side_effect=OSError("no lsblk")):
            self.assertEqual(usb_storage._lsblk(), {})

    def test_a_real_lsblk_json_blob_is_read(self):
        payload = json.dumps({"blockdevices": [SD_CARD, SWAP, KEY]}).encode()
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=payload)):
            self.assertEqual([d["device"] for d in usb_storage.devices()], ["/dev/sda1"])


class MountPointTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_the_mount_point_is_read_at_every_call(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_USB_MOUNT": self.dir}):
            self.assertEqual(usb_storage.mount_point(), self.dir)

    def test_a_plain_folder_is_not_mounted(self):
        self.assertFalse(usb_storage.is_mounted(self.dir))
        self.assertFalse(usb_storage.is_mounted(os.path.join(self.dir, "nothing")))
        self.assertFalse(usb_storage.is_mounted(""),
                         "an empty path is not the root of the machine")


class CountTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, *parts, size=10):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def test_it_counts_the_audio_files_and_says_nothing_about_the_rest(self):
        self.write(self.dir, "Artiste", "Album", "01 - A.opus", size=100)
        self.write(self.dir, "Artiste", "Album", "02 - B.mp3", size=50)
        self.write(self.dir, "Artiste", "Album", "cover.jpg", size=999)
        self.write(self.dir, "Artiste", "Album", "notes.txt", size=999)
        self.write(self.dir, ".Trashes", "old.mp3", size=999)
        self.assertEqual(usb_storage.count_music(self.dir), (2, 150))

    def test_a_folder_that_is_not_there_or_not_a_folder(self):
        self.assertEqual(usb_storage.count_music(os.path.join(self.dir, "nope")), (0, 0))
        self.assertEqual(usb_storage.count_music(""), (0, 0))
        self.assertEqual(usb_storage.count_music(None), (0, 0))


class SpaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_it_reports_the_used_free_and_total_of_the_file_system(self):
        usage = shutil.disk_usage("/")
        with mock.patch.object(usb_storage.shutil, "disk_usage", return_value=usage):
            space = usb_storage.space(self.dir)
        self.assertEqual(space["total"], usage.total)
        self.assertEqual(space["free"], usage.free)
        self.assertEqual(space["used"], usage.used)
        self.assertEqual(space["percent"], round(usage.used * 100.0 / usage.total))

    def test_something_that_cannot_be_measured_says_nothing(self):
        self.assertIsNone(usb_storage.space(os.path.join(self.dir, "nope")))
        self.assertIsNone(usb_storage.space(""))
        self.assertIsNone(usb_storage.space(None))
        with mock.patch.object(usb_storage.shutil, "disk_usage", side_effect=OSError("gone")):
            self.assertIsNone(usb_storage.space(self.dir))
        empty = mock.Mock(total=0, used=0, free=0)
        with mock.patch.object(usb_storage.shutil, "disk_usage", return_value=empty):
            self.assertIsNone(usb_storage.space(self.dir), "a size of zero says nothing")


if __name__ == "__main__":
    unittest.main()

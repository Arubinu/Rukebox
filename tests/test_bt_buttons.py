"""Bluetooth remotes used as a button: which input devices are theirs, and how
their keys become single, double and long presses (src/bt_buttons.py)."""
import struct
import unittest

import _path  # noqa: F401
import bt_buttons
from test_buttons import Time

DEVICES = """I: Bus=0005 Vendor=248a Product=8266 Version=0001
N: Name="AB Shutter3"
P: Phys=b8:27:eb:d7:fc:3a
U: Uniq=2a:07:98:10:34:ff
H: Handlers=kbd event4

I: Bus=0005 Vendor=248a Product=8266 Version=0001
N: Name="AB Shutter3 Consumer Control"
P: Phys=b8:27:eb:d7:fc:3a
U: Uniq=2a:07:98:10:34:ff
H: Handlers=kbd event5

I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="soundcore Select 4 Go (AVRCP)"
P: Phys=b8:27:eb:d7:fc:3a
U: Uniq=7c:e9:13:69:66:55
H: Handlers=kbd event2

I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="Logitech USB Keyboard"
U: Uniq=
H: Handlers=sysrq kbd event0
"""
SHUTTER = "2A:07:98:10:34:FF"


class DevicesTest(unittest.TestCase):
    def test_the_remotes_named_in_the_settings_only(self):
        cfg = {"BT_BUTTONS": "2a:07:98:10:34:ff, nonsense ,2A:07:98:10:34:FF;7C:E9:13:69:66:55"}
        self.assertEqual(bt_buttons.remotes(cfg), [SHUTTER, "7C:E9:13:69:66:55"])

    def test_every_input_device_of_a_remote_is_read(self):
        found = bt_buttons.find_devices([SHUTTER], DEVICES)
        self.assertEqual(found, {"/dev/input/event4": SHUTTER, "/dev/input/event5": SHUTTER})

    def test_a_speakers_own_keys_are_left_to_speaker_buttons(self):
        self.assertEqual(bt_buttons.find_devices(["7C:E9:13:69:66:55"], DEVICES), {})

    def test_only_key_events_are_kept(self):
        raw = (struct.pack(bt_buttons.EVENT_FORMAT, 0, 0, 1, 115, 1)
               + struct.pack(bt_buttons.EVENT_FORMAT, 0, 0, 0, 0, 0)
               + struct.pack(bt_buttons.EVENT_FORMAT, 0, 0, 1, 115, 0))
        self.assertEqual(list(bt_buttons.key_events(raw)), [(115, 1), (115, 0)])


class RemoteTest(unittest.TestCase):
    def setUp(self):
        self.time = Time()
        self.sent = []
        self.remote = bt_buttons.Remote(SHUTTER, 0.4, 1.0, self.sent.append,
                                        clock=self.time.clock, timer=self.time.timer)

    def wait(self, seconds):
        end = self.time.now + seconds
        while self.time.now < end:
            self.time.passes(min(0.01, end - self.time.now))
            self.remote.tick()

    def key(self, code, value, path="/dev/input/event4"):
        self.remote.key(path, code, value)

    def test_one_press_is_a_single_click(self):
        self.key(115, 1)
        self.wait(0.05)
        self.key(115, 0)
        self.wait(1.0)
        self.assertEqual(self.sent, ["single_click"])

    def test_two_presses_are_a_double_click(self):
        for _ in range(2):
            self.key(115, 1)
            self.wait(0.05)
            self.key(115, 0)
            self.wait(0.15)
        self.wait(1.0)
        self.assertEqual(self.sent, ["double_click"])

    def test_a_held_key_is_a_long_press(self):
        self.key(115, 1)
        for _ in range(12):
            self.key(115, 2)
            self.wait(0.1)
        self.key(115, 0)
        self.wait(1.0)
        self.assertEqual(self.sent, ["long_press"])

    def test_two_keys_sent_for_one_press_stay_one_press(self):
        """A remote that releases one key and presses another at once."""
        self.key(115, 1)
        self.key(115, 0)
        self.wait(0.01)
        self.key(28, 1, "/dev/input/event5")
        self.key(28, 0, "/dev/input/event5")
        self.wait(1.0)
        self.assertEqual(self.sent, ["single_click"])


if __name__ == "__main__":
    unittest.main()

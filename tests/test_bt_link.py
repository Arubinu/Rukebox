"""Which controller carries the speaker (src/bt_link.py). The reported bug:
the speaker reconnected on the built-in controller while the settings named
the USB dongle, and everything read "disconnected" while the music played."""
import unittest

import _path  # noqa: F401
import bt_link

LIST = ("Controller 00:A7:50:72:14:C4 rukebox #2 [default]\n"
        "Controller B8:27:EB:62:82:CB rukebox\n")

CONNECTED = ("Device 7C:E9:13:69:66:55 (public)\n"
             "\tName: soundcore Select 4 Go\n"
             "\tAlias: soundcore Select 4 Go\n"
             "\tPaired: yes\n"
             "\tTrusted: yes\n"
             "\tConnected: yes\n")

PAIRED_OFF = CONNECTED.replace("Connected: yes", "Connected: no")
ABSENT = ("Device 7C:E9:13:69:66:55 not available\n"
          "DeviceSet 7C:E9:13:69:66:55 not available\n")

DONGLE = "00:A7:50:72:14:C4"
BUILTIN = "B8:27:EB:62:82:CB"
SPEAKER = "7C:E9:13:69:66:55"


class Fake:
    """Stands in for bluetoothctl: one answer per controller."""

    def __init__(self, answers, connect=None):
        self.answers = answers      # controller -> output (None: no answer)
        self.connect = connect
        self.scripts = []

    def __call__(self, script, timeout=8):
        self.scripts.append(script)
        if script.startswith("list"):
            return LIST
        selected = None
        for line in script.splitlines():
            if line.startswith("select "):
                selected = line.split(" ", 1)[1].strip().upper()
            elif line.startswith("connect "):
                return self.connect
        return self.answers.get(selected)

    def asked(self):
        return [script for script in self.scripts if "info" in script]


class PatchTest(unittest.TestCase):
    """Every test here replaces the one function that runs bluetoothctl."""

    def patch(self, fake):
        self.real = bt_link.bluetoothctl
        bt_link.bluetoothctl = fake
        self.addCleanup(lambda: setattr(bt_link, "bluetoothctl", self.real))
        return fake


class ParseTest(unittest.TestCase):
    def test_controllers(self):
        self.assertEqual(bt_link.parse_controllers(LIST), [
            {"address": DONGLE, "name": "rukebox #2", "default": True},
            {"address": BUILTIN, "name": "rukebox", "default": False},
        ])
        self.assertEqual(bt_link.parse_controllers(""), [])
        self.assertEqual(bt_link.default_controller(bt_link.parse_controllers(LIST)), DONGLE)

    def test_info(self):
        self.assertEqual(bt_link.parse_info(CONNECTED),
                         {"known": True, "connected": True, "paired": True,
                          "name": "soundcore Select 4 Go"})
        self.assertTrue(bt_link.parse_info(PAIRED_OFF)["known"])
        self.assertFalse(bt_link.parse_info(PAIRED_OFF)["connected"])
        self.assertFalse(bt_link.parse_info(ABSENT)["known"])
        self.assertFalse(bt_link.parse_info(None)["known"])
        self.assertFalse(bt_link.parse_info("")["known"])


class LocateTest(PatchTest):
    def test_connected_on_the_expected_controller_costs_one_call(self):
        fake = self.patch(Fake({DONGLE: CONNECTED, BUILTIN: ABSENT}))
        state = bt_link.locate(SPEAKER, DONGLE)
        self.assertEqual((state["connected"], state["controller"], state["name"]),
                         (True, DONGLE, "soundcore Select 4 Go"))
        self.assertTrue(state["known_here"])
        self.assertFalse(state["unknown"])
        self.assertEqual(len(fake.asked()), 1,
                         "the other controller is not asked when the first one says yes")

    def test_connected_elsewhere_is_still_connected(self):
        # The reported case: paired and connected on the built-in, unknown to
        # the dongle the settings name.
        self.patch(Fake({DONGLE: ABSENT, BUILTIN: CONNECTED}))
        state = bt_link.locate(SPEAKER, DONGLE)
        self.assertEqual((state["connected"], state["controller"], state["expected"]),
                         (True, BUILTIN, DONGLE))
        self.assertFalse(state["paired_here"], "the dongle does not know it")
        self.assertEqual(state["name"], "soundcore Select 4 Go")

    def test_the_default_controller_is_the_one_bluez_names(self):
        self.patch(Fake({DONGLE: ABSENT, BUILTIN: CONNECTED}))
        state = bt_link.locate(SPEAKER)
        self.assertEqual(state["expected"], DONGLE)
        self.assertEqual(state["controller"], BUILTIN)

    def test_not_connected_anywhere(self):
        self.patch(Fake({DONGLE: PAIRED_OFF, BUILTIN: ABSENT}))
        state = bt_link.locate(SPEAKER, DONGLE)
        self.assertEqual((state["connected"], state["controller"], state["unknown"]),
                         (False, None, False))
        self.assertTrue(state["known_here"])
        self.assertTrue(state["paired_here"], "paired but off is not 'unknown'")

    def test_no_answer_is_unknown_not_disconnected(self):
        self.patch(Fake({DONGLE: None, BUILTIN: None}))
        state = bt_link.locate(SPEAKER, DONGLE)
        self.assertTrue(state["unknown"])
        self.assertFalse(state["connected"])

    def test_a_device_that_is_not_a_mac_is_never_asked(self):
        fake = self.patch(Fake({}))
        self.assertFalse(bt_link.locate("", DONGLE)["connected"])
        self.assertFalse(bt_link.locate(bt_link.PLACEHOLDER, DONGLE)["connected"])
        self.assertEqual(fake.scripts, [], "nothing is asked about a placeholder")


class ConnectTest(PatchTest):
    def test_moving_the_speaker_asks_that_controller(self):
        fake = self.patch(Fake({}, connect="[CHG] Device 7C:E9:13:69:66:55 Connected: yes\n"))
        self.assertTrue(bt_link.connect_here(SPEAKER, DONGLE))
        self.assertEqual(fake.scripts[-1], "select %s\nconnect %s\n" % (DONGLE, SPEAKER))

    def test_a_refusal_is_reported(self):
        for answer in ("Failed to connect: org.bluez.Error.Failed\n", None):
            self.patch(Fake({}, connect=answer))
            self.assertFalse(bt_link.connect_here(SPEAKER, DONGLE))
        self.patch(Fake({}))
        self.assertFalse(bt_link.connect_here(SPEAKER, ""))
        self.assertFalse(bt_link.connect_here("not-a-mac", DONGLE))


if __name__ == "__main__":
    unittest.main()

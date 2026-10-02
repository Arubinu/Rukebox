"""The three button bridges and the socket they talk through: a GPIO button's
presses sorted into single, double and long (src/gpio_click.py), the speaker's
own keys found and read (src/speaker_buttons.py), the Flic button's events
(src/flic_click.py), and the control socket's client (src/control_client.py)."""
import importlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import _path  # noqa: F401
import control_client
import gpio_click
import speaker_buttons

DEBOUNCE = 0.03
WINDOW = 0.4
LONG = 1.5


class Time:
    """The clock and the timers of a ButtonWatcher, moved by hand: a press of
    1.5 s lasts no time at all, and never a little more on a busy machine."""

    def __init__(self):
        self.now = 1000.0
        self.timers = []

    def clock(self):
        return self.now

    def timer(self, delay, callback):
        entry = types.SimpleNamespace(due=self.now + delay, callback=callback, live=False)
        entry.start = lambda: setattr(entry, "live", True)
        entry.cancel = lambda: setattr(entry, "live", False)
        self.timers.append(entry)
        return entry

    def passes(self, seconds):
        end = self.now + seconds
        for entry in sorted(self.timers, key=lambda one: one.due):
            if entry.live and entry.due <= end:
                self.now = entry.due
                entry.live = False
                entry.callback()
        self.now = end


class GpioButtonTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        patcher = mock.patch.object(gpio_click, "send_command", side_effect=self.sent.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.time = Time()
        self.button = gpio_click.ButtonWatcher(DEBOUNCE, WINDOW, LONG,
                                               timer=self.time.timer, clock=self.time.clock)

    def press(self, held=0.1):
        self.button.on_change(True)
        self.time.passes(held)

    def release(self, wait=0.1):
        self.button.on_change(False)
        self.time.passes(wait)

    def test_one_press_is_a_single_click_once_the_window_has_passed(self):
        self.press()
        self.release()
        self.assertEqual(self.sent, [], "a second press may still come")
        self.time.passes(WINDOW)
        self.assertEqual(self.sent, ["single_click"])

    def test_two_presses_are_one_double_click(self):
        self.press()
        self.release()
        self.press()
        self.release(WINDOW * 3)
        self.assertEqual(self.sent, ["double_click"])

    def test_a_second_press_after_the_window_is_another_single_click(self):
        self.press()
        self.release(WINDOW + 0.1)
        self.press()
        self.release(WINDOW + 0.1)
        self.assertEqual(self.sent, ["single_click", "single_click"])

    def test_a_held_press_is_a_long_press_and_its_release_is_nothing(self):
        self.press(LONG - 0.01)
        self.assertEqual(self.sent, [], "not yet")
        self.time.passes(0.02)
        self.assertEqual(self.sent, ["long_press"])
        self.release(WINDOW * 3)
        self.assertEqual(self.sent, ["long_press"])

    def test_a_click_then_a_held_press_is_only_the_long_press(self):
        # The timer of the first click must not fire in the middle of the hold.
        self.press()
        self.release()
        self.press(LONG + WINDOW)
        self.release(WINDOW * 3)
        self.assertEqual(self.sent, ["long_press"])

    def test_a_bounce_is_not_a_second_press(self):
        self.button.on_change(True)
        self.time.passes(DEBOUNCE / 3)
        self.button.on_change(False)   # inside the debounce: the contact bouncing
        self.button.on_change(True)    # the state already kept: nothing
        self.time.passes(0.1)
        self.release(WINDOW * 3)
        self.assertEqual(self.sent, ["single_click"])

    def test_by_default_it_runs_on_the_real_clock(self):
        button = gpio_click.ButtonWatcher(DEBOUNCE, WINDOW, LONG)
        self.assertIs(button._timer, threading.Timer)


DEVICES = """\
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event0

I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="OpenFit Air by Shokz  (AVRCP)"
H: Handlers=kbd event2

I: Bus=0003 Vendor=0000 Product=0000 Version=0000
N: Name="A sensor with no event node"
H: Handlers=js0
"""
BY_ADDRESS = DEVICES + """
I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="7C:E9:13:69:66:55"
H: Handlers=kbd event3
"""
TWO_HEADSETS = DEVICES + """
I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="Another headset (AVRCP)"
H: Handlers=kbd event5
"""


def key(code, value=1, kind=speaker_buttons.EV_KEY):
    return struct.pack(speaker_buttons.EVENT_FORMAT, 0, 0, kind, code, value)


class SpeakerButtonsTest(unittest.TestCase):
    def test_the_device_named_after_the_address_comes_first(self):
        self.assertEqual(speaker_buttons.find_event_device("7c:e9:13:69:66:55", BY_ADDRESS),
                         "/dev/input/event3")

    def test_a_lone_avrcp_device_is_the_speaker(self):
        self.assertEqual(speaker_buttons.find_event_device("AA:BB:CC:DD:EE:FF", DEVICES),
                         "/dev/input/event2")

    def test_between_two_it_is_never_a_guess(self):
        self.assertIsNone(speaker_buttons.find_event_device("AA:BB:CC:DD:EE:FF", TWO_HEADSETS))
        self.assertEqual(
            speaker_buttons.find_event_device("AA:BB:CC:DD:EE:FF", TWO_HEADSETS, alias="OpenFit Air by Shokz"),
            "/dev/input/event2", "the name BlueZ gives it, whatever its spaces")

    def test_no_input_device_at_all(self):
        self.assertIsNone(speaker_buttons.find_event_device("AA:BB:CC:DD:EE:FF", ""))
        self.assertEqual(speaker_buttons.parse_input_devices(""), [])

    def test_only_a_key_going_down_is_a_gesture(self):
        self.assertEqual(speaker_buttons.gesture_of(key(163)), "next")
        self.assertEqual(speaker_buttons.gesture_of(key(165)), "previous")
        for code in (164, 166, 200, 201, 207):
            self.assertEqual(speaker_buttons.gesture_of(key(code)), "playpause", code)
        self.assertIsNone(speaker_buttons.gesture_of(key(163, value=0)), "a release")
        self.assertIsNone(speaker_buttons.gesture_of(key(163, value=2)), "a repeat")
        self.assertIsNone(speaker_buttons.gesture_of(key(163, kind=4)), "not a key")
        self.assertIsNone(speaker_buttons.gesture_of(key(30)), "a key that means nothing here")
        self.assertIsNone(speaker_buttons.gesture_of(b"\x00" * 4), "half an event")

    def test_the_speakers_name_is_its_alias_then_its_name(self):
        def answers(text):
            return mock.patch.object(speaker_buttons.subprocess, "run",
                                     return_value=types.SimpleNamespace(stdout=text))
        with answers("Device 7C:E9\n\tName: soundcore\n\tAlias: Salon\n"):
            self.assertEqual(speaker_buttons.speaker_alias("7C:E9"), "Salon")
        with answers("Device 7C:E9\n\tName: soundcore\n\tAlias:\n"):
            self.assertEqual(speaker_buttons.speaker_alias("7C:E9"), "soundcore")
        with mock.patch.object(speaker_buttons.subprocess, "run", side_effect=OSError("no bluetoothctl")):
            self.assertIsNone(speaker_buttons.speaker_alias("7C:E9"))
        self.assertIsNone(speaker_buttons.speaker_alias(""))

    def test_a_gesture_is_sent_as_the_speakers(self):
        with mock.patch.object(speaker_buttons, "send_control_command", return_value={"ok": True}) as sent:
            speaker_buttons.send("/tmp/sock", "next")
        sent.assert_called_once_with("/tmp/sock", "speaker_button", gesture="next", source="speaker")


class FakeFlicClient:
    def __init__(self, host):
        self.host = host
        self.channels = []
        self.handled = False

    def get_info(self, callback):
        callback({"bd_addr_of_verified_buttons": ["80:e4:da:00:00:01", "80:e4:da:00:00:02"]})

    def add_connection_channel(self, channel):
        self.channels.append(channel)

    def handle_events(self):
        self.handled = True


def load_flic_bridge():
    """The bridge runs at import, against a Flic library that is only on a Pi."""
    fake = types.ModuleType("fliclib")
    fake.ClickType = types.SimpleNamespace(ButtonSingleClick="single", ButtonDoubleClick="double",
                                           ButtonHold="hold", ButtonClick="click")
    fake.ButtonConnectionChannel = lambda address: types.SimpleNamespace(bd_addr=address)
    fake.FlicClient = FakeFlicClient
    with mock.patch.dict(sys.modules, {"fliclib": fake}):
        sys.modules.pop("flic_click", None)
        try:
            return importlib.import_module("flic_click")
        finally:
            sys.modules.pop("flic_click", None)


class FlicBridgeTest(unittest.TestCase):
    def setUp(self):
        self.bridge = load_flic_bridge()

    def test_every_verified_button_is_listened_to(self):
        client = self.bridge.client
        self.assertEqual([c.bd_addr for c in client.channels], ["80:e4:da:00:00:01", "80:e4:da:00:00:02"])
        self.assertTrue(all(c.on_button_single_or_double_click_or_hold is self.bridge.on_button_event
                            for c in client.channels))
        self.assertIs(client.on_new_verified_button, self.bridge.got_button,
                      "a button paired later is picked up without a restart")
        self.assertTrue(client.handled)

    def test_each_gesture_is_its_command_and_says_it_comes_from_the_flic(self):
        with mock.patch.object(self.bridge, "send_control_command", return_value={"ok": True}) as sent:
            for click in ("single", "double", "hold"):
                self.bridge.on_button_event(None, click, False, 0)
        self.assertEqual([call.args[1] for call in sent.call_args_list],
                         ["single_click", "double_click", "long_press"])
        self.assertTrue(all(call.kwargs == {"source": "flic"} for call in sent.call_args_list))

    def test_a_press_kept_while_the_button_was_away_is_dropped(self):
        with mock.patch.object(self.bridge, "send_control_command", return_value={"ok": True}) as sent:
            self.bridge.on_button_event(None, "single", True, 12)
            self.bridge.on_button_event(None, "click", False, 0)
        sent.assert_not_called()


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets only (Linux, the Pi)")
class ControlClientTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "control.sock")
        self.received = []

    def serve(self, reply):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.path)
        server.listen(1)
        self.addCleanup(server.close)

        def answer():
            conn, _ = server.accept()
            with conn:
                self.received.append(json.loads(conn.recv(65536).decode("utf-8")))
                if reply:
                    conn.sendall(reply)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)

    def test_the_command_goes_out_with_its_arguments_and_the_answer_comes_back(self):
        self.serve(b'{"ok": true, "data": {"volume": 42}}')
        answer = control_client.send_control_command(self.path, "set_volume", value=42, source="web")
        self.assertEqual(answer, {"ok": True, "data": {"volume": 42}})
        self.assertEqual(self.received, [{"cmd": "set_volume", "value": 42, "source": "web"}])

    def test_no_daemon_is_an_answer_not_an_exception(self):
        answer = control_client.send_control_command(self.path, "get_status", timeout=1)
        self.assertFalse(answer["ok"])
        self.assertIn("error", answer)

    def test_a_daemon_that_says_nothing_or_nonsense(self):
        self.serve(b"")
        self.assertFalse(control_client.send_control_command(self.path, "get_status")["ok"])
        os.remove(self.path)
        self.serve(b"not json")
        self.assertFalse(control_client.send_control_command(self.path, "get_status")["ok"])


if __name__ == "__main__":
    unittest.main()

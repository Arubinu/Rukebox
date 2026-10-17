"""The rotary encoder (src/rotary.py, scripts/rotary.sh): the kernel's devices found by name,
turns batched and sent as steps, the push read as a button, what a turn does in the daemon,
and the overlay lines written into config.txt only while it is on."""
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import _path
import rotary
from test_empty_library import DaemonCase

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


def event(kind, code, value):
    return struct.pack(rotary.EVENT_FORMAT, 0, 0, kind, code, value)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class ListenerTest(unittest.TestCase):
    def test_the_devices_are_found_by_the_names_the_kernel_gives(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for name, label in (("event0", "vc4-hdmi"), ("event1", "rotary@11"), ("event2", "button@16")):
            os.makedirs(os.path.join(root, name, "device"))
            with open(os.path.join(root, name, "device", "name"), "w") as f:
                f.write(label + "\n")
        self.assertEqual(rotary.find_devices(root), ("/dev/input/event1", "/dev/input/event2"))
        self.assertEqual(rotary.find_devices(os.path.join(root, "none")), (None, None))

    def test_turns_are_batched_and_the_push_is_a_button(self):
        sent, pressed, clock = [], [], Clock()
        watcher = mock.Mock(on_change=lambda grounded: pressed.append(grounded))
        knob = rotary.Rotary(lambda cmd, **fields: sent.append((cmd, fields)), watcher, clock=clock)
        data = event(rotary.EV_REL, rotary.REL_X, 1) * 3 + event(rotary.EV_REL, rotary.REL_X, -1)
        knob.on_events(rotary.parse(data + b"\x00" * 3))
        self.assertFalse(knob.flush(), "the batch is still open")
        clock.now += rotary.BATCH_SEC
        self.assertTrue(knob.flush())
        self.assertEqual(sent, [("rotary_turn", {"steps": 2})])
        knob.on_events(rotary.parse(event(rotary.EV_KEY, rotary.PUSH_KEYCODE, 1)
                                    + event(rotary.EV_KEY, rotary.PUSH_KEYCODE, 0)
                                    + event(rotary.EV_KEY, 28, 1)))
        self.assertEqual(pressed, [True, False], "only the push's own key")


class TurnTest(DaemonCase):
    def test_a_turn_follows_the_setting_of_the_moment(self):
        self.daemon._shown_volume = 50.0
        with mock.patch.object(self.daemon, "_set_user_volume") as volume:
            self.assertEqual(self.daemon._rotary_turn(3, "rotary"), "volume")
        volume.assert_called_once_with(56.0, "rotary")
        self.daemon.cfg["ROTARY_TURN_ACTION"] = "track"
        with mock.patch.object(self.daemon, "_perform_direct_action", return_value="next") as act:
            self.daemon._rotary_turn(1, "rotary")
            self.assertEqual(self.daemon._rotary_turn(1, "rotary"), "ignored", "one song per notch, not a burst")
            self.daemon._rotary_track_at -= self.daemon.ROTARY_TRACK_GAP_SEC
            self.daemon._rotary_turn(-1, "rotary")
        self.assertEqual([c.args[0] for c in act.call_args_list], ["next", "previous"])
        self.daemon.cfg["ROTARY_TURN_ACTION"] = "off"
        self.assertEqual(self.daemon._rotary_turn(1, "rotary"), "ignored")


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "the helper is a Linux script")
class OverlayScriptTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.config = os.path.join(self.dir, "config.txt")
        with open(self.config, "w") as f:
            f.write("[all]\ndtparam=audio=on\n")

    def run_with(self, **values):
        env_file = os.path.join(self.dir, "rukebox.env")
        with open(env_file, "w") as f:
            for key, value in values.items():
                f.write("BUTTONS_ROTARY_%s='%s'\n" % (key, value))
        script = _path.repo_file("scripts", "rotary.sh")
        env = dict(os.environ, RUKEBOX_ENV_FILE=env_file, RUKEBOX_CONFIG_TXT=self.config,
                   PATH="/usr/bin:/bin")
        subprocess.run(["bash", script], env=env, capture_output=True, timeout=20, check=True)
        with open(self.config) as f:
            return f.read()

    def test_the_overlays_are_written_once_changed_and_taken_away(self):
        text = self.run_with(ENABLED="true", PIN_A="17", PIN_B="27", PIN_PUSH="22")
        self.assertIn("dtoverlay=rotary-encoder,pin_a=17,pin_b=27,relative_axis=1", text)
        self.assertIn("dtoverlay=gpio-key,gpio=22,", text)
        self.assertIn("keycode=%d" % rotary.PUSH_KEYCODE, text, "the code the listener reads")
        self.assertEqual(self.run_with(ENABLED="true", PIN_A="17", PIN_B="27", PIN_PUSH="22"), text,
                         "nothing written twice")
        text = self.run_with(ENABLED="true", PIN_A="5", PIN_B="6", PIN_PUSH="0")
        self.assertIn("pin_a=5,pin_b=6", text)
        self.assertNotIn("gpio-key", text, "no push wired")
        self.assertEqual(text.count("rotary encoder (begin)"), 1)
        text = self.run_with(ENABLED="false", PIN_A="5", PIN_B="6", PIN_PUSH="0")
        self.assertEqual(text, "[all]\ndtparam=audio=on\n", "the file as it was")
        text = self.run_with(ENABLED="true", PIN_A="17", PIN_B="17", PIN_PUSH="0")
        self.assertNotIn("rotary", text, "two lines on one pin is no encoder")


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class SaveTest(unittest.TestCase):
    def test_a_pin_changed_rewrites_the_overlay_and_asks_for_a_restart(self):
        was = {key: ws.cfg().get(key) for key in ("ROTARY_PIN_A", "ROTARY_VOLUME_STEP")}
        self.addCleanup(ws.update_config_file, was)
        client = ws.app.test_client()
        with mock.patch.object(ws.system_actions, "service_action", return_value=True) as action:
            answer = client.post("/api/settings", json={"buttons.rotary_pin_a": 5}).get_json()
        self.assertTrue(answer["data"]["reboot_needed"])
        action.assert_any_call("restart", "rukebox-rotary-config", timeout=30)
        with mock.patch.object(ws.system_actions, "service_action", return_value=True) as action:
            answer = client.post("/api/settings", json={"buttons.rotary_volume_step": 4}).get_json()
        self.assertFalse(answer["data"]["reboot_needed"], "the step is read live")
        self.assertNotIn(mock.call("restart", "rukebox-rotary-config", timeout=30), action.call_args_list)

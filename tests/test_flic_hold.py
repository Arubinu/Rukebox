"""The Flic button put on hold while it and the speaker would have to share the
only Bluetooth controller: the daemon stops the services, leaves them enabled,
and starts them again once a controller is free - and never undoes a real
"off". systemctl is faked, so this runs off-hardware."""
import os
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import _path  # noqa: F401
import audio_diag
from config_and_scan import load_config
import rukebox_daemon


class FakeMpv:
    def set_volume(self, volume):
        pass

    def set_pause(self, paused):
        pass

    def loadfile(self, path):
        pass

    def stop_playback(self):
        pass

    def set_loop(self, mode="no"):
        pass


class FakeSystemctl:
    """Answers systemctl and records what it was asked."""

    def __init__(self, enabled=True, active=True, refuse=False):
        self.enabled = enabled
        self.active = active
        self.refuse = refuse
        self.calls = []

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        if args[0] == "sudo":
            return SimpleNamespace(returncode=1 if self.refuse else 0, stdout="",
                                   stderr="sudo: a password is required" if self.refuse else "")
        answer = ""
        if args[1] == "is-enabled":
            answer = "enabled" if self.enabled else "disabled"
        elif args[1] == "is-active":
            answer = "active" if self.active else "inactive"
        return SimpleNamespace(returncode=0, stdout=answer + "\n", stderr="")

    def actions(self):
        """The distinct verbs asked of the services, in the order they were
        first asked: one per unit, and the two units share a verb."""
        seen = []
        for call in self.calls:
            if call[0] != "sudo":
                continue
            verb = next((part for part in call[2:] if not part.startswith("-")), "")
            if verb and verb not in seen:
                seen.append(verb)
        return seen

    def units(self):
        return [call[-1] for call in self.calls if call[0] == "sudo"]


class FlicHoldTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cfg = load_config()
        self.cfg.update({
            "MUSIC_DIR": self.dir,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "AUDIO_OUTPUT": "bluetooth",
        })

    def build(self, hciconfig=None, **systemd):
        daemon = rukebox_daemon.RadioDaemon(self.cfg)
        daemon.mpv = FakeMpv()
        daemon._clock_ready = threading.Event()
        self.systemctl = FakeSystemctl(**systemd)
        # One patch for both: the daemon and system_actions share the sys.modules
        # entry, and the hold goes through src/system_actions.py now.
        mock.patch.object(rukebox_daemon.system_actions.subprocess, "run",
                          self.systemctl).start()
        self.addCleanup(mock.patch.stopall)
        if hciconfig is not None:
            mock.patch.object(audio_diag, "_run", hciconfig).start()
        return daemon

    def held(self, reason):
        return mock.patch.object(audio_diag, "flic_availability",
                                 return_value=(reason is None, reason)).start()

    def test_one_controller_and_bluetooth_puts_it_on_hold(self):
        daemon = self.build()
        self.held("single_controller")
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), ["stop"])
        self.assertTrue(daemon.state.flag("flic_held"))

    def test_the_hold_is_lifted_once_a_controller_is_free(self):
        daemon = self.build(active=False)
        daemon.state.set_flag("flic_held", True)
        self.held(None)
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), ["start"])
        self.assertFalse(daemon.state.flag("flic_held"))

    def test_a_button_turned_off_for_real_is_left_alone(self):
        daemon = self.build(enabled=False)
        self.held("single_controller")
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), [], "not ours to stop")
        self.assertFalse(daemon.state.flag("flic_held"))

    def test_a_button_we_never_held_is_not_started(self):
        daemon = self.build(active=False)
        self.held(None)
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), [],
                         "stopped by hand, and enabled: not ours to start")

    def test_nothing_is_touched_while_the_button_runs(self):
        daemon = self.build()
        self.held(None)
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), [])

    def test_a_refused_command_is_reported_once_and_does_not_crash(self):
        daemon = self.build(refuse=True)
        self.held("single_controller")
        daemon._watch_flic()
        daemon._watch_flic()
        self.assertEqual(self.systemctl.actions(), ["stop"], "it keeps trying")
        self.assertEqual(len(self.systemctl.units()), 4,
                         "both units, on both watch turns")
        self.assertFalse(daemon.state.flag("flic_held"), "nothing was held")
        self.assertTrue(daemon._flic_warned)

    def test_a_wired_output_leaves_the_button_alone(self):
        self.cfg["AUDIO_OUTPUT"] = "jack"
        daemon = self.build()
        with mock.patch.object(audio_diag, "flic_availability",
                               return_value=(True, None)) as availability:
            daemon._watch_flic()
        self.assertFalse(availability.call_args[0][0],
                         "the speaker is not on Bluetooth")

    def test_one_controller_says_why(self):
        daemon = self.build()
        self.held("single_controller")
        with mock.patch.object(rukebox_daemon.log, "info") as info:
            daemon._watch_flic()
        self.assertIn("only", " ".join(str(c) for c in info.call_args[0]).lower())


class AvailabilityTest(unittest.TestCase):
    """The rule itself: flicd takes a controller for itself."""

    def hciconfig(self, output, returncode=0):
        return mock.patch.object(audio_diag, "_run",
                                 return_value=SimpleNamespace(returncode=returncode, stdout=output))

    def test_one_controller_and_bluetooth_cannot_work(self):
        self.assertEqual(audio_diag.flic_availability(True, [{"name": "hci0"}]),
                         (False, "single_controller"))

    def test_the_same_controller_is_fine_on_a_wired_output(self):
        self.assertEqual(audio_diag.flic_availability(False, [{"name": "hci0"}]), (True, None))

    def test_two_controllers_are_fine(self):
        found = [{"name": "hci0"}, {"name": "hci1"}]
        self.assertEqual(audio_diag.flic_availability(True, found), (True, None))

    def test_no_controller_at_all_is_said_whatever_the_output(self):
        self.assertEqual(audio_diag.flic_availability(False, []), (False, "no_controller"))

    def test_an_unreadable_list_never_holds_the_button(self):
        with mock.patch.object(audio_diag, "_run", return_value=None):
            self.assertEqual(audio_diag.flic_availability(True), (True, None))

    def test_the_list_comes_from_the_kernel_not_from_bluez(self):
        text = "hci0:\tType: Primary  Bus: UART\n\tBD Address: B8:27:EB:62:82:CB\n"
        with self.hciconfig(text) as run:
            self.assertEqual([c["name"] for c in audio_diag.controller_list()], ["hci0"])
        self.assertEqual(run.call_args[0][0], ["hciconfig"], "bluetoothctl drops what flicd holds")
        with self.hciconfig("", returncode=1):
            self.assertIsNone(audio_diag.controller_list())


if __name__ == "__main__":
    unittest.main()

"""What this machine can do (src/platform.py).

The point of the whole module is that a container cannot have an access point,
GPIO, a hardware clock or a USB gadget, and that the interface must find that
out from here rather than from a missing file somewhere else. RUKEBOX_PLATFORM
forces the answer, which is also how the suite runs the same checks the way a
Docker image would."""
import os
import unittest
from unittest import mock

import _path  # noqa: F401
import platform as host


class DetectionTest(unittest.TestCase):
    def setUp(self):
        host.reset()
        self.addCleanup(host.reset)
        # Detection reads the environment, so a test that removes a variable
        # has to put it back - the suite runs with RUKEBOX_PLATFORM set.
        self.addCleanup(self._restore_environment,
                        {name: os.environ.get(name)
                         for name in ("RUKEBOX_PLATFORM", "container")})

    @staticmethod
    def _restore_environment(before):
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def with_only_environment(self, **values):
        """The environment detection sees, and nothing else: what the suite set
        must not leak into a test about detection."""
        for name in ("RUKEBOX_PLATFORM", "container"):
            os.environ.pop(name, None)
        os.environ.update(values)
        host.reset()

    def test_a_forced_platform_wins(self):
        for name in (host.PI, host.LXC, host.DOCKER, host.HOST):
            with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": name}):
                host.reset()
                self.assertEqual(host.detect(), name)
                self.assertEqual(host.name(), name)

    def test_an_unknown_forced_platform_is_ignored(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": "toaster"}):
            host.reset()
            self.assertIn(host.detect(), host.KNOWN)

    def test_the_docker_marker_file(self):
        self.with_only_environment()
        with mock.patch.object(host.os.path, "exists",
                               side_effect=lambda p: p == "/.dockerenv"):
            self.assertEqual(host.detect(), host.DOCKER)

    def test_lxc_is_told_apart_from_docker(self):
        self.with_only_environment(container="lxc")
        self.assertEqual(host.detect(), host.LXC)
        self.with_only_environment(container="docker")
        self.assertEqual(host.detect(), host.DOCKER)

    def test_a_raspberry_pi_model_says_pi(self):
        self.with_only_environment()
        with mock.patch.object(host, "_read_first",
                               return_value="Raspberry Pi Zero 2 W Rev 1.0"):
            with mock.patch.object(host, "_cgroup_text", return_value=""):
                self.assertEqual(host.detect(), host.PI)

    def test_an_ordinary_machine_is_host(self):
        self.with_only_environment()
        with mock.patch.object(host, "_read_first", return_value="Some Laptop"):
            with mock.patch.object(host, "_cgroup_text", return_value=""):
                with mock.patch.object(host.os.path, "exists", return_value=False):
                    self.assertEqual(host.detect(), host.HOST)


class CapabilityTest(unittest.TestCase):
    def setUp(self):
        host.reset()
        self.addCleanup(host.reset)

    def test_a_container_has_no_access_point_and_no_gpio(self):
        """Whatever the filesystem looks like: a mounted /dev/gpiochip0 must
        not put the GPIO card back on the page, and the RTC and the clock are
        the host's, not the container's."""
        with mock.patch.dict(host.HARDWARE, {name: mock.Mock(return_value=True)
                                             for name in host.HARDWARE}):
            with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.DOCKER}):
                host.reset()
                caps = host.caps()
        self.assertEqual(caps["platform"], host.DOCKER)
        for name in ("access_point", "captive_portal", "gpio", "usb_gadget",
                     "wireless", "power", "rtc", "set_clock", "self_update"):
            self.assertFalse(caps[name], name)

    def test_a_pi_profile_has_them(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.PI}):
            host.reset()
            with mock.patch.dict(host.HARDWARE, {name: mock.Mock(return_value=True)
                                                 for name in host.HARDWARE}):
                self.assertTrue(all(host.caps()[name] for name in host.CAPABILITY_NAMES))

    def test_the_capabilities_the_platform_decides_are_not_probed(self):
        always = mock.Mock(return_value=True)
        with mock.patch.dict(host.HARDWARE, {name: always for name in host.HARDWARE}):
            with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.DOCKER}):
                host.reset()
                caps = host.caps()
        for name in ("access_point", "captive_portal", "gpio", "usb_gadget",
                     "wireless", "power"):
            self.assertFalse(caps[name], name)
        # The Bluetooth of the host and a local sound card are still there.
        self.assertTrue(caps["bluetooth"])
        self.assertTrue(caps["local_audio"])

    def test_every_documented_capability_has_a_probe(self):
        self.assertEqual(set(host.CAPABILITY_NAMES), set(host.HARDWARE))
        self.assertEqual(set(host.CAPABILITY_NAMES), set(host.caps()) - {"platform"})

    def test_a_probe_that_raises_is_false_not_a_crash(self):
        """A capability check must never be what takes the radio down."""
        with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.PI}):
            host.reset()
            with mock.patch.dict(host.HARDWARE, {"bluetooth": mock.Mock(side_effect=OSError)}):
                self.assertFalse(host.has("bluetooth"))

    def test_an_override_pins_and_forgets(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.DOCKER}):
            host.reset()
            self.assertFalse(host.has("gpio"))
            host.override(gpio=True)
            self.assertTrue(host.has("gpio"))
            host.override(gpio=None)
            self.assertFalse(host.has("gpio"))

    def test_an_unknown_capability_is_false(self):
        self.assertFalse(host.has("teleporter"))


class ForcedPlatformSuiteTest(unittest.TestCase):
    def test_the_environment_the_suite_runs_under_is_readable(self):
        """RUKEBOX_PLATFORM is what the Docker passes of this suite will set;
        reading it must not depend on anything having been imported first."""
        with mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": host.DOCKER}):
            host.reset()
            self.assertEqual(host.name(), host.DOCKER)
            host.reset()


if __name__ == "__main__":
    unittest.main()

"""What the machine is asked to do (src/system_actions.py).

On a Pi these are the systemctl, date and hwclock calls that were always
there. In a container there is no machine to switch off, so "off" ends this
process and Docker's restart policy decides what happens next - which is why
the tests here care as much about what is NOT run as about what is."""
import os
import threading
import time
import unittest
from unittest import mock

import _path  # noqa: F401
import platform as host
import system_actions as sa


def fake_result(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class PlatformFixtures(unittest.TestCase):
    def setUp(self):
        host.reset()
        self.addCleanup(host.reset)
        # `_exiting` survives the mocked os._exit of the previous test.
        exiting = mock.patch.object(sa, "_exiting", threading.Event())
        exiting.start()
        self.addCleanup(exiting.stop)

    def as_platform(self, name, os_name="linux"):
        """A platform, on a machine that behaves like the one it imitates.

        `sys.platform` is patched with it: three of the predicates here are
        about a POSIX machine at all (there is no `sudo date` on Windows), and
        a test that only pretended to be a Pi found that out the hard way."""
        for patcher in (mock.patch.dict(os.environ, {"RUKEBOX_PLATFORM": name}),
                        mock.patch.object(sa.sys, "platform", os_name)):
            patcher.start()
            self.addCleanup(patcher.stop)
        host.reset()


class CapabilityPredicateTest(PlatformFixtures):
    def test_a_pi_can_do_all_of_it(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            self.assertTrue(sa.can_power_off())
        self.assertTrue(sa.can_set_clock())

    def test_a_container_switches_nothing_off_and_sets_no_clock(self):
        self.as_platform(host.DOCKER)
        self.assertFalse(sa.can_power_off())
        self.assertFalse(sa.can_set_clock())

    def test_a_container_never_updates_itself_in_place(self):
        """The image is the unit of update: docker pull, not update.sh."""
        self.as_platform(host.DOCKER)
        with mock.patch.object(sa.paths, "install_dir", return_value="/opt/rukebox"):
            with mock.patch.object(sa.os.path, "isdir", return_value=True):
                self.assertFalse(sa.can_self_update())

    def test_an_installed_tree_can_update_itself(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa.paths, "install_dir", return_value="/opt/rukebox"):
            with mock.patch.object(sa.os.path, "isdir", return_value=True):
                self.assertTrue(sa.can_self_update())


class PowerTest(PlatformFixtures):
    def test_a_pi_switches_itself_off(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=fake_result()) as run:
                self.assertTrue(sa.power_off())
        self.assertEqual(run.call_args[0][0], ["systemctl", "poweroff"])
        self.assertTrue(run.call_args[1]["sudo"])

    def test_a_pi_reboots_itself(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=fake_result()) as run:
                self.assertTrue(sa.reboot())
        self.assertEqual(run.call_args[0][0], ["systemctl", "reboot"])

    def test_a_container_ends_its_process_instead(self):
        self.as_platform(host.DOCKER)
        for verb in (sa.power_off, sa.reboot, sa.restart_daemon):
            with mock.patch.object(sa, "_run") as run:
                with mock.patch.object(sa, "_end_this_process",
                                       return_value=True) as end:
                    self.assertTrue(verb())
            self.assertFalse(run.called, verb.__name__)
            self.assertTrue(end.called, verb.__name__)

    def test_a_container_runs_its_exit_hooks_once(self):
        self.as_platform(host.DOCKER)
        ran = []
        with mock.patch.object(sa, "_exit_hooks", [lambda: ran.append("ran")]):
            with mock.patch.object(sa, "EXIT_DELAY_SEC", 0):
                with mock.patch.object(sa.os, "_exit") as leave:
                    sa._end_this_process(0)
                    for _ in range(200):
                        if leave.called:
                            break
                        time.sleep(0.01)
        self.assertEqual(ran, ["ran"])
        self.assertEqual(leave.call_args[0][0], 0)

    def test_a_hook_that_fails_does_not_stop_the_exit(self):
        self.as_platform(host.DOCKER)
        with mock.patch.object(sa, "_exit_hooks",
                               [mock.Mock(side_effect=RuntimeError), lambda: None]):
            with mock.patch.object(sa, "EXIT_DELAY_SEC", 0):
                with mock.patch.object(sa.os, "_exit") as leave:
                    sa._end_this_process(0)
                    for _ in range(200):
                        if leave.called:
                            break
                        time.sleep(0.01)
        self.assertTrue(leave.called)

    def test_asking_twice_leaves_once(self):
        self.as_platform(host.DOCKER)
        with mock.patch.object(sa, "_exiting") as exiting:
            exiting.is_set.return_value = True
            with mock.patch.object(sa.threading, "Thread") as thread:
                self.assertTrue(sa._end_this_process(0))
        self.assertFalse(thread.called)

    def test_going_down_reads_systemds_jobs(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run",
                                   return_value=fake_result(stdout="12 reboot.target\n")):
                self.assertEqual(sa.going_down(), "reboot")
            with mock.patch.object(sa, "_run",
                                   return_value=fake_result(stdout="12 poweroff.target\n")):
                self.assertEqual(sa.going_down(), "poweroff")
            with mock.patch.object(sa, "_run", return_value=fake_result(stdout="")):
                self.assertIsNone(sa.going_down())

    def test_a_container_has_no_job_to_read(self):
        self.as_platform(host.DOCKER)
        self.assertIsNone(sa.going_down())


class ServiceTest(PlatformFixtures):
    def setUp(self):
        super().setUp()
        self.as_platform(host.PI)

    def test_a_unit_gets_its_suffix(self):
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=fake_result()) as run:
                sa.service_action("restart", "rukebox-daemon")
        self.assertEqual(run.call_args[0][0], ["systemctl", "restart", "rukebox-daemon.service"])

    def test_an_already_suffixed_unit_is_not_suffixed_twice(self):
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=fake_result()) as run:
                sa.service_action("stop", "flicd.service")
        self.assertEqual(run.call_args[0][0], ["systemctl", "stop", "flicd.service"])

    def test_a_failure_is_false_not_an_exception(self):
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=None):
                self.assertFalse(sa.service_action("restart", "nope"))
            with mock.patch.object(sa, "_run", return_value=fake_result(returncode=5)):
                self.assertFalse(sa.service_action("restart", "nope"))

    def test_active_and_enabled_read_the_exit_code(self):
        with mock.patch.object(sa, "_systemctl_available", return_value=True):
            with mock.patch.object(sa, "_run", return_value=fake_result(0)):
                self.assertTrue(sa.service_is_active("flicd"))
                self.assertTrue(sa.service_is_enabled("flicd"))
            with mock.patch.object(sa, "_run", return_value=fake_result(3)):
                self.assertFalse(sa.service_is_active("flicd"))
                self.assertFalse(sa.service_is_enabled("flicd"))

    def test_without_systemd_there_is_nothing_to_run(self):
        with mock.patch.object(sa, "_systemctl_available", return_value=False):
            with mock.patch.object(sa.subprocess, "run") as run:
                self.assertIsNone(sa._run(["systemctl", "status"]))
        self.assertFalse(run.called)

    def test_a_container_has_no_unit_to_restart(self):
        self.as_platform(host.DOCKER)
        with mock.patch.object(sa, "_run") as run:
            with mock.patch.object(sa, "_end_this_process", return_value=True):
                sa.restart_daemon()
                sa.restart_web_server()
        self.assertFalse(run.called)


class ClockTest(PlatformFixtures):
    def test_a_pi_sets_the_clock_with_sudo_date(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa.subprocess, "run",
                               return_value=fake_result()) as run:
            ok, detail = sa.set_clock("2026-10-05 14:00:00", utc=True)
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(run.call_args[0][0],
                         ["sudo", "date", "-u", "-s", "2026-10-05 14:00:00"])

    def test_a_pi_says_why_the_clock_could_not_be_set(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa.subprocess, "run",
                               return_value=fake_result(1, stderr="date: invalid date")):
            ok, detail = sa.set_clock("nonsense")
        self.assertFalse(ok)
        self.assertEqual(detail, "date: invalid date")

    def test_a_container_refuses_to_set_the_hosts_clock(self):
        self.as_platform(host.DOCKER)
        with mock.patch.object(sa.subprocess, "run") as run:
            ok, detail = sa.set_clock("2026-10-05 14:00:00")
        self.assertFalse(ok)
        self.assertEqual(detail, "unsupported_here")
        self.assertFalse(run.called)

    def test_the_hardware_clock_is_written_only_when_there_is_one(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "has_rtc", return_value=False):
            with mock.patch.object(sa.subprocess, "run") as run:
                self.assertFalse(sa.write_rtc())
        self.assertFalse(run.called)
        with mock.patch.object(sa, "has_rtc", return_value=True):
            with mock.patch.object(sa.subprocess, "run",
                                   return_value=fake_result()) as run:
                self.assertTrue(sa.write_rtc())
        self.assertEqual(run.call_args[0][0], ["sudo", "hwclock", "-w"])

    def test_an_missing_hwclock_is_not_a_failure_of_the_radio(self):
        self.as_platform(host.PI)
        with mock.patch.object(sa, "has_rtc", return_value=True):
            with mock.patch.object(sa.subprocess, "run", side_effect=OSError):
                self.assertFalse(sa.write_rtc())


class UptimeTest(unittest.TestCase):
    def test_uptime_is_read_as_a_number(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="1234.56 9876.54\n")):
            self.assertEqual(sa.read_uptime(), 1234.56)

    def test_no_proc_uptime_is_none_not_a_crash(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertIsNone(sa.read_uptime())


if __name__ == "__main__":
    unittest.main()

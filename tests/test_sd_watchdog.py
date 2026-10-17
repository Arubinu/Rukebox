"""systemd's watchdog (src/sd_watchdog.py): told only while the scheduler loop turns, with
room for a slow start and a long rescan, and nothing at all where there is no systemd."""
import os
import re
import shutil
import socket
import tempfile
import unittest

import _path
import sd_watchdog as sw


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sent = []
        self.dog = sw.Watchdog(send=lambda state: self.sent.append(state) or True, clock=self.clock)

    def test_a_slow_start_is_waited_for_then_given_up(self):
        self.clock.now += sw.STARTUP_GRACE_SEC - 1
        self.assertTrue(self.dog.ping(), "the loop has not turned yet: still starting")
        self.clock.now += 2
        self.assertFalse(self.dog.ping(), "a start that never ends is a hang too")

    def test_a_loop_that_stops_turning_is_no_longer_vouched_for(self):
        self.dog.beat()
        self.clock.now += sw.STALE_AFTER_SEC - 1
        self.assertTrue(self.dog.ping())
        self.clock.now += 2
        self.assertFalse(self.dog.ping())
        self.dog.beat()
        self.assertTrue(self.dog.ping(), "turning again")
        self.assertEqual(set(self.sent), {"WATCHDOG=1"})

    def test_no_systemd_no_thread(self):
        self.assertFalse(sw.notify("WATCHDOG=1", environ={}))
        previous = os.environ.pop("NOTIFY_SOCKET", None)
        self.addCleanup(lambda: previous is None or os.environ.__setitem__("NOTIFY_SOCKET", previous))
        self.assertFalse(sw.Watchdog().start())

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "systemd's socket is a Unix socket")
    def test_the_line_reaches_systemds_socket(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "notify")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(path)
            server.settimeout(2)
            self.assertTrue(sw.notify("WATCHDOG=1", environ={"NOTIFY_SOCKET": path}))
            self.assertEqual(server.recv(64), b"WATCHDOG=1")

    def test_the_unit_waits_longer_than_a_rescan_may_take(self):
        unit = _path.read("systemd", "rukebox-daemon.service")
        seconds = int(re.search(r"^WatchdogSec=(\d+)$", unit, re.MULTILINE).group(1))
        self.assertIn("NotifyAccess=main", unit)
        self.assertGreater(seconds, sw.STALE_AFTER_SEC + sw.PING_EVERY_SEC,
                           "systemd must not give up before the thread stops vouching")


if __name__ == "__main__":
    unittest.main()

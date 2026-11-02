"""The router's button watcher (extender/rukebox-button), run by a real shell with a fake curl."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

import _path  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "extender", "rukebox-button")
SHELL = shutil.which("dash") or shutil.which("sh")


@unittest.skipUnless(SHELL and os.name == "posix", "needs a POSIX shell")
class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.calls = os.path.join(self.dir, "calls")
        self.bin = os.path.join(self.dir, "bin")
        os.mkdir(self.bin)
        fake = os.path.join(self.bin, "curl")
        with open(fake, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\necho "$*" >> "%s"\n[ ! -e "%s/down" ]\n' % (self.calls, self.dir))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.process = None
        self.addCleanup(self.stop)

    def conf(self, **values):
        path = os.path.join(self.dir, "conf")
        settings = {"RADIO_URL": "http://radio", "ID_FILE": os.path.join(self.dir, "id"), "POLL_SEC": "0.05", **values}
        with open(path, "w", encoding="utf-8") as out:
            out.writelines("%s=%s\n" % item for item in settings.items())
        return dict(os.environ, RUKEBOX_BUTTON_CONF=path, PATH=self.bin + os.pathsep + os.environ["PATH"])

    def start(self, **values):
        self.process = subprocess.Popen([SHELL, SCRIPT, "run"], env=self.conf(**values), start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The state at start is read once its identity is chosen, which a first run takes a while over.
        for _ in range(100):
            if os.path.exists(os.path.join(self.dir, "id")):
                break
            time.sleep(0.05)
        time.sleep(0.5)

    def stop(self):
        if self.process:
            subprocess.run(["pkill", "-g", str(self.process.pid)], check=False)
            self.process.wait()
            self.process = None

    def sent(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as lines:
            return [line.split()[-1].rsplit("/", 1)[1] for line in lines]

    def wait_sent(self, count):
        for _ in range(60):
            if len(self.sent()) >= count:
                break
            time.sleep(0.05)
        return self.sent()

    def link(self, value):
        with open(os.path.join(self.dir, "link"), "w", encoding="ascii") as out:
            out.write(value)

    def test_a_press_is_the_link_going_the_chosen_way_and_its_settling_is_ignored(self):
        self.link("1")
        self.start(LINK_PORT="file:" + os.path.join(self.dir, "link"), LINK_PRESS="down")
        self.assertEqual(self.sent(), [], "the state at start is no press")
        self.link("0")
        self.assertEqual(self.wait_sent(1), ["single"])
        self.link("1")
        time.sleep(0.2)
        self.link("0")
        time.sleep(0.3)
        self.assertEqual(self.sent(), ["single"], "a second drop within the rearm time is the same press")

    def test_the_other_wiring_presses_on_the_link_coming_up(self):
        self.link("0")
        self.start(LINK_PORT="file:" + os.path.join(self.dir, "link"), LINK_PRESS="up")
        self.link("1")
        self.assertEqual(self.wait_sent(1), ["single"])

    def test_the_serial_lines_are_gestures_and_nothing_else_is(self):
        fifo = os.path.join(self.dir, "serial")
        os.mkfifo(fifo)
        self.start(SERIAL_DEVICE=fifo)
        # Open both ways: the watcher's stty opens and closes it first, which would break a plain writer.
        board = os.open(fifo, os.O_RDWR)
        self.addCleanup(os.close, board)
        os.write(board, b"hello\nsingle\r\ndouble\nrm -rf\nlong\n")
        self.assertEqual(sorted(self.wait_sent(3)), ["double", "long", "single"])

    def run_command(self, *args, **values):
        return subprocess.run([SHELL, SCRIPT, *args], env=self.conf(**values), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, check=False)

    def test_its_identity_is_chosen_once_and_a_failure_is_told(self):
        done = self.run_command("press", "double")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.run_command("press", "single")
        with open(self.calls, encoding="utf-8") as lines:
            first, second = lines.read().splitlines()
        self.assertIn("http://radio/api/button/double", first)
        identity = re.search(r"id=([0-9a-f]{16})&secret=([0-9a-f]{32})&name=", first)
        self.assertTrue(identity, first)
        self.assertIn(identity.group(0), second, "the same identity at every press")
        with open(os.path.join(self.dir, "down"), "w", encoding="ascii"):
            pass
        failed = self.run_command("press")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("not delivered", failed.stdout)

    def test_settings_that_cannot_work_are_refused(self):
        self.assertEqual(self.run_command("press", "triple").returncode, 2)
        self.assertEqual(self.run_command("run").returncode, 2, "nothing to watch")


if __name__ == "__main__":
    unittest.main()

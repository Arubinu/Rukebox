"""The rising start: the loudness of each file, measured once, orders the first
songs of a start from the quietest to the loudest."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import library
from state import RadioState
from test_empty_library import DaemonCase

SUMMARY = (b"[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
           b"    I:         -11.9 LUFS\n    Threshold: -22.1 LUFS\n")


class LoudnessTest(unittest.TestCase):
    def test_the_summary_is_read(self):
        lines = b"[Parsed_ebur128_0 @ 0x1] t: 3.0 TARGET:-23 LUFS M: -12.0 S:-13.0 I: -14.0 LUFS\n"
        with mock.patch.object(library.subprocess, "run",
                               return_value=mock.Mock(stderr=lines + SUMMARY)):
            self.assertEqual(library.read_loudness("/a.mp3"), -11.9)

    def test_silence_or_a_failure_is_no_measurement(self):
        with mock.patch.object(library.subprocess, "run",
                               return_value=mock.Mock(stderr=b"    I:         -70.0 LUFS\n")):
            self.assertIsNone(library.read_loudness("/a.mp3"))
        with mock.patch.object(library.subprocess, "run", side_effect=OSError):
            self.assertIsNone(library.read_loudness("/a.mp3"))

    def test_measured_once_and_read_back(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        paths = []
        for name in ("a.mp3", "b.mp3"):
            path = os.path.join(folder, name)
            with open(path, "wb") as f:
                f.write(b"x")
            paths.append(path)
        lib = library.Library(os.path.join(folder, "lib.db"), lambda p: p)
        lib.sync(paths, folder)
        self.assertEqual(sorted(lib.unmeasured()), sorted(paths))
        lib.store_loudness(paths[0], -9.5)
        lib.store_loudness(paths[1], None)
        self.assertEqual(lib.unmeasured(), [])
        self.assertEqual(lib.loudness_for(paths), {paths[0]: -9.5})


class RiseHeadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = RadioState(os.path.join(self.dir, "state.json"))

    def test_the_head_rises_and_the_rest_keeps_its_order(self):
        self.state.data["play_queue"] = ["loud", "soft", "unknown", "mid", "after"]
        levels = {"loud": -8.0, "soft": -20.0, "mid": -14.0, "after": -30.0}
        self.assertTrue(self.state.rise_head(4, levels.get))
        self.assertEqual(self.state.data["play_queue"], ["soft", "mid", "loud", "unknown", "after"])

    def test_songs_asked_for_stay_first(self):
        self.state.data["play_queue"] = ["asked", "loud", "soft"]
        self.state.data["requests"] = ["asked"]
        self.state.rise_head(5, {"asked": -1.0, "loud": -8.0, "soft": -20.0}.get)
        self.assertEqual(self.state.data["play_queue"], ["asked", "soft", "loud"])


class DaemonRiseTest(DaemonCase):
    def test_a_start_rises_when_asked_for(self):
        self.daemon.cfg.update({"MORNING_RISE_TRACKS": 3, "MUSIC_RESUME_MODE": "next_track"})
        self.daemon.state.data["play_queue"] = ["/m/loud", "/m/soft", "/m/mid", "/m/later"]
        fake = mock.Mock()
        fake.loudness_for.return_value = {"/m/loud": -8.0, "/m/soft": -20.0, "/m/mid": -14.0}
        self.daemon._list_library = fake
        self.daemon._rise_queue_head()
        self.assertEqual(self.daemon.state.data["play_queue"], ["/m/soft", "/m/mid", "/m/loud", "/m/later"])

    def test_off_or_resuming_leaves_the_queue_alone(self):
        queue = ["/m/loud", "/m/soft"]
        fake = mock.Mock()
        fake.loudness_for.return_value = {"/m/loud": -8.0, "/m/soft": -20.0}
        self.daemon._list_library = fake
        self.daemon.cfg["MORNING_RISE_TRACKS"] = 0
        self.daemon.state.data["play_queue"] = list(queue)
        self.daemon._rise_queue_head()
        self.assertEqual(self.daemon.state.data["play_queue"], queue)
        self.daemon.cfg.update({"MORNING_RISE_TRACKS": 5, "MUSIC_RESUME_MODE": "same_position"})
        self.daemon._resume_armed = True
        self.daemon._rise_queue_head()
        self.assertEqual(self.daemon.state.data["play_queue"], queue, "the song taken up again stays first")


if __name__ == "__main__":
    unittest.main()

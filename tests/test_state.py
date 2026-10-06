"""The persisted state (src/state.py): songs asked for, first come first
served; exact "k out of n" chances; click sound bags."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import state


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "state.json")
        self.s = state.RadioState(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_requests_fifo(self):
        self.s.data["play_queue"] = ["a", "b", "c", "d", "e"]
        self.assertEqual(self.s.enqueue_request("d"), 0)
        self.assertEqual(self.s.enqueue_request("e"), 1)
        self.assertEqual(self.s.data["play_queue"], ["d", "e", "a", "b", "c"])
        self.assertEqual(self.s.requested_paths(), ["d", "e"])
        self.s.pop_next_track_or_none()
        self.assertEqual(self.s.requested_paths(), ["e"])
        self.s.enqueue_request("b")
        self.assertEqual(self.s.data["play_queue"][:2], ["e", "b"])

    def test_take_from_queue(self):
        self.s.data["play_queue"] = ["a", "b"]
        self.s.enqueue_request("b")
        self.s.take_from_queue("b")
        self.assertEqual(self.s.data["play_queue"], ["a"])
        self.assertEqual(self.s.data["requests"], [])

    def test_several_taken_out_of_the_queue_at_once(self):
        self.s.data["play_queue"] = ["a", "b", "c", "d"]
        self.s.enqueue_request("d")
        self.assertEqual(self.s.take_from_queue_many(["b", "d"]), 2)
        self.assertEqual(self.s.data["play_queue"], ["a", "c"],
                         "the order of what stays is untouched")
        self.assertEqual(self.s.data["requests"], [], "an asked-for song that went too")

    def test_taking_out_what_is_not_there_writes_nothing(self):
        self.s.data["play_queue"] = ["a"]
        self.assertEqual(self.s.take_from_queue_many(["z"]), 0)
        self.assertEqual(self.s.take_from_queue_many([]), 0)
        self.assertEqual(self.s.data["play_queue"], ["a"])

    def test_persisted(self):
        self.s.data["play_queue"] = ["a", "b"]
        self.s.enqueue_request("b")
        again = state.RadioState(self.path)
        self.assertEqual(again.requested_paths(), ["b"])

    def test_chance_is_exact(self):
        plays = sum(self.s.chance_allows("x", "1/10") for _ in range(1000))
        self.assertEqual(plays, 100)
        plays = [self.s.chance_allows("y", "1/2") for _ in range(2)]
        self.assertEqual(sorted(plays), [False, True])
        self.assertTrue(all(self.s.chance_allows("z", "1/1") for _ in range(5)))

    def test_active_list_survives_a_restart(self):
        self.assertIsNone(self.s.active_list())
        self.assertEqual(self.s.set_active_list("jazz"), "jazz")
        self.assertEqual(self.s.set_active_list(""), None)
        self.s.set_active_list("soir")
        self.assertEqual(state.RadioState(self.path).active_list(), "soir")

    def test_where_a_song_was_cut_short_is_kept_until_it_is_used(self):
        current = state.RadioState(self.path)
        self.assertIsNone(current.resume_point())
        current.set_resume_point("/m/a.mp3", 95.26)
        self.assertEqual(state.RadioState(self.path).resume_point(), ("/m/a.mp3", 95.3), "it survives a restart")
        current.clear_resume_point()
        self.assertIsNone(state.RadioState(self.path).resume_point())

    def test_click_bag_order(self):
        build = lambda: ["1.mp3", "2.mp3", "3.mp3"]  # noqa: E731
        got = [self.s.next_click_sound("meme", build) for _ in range(4)]
        self.assertEqual(got, ["1.mp3", "2.mp3", "3.mp3", "1.mp3"])
        self.assertIsNone(self.s.next_click_sound("empty", lambda: []))


if __name__ == "__main__":
    unittest.main()

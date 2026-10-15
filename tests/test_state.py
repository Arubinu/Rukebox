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
        _path.document(self.dir)
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

    def test_forgetting_a_run_keeps_the_days_cutoff(self):
        from datetime import date

        today = date.today().isoformat()
        self.s.data.update({
            "play_queue": ["a", "b"], "last_track": "a", "requests": ["b"],
            "request_by": {"b": "Al"}, "recent": [{"path": "a", "at": 1}],
            "click_bags": {"x": ["s1"]},
            "chance_bags": {"auto:x": {"ratio": "1/2", "draws": [True]}},
            "resume_point": {"path": "a", "seconds": 12},
            "last_music_start": today, "custom_abc": today, "cutoff_warning": today,
            "last_cutoff_trigger": today, "pending_cutoff": True,
            "reminders": [{"id": "1"}],
        })
        self.s.forget_run()

        self.assertEqual(self.s.data["play_queue"], [])
        self.assertEqual(self.s.data["requests"], [])
        self.assertEqual(self.s.data["recent"], [])
        self.assertEqual(self.s.data["click_bags"], {})
        self.assertEqual(self.s.data["chance_bags"], {})
        self.assertIsNone(self.s.data["last_track"])
        self.assertIsNone(self.s.resume_point())
        self.assertFalse(self.s.already_triggered_today("last_music_start"))
        self.assertFalse(self.s.already_triggered_today("custom_abc"))
        self.assertFalse(self.s.already_triggered_today("cutoff_warning"))
        self.assertTrue(self.s.already_triggered_today("last_cutoff_trigger"),
                        "the day's cutoff stays: erasing must not arm it again")
        self.assertTrue(self.s.is_pending_cutoff(),
                        "nor drop a shutdown already waiting for the end of a track")
        self.assertEqual(self.s.reminders(), [{"id": "1"}],
                         "a reminder is a person's, not the radio's doing")

    def test_the_memory_of_a_run_goes_back_exactly_as_it_was(self):
        from datetime import date

        today = date.today().isoformat()
        self.s.data.update({
            "play_queue": ["a", "b", "c"], "last_track": "a", "requests": ["b"],
            "request_by": {"b": "Al"}, "recent": [{"path": "a", "at": 1}],
            "click_bags": {"x": ["s1"]},
            "chance_bags": {"auto:x": {"ratio": "1/2", "draws": [True]}},
            "resume_point": {"path": "a", "seconds": 12},
            "last_music_start": today, "last_cutoff_trigger": today,
        })
        memory = self.s.run_memory()

        # What a run does by itself: it plays, it draws, it says, it is cut short.
        self.s.pop_next_track_or_none()
        self.s.add_recent("/music/z.mp3", 5)
        self.s.chance_allows("auto:x", "1/2")
        self.s.next_click_sound("x", lambda: ["s2"])
        self.s.mark_triggered_today("custom_new")
        self.s.set_resume_point("/music/z.mp3", 30)
        self.assertEqual(self.s.data["play_queue"], ["b", "c"])

        self.assertTrue(self.s.restore_run(memory))

        self.assertEqual(self.s.data["play_queue"], ["a", "b", "c"],
                         "the pass starts again with the songs the run played")
        self.assertEqual(self.s.recent_paths(), ["a"])
        self.assertEqual(self.s.data["request_by"], {"b": "Al"})
        self.assertEqual(self.s.resume_point(), ("a", 12.0),
                         "and the song it found cut short is taken up again")
        self.assertEqual(self.s.data["click_bags"], {"x": ["s1"]})
        self.assertEqual(self.s.data["chance_bags"],
                         {"auto:x": {"ratio": "1/2", "draws": [True]}},
                         "the draw of the run is not the one kept")
        self.assertTrue(self.s.already_triggered_today("last_music_start"))
        self.assertFalse(self.s.already_triggered_today("custom_new"),
                         "an announcement the run said can be said again")

    def test_restoring_leaves_the_days_cutoff_where_it_is(self):
        from datetime import date

        today = date.today().isoformat()
        self.s.data["last_cutoff_trigger"] = today
        self.s.data["last_music_start"] = today
        memory = self.s.run_memory()
        self.s.data.pop("last_music_start", None)
        self.s.data["pending_cutoff"] = True

        self.assertTrue(self.s.restore_run(memory))

        self.assertTrue(self.s.already_triggered_today("last_music_start"),
                        "the day's marker comes back")
        self.assertTrue(self.s.already_triggered_today("last_cutoff_trigger"))
        self.assertTrue(self.s.is_pending_cutoff(),
                      "and a shutdown already waiting for the end of a track stays")

    def test_a_memory_that_was_never_kept_is_refused(self):
        self.assertFalse(self.s.restore_run(None))
        self.assertFalse(self.s.restore_run({}))

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

"""The blind test: questions made of the library, answers, points, what each
player may see, and the radio stepping aside for the extracts."""
import random
import unittest
from unittest import mock

import _path  # noqa: F401
import blind_test
from test_empty_library import DaemonCase


def tracks(n=8):
    return [{"path": "/m/%d.mp3" % i, "title": "Song %d" % i, "artist": "Artist %d" % (i % 3),
             "duration": 200.0} for i in range(n)]


class GameTest(unittest.TestCase):
    def setUp(self):
        self.game = blind_test.Game(tracks(), rounds=3, clip_sec=20, rng=random.Random(1))

    def test_a_question_has_four_choices_and_one_answer(self):
        q = self.game.next_question(now=0)
        self.assertEqual(len(q["choices"]), 4)
        self.assertEqual(len(set(q["choices"])), 4)
        right = q["choices"][q["answer"]]
        self.assertEqual(right, blind_test.label(next(t for t in tracks() if t["path"] == q["path"])))
        self.assertTrue(10 <= q["start"] <= 200 - 20 - 10, "the extract starts inside the song")

    def test_the_answer_is_never_shown_before_the_reveal(self):
        self.game.next_question(now=0)
        self.assertNotIn("answer", self.game.view("a", now=1))
        self.game.close_round()
        self.assertIn("answer", self.game.view("a", now=1))

    def test_points_one_for_right_two_for_the_fastest(self):
        q = self.game.next_question(now=0)
        wrong = (q["answer"] + 1) % 4
        self.assertIsNone(self.game.answer("slow", "Slow", q["answer"], now=5))
        self.assertIsNone(self.game.answer("fast", "Fast", q["answer"], now=2))
        self.assertIsNone(self.game.answer("off", "Off", wrong, now=1))
        self.assertEqual(self.game.answer("fast", "Fast", wrong, now=3), "game_already_answered")
        reveal = self.game.close_round()
        self.assertEqual(reveal["gains"], {"slow": 1, "fast": 2, "off": 0})
        view = self.game.view("fast", now=6)
        self.assertEqual([s["name"] for s in view["scores"]], ["Fast", "Slow", "Off"])
        self.assertEqual(view["gain"], 2)

    def test_answers_are_refused_outside_a_question(self):
        self.assertEqual(self.game.answer("a", "A", 0), "game_not_asking")
        self.game.next_question(now=0)
        self.assertEqual(self.game.answer("a", "A", 7), "bad_request")

    def test_the_round_ends_when_everyone_looking_answered(self):
        q = self.game.next_question(now=0)
        self.game.seen("a", "A", now=0)
        self.game.seen("b", "B", now=0)
        self.game.answer("a", "A", q["answer"], now=1)
        self.assertFalse(self.game.everyone_answered(now=1))
        self.game.answer("b", "B", q["answer"], now=2)
        self.assertTrue(self.game.everyone_answered(now=2))

    def test_the_game_ends_after_its_rounds(self):
        for _ in range(3):
            self.assertIsNotNone(self.game.next_question(now=0))
            self.game.close_round()
        self.assertIsNone(self.game.next_question(now=0))

    def test_a_library_too_small_or_untagged_is_no_game(self):
        rows = tracks(3) + [{"path": "/x", "title": None, "artist": "A", "duration": 200},
                            {"path": "/y", "title": "Short", "artist": "A", "duration": 20}]
        playable = blind_test.Game.playable(rows, 20)
        self.assertEqual(len(playable), 3)
        self.assertFalse(blind_test.Game(playable, rounds=5).enough())


class DaemonGameTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.track = __import__("os").path.join(self.music, "a.mp3")
        with open(self.track, "wb") as f:
            f.write(b"x")
        mock.patch.object(self.daemon, "_library_path", side_effect=lambda p: p if p == self.track else None).start()
        mock.patch.object(self.daemon, "_set_timer").start()

    def test_the_radio_steps_aside_and_takes_its_song_up_again(self):
        self.daemon.mode = "music"
        self.daemon._last_music_track = "/m/song.mp3"
        self.daemon._position = 33.0
        self.assertIsNone(self.daemon._game_clip(self.track, 60, 20))
        self.assertEqual(self.daemon.mode, "game")
        self.assertEqual(self.daemon.mpv.files[-1], self.track)
        self.daemon._on_mpv_event({"event": "end-file", "reason": "eof"})
        self.assertEqual(self.daemon.mode, "game", "an extract that ends is not followed by anything")
        with mock.patch.object(self.daemon, "_resume_after_announce") as resume:
            self.daemon._game_end()
        resume.assert_called_once()
        self.assertEqual(self.daemon._resume_track, ("/m/song.mp3", 33.0))

    def test_from_idle_it_goes_back_to_idle(self):
        self.daemon.mode = "idle"
        self.daemon._game_clip(self.track, 0, 20)
        self.daemon._game_end()
        self.assertEqual(self.daemon.mode, "idle")

    def test_only_library_files_and_never_over_an_announcement(self):
        self.daemon.mode = "music"
        self.assertEqual(self.daemon._game_clip("/etc/passwd", 0, 20), "not_found")
        self.daemon.mode = "cutoff_announce"
        self.assertEqual(self.daemon._game_clip(self.track, 0, 20), "busy")


if __name__ == "__main__":
    unittest.main()

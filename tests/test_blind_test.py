"""The blind test: questions made of the library, answers, points, what each
player may see, and the radio stepping aside for the extracts."""
import os
import random
import shutil
import socket
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import blind_test
import control_client
import speech
from test_empty_library import DaemonCase


def tracks(n=8):
    return [{"path": "/m/%d.mp3" % i, "title": "Song %d" % i, "artist": "Artist %d" % (i % 3),
             "duration": 200.0} for i in range(n)]


class GameTest(unittest.TestCase):
    def setUp(self):
        self.game = blind_test.Game(tracks(), rounds=3, clip_sec=20, rng=random.Random(1), now=0)
        for person in ("a", "b", "slow", "fast", "off"):
            self.game.join(person, person.title(), True)

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
        self.assertEqual([s["name"] for s in view["scores"]], ["Fast", "Slow", "A", "B", "Off"],
                         "every player is on the board, from their choice to play")
        self.assertEqual(view["gain"], 2)

    def test_everyone_looking_chooses_then_the_game_begins(self):
        game = blind_test.Game(tracks(), rounds=3, clip_sec=20, now=0, join_sec=45)
        self.assertEqual(game.state, "joining")
        game.seen("a", "A", now=1)
        game.seen("b", "B", now=1)
        self.assertFalse(game.ready(now=2))
        game.join("a", "A", True)
        self.assertFalse(game.ready(now=2), "B has not chosen yet")
        game.join("b", "B", False)
        self.assertTrue(game.ready(now=2), "everyone looking chose, with one player")
        view = game.view("b", now=2)
        self.assertEqual((view["role"], view["players"], view["spectators"], view["undecided"]),
                         ("spectator", 1, 1, 0))

    def test_the_wait_runs_out_or_the_host_cuts_it_short(self):
        game = blind_test.Game(tracks(), rounds=3, clip_sec=20, now=0, join_sec=45)
        game.seen("a", "A", now=1)
        self.assertFalse(game.ready(now=10))
        self.assertEqual(game.view("a", now=10)["join_left"], 35)
        self.assertTrue(game.ready(now=45))
        other = blind_test.Game(tracks(), rounds=3, clip_sec=20, now=0)
        other.go_now = True
        self.assertTrue(other.ready(now=1))

    def test_a_spectator_never_answers_but_may_join_in(self):
        game = blind_test.Game(tracks(), rounds=3, clip_sec=20, now=0)
        game.join("w", "W", False)
        q = game.next_question(now=0)
        self.assertEqual(game.answer("w", "W", q["answer"], now=1), "game_not_player")
        game.join("w", "W", True)
        self.assertIsNone(game.answer("w", "W", q["answer"], now=2))
        self.assertNotIn("w", game.spectators)

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

    def test_who_found_it_fastest_first_and_who_won(self):
        q = self.game.next_question(now=0)
        self.game.answer("slow", "Slow", q["answer"], now=5)
        self.game.answer("fast", "Fast", q["answer"], now=2)
        self.game.answer("off", "Off", (q["answer"] + 1) % 4, now=1)
        self.game.close_round()
        self.assertEqual([p["name"] for p in self.game.right_people()], ["Fast", "Slow"])
        self.assertEqual([p["person"] for p in self.game.winners()], ["fast"])
        self.game.scores["slow"] = 2
        self.assertEqual([p["name"] for p in self.game.winners()], ["Fast", "Slow"], "a tie names both")

    def test_a_library_too_small_or_untagged_is_no_game(self):
        rows = tracks(3) + [{"path": "/x", "title": None, "artist": "A", "duration": 200},
                            {"path": "/y", "title": "Short", "artist": "A", "duration": 20}]
        playable = blind_test.Game.playable(rows, 20)
        self.assertEqual(len(playable), 3)
        self.assertFalse(blind_test.Game(playable, rounds=5).enough())


class GameSpeechTest(unittest.TestCase):
    def test_what_the_game_says(self):
        self.assertEqual(speech.game_parts("start", None, "fr"), [{"text": "C'est parti pour le blind test !"}])
        parts = speech.game_parts("round", [{"name": "Ana", "person": "a"}, {"name": "Bo", "person": "b"},
                                            {"name": "Cy", "person": "c"}], "en")
        self.assertEqual([p.get("text") or p["name"] for p in parts],
                         ["Right answer from", "Ana", ",", "Bo", "and", "Cy", "."])
        self.assertEqual(parts[1]["person"], "a", "a name keeps who it is, for that person's recording")
        self.assertEqual(speech.game_parts("round", [], "fr"), [{"text": "Personne n'a trouvé."}])
        self.assertEqual([p.get("text") or p["name"] for p in speech.game_parts("end", [{"name": "Ana"}], "fr")],
                         ["Le blind test est terminé.", "Bravo à", "Ana", "!"])


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

    def test_every_clip_fades_in_and_the_music_comes_back_faded_in(self):
        self.daemon.cfg["INTERACTIVE_FADE_DURATION_SEC"] = 0.01
        self.daemon.mode = "music"
        self.daemon._last_music_track = "/m/song.mp3"
        with mock.patch.object(self.daemon, "_glide_volume") as glide:
            self.daemon._game_clip(self.track, 0, 20)
            self.assertEqual((self.daemon.mpv.volume, glide.call_args[0][1]), (0, 0.01),
                             "the extract starts silent and rises")
            glide.reset_mock()
            with mock.patch.object(self.daemon, "_resume_after_announce",
                                   side_effect=lambda: setattr(self.daemon, "mode", "music")):
                self.daemon._game_end()
            self.assertEqual(glide.call_args[0][1], 0.01, "the music rises again too")

    def test_a_clip_never_names_itself(self):
        self.daemon.mode = "idle"
        self.daemon._game_clip(self.track, 0, 20)
        self.assertEqual(self.daemon._interrupting_sound(), (None, None))
        self.assertIsNone(self.daemon._last_sound_status(), "the extract's file name is the answer")

    def test_it_says_only_what_the_web_server_prepared(self):
        self.daemon.mode = "idle"
        self.assertEqual(self.daemon._game_say(self.track), "not_found")
        folder = self.daemon._game_speech_dir()
        os.makedirs(folder)
        said = os.path.join(folder, "say1.wav")
        with open(said, "wb") as f:
            f.write(b"x")
        self.assertIsNone(self.daemon._game_say(said))
        self.assertEqual((self.daemon.mode, self.daemon.mpv.files[-1]), ("game", said))
        self.daemon._game_end()
        self.assertEqual(self.daemon.mode, "idle")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
    def test_the_buttons_wait_while_a_game_plays(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        self.daemon.mode = "game"
        for cmd in ("next_track", "toggle_pause", "single_click"):
            self.assertEqual(control_client.send_control_command(path, cmd).get("error"), "game_running", cmd)
        self.assertTrue(control_client.send_control_command(path, "set_volume", value=40).get("ok"),
                        "the volume stays the listeners'")

    def test_only_library_files_and_never_over_an_announcement(self):
        self.daemon.mode = "music"
        self.assertEqual(self.daemon._game_clip("/etc/passwd", 0, 20), "not_found")
        self.daemon.mode = "cutoff_announce"
        self.assertEqual(self.daemon._game_clip(self.track, 0, 20), "busy")


if __name__ == "__main__":
    unittest.main()

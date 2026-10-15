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

    def test_the_podium_is_said_from_the_third_place_up(self):
        podium = [{"points": 12, "people": [{"name": "Ana", "person": "a"}]},
                  {"points": 7, "people": [{"name": "Bo"}, {"name": "Cy"}]},
                  {"points": 1, "people": [{"name": "Di"}]}]
        said = [p.get("text") or p["name"] for p in speech.game_parts("podium", podium, "fr")]
        self.assertEqual(said, ["Le blind test est terminé.",
                                "Troisième place :", "Di", ", avec 1 point.",
                                "Deuxième place :", "Bo", "et", "Cy", ", avec 7 points.",
                                "Et la première place :", "Ana", ", avec 12 points!"])
        alone = [p.get("text") or p["name"] for p in speech.game_parts("podium", podium[:1], "fr")]
        self.assertEqual(alone, ["Le blind test est terminé.", "Bravo à", "Ana", ", avec 12 points!"])
        self.assertEqual(speech.game_parts("podium", [], "en"), [{"text": "The blind test is over."}])


class WinsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "game_wins.json")

    def test_the_podium_ranks_by_points_and_skips_nobody_with_none(self):
        game = blind_test.Game(tracks(), rounds=3, now=0)
        for person, points in (("a", 5), ("b", 5), ("c", 3), ("d", 2), ("e", 1), ("f", 0)):
            game.join(person, person.upper(), True)
            game.scores[person] = points
        podium = game.podium()
        self.assertEqual([(p["points"], [x["name"] for x in p["people"]]) for p in podium],
                         [(5, ["A", "B"]), (3, ["C"]), (2, ["D"])])
        self.assertNotIn("podium", game.view("a"), "the podium waits for the end")
        game.finish()
        self.assertEqual(game.view("a")["podium"][0], {"points": 5, "names": ["A", "B"]})

    def test_wins_are_kept_and_follow_linked_devices(self):
        blind_test.record_wins(self.path, [{"person": "a", "name": "Ana"}])
        blind_test.record_wins(self.path, [{"person": "a", "name": "Ana"}, {"person": "b", "name": "Bo"}])
        self.assertEqual({p: e["wins"] for p, e in blind_test.wins(self.path).items()}, {"a": 2, "b": 1})
        blind_test.carry_wins(self.path, "b", "a")
        self.assertEqual({p: e["wins"] for p, e in blind_test.wins(self.path).items()}, {"a": 3})


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
        for cmd in ("next_track", "toggle_pause", "double_click"):
            self.assertEqual(control_client.send_control_command(path, cmd).get("error"), "game_running", cmd)
        tap = control_client.send_control_command(path, "single_click")
        self.assertEqual(tap.get("data"), {"game_taps": 1}, "a click is the host's tap, for a game aloud")
        self.assertTrue(control_client.send_control_command(path, "set_volume", value=40).get("ok"),
                        "the volume stays the listeners'")

    def test_the_cutoff_during_a_game_waits_for_its_end_then_stands_by(self):
        self.daemon.mode = "music"
        self.daemon._last_music_track = "/m/song.mp3"
        self.daemon._game_clip(self.track, 0, 20)
        with mock.patch.object(self.daemon, "_cutoff_due", return_value=True), \
                mock.patch.object(self.daemon, "_check_usb_music"), \
                mock.patch.object(self.daemon, "_do_shutdown_sequence") as shutdown:
            self.daemon._scheduler_tick()
        self.assertEqual(self.daemon.mode, "game", "the game is never cut short")
        shutdown.assert_not_called()
        self.assertTrue(self.daemon.state.already_triggered_today("last_cutoff_trigger"))
        with mock.patch.object(self.daemon, "_resume_after_announce") as resume:
            self.daemon._game_end()
        resume.assert_not_called()
        self.assertEqual(self.daemon.mode, "stopped", "standby, not the music, and not switched off")

    def test_a_schedule_ending_during_a_game_waits_for_it_too(self):
        self.daemon.mode = "idle"
        self.daemon._game_clip(self.track, 0, 20)
        with mock.patch.object(self.daemon, "_power_off_now") as off:
            self.daemon._schedule_stop({"id": "s", "name": "Evening", "stop_action": "poweroff"})
        off.assert_not_called()
        self.assertEqual(self.daemon.mode, "game")
        self.daemon._game_end()
        self.assertEqual(self.daemon.mode, "stopped")

    def test_only_library_files_and_never_over_an_announcement(self):
        self.daemon.mode = "music"
        self.assertEqual(self.daemon._game_clip("/etc/passwd", 0, 20), "not_found")
        self.daemon.mode = "cutoff_announce"
        self.assertEqual(self.daemon._game_clip(self.track, 0, 20), "busy")


def tagged(n=12):
    """Songs with an album, a genre ("Rock; Pop" for some) and a year in four decades."""
    out = []
    for i in range(n):
        out.append({"path": "/t/%d.mp3" % i, "title": "Tune %d" % i, "artist": "Band %d" % (i % 5),
                    "album": "Record %d" % (i % 6), "genre": ["Rock; Pop", "Jazz", "Pop", "Folk", "Soul"][i % 5],
                    "year": str(1970 + 10 * (i % 4) + i % 7), "duration": 200.0})
    return out


class KindsTest(unittest.TestCase):
    def test_each_kind_asks_its_own_fact_among_four_different_choices(self):
        for kind in ("artist", "year", "genre", "album"):
            game = blind_test.Game(tagged(), rounds=5, rng=random.Random(3), kinds=[kind])
            q = game.next_question(now=0)
            track = next(t for t in tagged() if t["path"] == q["path"])
            with self.subTest(kind=kind):
                self.assertEqual(q["kind"], kind)
                self.assertEqual(len(set(q["choices"])), 4)
                self.assertEqual(q["choices"][q["answer"]], str(blind_test.fact(track, kind)))

    def test_a_genre_the_song_also_has_is_never_a_wrong_answer(self):
        rock_pop = [t for t in tagged() if t["genre"] == "Rock; Pop"][0]
        game = blind_test.Game([rock_pop] + [t for t in tagged() if t is not rock_pop], rounds=5,
                               rng=random.Random(0), kinds=["genre"])
        game.tracks.sort(key=lambda t: t is not rock_pop)
        q = game.next_question(now=0)
        self.assertEqual(q["choices"][q["answer"]], "Rock")
        self.assertNotIn("Pop", q["choices"])

    def test_decades_are_completed_when_the_library_spans_too_few(self):
        few = [dict(t, year="1984") for t in tagged()]
        game = blind_test.Game(few, rounds=5, rng=random.Random(2), kinds=["year"])
        q = game.next_question(now=0)
        self.assertIn("1980", q["choices"])
        self.assertEqual(len(set(q["choices"])), 4)

    def test_mixed_kinds_only_ask_what_a_song_is_tagged_with(self):
        songs = tagged()
        for t in songs[:6]:
            t["year"] = None
        game = blind_test.Game(songs, rounds=12, rng=random.Random(5), kinds=["year", "artist"])
        for _ in range(12):
            q = game.next_question(now=0)
            track = next(t for t in songs if t["path"] == q["path"])
            self.assertTrue(q["kind"] == "artist" or track["year"])

    def test_a_game_is_refused_when_tags_are_too_scarce(self):
        bare = [dict(t, album=None) for t in tagged()]
        self.assertEqual(blind_test.check(bare, ["song", "album"], 5), (None, "game_too_few_album"))
        two_genres = [dict(t, genre="Jazz" if i % 2 else "Soul") for i, t in enumerate(tagged())]
        self.assertEqual(blind_test.check(two_genres, ["genre"], 5)[1], "game_too_few_genre")
        self.assertEqual(blind_test.check(tagged(), ["year"], 20)[1], "game_not_enough_tracks")
        kept, problem = blind_test.check(tagged(), ["song", "year"], 5)
        self.assertEqual((len(kept), problem), (12, None))


class HintTest(unittest.TestCase):
    def setUp(self):
        self.game = blind_test.Game(tagged(), rounds=5, clip_sec=20, rng=random.Random(1), hint=True)
        for person in ("early", "late", "first"):
            self.game.join(person, person, True)
        self.q = self.game.next_question(now=100)

    def test_the_hint_shows_halfway_through_the_extract(self):
        self.assertIsNone(self.game.view("early", now=105)["hint"])
        letter = self.game.view("early", now=111)["hint"]["letter"]
        self.assertEqual(letter, self.q["title"][0].upper())

    def test_an_answer_after_the_hint_scores_less(self):
        right = self.q["answer"]
        self.game.answer("first", "first", right, now=102)
        self.game.answer("early", "early", right, now=105)
        self.game.answer("late", "late", right, now=115)
        reveal = self.game.close_round()
        self.assertEqual(reveal["gains"], {"first": 3, "early": 2, "late": 1})
        self.assertEqual(reveal["fastest"], "first")

    def test_nobody_is_fastest_when_everyone_waited_for_the_hint(self):
        self.game.answer("late", "late", self.q["answer"], now=115)
        reveal = self.game.close_round()
        self.assertEqual((reveal["gains"], reveal["fastest"], reveal["right"]), ({"late": 1}, None, ["late"]))

    def test_aloud_there_is_no_hint(self):
        self.assertFalse(blind_test.Game(tagged(), mode="oral", hint=True).hint)


class AskedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "game_asked.json")

    def test_songs_asked_come_last_the_oldest_first_and_can_be_forgotten(self):
        for path in ("/t/3.mp3", "/t/1.mp3", "/t/3.mp3"):
            blind_test.remember(self.path, path)
        self.assertEqual(blind_test.asked(self.path), ["/t/1.mp3", "/t/3.mp3"])
        game = blind_test.Game(tagged(), rounds=12, rng=random.Random(4), asked_before=blind_test.asked(self.path))
        self.assertEqual([t["path"] for t in game.tracks[-2:]], ["/t/1.mp3", "/t/3.mp3"])
        blind_test.forget(self.path)
        self.assertEqual(blind_test.asked(self.path), [])


class KindSpeechTest(unittest.TestCase):
    def test_aloud_the_question_is_said_and_the_answer_gives_the_fact_asked(self):
        self.assertEqual(speech.game_parts("ask", [{"kind": "year"}], "en"), [{"text": "Which decade is it from?"}])
        said = speech.game_parts("answer", [{"title": "Fly", "artist": "Hilary Duff", "kind": "year",
                                             "year": 2004}], "en")
        self.assertEqual([p["text"] for p in said], ["That was Fly, by Hilary Duff.", "Released in 2004."])
        plain = speech.game_parts("answer", [{"title": "Fly", "artist": "Hilary Duff", "kind": "song"}], "en")
        self.assertEqual(len(plain), 1)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.game = blind_test.Game(tracks(), rounds=3, clip_sec=20, rng=random.Random(1), now=0)

    def test_two_devices_linked_mid_game_play_on_as_one_person(self):
        g = self.game
        g.join("phone", "Fox", True)
        g.join("tablet", None, True)
        q = g.next_question(now=0)
        g.answer("phone", "Fox", q["answer"], now=2)
        g.close_round()
        q = g.next_question(now=10)
        g.answer("tablet", None, (q["answer"] + 1) % 4, now=11)
        g.answer("phone", "Fox", q["answer"], now=13)
        g.merge("phone", "tablet")
        self.assertEqual((g.players, g.spectators), ({"tablet"}, set()))
        self.assertEqual(g.scores, {"tablet": 2})
        self.assertEqual(g.names["tablet"], "Fox")
        self.assertEqual(g.answers["tablet"][0], (q["answer"] + 1) % 4, "the first answer given stands")
        self.assertNotIn("phone", g.answers)
        self.assertEqual(g.view("tablet", now=14)["role"], "player")

    def test_a_watcher_linked_to_a_player_plays(self):
        self.game.join("phone", "Fox", True)
        self.game.join("tablet", None, False)
        self.game.merge("phone", "tablet")
        self.assertEqual((self.game.players, self.game.spectators), ({"tablet"}, set()))

    def test_a_merge_after_the_reveal_moves_the_round_too(self):
        g = self.game
        g.join("phone", "Fox", True)
        q = g.next_question(now=0)
        g.answer("phone", "Fox", q["answer"], now=1)
        g.close_round()
        g.merge("phone", "tablet")
        self.assertEqual((g.reveal["gains"], g.reveal["right"], g.reveal["fastest"]), ({"tablet": 2}, ["tablet"], "tablet"))
        g.merge("x", "x")
        g.merge(None, "tablet")
        self.assertEqual(g.scores, {"tablet": 2})


if __name__ == "__main__":
    unittest.main()

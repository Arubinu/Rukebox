"""The blind test played aloud: no phone needed, a thinking time, the answer said, the host's
taps to move on and to mark who found it, and the sounds that go with it."""
import os
import shutil
import tempfile
import unittest
import wave
from unittest import mock

import _path  # noqa: F401
import blind_test
import game_sounds
import name_voice
import speech
import web_server as ws


def tracks(n=8):
    return [{"path": "/m/%d.mp3" % i, "title": "Song %d" % i, "artist": "Artist %d" % (i % 3),
             "duration": 200.0} for i in range(n)]


class OralGameTest(unittest.TestCase):
    def test_no_wait_the_names_score_and_the_first_marked_scores_twice(self):
        game = blind_test.Game(tracks(), rounds=3, mode="oral", pace="host", names=["Ana", "Bo", "ana"], now=0)
        self.assertTrue(game.ready(now=0), "nobody to wait for")
        self.assertEqual(sorted(game.names.values()), ["Ana", "Bo"], "a name typed twice is one person")
        q = game.next_question(now=0)
        self.assertEqual(game.view(None)["choices"], [], "nothing to pick on a screen")
        game.think(5, now=1)
        self.assertEqual((game.view(None, now=2)["state"], game.view(None, now=2)["think_left"]), ("thinking", 4))
        self.assertEqual(game.mark("Ana", True), "game_not_asking", "not before the answer")
        game.close_round()
        view = game.view(None)
        self.assertEqual(view["answer_label"], blind_test.label(q))
        self.assertIsNone(game.mark("Bo", True))
        self.assertIsNone(game.mark("Ana", True))
        self.assertEqual(game.mark("Zoe", True), "not_found")
        self.assertEqual([(n["name"], n["marked"], n["first"]) for n in game.view(None)["oral_names"]],
                         [("Ana", True, False), ("Bo", True, True)])
        game.score_marks()
        game.score_marks()
        self.assertEqual({game.names[p]: pts for p, pts in game.scores.items()}, {"Bo": 2, "Ana": 1},
                         "scored once, the first marked twice")

    def test_without_names_it_is_only_listened_to(self):
        game = blind_test.Game(tracks(), rounds=3, mode="oral")
        self.assertEqual((game.players, game.podium()), (set(), []))


class AnswerAndSoundsTest(unittest.TestCase):
    def test_the_answer_names_the_song_and_its_artist(self):
        self.assertEqual(speech.game_parts("answer", [{"title": "Fly", "artist": "Hilary Duff"}], "fr"),
                         [{"text": "C'était Fly, de Hilary Duff."}])

    def test_sounds_are_made_once_and_last_the_thinking_time(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.assertIsNone(game_sounds.think_sound("", 5, folder))
        self.assertIsNone(game_sounds.think_sound("tick", 0, folder))
        path = game_sounds.think_sound("tick", 3, folder)
        with wave.open(path, "rb") as f:
            self.assertAlmostEqual(f.getnframes() / f.getframerate(), 3.0, delta=0.05)
        self.assertEqual(game_sounds.answer_sound("gong", folder), os.path.join(folder, "answer-gong.wav"))
        self.assertIsNone(game_sounds.answer_sound("trumpet", folder))
        for kind in game_sounds.THINK:
            self.assertTrue(game_sounds.think_sound(kind, 2, folder), kind)
        self.assertTrue(game_sounds.think_sound("rise", 2, folder).endswith("think-drumroll-2.wav"),
                        "a sound no longer offered becomes the one that replaced it")


class OralRouteTest(unittest.TestCase):
    def setUp(self):
        ws._game = None
        self.addCleanup(setattr, ws, "_game", None)
        self.addCleanup(mock.patch.stopall)
        patch = mock.patch.object
        patch(ws, "_require_auth", return_value=None).start()
        patch(ws, "_bans_active", return_value=False).start()
        patch(ws, "_game_player", return_value=("me", "Me")).start()
        patch(ws, "_get_library", return_value=mock.Mock(quiz_tracks=lambda: tracks(12))).start()
        self.thread = patch(ws.threading, "Thread").start()
        patch(ws, "stats").start()
        self.client = ws.app.test_client()

    def test_started_aloud_with_names_then_marked_and_moved_on(self):
        r = self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20, "mode": "oral", "pace": "host",
                                                      "names": ["Ana", " Bo ", ""]})
        self.assertTrue(r.get_json()["ok"])
        game = ws._game
        self.assertEqual((game.mode, game.pace, sorted(game.names.values())), ("oral", "host", ["Ana", "Bo"]))
        self.assertEqual(self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20, "mode": "oral"})
                         .status_code, 409, "a game is already on")
        game.next_question()
        self.client.post("/api/game/cut")
        self.assertTrue(game.cut)
        game.close_round()
        data = self.client.post("/api/game/mark", json={"name": "Ana", "on": True}).get_json()["data"]
        self.assertTrue(data["oral_names"][0]["marked"])
        self.client.post("/api/game/next")
        self.assertTrue(game.advance)

    def test_a_bad_mode_is_refused(self):
        r = self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20, "mode": "radio"})
        self.assertEqual(r.status_code, 400)


class SaidAtTheEndOfARoundTest(unittest.TestCase):
    def said(self, mode, **settings):
        game = blind_test.Game(tracks(), rounds=3, mode=mode, names=["Ana"])
        question = game.next_question()
        game.close_round()
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        values = dict(ws.cfg(), GAME_VOICE_ANSWER=False, GAME_VOICE_WINNERS=False, GAME_ANSWER_SOUND="",
                      STATE_DIR=folder)
        values.update(settings)
        with mock.patch.object(ws, "cfg", return_value=values), \
                mock.patch.object(ws, "_game_speak", return_value=0) as speak:
            ws._round_speech(game, question)
        return speak.call_args[0][0]

    def test_aloud_the_answer_is_always_said_after_its_sound(self):
        parts = self.said("oral", GAME_ANSWER_SOUND="ding")
        self.assertTrue(parts[0]["sound"].endswith("answer-ding.wav"))
        self.assertIn("Song", parts[1]["text"])

    def test_a_sound_that_cannot_be_made_leaves_the_answer_alone(self):
        with mock.patch.object(game_sounds, "answer_sound", side_effect=PermissionError("read-only")):
            parts = self.said("oral", GAME_ANSWER_SOUND="ding")
        self.assertEqual(len(parts), 1)
        self.assertIn("Song", parts[0]["text"])

    def test_on_the_phones_only_when_asked(self):
        self.assertEqual(self.said("phones"), [])
        self.assertEqual(len(self.said("phones", GAME_VOICE_ANSWER=True)), 1)


class WarmedSpeechTest(unittest.TestCase):
    """The sentences that carry no name are made while the clip plays, not once it is over."""

    def values(self, **settings):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        return dict(ws.cfg(), STATE_DIR=folder, **settings)

    def test_the_answer_carries_no_name(self):
        c = self.values()
        question = {"title": "Song", "artist": "Artist", "kind": "song"}
        groups = ws._speech_groups(speech.game_parts("answer", [question], c.get("SPEECH_LANGUAGE")), c)
        self.assertEqual([named for _kind, _value, named in groups], [False])

    def test_who_found_it_is_never_made_ahead(self):
        c = self.values()
        parts = [{"text": "Right answer from"}, {"name": "Ana", "person": None}, {"text": "."}]
        groups = ws._speech_groups(parts, c)
        self.assertEqual([kind for kind, _value, _named in groups], ["text"])
        self.assertTrue(groups[0][2], "a name cannot be guessed before the round ends")

    def test_a_sound_keeps_the_sentences_apart(self):
        c = self.values()
        parts = [{"text": "Nobody found it."}, {"sound": "/s/ding.wav"}, {"text": "That was Song."}]
        groups = ws._speech_groups(parts, c)
        self.assertEqual([(kind, named) for kind, _value, named in groups],
                         [("text", False), ("voice", False), ("text", False)])

    def test_the_made_ahead_sentences_go_through_the_cache_and_are_released(self):
        ws._speech_warming.update(["One.", "Two."])
        warmed = []
        with mock.patch.object(ws.speech, "warm",
                               side_effect=lambda text, lang, voice: warmed.append((text, lang))):
            ws._warm_texts(["One.", "Two."], "fr", "")
        self.assertEqual(warmed, [("One.", "fr"), ("Two.", "fr")])
        self.assertEqual(ws._speech_warming, set(), "nothing stays in flight")

    def test_a_name_with_its_own_recording_leaves_only_the_words_before_it(self):
        c = self.values()
        os.makedirs(name_voice.folder(c["STATE_DIR"]))
        with open(name_voice.path(c["STATE_DIR"], "p1"), "wb") as handle:
            handle.write(b"x")
        parts = [{"text": "Right answer from"}, {"name": "Fox", "person": "p1"}, {"text": "and"},
                 {"name": "Owl", "person": "p9"}, {"text": "."}]
        groups = ws._speech_groups(parts, c)
        self.assertEqual([value for kind, value, named in groups if kind == "text" and not named],
                         ["Right answer from"], "the rest of the sentence names a player")

    def test_a_sentence_that_cannot_be_made_releases_the_guard(self):
        ws._speech_warming.update(["One."])
        with mock.patch.object(ws.speech, "warm", side_effect=OSError("no room")):
            ws._warm_texts(["One."], "fr", "")
        self.assertEqual(ws._speech_warming, set(), "a failure never blocks that sentence for good")

    def inline(self):
        """A thread that runs its work at once, so the test sees what was made."""

        class Inline:
            def __init__(self, target=None, args=(), daemon=None):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        return mock.Mock(Thread=Inline)

    def made_ahead(self, parts, names_known=False, **settings):
        """What speech.warm is asked for, with a thread that runs now."""
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(**settings)), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_speech(parts, names_known=names_known)
        return collected

    def test_a_known_name_is_made_ahead_only_when_it_is_asked_for(self):
        parts = [{"text": "Right answer from"}, {"name": "Ana", "person": None}, {"text": "."}]
        self.assertEqual(self.made_ahead(parts), [], "a name is not guessed")
        self.assertEqual(self.made_ahead(parts, names_known=True), ["Right answer from Ana."])

    def test_the_glue_and_each_player_are_made_before_the_first_round(self):
        values = dict(GAME_VOICE_WINNERS=True, SPEECH_LANGUAGE="fr")
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        game.join("p2", "Bo", True)
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(**values)), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_game_speech(game)
        self.assertIn(speech.game_fixed_sentences("fr")[0], collected, "the question before the clip")
        self.assertIn(speech.GAME_WORDS["nobody"]["fr"], collected)
        self.assertEqual(sorted(text for text in collected if text.endswith(("Ana.", "Bo."))),
                         ["Bonne réponse de Ana.", "Bonne réponse de Bo."])

    def test_the_whole_game_is_made_by_one_voice_at_a_time(self):
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        started = []

        class Inline:
            def __init__(self, target=None, args=(), daemon=None):
                started.append(target)
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with mock.patch.object(ws, "cfg", return_value=self.values(GAME_VOICE_WINNERS=True,
                                                                  SPEECH_LANGUAGE="fr")), \
                mock.patch.object(ws, "threading", mock.Mock(Thread=Inline)), \
                mock.patch.object(ws.speech, "warm", return_value=True):
            ws._warm_game_speech(game)
        self.assertEqual(len(started), 1, "one thread, so one model loaded at a time")

    def test_a_silent_game_is_not_made_ahead_at_all(self):
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        made = []
        with mock.patch.object(ws, "cfg", return_value=self.values(GAME_VOICE_ANSWER=False,
                                                                  GAME_VOICE_WINNERS=False,
                                                                  SPEECH_LANGUAGE="fr")), \
                mock.patch.object(ws, "_start_warming", side_effect=lambda texts, c=None: made.append(texts)):
            ws._warm_game_speech(game)
        self.assertEqual(made, [], "a game that says nothing costs nothing")

    def test_without_who_found_it_no_player_is_made_ahead(self):
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(GAME_VOICE_WINNERS=False,
                                                                  GAME_VOICE_ANSWER=True,
                                                                  SPEECH_LANGUAGE="fr")), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_game_speech(game)
        self.assertIn(speech.GAME_WORDS["nobody"]["fr"], collected)
        self.assertEqual([text for text in collected if text.endswith("Ana.")], [])

    def test_a_spectator_is_never_made_ahead(self):
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        game.join("p2", "Bo", False)
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(GAME_VOICE_WINNERS=True,
                                                                  SPEECH_LANGUAGE="fr")), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_game_speech(game)
        self.assertEqual([text for text in collected if text.endswith("Bo.")], [],
                         "a spectator never wins a round")

    def test_one_sentence_is_made_once_however_many_players_share_the_name(self):
        game = blind_test.Game(tracks(), rounds=3, mode="phones")
        game.join("p1", "Ana", True)
        game.join("p2", "Ana", True)
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(GAME_VOICE_WINNERS=True,
                                                                  SPEECH_LANGUAGE="fr")), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_game_speech(game)
        self.assertEqual(collected.count("Bonne réponse de Ana."), 1)

    def test_a_sentence_already_on_its_way_is_not_asked_again(self):
        started = []
        thread = mock.Mock(return_value=mock.Mock(start=lambda: started.append(1)))
        with mock.patch.object(ws, "cfg", return_value=self.values()), \
                mock.patch.object(ws, "threading", mock.Mock(Thread=thread)), \
                mock.patch.object(ws.speech, "warm", return_value=True):
            self.addCleanup(ws._speech_warming.clear)
            ws._warm_speech([{"text": "One."}])
            ws._warm_speech([{"text": "One."}])
            self.assertEqual(ws._speech_warming, {"One."})
        self.assertEqual(started, [1], "one voice for one sentence")

    def test_the_round_makes_the_answer_only_when_it_will_be_said(self):
        question = {"title": "Song", "artist": "Artist", "kind": "song"}
        phones = blind_test.Game(tracks(), rounds=3, mode="phones")
        oral = blind_test.Game(tracks(), rounds=3, mode="oral", names=["Ana"])
        self.assertEqual(self.made_for_the_round(phones, question, GAME_VOICE_ANSWER=False), [])
        self.assertEqual(len(self.made_for_the_round(phones, question, GAME_VOICE_ANSWER=True)), 1)
        self.assertEqual(len(self.made_for_the_round(oral, question, GAME_VOICE_ANSWER=False)), 1,
                         "a game aloud always says the answer")

    def made_for_the_round(self, game, question, **settings):
        collected = []
        with mock.patch.object(ws, "cfg", return_value=self.values(**settings)), \
                mock.patch.object(ws, "threading", self.inline()), \
                mock.patch.object(ws.speech, "warm",
                                  side_effect=lambda text, lang, voice: collected.append(text)):
            ws._warm_round_speech(game, question)
        return collected


class HostTapsTest(unittest.TestCase):
    def test_a_click_or_the_page_moves_the_game_on(self):
        game = blind_test.Game(tracks(), rounds=3, mode="oral", pace="host")
        counts = iter([0, 0, 1])
        with mock.patch.object(ws, "_game_taps", side_effect=lambda: next(counts)):
            taps = ws._HostTaps(game)
            game.advance = True
            self.assertTrue(taps.fresh("advance"), "the page's button")
            self.assertFalse(game.advance)
            taps.polled = 0
            self.assertFalse(taps.fresh("advance"))
            taps.polled = 0
            self.assertTrue(taps.fresh("advance"), "a button's click")
        auto = blind_test.Game(tracks(), rounds=3, mode="oral", pace="auto")
        with mock.patch.object(ws, "_game_taps", return_value=5):
            self.assertFalse(ws._HostTaps(auto).fresh("advance"), "automatic: nobody waits for a tap")


if __name__ == "__main__":
    unittest.main()

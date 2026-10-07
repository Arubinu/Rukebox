"""What the radio says out loud: the sentences in six languages, the program
that says them, and where the daemon uses them (a click, an announcement,
the warning before the cutoff)."""
import os
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import speech
from test_empty_library import DaemonCase


class SentenceTest(unittest.TestCase):
    def test_the_time(self):
        when = datetime(2026, 10, 4, 7, 12)
        self.assertEqual(speech.sentence("time", when, "fr"), "Il est 7 heures 12.")
        self.assertEqual(speech.sentence("time", when, "en"), "It is 7:12.")
        self.assertEqual(speech.sentence("time", when, "de"), "Es ist 7 Uhr 12.")
        self.assertEqual(speech.sentence("time", when, "es"), "Son las 7 y 12.")
        self.assertEqual(speech.sentence("time", when, "it"), "Sono le 7 e 12.")
        self.assertEqual(speech.sentence("time", when, "nl"), "Het is 7 uur 12.")

    def test_the_round_hours_read_naturally(self):
        self.assertEqual(speech.sentence("time", datetime(2026, 1, 1, 12, 0), "fr"), "Il est midi.")
        self.assertEqual(speech.sentence("time", datetime(2026, 1, 1, 0, 5), "fr"), "Il est minuit 5.")
        self.assertEqual(speech.sentence("time", datetime(2026, 1, 1, 1, 0), "fr"), "Il est 1 heure.")
        self.assertEqual(speech.sentence("time", datetime(2026, 1, 1, 1, 0), "it"), "È l'una.")
        self.assertEqual(speech.sentence("time", datetime(2026, 1, 1, 9, 0), "en"), "It is 9 o'clock.")

    def test_the_date_follows_the_time(self):
        when = datetime(2026, 10, 1, 7, 0)
        self.assertEqual(speech.sentence("time_date", when, "fr"),
                         "Il est 7 heures. Nous sommes jeudi 1er octobre.")
        self.assertIn("Thursday, October 1", speech.sentence("time_date", when, "en"))

    def test_the_cutoff_and_the_unknown(self):
        when = datetime(2026, 10, 4, 6, 50)
        self.assertEqual(speech.sentence("cutoff", when, "fr", minutes=10), "La radio s'arrête dans 10 minutes.")
        self.assertEqual(speech.sentence("cutoff", when, "en", minutes=1), "The radio stops in 1 minute.")
        self.assertEqual(speech.sentence("none", when, "fr"), "")
        self.assertEqual(speech.language("xx"), "en", "an unknown language falls back to English")


class RenderTest(unittest.TestCase):
    def setUp(self):
        import shutil as shutil_mod
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil_mod.rmtree, self.dir, ignore_errors=True)
        # What has been said once is kept under the state root: a test's own, so
        # a sentence another test cached is never found here.
        state = mock.patch.object(speech.paths, "state_dir", lambda: self.dir)
        state.start()
        self.addCleanup(state.stop)

    def install_piper(self):
        """Piper's program and the French model, where the project looks."""
        voice = speech.default_voice("fr")
        binary, model, config = speech.piper_files(voice)
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        for path in (binary, model, config):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x")
        os.chmod(binary, 0o755)
        return voice

    def run_with(self, installed, lang, fails=(), text="Il est 7 heures.", voice=None):
        calls = []

        def written_path(command):
            for flag in ("-w", "--output_file", "-f"):
                if flag in command:
                    return command[command.index(flag) + 1]
            return None

        def run(command, **kwargs):
            calls.append(command[0])
            ok = command[0] not in fails
            if ok and written_path(command):
                with open(written_path(command), "wb") as handle:
                    handle.write(b"\0" * 100)
            return mock.Mock(returncode=0 if ok else 1, stderr=b"")

        path = os.path.join(self.dir, "out.wav")
        with mock.patch.object(speech.shutil, "which",
                               lambda name: name if name in installed else None), \
                mock.patch.object(speech.subprocess, "run", run):
            done = speech.render(text, lang, path, voice)
        return done, calls

    def test_pico_first_espeak_for_dutch(self):
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "fr"), (True, ["pico2wave"]))
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "nl", text="Het is 7 uur."),
                         (True, ["espeak-ng"]))

    def test_a_failure_falls_back_and_nothing_installed_says_no(self):
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "fr", fails={"pico2wave"}),
                         (True, ["pico2wave", "espeak-ng"]))
        self.assertEqual(self.run_with(set(), "fr", text="Rien du tout."), (False, []))

    def test_piper_speaks_first_when_its_model_is_there(self):
        voice = self.install_piper()
        done, calls = self.run_with({"piper", "pico2wave", "espeak-ng"}, "fr")
        self.assertTrue(done)
        self.assertEqual(calls, [speech.piper_files(voice)[0]],
                         "the natural voice, and not the light ones")

    def test_a_sentence_already_said_is_not_said_again(self):
        """Loading a model costs 3.5-4.4s on a Pi Zero 2 W, and the same
        sentence comes back every day at the same hour."""
        voice = self.install_piper()
        self.assertEqual(self.run_with({"piper"}, "fr"),
                         (True, [speech.piper_files(voice)[0]]))
        self.assertEqual(self.run_with({"piper"}, "fr"), (True, []),
                         "what was said once is reused")

    def test_the_kept_sentence_belongs_to_the_voice_and_the_language(self):
        voice = self.install_piper()
        said = speech._cache_path("Il est 7 heures.", "fr", None)
        self.assertIn(voice, speech.engine("fr") or "", "the cache is keyed by the voice")
        self.assertNotEqual(said, speech._cache_path("Il est 7 heures.", "nl", None))
        self.assertNotEqual(said, speech._cache_path("Il est 8 heures.", "fr", None))

    def test_every_spoken_language_has_a_natural_voice(self):
        for lang in speech.LANGUAGES:
            voice = speech.default_voice(lang)
            self.assertTrue(voice.startswith({"en": "en_", "fr": "fr_", "de": "de_",
                                              "es": "es_", "it": "it_", "nl": "nl_"}[lang]), voice)
            self.assertTrue(voice.endswith("-medium"), voice)

    def test_the_diagnostic_names_piper_when_a_model_is_there(self):
        with mock.patch.object(speech.shutil, "which", lambda name: None):
            self.assertEqual(speech.engines(), [])
            self.install_piper()
            self.assertEqual(speech.engines(), ["piper"],
                             "the program is not on the PATH: its model is what says so")


class DaemonSpeechTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.spoken = []

        def render(text, lang, path, choice=None):
            self.spoken.append(text)
            with open(path, "wb") as f:
                f.write(b"\0" * 100)
            return True

        mock.patch.object(speech, "render", render).start()
        self.cues = []
        self.daemon._play_cue_sound = lambda path, key=None: self.cues.append(path)
        self.daemon.cfg["SPEECH_LANGUAGE"] = "fr"

    def test_a_click_says_the_time_and_comes_back_to_the_song(self):
        self.daemon.mode = "music"
        self.daemon._last_music_track = "/music/a.mp3"
        self.daemon._position = 42.0
        self.daemon._perform_click_action("single", "flic", "time", "none")
        self.assertEqual(self.daemon.mode, "meme")
        self.assertTrue(self.spoken[0].startswith("Il est "))
        self.assertEqual(self.daemon._resume_track, ("/music/a.mp3", 42.0))
        self.assertTrue(self.daemon.mpv.files[-1].endswith(".wav"))

    def test_with_nothing_playing_it_is_a_cue(self):
        self.daemon.mode = "idle"
        self.assertIsNone(self.daemon._speak("time", "web"))
        self.assertEqual(len(self.cues), 1)
        self.assertEqual(self.daemon.mode, "idle")

    def test_an_announcement_says_the_time_before_its_sound(self):
        item = {"id": "morning", "name": "Morning", "folder": self.dir, "speech": "time_date"}
        with mock.patch.object(self.daemon, "_play_announce_queue") as queue, \
                mock.patch.object(self.daemon, "_next_announce_file", return_value=["/a/jingle.mp3"]):
            self.daemon._trigger_custom_announcement(item, on_demand=True)
        files = queue.call_args[0][1]
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].endswith(".wav") and "Nous sommes" in files[0])
        self.assertEqual(files[1], "/a/jingle.mp3")

    def test_the_cutoff_warning_is_said_once_at_its_minute(self):
        self.daemon.cfg.update({"CUTOFF_WARNING_MIN": 10, "CUTOFF_HOUR": 7, "CUTOFF_MINUTE": 0,
                                "CUTOFF_ENABLED": True})
        self.daemon.mode = "music"
        with mock.patch.object(self.daemon, "_speak") as speak:
            self.daemon._check_cutoff_warning(datetime(2026, 10, 4, 6, 49, 30))
            speak.assert_not_called()
            self.daemon._check_cutoff_warning(datetime(2026, 10, 4, 6, 50, 15))
            self.daemon._check_cutoff_warning(datetime(2026, 10, 4, 6, 50, 45))
            speak.assert_called_once_with("cutoff", "scheduler", minutes=10)

    def test_no_warning_without_music(self):
        self.daemon.cfg.update({"CUTOFF_WARNING_MIN": 10, "CUTOFF_HOUR": 7, "CUTOFF_MINUTE": 0})
        self.daemon.mode = "idle"
        with mock.patch.object(self.daemon, "_speak") as speak:
            self.daemon._check_cutoff_warning(datetime(2026, 10, 4, 6, 50))
        speak.assert_not_called()

    def test_spoken_files_stay_out_of_the_statistics(self):
        self.daemon.stats = mock.Mock()
        path = self.daemon._speech_file("time")
        self.daemon._begin_play("speech", path)
        self.daemon._end_play("eof")
        self.daemon.stats.record.assert_not_called()


if __name__ == "__main__":
    unittest.main()

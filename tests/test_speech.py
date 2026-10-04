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
    def run_with(self, installed, lang, fails=()):
        calls = []

        def run(command, **kwargs):
            calls.append(command[0])
            ok = command[0] not in fails
            if ok:
                with open(command[command.index("-w") + 1], "wb") as f:
                    f.write(b"\0" * 100)
            return mock.Mock(returncode=0 if ok else 1, stderr=b"")

        path = os.path.join(self.dir, "out.wav")
        with mock.patch.object(speech.shutil, "which", lambda name: name if name in installed else None), \
                mock.patch.object(speech.subprocess, "run", run):
            done = speech.render("Il est 7 heures.", lang, path)
        return done, calls

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()

    def test_pico_first_espeak_for_dutch(self):
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "fr"), (True, ["pico2wave"]))
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "nl"), (True, ["espeak-ng"]))

    def test_a_failure_falls_back_and_nothing_installed_says_no(self):
        self.assertEqual(self.run_with({"pico2wave", "espeak-ng"}, "fr", fails={"pico2wave"}),
                         (True, ["pico2wave", "espeak-ng"]))
        self.assertEqual(self.run_with(set(), "fr"), (False, []))


class DaemonSpeechTest(DaemonCase):
    def setUp(self):
        super().setUp()
        self.spoken = []

        def render(text, lang, path):
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

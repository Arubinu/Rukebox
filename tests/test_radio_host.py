"""The songs introduced out loud, dedications, reminders, taking turns, the
silence at the end of a song, the crossfade and the sound profiles."""
import math
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import _path  # noqa: F401
from config_and_scan import load_config
import library
import mpv_controller
import rukebox_daemon
import speech
from state import RadioState


class FakeMpv:
    def __init__(self):
        self.files = []
        self.calls = []
        self.volumes = []

    def set_volume(self, volume):
        self.volumes.append(volume)

    def loadfile(self, path):
        self.files.append(path)

    def seek_end(self):
        self.calls.append("seek_end")

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def touch(folder, *names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"x")


class SentencesTest(unittest.TestCase):
    def test_a_song_is_introduced_with_its_artist_when_known(self):
        self.assertEqual(speech.track_sentence("Paradise", "Coldplay", "fr"),
                         "Et maintenant : Paradise, de Coldplay.")
        self.assertEqual(speech.track_sentence("Paradise", None, "en"), "Up next: Paradise.")
        self.assertEqual(speech.track_sentence("", "Coldplay", "en"), "")

    def test_a_dedication_says_who_then_the_song(self):
        self.assertEqual(speech.dedication_sentence("Fly", "Renard bleu", "Pour Marie !", "fr"),
                         "Une dédicace de Renard bleu : Pour Marie ! Voici Fly.")
        self.assertEqual(speech.dedication_sentence("Fly", None, "hello", "en"),
                         "A dedication: hello. Here is Fly.")
        self.assertEqual(speech.dedication_sentence("Fly", "x", "   ", "en"), "")

    def test_free_text_is_one_short_line(self):
        self.assertEqual(speech.clean_text("a\n\tb\x00  c"), "a b c")
        self.assertEqual(len(speech.clean_text("x" * 500)), 160)
        self.assertEqual(speech.reminder_sentence("--sortir le gâteau", "fr"), "Rappel : --sortir le gâteau.")


class FairQueueTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = RadioState(os.path.join(self.dir, "state.json"))
        self.state.data["play_queue"] = ["q1", "q2"]

    def ask(self, path, person, fair=True):
        self.state.enqueue_request(path, person, fair)

    def test_people_take_turns(self):
        for path in ("a1", "a2", "a3"):
            self.ask(path, "anne")
        self.ask("b1", "bob")
        self.ask("c1", "cleo")
        self.ask("b2", "bob")
        self.assertEqual(self.state.requested_paths(), ["a1", "b1", "c1", "a2", "b2", "a3"])
        self.assertEqual(self.state.data["play_queue"][-2:], ["q1", "q2"])

    def test_without_turns_it_is_first_come_first_served(self):
        for path in ("a1", "a2"):
            self.ask(path, "anne", fair=False)
        self.ask("b1", "bob", fair=False)
        self.assertEqual(self.state.requested_paths(), ["a1", "a2", "b1"])

    def test_dedications_and_reminders_are_kept(self):
        self.state.set_dedication("a1", {"from": "Anne", "text": "hi"})
        self.assertEqual(self.state.pop_dedication("a1"), {"from": "Anne", "text": "hi"})
        self.assertIsNone(self.state.pop_dedication("a1"))
        rid = self.state.add_reminder(time.time() + 60, "cake")
        self.assertEqual([r["text"] for r in self.state.reminders()], ["cake"])
        self.assertTrue(self.state.remove_reminder(rid))
        self.assertEqual(self.state.reminders(), [])


class SilenceTest(unittest.TestCase):
    def test_a_silence_that_runs_to_the_end(self):
        report = "[silencedetect] silence_start: 27.5\n"
        self.assertEqual(library.trailing_silence(report, 200.0), 197.5)

    def test_a_silence_that_ends_before_the_end_is_not_trailing(self):
        report = "silence_start: 10\nsilence_end: 14 | silence_duration: 4\n"
        self.assertIsNone(library.trailing_silence(report, 200.0))
        report += "silence_start: 28.1\nsilence_end: 30 | silence_duration: 1.9\n"
        self.assertEqual(library.trailing_silence(report, 200.0), 198.1)

    def test_nothing_found(self):
        self.assertIsNone(library.trailing_silence("", 200.0))
        self.assertIsNone(library.trailing_silence("silence_start: 1", None))

    def test_the_profiles_come_before_the_compression(self):
        self.assertEqual(mpv_controller.audio_chain("off", "off"), "")
        chain = mpv_controller.audio_chain("bass", "soft")
        self.assertTrue(chain.startswith("bass=") and "acompressor" in chain)
        self.assertEqual(mpv_controller.audio_chain("nope", "off"), "")


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(mock.patch.stopall)
        self.music = os.path.join(self.dir, "music")
        touch(self.music, "a.mp3", "b.mp3", "c.mp3", "d.mp3")
        cfg = load_config()
        cfg.update({
            "MUSIC_DIR": self.music,
            "MUSIC_CACHE_FILE": os.path.join(self.dir, "music_cache.json"),
            "STATE_DIR": self.dir,
            "STATS_ENABLED": False,
            "STATS_DB_FILE": os.path.join(self.dir, "stats.db"),
            "LIBRARY_DB_FILE": os.path.join(self.dir, "library.db"),
            "ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
            "MUSIC_ORDER_MODE": "ordered",
            "FADE_DURATION_SEC": 0, "INTERACTIVE_FADE_DURATION_SEC": 0,
            "LONGPRESS_FADE_DURATION_SEC": 0, "PAUSE_FADE_SEC": 0, "START_FADE_SEC": 0,
            "SPEAKER_VOLUME_LINK": False, "SPEECH_LANGUAGE": "en",
        })
        mock.patch.object(rukebox_daemon, "audio_env", return_value={}).start()
        self.said = []

        def render(text, lang, path):
            self.said.append(text)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()
            return True
        mock.patch.object(speech, "render", side_effect=render).start()
        self.daemon = rukebox_daemon.RadioDaemon(cfg)
        self.daemon.mpv = FakeMpv()
        self.daemon._clock_ready = threading.Event()
        self.daemon.mode = "music"
        self.daemon._rebuild_queue(self.daemon._playable_tracks())

    def end_song(self):
        self.daemon._on_mpv_event({"event": "end-file", "reason": "eof"})

    def played(self):
        return [os.path.basename(f) for f in self.daemon.mpv.files]

    def test_every_second_song_is_introduced(self):
        self.daemon.cfg["DJ_ANNOUNCE_EVERY"] = 2
        self.daemon._play_next_track(user=True)
        self.end_song()
        self.assertEqual(self.said, [])
        self.end_song()
        self.assertEqual(self.said, ["Up next: c."])
        self.assertEqual(self.daemon.mode, "meme")
        self.end_song()
        self.assertEqual(self.played()[-1], "c.mp3")
        self.assertEqual(self.daemon.mode, "music")

    def test_a_next_asked_for_is_not_introduced(self):
        self.daemon.cfg["DJ_ANNOUNCE_EVERY"] = 1
        self.daemon._play_next_track(user=True)
        self.daemon._play_next_track(user=True)
        self.assertEqual(self.said, [])

    def test_a_dedication_is_said_before_its_song(self):
        self.daemon.cfg["DEDICATIONS_ENABLED"] = True
        self.daemon._play_next_track()
        target = os.path.join(self.music, "d.mp3")
        self.daemon.state.set_dedication(target, {"from": "Anne", "text": "For you"})
        self.daemon.state.enqueue_request(target, "anne", True)
        self.end_song()
        self.assertEqual(self.said, ["A dedication from Anne: For you. Here is d."])
        self.end_song()
        self.assertEqual(self.played()[-1], "d.mp3")

    def test_a_dedication_is_not_said_when_they_are_off(self):
        self.daemon.cfg["DEDICATIONS_ENABLED"] = False
        self.daemon._play_next_track()
        target = os.path.join(self.music, "b.mp3")
        self.daemon.state.set_dedication(target, {"from": "Anne", "text": "For you"})
        self.end_song()
        self.assertEqual(self.said, [])
        self.assertEqual(self.played()[-1], "b.mp3")
        self.assertEqual(self.daemon.state.dedications(), {})

    def test_the_silence_at_the_end_is_skipped_once(self):
        self.daemon.cfg["SKIP_TRAILING_SILENCE"] = True
        with mock.patch.object(self.daemon, "_tail_for", return_value=180.0):
            self.daemon._play_next_track()
        self.daemon._duration = 185.0
        for pos in (100.0, 179.9, 180.2, 181.0):
            self.daemon._on_mpv_event({"event": "property-change", "name": "time-pos", "data": pos})
        self.assertEqual(self.daemon.mpv.calls, ["seek_end"])

    def tail_players(self, ready=True):
        made = []

        class FakeTail:
            def __init__(self, command, socket_path):
                self.command, self.ready, self.started, self.stopped = command, ready, False, False
                self.handing, self.cued = False, []
                made.append(self)

            def cue(self, at):
                self.cued.append(at)
                return True

            def start(self):
                return True

            def play(self, volume):
                self.started = True
                return True

            def output(self):
                return "-"

            def stop(self):
                self.stopped = True
        self.daemon._tail_player_cls = FakeTail
        # The hand-over runs in the test's own thread, so its result is there at once.
        mock.patch.object(self.daemon, "_start_hand_over",
                          side_effect=lambda *a: self.daemon._hand_over(*a)).start()
        mock.patch.object(rukebox_daemon.time, "sleep").start()
        return made

    def move_to(self, *positions):
        for pos in positions:
            self.daemon._on_mpv_event({"event": "property-change", "name": "time-pos", "data": pos})

    def test_the_crossfade_hands_the_end_to_a_prepared_player(self):
        self.daemon.cfg["CROSSFADE_SEC"] = 4
        made = self.tail_players()
        self.daemon._play_next_track()
        self.daemon._duration = 200.0
        self.move_to(150.0)
        self.assertEqual(made, [])
        self.move_to(188.5)
        self.assertEqual(len(made), 1, "prepared ahead, paused")
        self.assertIn("--start=196.00", made[0].command)
        self.assertIn("--pause", made[0].command)
        # On the song's own clock: a fade reset to 0 made mpv drop every frame.
        self.assertTrue(any("afade=t=out:st=196.00:d=4.00:curve=qsin" in part for part in made[0].command))
        self.assertFalse(any("asetpts" in part for part in made[0].command))
        self.assertFalse(made[0].started)
        self.move_to(196.1)
        self.assertTrue(made[0].started)
        self.assertEqual(made[0].cued, [196.1 + self.daemon.XFADE_LEAD_SEC],
                         "taken over where the main player is, not at end - fade")
        self.assertEqual(self.daemon.mpv.calls, ["seek_end"])
        self.end_song()
        self.assertEqual(self.played()[-1], "b.mp3")
        self.assertFalse(made[0].stopped, "the end fades out over the next song")

    def test_the_next_song_rises_from_silence_even_with_the_speaker_volume_linked(self):
        self.daemon.cfg.update({"CROSSFADE_SEC": 4, "SPEAKER_VOLUME_LINK": True})
        self.tail_players()
        self.daemon._play_next_track()
        self.daemon._duration = 200.0
        self.move_to(188.5, 196.1)
        with mock.patch.object(self.daemon, "_glide_volume") as glide:
            self.daemon.mpv.volumes.clear()
            self.end_song()
        self.assertEqual(self.daemon.mpv.volumes, [0], "no full-volume moment before the rise")
        self.assertIs(glide.call_args.kwargs["curve"], rukebox_daemon._crossfade_in_curve)

    def test_the_two_fades_keep_the_loudness_even(self):
        for share in (0.0, 0.25, 0.5, 0.75, 1.0):
            incoming = rukebox_daemon._crossfade_in_curve(share) ** 3
            outgoing = math.sin(math.pi / 2 * (1 - share))
            self.assertAlmostEqual(incoming ** 2 + outgoing ** 2, 1.0, places=6)

    def test_a_player_not_ready_in_time_lets_the_song_play_out(self):
        self.daemon.cfg["CROSSFADE_SEC"] = 4
        made = self.tail_players(ready=False)
        self.daemon._play_next_track()
        self.daemon._duration = 200.0
        self.move_to(189.0, 196.5, 199.0)
        self.assertTrue(made[0].stopped)
        self.assertFalse(made[0].started)
        self.assertEqual(self.daemon.mpv.calls, [])

    def test_a_skip_drops_the_prepared_player(self):
        self.daemon.cfg["CROSSFADE_SEC"] = 4
        made = self.tail_players()
        self.daemon._play_next_track()
        self.daemon._duration = 200.0
        self.move_to(190.0)
        self.daemon._play_next_track(user=True)
        self.assertTrue(made[0].stopped)

    def test_no_crossfade_before_an_introduction(self):
        self.daemon.cfg.update({"CROSSFADE_SEC": 4, "DJ_ANNOUNCE_EVERY": 1})
        made = self.tail_players()
        self.daemon._play_next_track(user=True)
        self.daemon._duration = 200.0
        self.move_to(190.0, 197.0)
        self.assertEqual(made, [])

    def test_a_due_reminder_is_said_and_forgotten(self):
        self.daemon.mode = "idle"
        self.daemon.state.add_reminder(time.time() - 5, "the cake")
        self.daemon.state.add_reminder(time.time() - 3600, "missed")
        self.daemon.state.add_reminder(time.time() + 3600, "later")
        with mock.patch.object(self.daemon, "_play_cue_sound") as cue:
            self.daemon._check_reminders()
        self.assertEqual(self.said, ["Reminder: the cake."])
        self.assertTrue(cue.called)
        self.assertEqual([r["text"] for r in self.daemon.state.reminders()], ["later"])

    def test_a_reminder_keeps_the_music_under_it(self):
        self.daemon._play_next_track()
        self.daemon.state.add_reminder(time.time() - 1, "the cake")
        with mock.patch.object(self.daemon, "_play_ducked", return_value=True) as ducked:
            self.daemon._check_reminders()
        self.assertEqual(ducked.call_args.kwargs.get("under"), self.daemon.REMINDER_UNDER)


if __name__ == "__main__":
    unittest.main()

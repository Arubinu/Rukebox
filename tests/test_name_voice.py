"""A name said in its owner's voice: kept per person, sent by the person or by the owner,
and played by the blind test in place of the synthetic voice."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import name_voice
import web_server as ws


def fake_prepare(length):
    def prepare(source, target):
        with open(target, "wb") as f:
            f.write(b"RIFF")
        return length
    return prepare


class NameVoiceFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_an_id_never_leaves_the_folder(self):
        self.assertEqual(os.path.dirname(name_voice.path(self.dir, "../../etc/passwd")), name_voice.folder(self.dir))
        self.assertIsNone(name_voice.path(self.dir, "../.."))

    def test_linking_keeps_the_recording_and_unlinking_gives_it_to_both(self):
        os.makedirs(name_voice.folder(self.dir))
        with open(name_voice.path(self.dir, "a"), "wb") as f:
            f.write(b"x")
        self.assertTrue(name_voice.carry(self.dir, "a", "b"))
        self.assertEqual((name_voice.existing(self.dir, "a"), bool(name_voice.existing(self.dir, "b"))), (None, True))
        self.assertTrue(name_voice.carry(self.dir, "b", "c", keep=True))
        self.assertTrue(name_voice.existing(self.dir, "b") and name_voice.existing(self.dir, "c"))


class NameVoiceRouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(mock.patch.stopall)
        patch = mock.patch.object
        values = dict(ws.cfg(), STATE_DIR=self.dir)
        patch(ws, "cfg", return_value=values).start()
        patch(ws, "_require_auth", return_value=None).start()
        patch(ws, "_bans_active", return_value=False).start()
        self.me = {"id": "d1", "person": "p1", "name": "Fox"}
        patch(ws, "_this_device", side_effect=lambda box: self.me).start()
        box = mock.Mock(device_by_id=lambda i: {"id": i} if i == "d2" else None, person_id=lambda i: "p2")
        patch(ws, "_suggestion_box", return_value=box).start()
        self.client = ws.app.test_client()

    def send(self, url, length=2.0, kind="audio/webm"):
        with mock.patch.object(name_voice, "prepare", side_effect=fake_prepare(length)):
            return self.client.post(url, data=b"sound", content_type=kind)

    def test_a_person_records_their_own_name(self):
        self.assertTrue(self.send("/api/suggestions/name_voice").get_json()["ok"])
        self.assertTrue(name_voice.existing(self.dir, "p1"))
        heard = self.client.get("/api/suggestions/name_voice")
        heard.close()
        self.assertEqual(heard.status_code, 200)
        self.assertTrue(self.client.delete("/api/suggestions/name_voice").get_json()["ok"])
        self.assertIsNone(name_voice.existing(self.dir, "p1"))

    def test_refused_without_a_name_too_long_or_not_audio(self):
        self.assertEqual(self.send("/api/suggestions/name_voice", kind="text/plain").get_json()["error"], "voice_bad_type")
        self.assertEqual(self.send("/api/suggestions/name_voice", length=9).get_json()["error"], "name_voice_too_long")
        self.assertIsNone(name_voice.existing(self.dir, "p1"), "a refused recording keeps nothing")
        self.me = {"id": "d1", "person": "p1", "name": None}
        self.assertEqual(self.send("/api/suggestions/name_voice").get_json()["error"], "name_required")

    def test_the_owner_sends_one_for_someone(self):
        self.assertTrue(self.send("/api/devices/name_voice?device_id=d2", kind="audio/mpeg").get_json()["ok"])
        self.assertTrue(name_voice.existing(self.dir, "p2"))
        self.assertEqual(self.send("/api/devices/name_voice?device_id=nobody").status_code, 404)

    def test_the_game_says_a_recorded_name_and_synthesises_the_rest(self):
        os.makedirs(name_voice.folder(self.dir))
        with open(name_voice.path(self.dir, "p1"), "wb") as f:
            f.write(b"x")
        said, ran = [], []

        def render(text, lang, path, voice=None):
            said.append(text)
            with open(path, "wb") as f:
                f.write(b"x")
            return True

        def ffmpeg(args, **kw):
            ran.append(args)
            return mock.Mock(returncode=0)

        parts = [{"text": "Right answer from"}, {"name": "Fox", "person": "p1"}, {"text": "and"},
                 {"name": "Owl", "person": "p9"}, {"text": "."}]
        with mock.patch.object(ws.speech, "render", side_effect=render), \
                mock.patch.object(ws.subprocess, "run", side_effect=ffmpeg), \
                mock.patch.object(name_voice, "seconds", return_value=3.2):
            path, length = ws._game_speech(parts)
        self.assertEqual(said, ["Right answer from", "and Owl."])
        inputs = [ran[0][i + 1] for i, a in enumerate(ran[0]) if a == "-i"]
        self.assertEqual(inputs[1], name_voice.path(self.dir, "p1"), "Fox is said in Fox's own voice")
        self.assertEqual((os.path.dirname(path), length), (ws._game_speech_dir(), 3.2))


if __name__ == "__main__":
    unittest.main()

"""The multiroom output: snapserver's configuration, the pipe sink loaded once, the clients read
from its status, the output following the choice, and the volume routes."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import audio_output
import snapcast
import web_server as ws
from test_empty_library import DaemonCase

STATUS = {"server": {"groups": [{"clients": [
    {"id": "b", "connected": False, "host": {"name": "kitchen", "ip": "::ffff:192.168.1.9"},
     "config": {"name": "", "volume": {"percent": 40, "muted": True}}},
    {"id": "a", "connected": True, "host": {"name": "salon-pi", "ip": "192.168.1.8"},
     "config": {"name": "Salon", "volume": {"percent": 75, "muted": False}}},
]}]}}


class SnapcastTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_one_stream_read_from_the_pipe_the_sink_writes(self):
        text = snapcast.config_text(self.dir, "flac")
        self.assertIn("source = pipe://%s?name=Rukebox&sampleformat=48000:16:2&mode=create"
                      % snapcast.fifo(self.dir), text)
        self.assertIn("codec = flac", text)
        self.assertIn("buffer = 1000", text)
        self.assertIn("codec = opus", snapcast.config_text(self.dir, "mp3"), "an unknown codec falls back")

    def test_the_sink_is_what_the_outputs_call_snapcast(self):
        self.assertEqual(snapcast.SINK, audio_output.SNAPCAST_SINK)
        self.assertEqual(audio_output.classify({"node.name": snapcast.SINK}), "snapcast")

    def test_the_sink_is_loaded_once(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            out = ("31\tmodule-pipe-sink\tfile=x sink_name=rukebox_snapcast sink_properties='%s'\n"
                   % snapcast.SINK_PROPERTIES) if len(calls) > 3 else ""
            return mock.Mock(stdout=out, returncode=0, stderr="")

        with mock.patch.object(snapcast.subprocess, "run", side_effect=run):
            self.assertTrue(snapcast.load_sink(self.dir, {}))
            self.assertTrue(snapcast.load_sink(self.dir, {}))
        loads = [c for c in calls if c[:2] == ["pactl", "load-module"]]
        self.assertEqual(len(loads), 1)
        self.assertIn("sink_name=rukebox_snapcast", loads[0])
        self.assertIn("file=%s" % snapcast.fifo(self.dir), loads[0])

    def test_a_sink_loaded_by_an_older_radio_is_loaded_again(self):
        old = mock.Mock(stdout="31\tmodule-pipe-sink\tfile=x sink_name=rukebox_snapcast\n", returncode=0, stderr="")
        with mock.patch.object(snapcast.subprocess, "run", return_value=old) as run:
            snapcast.load_sink(self.dir, {})
        commands = [c[0][0][:2] for c in run.call_args_list]
        self.assertIn(["pactl", "unload-module"], commands, "one that may fall asleep is replaced")
        self.assertIn(["pactl", "load-module"], commands)

    def test_clients_connected_first_with_their_volume(self):
        found = snapcast.clients(STATUS)
        self.assertEqual([(c["name"], c["connected"], c["volume"], c["muted"]) for c in found],
                         [("Salon", True, 75, False), ("kitchen", False, 40, True)])


class OutputFollowsTheChoiceTest(DaemonCase):
    def test_the_server_and_the_sink_exist_only_while_chosen(self):
        self.daemon.cfg.update(AUDIO_OUTPUT="snapcast", SNAPCAST_CODEC="flac")
        with mock.patch.object(snapcast, "available", return_value=True), \
                mock.patch.object(self.daemon._snapcast, "ensure") as ensure, \
                mock.patch.object(snapcast, "load_sink", return_value=True) as load, \
                mock.patch.object(snapcast, "unload_sink") as unload, \
                mock.patch.object(self.daemon._snapcast, "stop") as stop, \
                mock.patch.object(audio_output, "list_sinks", return_value=[]):
            self.daemon._apply_audio_output(force=True)
            self.assertEqual(ensure.call_args[0][1], "flac")
            load.assert_called_once()
            self.assertTrue(self.daemon._wired_output(), "losing the Bluetooth speaker no longer pauses it")
            self.daemon.cfg["AUDIO_OUTPUT"] = "jack"
            self.daemon._apply_audio_output(force=True)
            stop.assert_called_once()
            unload.assert_called_once()


class SnapcastRouteTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ws, "_require_auth", return_value=None).start()
        mock.patch.object(ws, "_bans_active", return_value=False).start()
        mock.patch.object(snapcast, "available", return_value=True).start()
        self.rpc = mock.patch.object(snapcast, "rpc", return_value=STATUS).start()
        self.client = ws.app.test_client()

    def test_the_clients_and_their_volume(self):
        data = self.client.get("/api/snapcast/clients").get_json()["data"]
        self.assertEqual((data["running"], len(data["clients"])), (True, 2))
        self.assertTrue(self.client.post("/api/snapcast/client", json={"id": "a", "volume": 140}).get_json()["ok"])
        self.assertEqual(self.rpc.call_args[0], ("Client.SetVolume", {"id": "a", "volume": {"percent": 100}}))
        self.client.post("/api/snapcast/client", json={"id": "a", "muted": True})
        self.assertEqual(self.rpc.call_args[0][1]["volume"], {"muted": True})
        self.client.delete("/api/snapcast/client", json={"id": "b"})
        self.assertEqual(self.rpc.call_args[0], ("Server.DeleteClient", {"id": "b"}))

    def test_a_server_that_does_not_answer(self):
        self.rpc.side_effect = OSError("refused")
        self.assertFalse(self.client.get("/api/snapcast/clients").get_json()["data"]["running"])
        answer = self.client.post("/api/snapcast/client", json={"id": "a", "volume": 10})
        self.assertEqual((answer.status_code, answer.get_json()["error"]), (503, "snapcast_unreachable"))


if __name__ == "__main__":
    unittest.main()

"""The Flic pairing state: a scan has to end, even when the wizard thread dies
with its connection to flicd. No hardware, no SDK: the state machine only."""
import io
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _path  # noqa: F401
import audio_diag
import web_server as ws


class FlicPairTest(unittest.TestCase):
    def setUp(self):
        self.client = ws.app.test_client()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ws, "_service_is_active", return_value=True).start()
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="idle", result=None, address=None, name=None,
                                 client=None, wizard=None, until=0.0)

    def status(self):
        return self.client.get("/api/flic/pair/status").get_json()["data"]

    def test_a_scan_that_overran_its_deadline_is_over(self):
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="searching", until=time.time() - 1)
        data = self.status()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["result"], "WizardFailedTimeout")

    def test_a_scan_in_time_is_still_running(self):
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="searching", until=time.time() + 60)
        self.assertEqual(self.status()["state"], "searching")

    def test_a_second_attempt_is_refused_while_one_runs(self):
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="searching", until=time.time() + 60)
        r = self.client.post("/api/flic/pair/start")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "flic_pairing_running")

    def test_an_overrun_lets_the_next_attempt_start(self):
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="found", until=time.time() - 1)
        # The real wizard thread ends at once without the SDK: the state would read "done" or not by luck.
        mock.patch.object(ws, "_flic_pair_run").start()
        r = self.client.post("/api/flic/pair/start")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.status()["state"], "searching")

    def test_cancelling_ends_it_at_once(self):
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="private", until=time.time() + 60)
        self.client.post("/api/flic/pair/cancel")
        data = self.status()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["result"], "WizardCancelledByUser")

    def test_the_sdk_missing_does_not_leave_a_scan_running(self):
        # flicd is up but the SDK is not importable: the thread gives up, and
        # the state has to say so instead of waiting for ever.
        with mock.patch.object(ws, "_fliclib", side_effect=ImportError("no sdk")):
            self.client.post("/api/flic/pair/start")
            for _ in range(50):
                if self.status()["state"] == "done":
                    break
                time.sleep(0.02)
        self.assertEqual(self.status()["state"], "done")


class FlicWakeTest(unittest.TestCase):
    """flicd sleeps while no button is paired: asking to pair one wakes it, the pairing over lets it sleep."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(mock.patch.stopall)
        values = dict(ws.cfg(), STATE_DIR=self.dir)
        mock.patch.object(ws, "cfg", return_value=values).start()
        mock.patch.object(ws, "_service_is_active", return_value=False).start()
        mock.patch.object(ws, "_flic_availability", return_value=(True, None)).start()
        mock.patch.object(ws, "_flic_pair_run").start()
        self.started = []
        mock.patch.object(ws.system_actions, "service_action",
                          side_effect=lambda verb, unit: self.started.append((verb, unit)) or True).start()
        with ws._flic_pair_lock:
            ws._flic_pair.update(state="idle", result=None, until=0.0)
        self.client = ws.app.test_client()

    def test_pairing_wakes_a_sleeping_flicd(self):
        mock.patch.object(ws, "_flic_enabled", return_value=True).start()
        mock.patch.object(ws, "_flic_buttons", return_value=[]).start()
        self.assertEqual(self.client.post("/api/flic/pair/start").status_code, 200)
        self.assertEqual(self.started, [("start", "flicd"), ("start", "flic-bridge")])
        self.assertTrue(audio_diag.flic_pairing(self.dir), "the daemon leaves it awake meanwhile")

    def test_a_flic_turned_off_is_not_woken(self):
        mock.patch.object(ws, "_flic_enabled", return_value=False).start()
        answer = self.client.post("/api/flic/pair/start")
        self.assertEqual((answer.status_code, answer.get_json()["error"]), (400, "flicd_not_running"))
        self.assertEqual(self.started, [])

    def test_the_status_lists_the_buttons_of_a_sleeping_flicd(self):
        mock.patch.object(ws, "_flic_enabled", return_value=True).start()
        mock.patch.object(ws, "_bt_controllers", return_value=[{"address": "B8:27:EB:D7:FC:3A", "name": "hci0", "bus": "uart"}]).start()
        data = self.client.get("/api/flic/status").get_json()["data"]
        self.assertEqual((data["buttons"], data["idle"]), ([], True))


class FlicTransferPauseTest(unittest.TestCase):
    """A transfer asks flicd to rest, only where it shares its radio with the Wi-Fi."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(mock.patch.stopall)
        values = dict(ws.cfg(), STATE_DIR=self.dir)
        mock.patch.object(ws, "cfg", return_value=values).start()
        mock.patch.object(ws, "_flic_enabled", return_value=True).start()
        self.told = []
        mock.patch.object(ws, "notify_daemon", side_effect=lambda cmd, **kw: self.told.append(cmd)).start()
        ws._flic_shared.update(at=float("-inf"), shared=False)

    def controller(self, bus):
        mock.patch.object(ws, "_bt_controllers",
                          return_value=[{"address": "B8:27:EB:D7:FC:3A", "name": "hci0", "bus": bus}]).start()

    def test_a_flic_on_the_built_in_chip_rests_during_a_transfer(self):
        self.controller("uart")
        ws._pause_flic_for_transfer()
        ws._pause_flic_for_transfer()
        self.assertTrue(audio_diag.flic_paused(self.dir))
        self.assertEqual(self.told, ["speaker_check"], "the daemon is told once, when it starts")

    def test_a_flic_on_a_dongle_is_left_alone(self):
        self.controller("usb")
        ws._pause_flic_for_transfer()
        self.assertFalse(audio_diag.flic_paused(self.dir))
        self.assertEqual(self.told, [])

    def test_a_big_upload_pauses_it(self):
        self.controller("uart")
        client = ws.app.test_client()
        client.post("/api/music/upload", data={"path": "a.mp3", "file": (io.BytesIO(b"x" * 300000), "a.mp3")},
                    content_type="multipart/form-data")
        self.assertTrue(audio_diag.flic_paused(self.dir))


if __name__ == "__main__":
    unittest.main()


class FlicUpdatePauseTest(unittest.TestCase):
    """An update sent over SSH: the push scripts call pause_flic_for_update() on the radio."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_waiting_button_rests_for_the_update_and_comes_back_after(self):
        with mock.patch.object(audio_diag.control_client, "send_control_command") as nudge:
            self.assertEqual(audio_diag.pause_flic_for_update(900, self.dir, "sock"), "paused")
            self.assertTrue(audio_diag.flic_paused(self.dir))
            self.assertEqual(nudge.call_args[0][1], "speaker_check", "the daemon stops flicd at once")
            self.assertEqual(audio_diag.pause_flic_for_update(0, self.dir, "sock"), "released")
        self.assertFalse(audio_diag.flic_paused(self.dir))

    def test_a_connected_button_is_left_alone(self):
        audio_diag.set_flic_linked(self.dir, True)
        with mock.patch.object(audio_diag.control_client, "send_control_command") as nudge:
            self.assertEqual(audio_diag.pause_flic_for_update(900, self.dir, "sock"), "connected")
        self.assertFalse(audio_diag.flic_paused(self.dir))
        nudge.assert_not_called()

    def test_a_daemon_that_does_not_answer_changes_nothing(self):
        with mock.patch.object(audio_diag.control_client, "send_control_command", side_effect=OSError):
            self.assertEqual(audio_diag.pause_flic_for_update(900, self.dir, "sock"), "paused")

    def test_an_update_from_the_page_rests_the_button_and_the_updater_lets_it_go(self):
        log_path = tempfile.mktemp(dir=self.dir)
        with mock.patch.object(ws, "UPDATE_LOG", log_path), \
                mock.patch.object(ws, "_flic_on_shared_radio", return_value=True), \
                mock.patch.object(ws.audio_diag, "pause_flic_for_update") as pause, \
                mock.patch.object(ws.subprocess, "Popen") as popen, ws.app.app_context():
            ws._start_update("--from-release")
        self.assertEqual(pause.call_args[0][0], 900, "rested for the download")
        popen.assert_called_once()
        script = _path.read("scripts", "update.sh")
        clear = next(line for line in script.splitlines() if line.startswith("clear_updating()"))
        self.assertIn("flic-pause.json", clear, "the updater ends the pause whatever the outcome")
        self.assertIn("trap 'rm -f \"$0\"; clear_updating' EXIT", script)

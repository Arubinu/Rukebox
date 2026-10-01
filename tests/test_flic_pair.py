"""The Flic pairing state: a scan has to end, even when the wizard thread dies
with its connection to flicd. No hardware, no SDK: the state machine only."""
import time
import unittest
from unittest import mock

import _path  # noqa: F401
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


if __name__ == "__main__":
    unittest.main()

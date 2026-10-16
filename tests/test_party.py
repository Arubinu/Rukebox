"""The party: a schedule with no time of its own, started from the player for a
while, whose settings hold until it ends - by its timer, by hand or with the cutoff."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import schedules
import web_server as ws


class PartyRulesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_party_has_no_time_and_keeps_what_a_start_says(self):
        clean = schedules.validate({"name": "Party", "party": True, "start": "07:00", "date": "2026-10-05",
                                    "say": "Bonne soirée", "guests": True})
        self.assertEqual((clean["start"], clean["stop"], clean["date"], clean["days"]), (None, None, None, []))
        self.assertEqual((clean["say"], clean["guests"]), ("Bonne soirée", True))
        self.assertNotIn("party", schedules.validate({"name": "Morning", "start": "07:00"}))
        self.assertIsNone(schedules.window(clean, datetime(2026, 10, 5, 7, 0)), "never runs by the clock")

    def test_only_one_party(self):
        path = _path.document(self.dir)
        first = schedules.add({"name": "Party", "party": True}, path)
        with self.assertRaisesRegex(ValueError, "schedule_party_exists"):
            schedules.add({"name": "Other", "party": True}, path)
        schedules.update(first["id"], {"name": "Party!"}, path)

    def test_a_party_runs_until_its_end(self):
        self.assertIsNone(schedules.party_state(self.dir))
        started = schedules.start_party(self.dir, 3, guests=True, now=1000)
        self.assertEqual((started["until"], started["open"]), (1000 + 3 * 3600, False))
        self.assertTrue(schedules.party_state(self.dir, now=2000)["guests"])
        self.assertIsNone(schedules.party_state(self.dir, now=1000 + 3 * 3600))
        self.assertEqual(schedules.start_party(self.dir, 0, False, now=1000)["until"], 1000 + schedules.PARTY_MAX_SEC)
        self.assertTrue(schedules.end_party(self.dir))
        self.assertIsNone(schedules.party_state(self.dir, now=2000))


class PartyRouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(mock.patch.stopall)
        values = dict(ws.cfg(), STATE_DIR=self.dir, GUEST_MODE_ENABLED=False)
        mock.patch.object(ws, "cfg", return_value=values).start()
        mock.patch.object(ws, "_require_auth", return_value=None).start()
        mock.patch.object(ws, "notify_daemon").start()
        self.items = []
        mock.patch.object(ws.schedules, "load", side_effect=lambda: self.items).start()
        self.client = ws.app.test_client()

    def test_started_for_a_while_it_opens_to_guests_then_ends(self):
        self.assertEqual(self.client.post("/api/party", json={"hours": 3}).get_json()["error"], "party_not_set")
        self.items = [{"id": "party", "name": "Party", "party": True, "guests": True}]
        self.assertEqual(self.client.post("/api/party", json={"hours": 99}).status_code, 400)
        self.assertFalse(ws._guest_access())
        self.assertTrue(self.client.post("/api/party", json={"hours": 3}).get_json()["ok"])
        self.assertTrue(ws._guest_access(), "a party open to guests opens the interface")
        self.assertTrue(self.client.delete("/api/party").get_json()["ok"])
        self.assertFalse(ws._guest_access())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "party.json")))


if __name__ == "__main__":
    unittest.main()

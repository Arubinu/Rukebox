"""Who asks the most (statistics.by_person): a request counts its asker's name
only while the owner keeps it, and the list is shown only then."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import rukebox_daemon
from stats import StatsRecorder

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


class AskerTest(unittest.TestCase):
    def daemon(self, on):
        daemon = rukebox_daemon.RadioDaemon.__new__(rukebox_daemon.RadioDaemon)
        daemon.cfg = {"STATS_BY_PERSON": on}
        return daemon

    def test_the_name_is_kept_only_when_asked_for(self):
        self.assertEqual(self.daemon(True)._asker({"person_name": "Renard bleu"}), ("person", "Renard bleu"))
        self.assertIsNone(self.daemon(False)._asker({"person_name": "Renard bleu"}))
        self.assertIsNone(self.daemon(True)._asker({}), "a request with no name counts nobody")

    def test_the_names_are_a_top_list_like_the_songs(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        recorder = StatsRecorder(os.path.join(folder, "stats.db"))
        recorder.open_session(clock_ok=True)
        for name in ("Renard bleu", "Renard bleu", "Chat roux"):
            recorder.record("track_queued", label="a.mp3", item=("person", name))
        top = recorder.summary()["top"]["person"]
        self.assertEqual([(row["name"], row["count"]) for row in top], [("Renard bleu", 2), ("Chat roux", 1)])


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class SummaryTest(unittest.TestCase):
    def test_the_list_is_left_out_while_the_setting_is_off(self):
        summary = {"top": {"music": [], "person": [{"name": "Renard bleu", "count": 2}]}}
        client = ws.app.test_client()
        for on, shown in ((False, False), (True, True)):
            with mock.patch.object(ws.stats, "summary", return_value={"top": dict(summary["top"])}), \
                    mock.patch.object(ws, "cfg", return_value=dict(ws.cfg(), STATS_BY_PERSON=on)), \
                    mock.patch.object(ws, "_get_library"):
                top = client.get("/api/journal/summary").get_json()["data"]["top"]
            self.assertEqual("person" in top, shown, on)


if __name__ == "__main__":
    unittest.main()

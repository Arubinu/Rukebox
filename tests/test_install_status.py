"""The page shown while the first boot installs everything
(src/install_status.py), and the Bluetooth clock's reading (src/bt_clock.py)."""
import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

import _path
import bt_clock
import install_status


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.status = os.path.join(self.dir, "status.json")
        self.log = os.path.join(self.dir, "install.log")

    def test_before_anything_is_written_it_is_the_first_step(self):
        data = install_status.read_status(self.status, self.log)
        self.assertEqual((data["step"], data["total"], data["error"]), (0, 6, None))
        self.assertEqual((data["section"], data["log"]), (None, []))

    def test_the_section_is_the_installers_last_heading_and_the_log_its_tail(self):
        with open(self.status, "w", encoding="utf-8") as f:
            json.dump({"step": 4, "total": 6, "key": "install.step_packages", "detail": "", "error": None}, f)
        lines = ["== Packages ==", "apt-get update"] + ["line %d" % n for n in range(40)]
        lines[20:20] = ["== Python modules =="]
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        data = install_status.read_status(self.status, self.log)
        self.assertEqual(data["step"], 4)
        self.assertEqual(data["section"], "Python modules")
        self.assertEqual(len(data["log"]), install_status.LOG_TAIL)
        self.assertEqual(data["log"][-1], "line 39")

    def test_a_status_file_half_written_is_the_first_step_too(self):
        with open(self.status, "w", encoding="utf-8") as f:
            f.write('{"step": 3,')
        self.assertEqual(install_status.read_status(self.status, self.log)["step"], 0)


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        args = types.SimpleNamespace(
            web=os.path.dirname(_path.repo_file("web", "installing.html")),
            status=os.path.join(cls.dir, "status.json"), log=os.path.join(cls.dir, "install.log"))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), install_status.make_handler(args))
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ask(self, path, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.request(method, path)
            answer = conn.getresponse()
            return answer.status, dict(answer.getheaders()), answer.read()
        finally:
            conn.close()

    def test_the_page_and_what_it_loads(self):
        status, headers, body = self.ask("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, self.ask("/installing.html")[2])
        for path in sorted(install_status.STATIC):
            self.assertEqual(self.ask(path)[0], 200, path)
        self.assertEqual(self.ask("/manifest.webmanifest")[1]["Content-Type"], "application/manifest+json")

    def test_a_head_request_gets_the_headers_alone(self):
        status, headers, body = self.ask("/", method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_the_progress_is_readable_from_another_page(self):
        status, headers, body = self.ask("/api/install/status?x=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*", "the setup page asks from a file://")
        self.assertEqual(json.loads(body)["data"]["step"], 0)
        self.assertEqual(json.loads(self.ask("/api/portal/status")[2])["data"], {"installing": True})

    def test_the_real_interface_is_not_there_yet(self):
        status, _, body = self.ask("/api/settings")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"ok": False, "error": "installing"})

    def test_anything_else_is_sent_to_the_page_and_never_served(self):
        for path in ("/hotspot-detect.html", "/generate_204", "/app.js", "/../src/web_server.py"):
            status, headers, body = self.ask(path)
            self.assertEqual((status, body), (302, b""), path)
            self.assertTrue(headers["Location"].endswith("/"), headers["Location"])

    def test_with_no_address_yet_the_redirect_is_still_a_place(self):
        with mock.patch.object(install_status.captive_portal, "redirect_url_for", return_value=None):
            self.assertEqual(self.ask("/generate_204")[1]["Location"], "/")
        with mock.patch.object(install_status.captive_portal, "redirect_url_for", side_effect=OSError("no ip")):
            self.assertEqual(self.ask("/generate_204")[1]["Location"], "/")


def payload(year, month, day, hour, minute, second):
    return bytes([year & 0xFF, year >> 8, month, day, hour, minute, second, 5, 0, 0])


class BluetoothClockTest(unittest.TestCase):
    def test_the_time_read_is_utc_and_says_so(self):
        read = bt_clock._parse_cts_payload(payload(2026, 10, 2, 14, 30, 5))
        self.assertEqual(read, datetime(2026, 10, 2, 14, 30, 5, tzinfo=timezone.utc))
        self.assertIsNotNone(read.tzinfo, "a naive time would be set as local time, hours off")

    def test_a_short_answer_is_refused(self):
        with self.assertRaises(ValueError):
            bt_clock._parse_cts_payload(b"\xea\x07\x0a")

    def test_a_device_that_answers_gives_its_time_and_one_that_does_not_gives_none(self):
        moment = datetime(2026, 10, 2, 14, 30, 5, tzinfo=timezone.utc)

        async def answers(mac, timeout):
            return moment

        async def refuses(mac, timeout):
            raise RuntimeError("no Current Time Service")

        with mock.patch.object(bt_clock, "_read_time_async", answers):
            self.assertEqual(bt_clock.fetch_time_from_bt("AA:BB:CC:DD:EE:FF", 1), moment)
        with mock.patch.object(bt_clock, "_read_time_async", refuses), \
                self.assertLogs("bt_clock", level="ERROR"):
            self.assertIsNone(bt_clock.fetch_time_from_bt("AA:BB:CC:DD:EE:FF", 1))


if __name__ == "__main__":
    unittest.main()

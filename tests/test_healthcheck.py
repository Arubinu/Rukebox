"""docker/healthcheck.py: healthy only when the daemon and the web server both answer."""

import importlib.util
import os
import unittest
import urllib.error
from unittest import mock

import _path  # noqa: F401

SPEC = importlib.util.spec_from_file_location(
    "healthcheck", os.path.join(os.path.dirname(__file__), "..", "docker", "healthcheck.py"))
healthcheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(healthcheck)


class HealthcheckTest(unittest.TestCase):
    def run_with(self, status, opened):
        cfg = {"CONTROL_SOCKET": "/tmp/x.sock", "WEB_PORT": 8080}
        with mock.patch.object(healthcheck, "load_config", return_value=cfg), \
                mock.patch.object(healthcheck, "send_control_command", return_value=status), \
                mock.patch.object(healthcheck.urllib.request, "urlopen", side_effect=opened) as urlopen, \
                mock.patch("builtins.print"):
            return healthcheck.main(), urlopen

    def test_both_answering_is_healthy_on_the_configured_port(self):
        code, urlopen = self.run_with({"ok": True, "mode": "music"}, lambda *a, **k: mock.MagicMock())
        self.assertEqual(code, 0)
        self.assertEqual(urlopen.call_args[0][0], "http://127.0.0.1:8080/")

    def test_a_web_refusal_is_still_an_answer(self):
        def refuse(*args, **kwargs):
            raise urllib.error.HTTPError("http://x/", 401, "auth", {}, None)
        self.assertEqual(self.run_with({"ok": True}, refuse)[0], 0)

    def test_a_silent_daemon_or_web_server_is_unhealthy(self):
        self.assertEqual(self.run_with({"ok": False, "error": "daemon_unreachable"},
                                       lambda *a, **k: mock.MagicMock())[0], 1)

        def down(*args, **kwargs):
            raise urllib.error.URLError("refused")
        self.assertEqual(self.run_with({"ok": True}, down)[0], 1)


if __name__ == "__main__":
    unittest.main()

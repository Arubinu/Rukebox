"""The web server's rules, through Flask's test client with a fake daemon
and throwaway databases: the guest surface, the credits (and the devices
spared them), the two-step "Next" then "play now", and the backup
restore's refusals. Skipped where Flask is not installed - run them on
the Pi:

    scp -r tests pi@169.254.7.7:/tmp/rukebox-tests
    ssh pi@169.254.7.7 'RUKEBOX_SRC=/opt/rukebox/src python3 -m unittest discover -s /tmp/rukebox-tests -v'
"""
import io
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401

try:
    import flask  # noqa: F401
except ImportError:
    flask = None

TMP = tempfile.mkdtemp()
if flask:
    # Read by web_server at import time (load_config lets the environment
    # win): nothing of the real installation is opened for writing.
    os.environ["STATS_DB_FILE"] = os.path.join(TMP, "stats.db")
    import web_auth
    import web_server as ws


@unittest.skipUnless(flask, "Flask is not installed (run these on the Pi)")
class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.orig_cfg = ws.cfg
        cls.orig_control = ws.control
        cls.extra = {
            "WEB_PASSWORD_HASH": web_auth.hash_password("secret"), "GUEST_MODE_ENABLED": True,
            "SUGGESTIONS_ENABLED": True, "SUGGESTIONS_DB_FILE": os.path.join(cls.dir, "s.db"),
            "LIBRARY_DB_FILE": os.path.join(cls.dir, "l.db"), "STATE_DIR": cls.dir,
            "GUEST_QUOTA_ENABLED": True, "GUEST_QUOTA_MAX": 3, "GUEST_QUOTA_REFILL_SEC": 600,
            "GUEST_QUOTA_REPEAT_MIN": 0, "GUEST_COST_NEXT": 1,
        }
        ws.cfg = lambda: dict(cls.orig_cfg(), **cls.extra)
        cls.calls = []

        def fake(cmd, **kw):
            cls.calls.append((cmd, kw))
            if cmd == "get_status":
                return {"ok": True, "data": {"mode": "music"}}
            if cmd == "get_queue":
                return {"ok": True, "data": {"paths": [], "requested": []}}
            return {"ok": True}
        ws.control = fake

    @classmethod
    def tearDownClass(cls):
        ws.cfg = cls.orig_cfg
        ws.control = cls.orig_control
        shutil.rmtree(cls.dir, ignore_errors=True)

    def owner(self):
        client = ws.app.test_client()
        client.post("/api/auth/login", json={"password": "secret"})
        return client

    def test_guest_surface(self):
        guest = ws.app.test_client()
        self.assertEqual(guest.post("/api/action/long_press").status_code, 401, "a guest never powers off")
        self.assertEqual(guest.post("/api/action/standby").status_code, 401)
        self.assertEqual(guest.post("/api/mute", json={"on": True}).status_code, 401)
        self.assertEqual(guest.get("/api/settings").status_code, 401)
        self.assertEqual(guest.get("/api/today").status_code, 200)
        self.assertEqual(guest.get("/api/queue").status_code, 200)

    def test_credits_and_free_devices(self):
        guest = ws.app.test_client()
        codes = [guest.post("/api/action/next_track").status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        token = guest.get("/api/device").get_json()["data"]["token"]
        device = ws._suggestion_box().resolve_device(token, None, "127.0.0.1")[0]
        owner = self.owner()
        self.assertEqual(owner.post("/api/devices/free_credits", json={"device_id": device["id"], "on": True}).status_code, 200)
        self.assertEqual([guest.post("/api/action/next_track").status_code for _ in range(3)], [200] * 3)
        owner.post("/api/devices/free_credits", json={"device_id": device["id"], "on": False})
        self.assertEqual(guest.post("/api/action/next_track").status_code, 429)

    def test_play_now_only_from_up_next(self):
        owner = self.owner()
        self.assertEqual(owner.post("/api/library/queue", json={"key": "unknown"}).status_code, 404)
        self.assertEqual(owner.post("/api/library/play", json={"key": "unknown"}).status_code, 404)

    def test_backup_refusals(self):
        owner = self.owner()
        r = owner.post("/api/backup/inspect", data={"file": (io.BytesIO(b"not a zip"), "x.zip")},
                       content_type="multipart/form-data")
        self.assertEqual(r.get_json()["error"], "not_a_backup")
        r = owner.post("/api/backup/restore", json={"token": "nope"})
        self.assertEqual(r.get_json()["error"], "backup_expired")
        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/backup").status_code, 401)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

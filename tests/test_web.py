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
            "MUSIC_DIR": os.path.join(cls.dir, "music"),
            "MUSIC_CACHE_FILE": os.path.join(cls.dir, "music_cache.json"),
            "MUSIC_LISTS_FILE": os.path.join(cls.dir, "music_lists.json"),
            "GUEST_QUOTA_ENABLED": True, "GUEST_QUOTA_MAX": 3, "GUEST_QUOTA_REFILL_SEC": 600,
            "GUEST_QUOTA_REPEAT_MIN": 0, "GUEST_COST_NEXT": 1,
        }
        ws.cfg = lambda: dict(cls.orig_cfg(), **cls.extra)
        cls.calls = []
        cls.status = {"mode": "music"}

        def fake(cmd, **kw):
            cls.calls.append((cmd, kw))
            if cmd == "get_status":
                return {"ok": True, "data": dict(cls.status)}
            if cmd == "get_queue":
                return {"ok": True, "data": {"paths": [], "requested": []}}
            if cmd == "set_active_list":
                cls.status["active_list"] = (
                    {"id": kw.get("id"), "name": "Soir", "kind": "manual", "tracks": 1}
                    if kw.get("id") else None)
                return {"ok": True, "data": {"active": kw.get("id"), "tracks": 1}}
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

    def test_lists(self):
        music = self.extra["MUSIC_DIR"]
        os.makedirs(music, exist_ok=True)
        track = os.path.join(music, "a.mp3")
        with open(track, "wb") as f:
            f.write(b"x" * 10)
        lib = ws._get_library()
        lib.sync([track], music)
        key = lib.item_for_path(track)["key"]

        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/lists").status_code, 401, "lists are the owner's")

        owner = self.owner()
        created = owner.post("/api/lists", json={"name": "Soir", "kind": "manual"}).get_json()
        self.assertTrue(created["ok"], created)
        list_id = created["data"]["id"]
        self.assertEqual((created["data"]["kind"], created["data"]["count"]), ("manual", 0))
        self.assertEqual(owner.post("/api/lists", json={"name": " "}).get_json()["error"],
                         "list_name_required")
        self.assertEqual(
            owner.post("/api/lists", json={"name": "Jazz", "kind": "genre"}).get_json()["error"],
            "list_genres_required")

        added = owner.post("/api/lists/%s/tracks" % list_id, json={"key": key}).get_json()
        self.assertEqual(added["data"]["count"], 1)
        contents = owner.get("/api/lists/%s/tracks" % list_id).get_json()["data"]
        self.assertEqual([(i["title"], bool(i.get("missing"))) for i in contents["items"]],
                         [("a", False)])
        self.assertEqual(contents["missing"], 0)

        # The active switch goes through the daemon, and comes back in the list.
        active = owner.post("/api/lists/active", json={"id": list_id, "start": True}).get_json()
        self.assertTrue(active["ok"], active)
        self.assertEqual(active["data"]["active"], list_id)
        self.assertEqual(owner.get("/api/lists").get_json()["data"]["active"], list_id)
        self.assertIn(("set_active_list", list_id),
                      [(cmd, kw.get("id")) for cmd, kw in self.calls])
        self.assertEqual(owner.post("/api/lists/active", json={"id": "nope"}).status_code, 404)

        # The genre shortcut reuses the list that already matches.
        made = owner.post("/api/lists/from_genres", json={"genres": ["Jazz"]}).get_json()
        self.assertTrue(made["ok"], made)
        again = owner.post("/api/lists/from_genres", json={"genres": ["jazz"]}).get_json()
        self.assertEqual(again["data"]["id"], made["data"]["id"])
        self.assertEqual(len(owner.get("/api/lists").get_json()["data"]["lists"]), 2)

        removed = owner.delete("/api/lists/%s/tracks" % list_id, json={"key": key}).get_json()
        self.assertEqual(removed["data"]["count"], 0)
        self.assertEqual(owner.delete("/api/lists/%s" % made["data"]["id"]).status_code, 200)
        self.assertIsNone(owner.get("/api/lists").get_json()["data"]["active"],
                          "deleting the list being played goes back to everything")
        self.assertEqual(owner.delete("/api/lists/%s" % list_id).status_code, 200)
        self.assertEqual(owner.delete("/api/lists/%s" % list_id).status_code, 404)

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

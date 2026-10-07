"""The web server's rules, through Flask's test client with a fake daemon
and throwaway databases: who may call what (the guest surface, the owner's
routes, foreign origins and hosts, the login's throttle), the credits and
the devices spared them, the devices themselves (names, bans, the three
lists, several devices as one person), the captive portal's release, the
music lists, the schedules, the announcement volumes, the two-step "Next"
then "play now", and the backup restore's refusals. Skipped where Flask is
not installed - run them on the Pi:

    scp -r tests pi@169.254.7.7:/tmp/rukebox-tests
    ssh pi@169.254.7.7 'RUKEBOX_SRC=/opt/rukebox/src python3 -m unittest discover -s /tmp/rukebox-tests -v'
"""
import io
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
from datetime import datetime

import _path  # noqa: F401

try:
    import flask  # noqa: F401
except ImportError:
    flask = None

TMP = tempfile.mkdtemp()
if flask:
    # Read by web_server at import time; nothing of the real installation is opened for writing.
    os.environ["STATS_DB_FILE"] = os.path.join(TMP, "stats.db")
    import library
    import track_media
    import web_auth
    import web_server as ws


class FakeBluetoothctl:
    """The little of a `bluetoothctl` process that `_bt_await` uses."""

    started = []

    def __init__(self, *args, **kwargs):
        self.lines = []
        self.at = []
        self.out = []
        self.terminated = False
        self.stdin = self
        self.stdout = self
        FakeBluetoothctl.started.append(self)

    def write(self, line):
        line = line.strip()
        self.lines.append(line)
        self.at.append(time.monotonic())
        if line.startswith(("pair", "connect")):
            self.out.append("Pairing successful\n")

    def flush(self):
        pass

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if self.out:
                return self.out.pop(0)
            if self.terminated:
                raise StopIteration
            time.sleep(0.01)

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


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
            "SCHEDULES_FILE": os.path.join(cls.dir, "schedules.json"),
            "ANNOUNCEMENTS_FILE": os.path.join(cls.dir, "announcements.json"),
            "HOME_WIFI_CONN_NAME": "rukebox-home",
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

    def setUp(self):
        # These tests repeat one action on purpose; the one-second guard has its own test.
        patcher = unittest.mock.patch.object(ws, "ACTION_REPEAT_SEC", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        ws._repeats.clear()

    def owner(self):
        client = ws.app.test_client()
        client.post("/api/auth/login", json={"password": "secret"})
        return client

    def test_a_clock_module_that_was_not_written_is_not_called_written(self):
        owner = self.owner()
        real_exists = os.path.exists
        calls = []

        def run(cmd, **_kwargs):
            calls.append(cmd)
            # A recent Raspberry Pi OS without util-linux-extra: sudo finds no hwclock.
            return types.SimpleNamespace(returncode=1 if "hwclock" in cmd else 0, stdout="", stderr="")

        with unittest.mock.patch.object(ws.os.path, "exists",
                                        side_effect=lambda p: p in ("/dev/rtc0", "/dev/rtc") or real_exists(p)), \
                unittest.mock.patch.object(ws.subprocess, "run", side_effect=run):
            answer = owner.post("/api/time", json={"utc": "2026-10-04 05:30:00"}).get_json()
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["data"]["has_rtc"])
        self.assertFalse(answer["data"]["written_to_rtc"])
        self.assertIn(["sudo", "hwclock", "-w"], calls)

    def test_the_access_point_routes_say_so_where_there_cannot_be_one(self):
        # A container has no NetworkManager, and those routes ask nmcli with
        # nothing under them: they answer a code rather than a traceback.
        owner = self.owner()
        ws.platform_mod.override(access_point=False)
        self.addCleanup(ws.platform_mod.override, access_point=None)
        for method, path in (("get", "/api/wifi/ap"), ("get", "/api/wifi/ap/share"),
                             ("post", "/api/wifi/ap")):
            answer = getattr(owner, method)(path, json={})
            self.assertEqual(answer.status_code, 501, path)
            self.assertEqual(answer.get_json()["error"], "unsupported_here", path)
            self.assertIn("access_point", answer.get_json()["missing"], path)

        ws.platform_mod.override(access_point=True)
        # Where there is one, a machine without nmcli (this one) still gets an
        # answer instead of a FileNotFoundError.
        answer = owner.get("/api/wifi/ap")
        self.assertEqual(answer.status_code, 200)
        self.assertFalse(answer.get_json()["data"]["configured"])
        self.assertEqual(owner.get("/api/wifi/ap/share").status_code, 200)

    def test_the_upload_routes_say_so_where_media_cannot_be_written(self):
        # A container mounts the music `:ro` and files are added from the host:
        # the page leaves the menu, the send button goes, and every route that
        # would write into that folder answers a code rather than failing on a
        # read-only file system. The id-carrying paths are matched by rule.
        owner = self.owner()
        ws.platform_mod.override(media_upload=False)
        self.addCleanup(ws.platform_mod.override, media_upload=None)
        calls = [
            ("post", "/api/music/upload", {}),
            ("post", "/api/announce_files/meme", {}),
            ("delete", "/api/announce_files/meme", None),
            ("post", "/api/system_sounds/" + ws.config_schema.SYSTEM_SOUNDS[0], {}),
            ("delete", "/api/system_sounds/" + ws.config_schema.SYSTEM_SOUNDS[0], None),
        ]
        for method, path, payload in calls:
            answer = getattr(owner, method)(path, **({"data": payload} if payload is not None else {}))
            self.assertEqual(answer.status_code, 501, path)
            self.assertEqual(answer.get_json()["error"], "unsupported_here", path)
            self.assertIn("media_upload", answer.get_json()["missing"], path)
        # And what does not write into the folder is not refused: the list of
        # sounds, and switching one off (that is a setting, not a file).
        self.assertNotEqual(owner.get("/api/announce_files/meme").status_code, 501)
        self.assertEqual(owner.get("/api/system_sounds").status_code, 200)
        self.assertEqual(owner.post("/api/system_sounds/" + ws.config_schema.SYSTEM_SOUNDS[0] + "/off")
                         .status_code, 200)

    def test_the_folders_this_installation_plays_from_are_reachable(self):
        # A container keeps its music outside the Pi's usual places, and both
        # the folder browser and an announcement's own folder are checked
        # against this list: without it, a container whose music is mounted
        # writable could add a song but never a sound. Reported on the owner's
        # LXC: an announcement's folder and a system sound were both refused.
        music = ws.cfg()["MUSIC_DIR"]
        os.makedirs(music, exist_ok=True)
        self.assertIn(os.path.realpath(music), [os.path.realpath(r) for r in ws._browse_roots()])

        key = ws.config_schema.SYSTEM_SOUNDS[0]
        # The sound of a test installation is the test's own: the real one would
        # have this create a folder outside the sandbox, which a runner running
        # as an ordinary user cannot do.
        self.extra[key] = os.path.join(self.dir, "system", "restart.wav")
        self.addCleanup(self.extra.pop, key, None)
        custom = ws._system_sound_custom_dir(key)
        os.makedirs(custom, exist_ok=True)
        self.assertIn(os.path.realpath(custom),
                      [os.path.realpath(r) for r in ws._browse_roots()],
                      "a system sound's own folder is written there")
        self.assertEqual(os.path.dirname(custom),
                         os.path.dirname(ws.cfg().get(key) or ws.DEFAULTS[key]),
                         "and it follows the configured sound, not the template's")

        folder = os.path.join(os.path.dirname(music.rstrip("/")), "morning_announcements")
        os.makedirs(folder, exist_ok=True)
        item = {"id": "morning", "name": "Morning", "folder": folder}
        with unittest.mock.patch.object(ws.announcements, "read_items", return_value=[item]):
            self.assertIn(os.path.realpath(folder),
                          [os.path.realpath(r) for r in ws._browse_roots()],
                          "an announcement type has a folder of its own")
            if os.sep == "/":
                # _inside_roots compares with a forward slash, which is what the
                # product runs on: the Pi and every container are Linux.
                self.assertTrue(ws._inside_roots(os.path.join(folder, "jingle.mp3")),
                                "a folder it has not created yet is still written into")
                self.assertTrue(ws._inside_roots(os.path.join(music, "memes")))

    def test_the_event_log_is_searched_in_the_database(self):
        """Asked for as: a search bar for the event log. The query goes to the
        recorder (so it covers what the page has not loaded) and the total the
        interface shows is the number of matches."""
        owner = self.owner()
        with unittest.mock.patch.object(ws, "stats") as fake:
            fake.events.return_value = []
            fake.event_types.return_value = []
            fake.count_rows.return_value = 3
            answer = owner.get("/api/journal/entries?q=error&type=playback_error")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(fake.events.call_args.kwargs.get("query"), "error")
        self.assertEqual(fake.events.call_args.kwargs.get("event_type"), "playback_error")
        self.assertEqual(fake.count_rows.call_args.args[2], "error")
        self.assertEqual(answer.get_json()["data"]["total"], 3)

    def test_another_sites_page_cannot_post(self):
        owner = self.owner()
        foreign = owner.post("/api/mute", json={"on": True}, headers={"Origin": "https://evil.example"})
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(foreign.get_json()["error"], "bad_origin")
        self.assertEqual(owner.post("/api/mute", json={"on": True},
                                    headers={"Origin": "null"}).status_code, 403)
        for origin in (None, "http://localhost", "http://LOCALHOST:80"):
            headers = {"Origin": origin} if origin else {}
            self.assertEqual(owner.post("/api/mute", json={"on": True}, headers=headers).status_code,
                             200, origin)
        self.assertEqual(owner.get("/api/auth/status", headers={"Origin": "https://evil.example"}).status_code, 200)

    def test_the_api_only_answers_under_the_pis_own_names(self):
        import socket
        owner = self.owner()
        own = socket.gethostname().split(".", 1)[0].lower()
        for host in ("10.42.0.1", "10.42.0.1:8080", "localhost", "[fe80::1]:80",
                     own + ".local", own + "-2.local", own + ".lan", own.upper()):
            self.assertEqual(owner.get("/api/auth/status", headers={"Host": host}).status_code, 200, host)
        for host in ("rebind.evil.example", own + ".evil.example", own + "x.local"):
            foreign = owner.get("/api/auth/status", headers={"Host": host})
            self.assertEqual(foreign.status_code, 403, host)
        self.assertEqual(foreign.get_json()["error"], "bad_host")
        self.assertNotEqual(owner.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"}).status_code,
                            403, "a connectivity probe is not an API call")
        type(self).extra["WEB_EXTRA_HOSTS"] = "radio.example, other.example"
        try:
            self.assertEqual(owner.get("/api/auth/status", headers={"Host": "radio.example"}).status_code, 200)
        finally:
            del type(self).extra["WEB_EXTRA_HOSTS"]

    def test_a_json_body_is_bounded(self):
        owner_body = "x" * (1024 * 1024 + 10)
        answer = self.owner().post("/api/mute", data=owner_body, content_type="application/json")
        self.assertEqual(answer.status_code, 413)
        self.assertEqual(answer.get_json()["error"], "too_large")

    def test_a_new_password_needs_eight_characters(self):
        owner = self.owner()
        short = owner.post("/api/auth/set_password",
                           json={"current_password": "secret", "new_password": "1234567"})
        self.assertEqual(short.status_code, 400)
        self.assertEqual(short.get_json()["error"], "password_too_short")
        self.assertEqual(self.owner().get("/api/settings").status_code, 200, "the old one still works")

    def test_guest_surface(self):
        guest = ws.app.test_client()
        self.assertEqual(guest.post("/api/action/long_press").status_code, 401, "a guest never powers off")
        self.assertEqual(guest.post("/api/action/standby").status_code, 401)
        self.assertEqual(guest.post("/api/mute", json={"on": True}).status_code, 401)
        self.assertEqual(guest.get("/api/settings").status_code, 401)
        self.assertEqual(guest.get("/api/today").status_code, 200)
        self.assertEqual(guest.get("/api/queue").status_code, 200)

    def test_an_update_that_died_halfway_is_not_still_running(self):
        # The shell appends the end marker after the updater returns, so a killed run leaves none.
        log = os.path.join(self.dir, "update.log")
        original = ws.UPDATE_LOG
        ws.UPDATE_LOG = log
        client = self.owner()
        try:
            with open(log, "w", encoding="utf-8") as f:
                f.write("== updating ==\nstopping rukebox-web.service\n")
            with unittest.mock.patch.object(ws, "_updater_alive", return_value=False):
                data = client.get("/api/update/status").get_json()["data"]
            self.assertFalse(data["running"], "no updater process, so it is not running")
            self.assertTrue(data["interrupted"], "and the page can say the run was cut short")

            with unittest.mock.patch.object(ws, "_updater_alive", return_value=True):
                data = client.get("/api/update/status").get_json()["data"]
            self.assertTrue(data["running"])
            self.assertFalse(data["interrupted"])

            with open(log, "a", encoding="utf-8") as f:
                f.write("__RUKEBOX_UPDATE_DONE__\n")
            data = client.get("/api/update/status").get_json()["data"]
            self.assertFalse(data["running"])
            self.assertFalse(data["interrupted"])

            os.remove(log)
            data = client.get("/api/update/status").get_json()["data"]
            self.assertFalse(data["running"], "no log at all is not a running update")
            self.assertFalse(data["interrupted"])
        finally:
            ws.UPDATE_LOG = original

    def test_the_page_says_an_update_is_running(self):
        # Tells the page a service-stopping update is running instead of looking like a dead Pi.
        flag = os.path.join(self.dir, "updating")
        state = {"STATE_DIR": self.dir}
        self.assertFalse(ws.update_in_progress(state))
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write("1790779000\n")
        self.assertTrue(ws.update_in_progress(state))
        # An old flag is an updater that died: it must not hold the page there for ever.
        old = time.time() - ws.UPDATE_FLAG_MAX_AGE_SEC - 60
        os.utime(flag, (old, old))
        self.assertFalse(ws.update_in_progress(state))
        self.assertFalse(os.path.exists(flag), "the stale flag is cleared while we are there")

    def test_the_same_action_within_a_second_runs_once_and_costs_once(self):
        ws.ACTION_REPEAT_SEC = 1.0
        first, second = ws.app.test_client(), ws.app.test_client()
        before = len([c for c in self.calls if c[0] == "next_track"])
        self.assertEqual(first.post("/api/action/next_track").status_code, 200)
        self.assertEqual(second.post("/api/action/next_track").status_code, 200,
                         "the second person gets the first answer, not a refusal")
        self.assertEqual(len([c for c in self.calls if c[0] == "next_track"]) - before, 1,
                         "two people pressing Next at once skip one song, not two")
        token = second.get("/api/device").get_json()["data"]["token"]
        device = ws._suggestion_box().resolve_device(token, None, "127.0.0.1")[0]
        with ws._quota_lock:
            spent = ws._quota.get(device["person"], {}).get("tokens", 3)
        self.assertEqual(spent, 3, "and the second one pays nothing")

        sets = len([c for c in self.calls if c[0] == "set_volume"])
        owner = self.owner()
        owner.post("/api/volume", json={"value": 40})
        owner.post("/api/volume", json={"value": 41})
        self.assertEqual(len([c for c in self.calls if c[0] == "set_volume"]) - sets, 2,
                         "a different value is a different action")

        ws._repeats.clear()
        ws.ACTION_REPEAT_SEC = 0.05
        owner.post("/api/action/toggle_pause")
        time.sleep(0.1)
        owner.post("/api/action/toggle_pause")
        self.assertEqual(len([c for c in self.calls if c[0] == "toggle_pause"]) >= 2, True,
                         "past the window, the same action runs again")

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

    def test_a_device_name_is_the_owners_to_pin(self):
        guest = ws.app.test_client()
        token = guest.get("/api/device").get_json()["data"]["token"]
        box = ws._suggestion_box()
        device = box.resolve_device(token, None, "127.0.0.1")[0]
        owner = self.owner()

        named = owner.post("/api/devices/name", json={"device_id": device["id"], "name": "Koala rose"})
        self.assertEqual(named.status_code, 200)
        self.assertEqual(named.get_json()["data"]["name"], "Koala rose")
        self.assertEqual(guest.get("/api/suggestions").get_json()["data"]["me"]["name"], "Koala rose")

        self.assertEqual(owner.post("/api/devices/name_locked",
                                    json={"device_id": device["id"], "on": True}).status_code, 200)
        self.assertTrue(guest.get("/api/suggestions").get_json()["data"]["me"]["locked"],
                        "the device is told, so the page does not offer a change it would refuse")
        self.assertEqual(guest.post("/api/suggestions/name", json={"name": "Koala bleu"})
                         .get_json()["error"], "name_locked")
        self.assertEqual(guest.get("/api/suggestions").get_json()["data"]["me"]["name"], "Koala rose")

        self.assertEqual(owner.post("/api/devices/name",
                                    json={"device_id": device["id"], "name": "Koala bleu"}).status_code, 200,
                         "the owner renames it anyway")
        self.assertEqual(guest.get("/api/suggestions").get_json()["data"]["me"]["name"], "Koala bleu")

        owner.post("/api/devices/name_locked", json={"device_id": device["id"], "on": False})
        self.assertFalse(guest.get("/api/suggestions").get_json()["data"]["me"]["locked"])
        self.assertEqual(guest.post("/api/suggestions/name", json={"name": "Koala vert"}).status_code, 200)

    def test_two_devices_become_one_person_with_a_code(self):
        phone, laptop = ws.app.test_client(), ws.app.test_client()
        me = lambda client: client.get("/api/suggestions").get_json()["data"]["me"]  # noqa: E731
        self.assertEqual(phone.post("/api/suggestions/name", json={"name": "Renard bleu"}).status_code, 200)
        self.assertEqual(laptop.post("/api/suggestions/name", json={"name": "Loutre verte"}).status_code, 200)

        code = phone.post("/api/devices/link_code").get_json()["data"]["code"]
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(phone.post("/api/devices/link_join", json={"code": code}).get_json()["error"],
                         "link_same_device")
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        self.assertEqual(laptop.post("/api/devices/link_join", json={"code": wrong}).get_json()["error"],
                         "link_code_bad")
        joined = laptop.post("/api/devices/link_join", json={"code": code[:3] + " " + code[3:]})
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(joined.get_json()["data"]["name"], "Renard bleu")
        self.assertEqual(me(laptop)["name"], "Renard bleu")
        self.assertEqual(me(laptop)["linked"], 1)
        self.assertEqual(me(phone)["linked"], 1)

        tablet = ws.app.test_client()
        self.assertEqual(tablet.post("/api/devices/link_join", json={"code": code}).get_json()["error"],
                         "link_code_bad", "a code works once")
        for _ in range(ws.LINK_CODE_TRIES):
            answer = tablet.post("/api/devices/link_join", json={"code": "000000"})
        self.assertEqual(answer.status_code, 429, "guessing is cut short")

        box = ws._suggestion_box()
        device = box.resolve_device(laptop.get("/api/device").get_json()["data"]["token"], None, "127.0.0.1")[0]
        self.assertEqual(laptop.post("/api/devices/unlink", json={"device_id": device["id"]}).status_code, 401,
                         "leaving is the owner's to decide")
        owner = self.owner()
        self.assertEqual(owner.post("/api/devices/unlink", json={"device_id": device["id"]}).status_code, 200)
        self.assertIsNone(me(laptop)["name"])
        self.assertEqual(me(phone)["linked"], 0)

        target = box.resolve_device(phone.get("/api/device").get_json()["data"]["token"], None, "127.0.0.1")[0]
        self.assertEqual(owner.post("/api/devices/link",
                                    json={"device_id": device["id"], "to": target["id"]}).status_code, 200)
        self.assertEqual(me(laptop)["name"], "Renard bleu")
        self.assertEqual(owner.post("/api/devices/link",
                                    json={"device_id": device["id"], "to": target["id"]}).get_json()["error"],
                         "already_linked")
        owner.post("/api/devices/unlink", json={"device_id": device["id"]})

    def test_a_device_can_leave_by_itself_and_keeps_the_credits_it_shared(self):
        phone, laptop = ws.app.test_client(), ws.app.test_client()
        me = lambda client: client.get("/api/suggestions").get_json()["data"]["me"]  # noqa: E731
        phone.post("/api/suggestions/name", json={"name": "Hibou gris"})
        self.assertEqual(laptop.post("/api/devices/link_leave").get_json()["error"], "not_linked")
        code = phone.post("/api/devices/link_code").get_json()["data"]["code"]
        self.assertEqual(laptop.post("/api/devices/link_join", json={"code": code}).status_code, 200)

        box = ws._suggestion_box()
        device = box.resolve_device(laptop.get("/api/device").get_json()["data"]["token"], None, "127.0.0.1")[0]
        spent = {"tokens": 0.0, "at": time.time(), "history": {"next": [time.time()]}, "volume_at": 0.0}
        with ws._quota_lock:
            ws._quota[device["person"]] = spent

        self.assertEqual(laptop.post("/api/devices/link_leave").status_code, 200)
        self.assertIsNone(me(laptop)["name"])
        self.assertEqual((me(phone)["name"], me(phone)["linked"]), ("Hibou gris", 0))
        with ws._quota_lock:
            mine, theirs = ws._quota[device["id"]], ws._quota[device["person"]]
        self.assertLess(mine["tokens"], 1, "leaving is not a way to a full counter")
        self.assertLess(theirs["tokens"], 1)
        self.assertIsNot(mine["history"], theirs["history"], "two counters from now on")

    def test_schedules_are_the_owners_and_only_hold_settings_a_schedule_may_change(self):
        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/schedules").status_code, 401)
        self.assertEqual(guest.post("/api/schedules", json={"name": "A", "start": "07:00"}).status_code, 401)

        owner = self.owner()
        listed = owner.get("/api/schedules").get_json()["data"]
        self.assertIn("MUSIC_LOOP", listed["settings"])
        self.assertNotIn("WEB_PORT", listed["settings"])
        self.assertNotIn("WEB_PASSWORD_HASH", listed["settings"])

        refused = owner.post("/api/schedules", json={"name": "A", "start": "07:00",
                                                     "settings": {"WEB_PASSWORD_HASH": ""}})
        self.assertEqual((refused.status_code, refused.get_json()["error"]), (400, "schedule_bad_setting"))
        self.assertEqual(owner.post("/api/schedules", json={"name": "A"}).get_json()["error"], "schedule_no_time")

        made = owner.post("/api/schedules", json={"name": "Le matin", "days": [0, 1, 2, 3, 4], "start": "07:00",
                                                  "stop": "09:00", "stop_action": "standby",
                                                  "settings": {"BASE_VOLUME": "35"}})
        self.assertEqual(made.status_code, 200)
        entry = made.get_json()["data"]
        self.assertEqual((entry["id"], entry["settings"]), ("le-matin", {"BASE_VOLUME": "35"}))
        off = owner.post("/api/schedules/le-matin", json={"enabled": False}).get_json()["data"]
        self.assertFalse(off["enabled"])
        self.assertEqual(off["stop"], "09:00", "a partial change keeps the rest")
        self.assertEqual(owner.post("/api/schedules/nope", json={"enabled": False}).status_code, 404)
        owner.post("/api/schedules", json={"name": "Le soir", "start": "20:00"})
        self.assertEqual(guest.post("/api/schedule_order", json={"order": ["le-soir"]}).status_code, 401)
        ordered = owner.post("/api/schedule_order", json={"order": ["le-soir", "le-matin"]})
        self.assertEqual([item["id"] for item in ordered.get_json()["data"]["schedules"]], ["le-soir", "le-matin"])
        self.assertEqual(owner.post("/api/schedule_order", json={}).get_json()["error"], "schedule_bad_order")
        self.assertEqual(owner.delete("/api/schedules/le-soir").status_code, 200)
        self.assertEqual(owner.delete("/api/schedules/le-matin").status_code, 200)
        self.assertEqual(owner.get("/api/schedules").get_json()["data"]["schedules"], [])

    def test_a_pin_typed_by_hand_is_checked_like_one_picked(self):
        owner = self.owner()
        for pin in ("2", "14", "0", "28", "abc"):
            answer = owner.post("/api/settings", json={"GPIO_RESET_PIN": pin})
            self.assertEqual((answer.status_code, answer.get_json()["error"]), (400, "gpio_pin_reserved"), pin)
        button = str(ws.cfg()["GPIO_BUTTON_PIN"])
        taken = owner.post("/api/settings", json={"GPIO_RESET_PIN": button})
        self.assertEqual((taken.status_code, taken.get_json()["error"]), (400, "gpio_pin_taken"))
        both = owner.post("/api/settings", json={"GPIO_RESET_PIN": "17", "GPIO_BUTTON_PIN": "17"})
        self.assertEqual(both.get_json()["error"], "gpio_pin_taken")
        before = str(ws.cfg()["GPIO_RESET_PIN"])
        self.assertEqual(owner.post("/api/settings", json={"GPIO_RESET_PIN": "17"}).status_code, 200)
        self.assertEqual(owner.post("/api/settings", json={"GPIO_RESET_PIN": before}).status_code, 200)

    def test_an_announcements_volume_is_the_owners(self):
        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/announcement_volumes").status_code, 401)
        self.assertEqual(
            guest.post("/api/announcement_volumes/meme", json={"on": True, "volume": 20}).status_code,
            401, "a guest does not set the volume of the announcements")

        owner = self.owner()
        self.assertNotIn("meme", owner.get("/api/announcement_volumes").get_json()["data"])
        saved = owner.post("/api/announcement_volumes/meme", json={"on": True, "volume": 20})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["data"], {"on": True, "volume": 20})
        self.assertEqual(owner.get("/api/announcement_volumes").get_json()["data"]["meme"],
                         {"on": True, "volume": 20})
        self.assertIn("reload_announcements", [cmd for cmd, _ in self.calls],
                      "the daemon is told, so the next play uses it")

        self.assertEqual(
            owner.post("/api/announcement_volumes/meme", json={"on": True, "volume": 101})
            .get_json()["error"], "announcement_bad_volume")
        self.assertEqual(
            owner.post("/api/announcement_volumes/meme", json={"on": True, "volume": "loud"})
            .get_json()["error"], "announcement_bad_volume")
        self.assertEqual(owner.get("/api/announcement_volumes").get_json()["data"]["meme"],
                         {"on": True, "volume": 20}, "a refused value changed nothing")

    def test_pairing_keeps_the_scan_running(self):
        # BlueZ drops a device seen only once discovery stops, so the scan starts inside the pairing session.
        seen = {}
        original = (ws._bt_device_info, ws._bt_discover, ws._bt_await,
                    ws._bt_wait_flag, ws._bt_script)
        ws._bt_device_info = lambda mac: {"paired": False, "available": True, "connected": False,
                                          "name": "", "kind": "", "custom": False}
        ws._bt_discover = lambda mac, timeout=12: True
        ws._bt_wait_flag = lambda mac, flag, **kw: {"paired": True, "connected": True,
                                                    "available": True, "name": "", "kind": ""}

        def fake_await(commands, verdicts, **kwargs):
            seen["commands"] = list(commands)
            return "Pairing successful", True

        ws._bt_await = fake_await
        ws._bt_script = lambda commands, **kw: types.SimpleNamespace(stdout="", returncode=0)
        try:
            answer = self.owner().post("/api/bluetooth/pair", json={"mac": "7C:E9:13:69:66:55"})
        finally:
            (ws._bt_device_info, ws._bt_discover, ws._bt_await,
             ws._bt_wait_flag, ws._bt_script) = original
        self.assertTrue(answer.get_json()["ok"], answer.get_json())
        self.assertEqual(seen["commands"][0], "scan on")
        self.assertIn("pair 7C:E9:13:69:66:55", seen["commands"])
        self.assertIsInstance(seen["commands"][1], float, "the scan needs a moment first")

    def test_a_number_in_the_commands_is_a_pause(self):
        with unittest.mock.patch.object(ws.subprocess, "Popen", FakeBluetoothctl):
            verdict = ws._bt_await(["scan on", 0.05, "pair AA:BB:CC:DD:EE:FF"],
                                   {"Pairing successful": True}, timeout=5)[1]
        proc = FakeBluetoothctl.started[-1]
        self.assertTrue(verdict)
        self.assertEqual(proc.lines, ["scan on", "pair AA:BB:CC:DD:EE:FF"])
        self.assertGreaterEqual(proc.at[1] - proc.at[0], 0.04, "the pause was honoured")

    def wifi_card(self, active=True, address="192.168.42.12/24", autoconnect="yes",
                  client="192.168.42.42"):
        """The Wi-Fi card's status, with nmcli stood in for."""
        original = ws.subprocess.run

        def fake(args, **kw):
            if "-f" in args:
                out = "rukebox-home:wlan0\n" if active else "lo:lo\n"
            elif "IP4.ADDRESS" in args:
                out = address + "\n"
            elif "connection.autoconnect" in args:
                out = autoconnect + "\n"
            else:
                out = ""
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

        ws.subprocess.run = fake
        try:
            return self.owner().get("/api/wifi/status",
                                    environ_base={"REMOTE_ADDR": client}).get_json()["data"]
        finally:
            ws.subprocess.run = original

    def test_the_wifi_card_knows_when_it_is_its_own_lifeline(self):
        # Cutting the network the page arrived through closes the page.
        data = self.wifi_card()
        self.assertTrue(data["active"])
        self.assertTrue(data["client_here"], "the page is reached through it")

        ap = self.wifi_card(address="10.42.0.1/24")
        self.assertFalse(ap["client_here"], "the access point is not that lifeline")

        self.assertTrue(ws._same_network("192.168.42.42", "192.168.42.12/24"))
        self.assertFalse(ws._same_network("::1", "192.168.42.12/24"))
        self.assertFalse(ws._same_network(None, "192.168.42.12/24"))

    def test_the_translations_sent_are_english_and_the_pages_language(self):
        client = ws.app.test_client()

        def langs(response):
            body = response.get_data(as_text=True)
            return [l for l in ws.I18N_LANGS if "\n  %s: {" % l in body or "I18N.%s =" % l in body]

        self.assertEqual(langs(client.get("/i18n.js", headers={"Accept-Language": "de-DE,de;q=0.9"})), ["en", "de"])
        client.set_cookie(ws.LANG_COOKIE, "it")
        self.assertEqual(langs(client.get("/i18n.js", headers={"Accept-Language": "de-DE"})), ["en", "it"],
                         "the page's own choice beats the browser's")
        added = client.get("/i18n.js?add=1&lang=nl")
        self.assertEqual(langs(added), ["nl"], "a language added later comes alone")
        self.assertEqual(client.get("/i18n.js?add=1&lang=nl",
                                    headers={"If-None-Match": added.headers["ETag"]}).status_code, 304)

    def test_the_wifi_status_is_read_once_for_every_page_that_asks(self):
        calls = []
        original = ws.subprocess.run

        def fake(args, **kw):
            calls.append(args)
            out = {"-f": "rukebox-home:wlan0\n", "IP4.ADDRESS": "192.168.42.12/24\n",
                   "connection.autoconnect": "yes\n"}
            return types.SimpleNamespace(returncode=0, stderr="",
                                         stdout=next((v for k, v in out.items() if k in args), ""))

        owner = self.owner()
        ws.subprocess.run = fake
        try:
            here = owner.get("/api/wifi/status", environ_base={"REMOTE_ADDR": "192.168.42.42"}).get_json()["data"]
            first = len(calls)
            away = owner.get("/api/wifi/status", environ_base={"REMOTE_ADDR": "10.42.0.5"}).get_json()["data"]
        finally:
            ws.subprocess.run = original
        self.assertEqual(len(calls), first, "the second page within seconds costs no nmcli call")
        self.assertEqual((here["client_here"], away["client_here"]), (True, False),
                         "but each page is told whether it is its own lifeline")

    def test_turning_the_personal_wifi_off_is_remembered(self):
        # Read back by scripts/home-wifi-connect.sh, which would otherwise reconnect behind the user's back.
        written = []
        original = (ws.subprocess.run, ws.update_config_file)
        ws.subprocess.run = lambda args, **kw: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")
        ws.update_config_file = lambda values: written.append(values)
        try:
            off = self.owner().post("/api/wifi/toggle", json={"enabled": False})
            on = self.owner().post("/api/wifi/toggle", json={"enabled": True})
        finally:
            ws.subprocess.run, ws.update_config_file = original
        self.assertTrue(off.get_json()["ok"], off.get_json())
        self.assertTrue(on.get_json()["ok"], on.get_json())
        self.assertEqual(written, [{"HOME_WIFI_ENABLED": False}, {"HOME_WIFI_ENABLED": True}])

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

        active = owner.post("/api/lists/active", json={"id": list_id, "start": True}).get_json()
        self.assertTrue(active["ok"], active)
        self.assertEqual(active["data"]["active"], list_id)
        self.assertEqual(owner.get("/api/lists").get_json()["data"]["active"], list_id)
        self.assertIn(("set_active_list", list_id),
                      [(cmd, kw.get("id")) for cmd, kw in self.calls])
        self.assertEqual(owner.post("/api/lists/active", json={"id": "nope"}).status_code, 404)

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

    def status_of(self, link_state, controllers=None):
        """The status payload, with the speaker link and the audio-output probe
        (which needs a real Linux session) stood in for."""
        original = (ws.bt_link.locate, ws._bt_controllers, ws._audio_output_state)
        ws.bt_link.locate = lambda mac, adapter="": dict(link_state, mac=mac,
                                                         expected=link_state.get("expected"))
        ws._bt_controllers = lambda: controllers or []
        ws._audio_output_state = lambda: {}
        ws._status_probes.clear()
        try:
            return self.owner().get("/api/status").get_json()["data"]
        finally:
            ws.bt_link.locate, ws._bt_controllers, ws._audio_output_state = original
            ws._status_probes.clear()

    def test_the_speaker_status_says_where_it_is(self):
        dongle, builtin = "00:A7:50:72:14:C4", "B8:27:EB:62:82:CB"
        data = self.status_of(
            {"connected": True, "controller": builtin, "expected": dongle,
             "paired_here": False, "known_here": False, "name": "soundcore",
             "unknown": False},
            [{"address": dongle, "bus": "usb", "name": "hci1"},
             {"address": builtin, "bus": "uart", "name": "hci0"}])
        self.assertTrue(data["speaker_connected"], "connected somewhere is connected")
        self.assertEqual(data["speaker_controller"], builtin)
        self.assertEqual(data["speaker_controller_kind"], "builtin")
        self.assertEqual(data["speaker_expected"], dongle)
        self.assertEqual(data["speaker_expected_kind"], "usb")

    def test_a_controller_that_does_not_answer_is_not_a_speaker_that_is_off(self):
        data = self.status_of({"connected": False, "controller": None, "expected": None,
                               "paired_here": False, "known_here": False, "name": "",
                               "unknown": True})
        self.assertIsNone(data["speaker_connected"])

    def test_backup_refusals(self):
        owner = self.owner()
        r = owner.post("/api/backup/inspect", data={"file": (io.BytesIO(b"not a zip"), "x.zip")},
                       content_type="multipart/form-data")
        self.assertEqual(r.get_json()["error"], "not_a_backup")
        r = owner.post("/api/backup/restore", json={"token": "nope"})
        self.assertEqual(r.get_json()["error"], "backup_expired")
        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/backup").status_code, 401)

    def test_a_device_can_be_put_back_on_the_portal(self):
        mac = "aa:bb:cc:dd:ee:99"
        guest = ws.app.test_client()
        owner = self.owner()
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac), \
                unittest.mock.patch.object(ws, "_stations", return_value={mac: {"signal": -55}}):
            self.assertFalse(guest.get("/api/portal/status").get_json()["data"]["released"],
                             "the portal holds a device that has not finished")
            held = guest.get("/hotspot-detect.html")
            self.assertEqual(held.status_code, 302)
            self.assertEqual(held.headers["Cache-Control"], "no-store",
                             "or the window could replay it instead of asking again")
            guest.post("/api/portal/release")
            self.assertTrue(guest.get("/api/portal/status").get_json()["data"]["released"])
            free = guest.get("/hotspot-detect.html")
            self.assertEqual((free.status_code, free.get_data(as_text=True).strip()),
                             (200, "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"))
            entry = owner.get("/api/wifi/clients").get_json()["data"]["clients"][0]
            self.assertTrue(entry["portal_released"], "the page can offer to forget it")

            self.assertEqual(guest.post("/api/devices/portal_release", json={"mac": mac}).status_code, 401,
                             "forgetting a tap is the owner's")
            r = owner.post("/api/devices/portal_release", json={"mac": mac}).get_json()
            self.assertTrue(r["data"]["forgotten"], "there was a tap to forget")
            self.assertFalse(guest.get("/api/portal/status").get_json()["data"]["released"],
                             "the portal holds it again")
            entry = owner.get("/api/wifi/clients").get_json()["data"]["clients"][0]
            self.assertFalse(entry["portal_released"], "and the button says there is nothing to forget")

            again = owner.post("/api/devices/portal_release", json={"mac": mac}).get_json()
            self.assertFalse(again["data"]["forgotten"])
            self.assertEqual(owner.post("/api/devices/portal_release", json={"mac": "nope"}).status_code, 404)

    def test_a_device_off_the_access_point_is_still_connected(self):
        # Devices on the owner's own network are invisible to `iw`: a request is the only sign of life.
        guest = ws.app.test_client()
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=False), \
                unittest.mock.patch.object(ws, "_stations", return_value={}):
            guest.get("/api/portal/status")
            owner = self.owner()
            clients = owner.get("/api/wifi/clients").get_json()["data"]["clients"]
        self.assertTrue(clients, "the device that just asked is connected")
        self.assertTrue(all(not c["on_ap"] for c in clients), "and not through the access point")
        self.assertTrue(all(isinstance(c["seen_sec"], int) for c in clients))
        self.assertFalse(any(c.get("connected_sec") for c in clients))

    def test_a_device_that_left_can_still_be_acted_on(self):
        # A ban is the one thing that takes a device out of both lists.
        mac = "aa:bb:cc:dd:ee:97"
        owner = self.owner()
        owner.post("/api/devices/name", json={"mac": mac, "name": "Merle ivoire"})
        device = ws._suggestion_box().device_by_mac(mac)
        ws._seen_devices.clear()
        with unittest.mock.patch.object(ws, "_stations", return_value={}):
            devices = owner.get("/api/devices/seen").get_json()["data"]["devices"]
        entry = [d for d in devices if d["device_id"] == device["id"]]
        self.assertTrue(entry, "it is still there to be acted on")
        self.assertEqual(entry[0]["name"], "Merle ivoire")
        self.assertEqual(entry[0]["macs"], [mac])
        self.assertTrue(entry[0]["last_seen"], "with the date it was last seen")

        owner.post("/api/devices/ban", json={"device_id": device["id"], "minutes": 60})
        try:
            with unittest.mock.patch.object(ws, "_stations", return_value={}):
                devices = owner.get("/api/devices/seen").get_json()["data"]["devices"]
                banned = owner.get("/api/devices/banned").get_json()["data"]["banned"]
                clients = owner.get("/api/wifi/clients").get_json()["data"]["clients"]
            self.assertFalse([d for d in devices if d["device_id"] == device["id"]],
                             "a banned device is in neither of the other two lists")
            self.assertFalse([c for c in clients if c.get("device_id") == device["id"]])
            self.assertEqual([b["device_id"] for b in banned], [device["id"]])
        finally:
            owner.post("/api/devices/ban", json={"device_id": device["id"], "lift": True})

    def test_a_device_can_be_forgotten(self):
        named_mac, unnamed_mac = "aa:bb:cc:dd:ee:96", "aa:bb:cc:dd:ee:95"
        owner = self.owner()
        box = ws._suggestion_box()
        owner.post("/api/devices/name", json={"mac": named_mac, "name": "Merle noir"})
        named = box.device_by_mac(named_mac)
        unnamed = box.ensure_device_for_mac(unnamed_mac, "10.42.0.95")

        r = owner.post("/api/devices/forget", json={"device_id": named["id"]}).get_json()
        self.assertEqual(r["data"]["forgotten"], 1)
        self.assertIsNone(box.device_by_id(named["id"]), "the device is gone")
        self.assertIsNone(box.device_by_mac(named_mac), "and its address with it")
        self.assertEqual(owner.post("/api/devices/forget", json={"device_id": named["id"]}).status_code, 404)
        self.assertEqual(owner.post("/api/devices/forget", json={"mac": "aa:bb:cc:dd:ee:00"}).status_code, 404)

        # A ban goes with it: forgotten means the Rukebox has never met it.
        owner.post("/api/devices/ban", json={"device_id": unnamed["id"], "minutes": 60})
        sweep = owner.post("/api/devices/forget", json={"unnamed": True}).get_json()["data"]
        self.assertGreaterEqual(sweep["forgotten"], 1)
        self.assertIsNone(box.device_by_id(unnamed["id"]))
        self.assertIsNone(box.ban_until(unnamed["id"]))
        self.assertEqual(owner.post("/api/devices/forget", json={"unnamed": True}).get_json()["data"]["forgotten"],
                         0, "nothing left to sweep")

    def test_a_remembered_tap_follows_the_device_not_its_address(self):
        # A device that comes back on another address would otherwise let the next one through.
        mac = "aa:bb:cc:dd:ee:98"
        guest = ws.app.test_client()
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            guest.post("/api/portal/release")
            self.assertTrue(guest.get("/api/portal/status").get_json()["data"]["released"])
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=None):
            self.assertFalse(guest.get("/api/portal/status").get_json()["data"]["released"],
                             "another device on the same address is asked to finish")
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            self.assertTrue(guest.get("/api/portal/status").get_json()["data"]["released"],
                            "and the device that finished keeps its release on a new address")
            ws._portal_forget(mac)

    def test_the_status_dates_the_tap(self):
        # The page confirms a tap once and briefly, so it needs to know which tap it is looking at.
        mac = "aa:bb:cc:dd:ee:95"
        guest = ws.app.test_client()
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            before = time.time()
            self.assertIsNone(guest.get("/api/portal/status").get_json()["data"]["released_at"])
            guest.post("/api/portal/release")
            status = guest.get("/api/portal/status").get_json()["data"]
        self.assertTrue(status["released"])
        self.assertGreaterEqual(status["released_at"], before)
        self.assertLessEqual(status["released_at"], time.time())
        ws._portal_forget(mac)

    def test_a_tap_survives_a_restart_of_the_web_server(self):
        # The in-memory map dies with the process, so the tap is also written against the device.
        mac = "aa:bb:cc:dd:ee:97"
        guest = ws.app.test_client()
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            guest.post("/api/portal/release")
            ws._portal_released.clear()          # what a restart leaves behind
            self.assertTrue(guest.get("/api/portal/status").get_json()["data"]["released"],
                            "the device is still the one that finished")
            forgotten = ws._portal_forget(mac)
        self.assertTrue(forgotten)
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            self.assertFalse(guest.get("/api/portal/status").get_json()["data"]["released"],
                             "and forgetting it works on the written-down tap too")

    def test_a_tap_older_than_twelve_hours_is_not_a_tap(self):
        mac = "aa:bb:cc:dd:ee:96"
        guest = ws.app.test_client()
        box = ws._suggestion_box()
        box.note_portal_release(mac, when=time.time() - 13 * 3600)
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            self.assertFalse(guest.get("/api/portal/status").get_json()["data"]["released"],
                             "the portal asks again the next day")
        box.note_portal_release(mac)
        with unittest.mock.patch.object(ws.captive_portal, "is_ap_client", return_value=True), \
                unittest.mock.patch.object(ws.suggestions, "mac_for_ip", return_value=mac):
            self.assertTrue(guest.get("/api/portal/status").get_json()["data"]["released"])
        ws._portal_forget(mac)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


@unittest.skipUnless(flask, "Flask is not installed")
class QuietReadsTest(unittest.TestCase):
    def kept(self, line):
        record = ws.logging.LogRecord("werkzeug", ws.logging.INFO, __file__, 1, line, None, None)
        return ws._QuietReads().filter(record)

    def test_only_the_reads_that_worked_are_left_out(self):
        self.assertFalse(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "GET /api/status HTTP/1.1" 200 -'))
        self.assertFalse(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "GET /api/library?q=a HTTP/1.1" 304 -'))
        self.assertTrue(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "POST /api/action/next_track HTTP/1.1" 200 -'))
        self.assertTrue(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "GET /api/status HTTP/1.1" 500 -'))
        self.assertTrue(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "GET /hotspot-detect.html HTTP/1.1" 302 -'))
        self.assertTrue(self.kept('10.42.0.5 - - [03/Oct/2026 10:08:46] "GET / HTTP/1.1" 200 -'))


@unittest.skipUnless(flask, "Flask is not installed")
class StreamRouteTest(unittest.TestCase):
    """The network output's two doors: where it is, and the audio itself."""

    def setUp(self):
        self.addCleanup(ws.forget_stream)
        ws.forget_stream()

    def test_it_is_off_by_default_and_says_so(self):
        answer = ws.app.test_client().get("/api/stream").get_json()
        self.assertTrue(answer["ok"])
        self.assertFalse(answer["data"]["enabled"])
        self.assertFalse(answer["data"]["available"])
        self.assertEqual(answer["data"]["url"], "")

    def test_the_audio_door_refuses_when_the_stream_is_off(self):
        answer = ws.app.test_client().get("/stream.opus")
        self.assertEqual(answer.status_code, 503)
        self.assertEqual(answer.get_json()["error"], "stream_unavailable")

    def test_a_ready_stream_is_offered_with_its_url(self):
        with unittest.mock.patch.object(ws.stream_mod, "encoders_available",
                                        return_value=["opus"]):
            ready = ws.stream_mod.build(
                {"STREAM_ENABLED": True, "STREAM_SOURCE": "s.monitor"}, probe=False)
            with unittest.mock.patch.object(ws, "stream_server", return_value=ready):
                answer = ws.app.test_client().get("/api/stream").get_json()["data"]
        self.assertTrue(answer["available"])
        self.assertEqual(answer["encoder"], "opus")
        self.assertEqual(answer["content_type"], "audio/ogg")
        self.assertTrue(answer["url"].endswith("/stream.opus"), answer["url"])

    def test_the_status_carries_the_stream_for_the_player_button(self):
        """The player's button reads it from the status it already polls, so a
        device that cannot listen is never offered one. /api/status also reads
        the audio output, which needs a POSIX machine - hence the patch."""
        status = {"ok": True, "data": {"mode": "music", "epoch": 1}}
        with unittest.mock.patch.object(ws, "control", return_value=status):
            with unittest.mock.patch.object(ws, "stream_server", return_value=None):
                with unittest.mock.patch.object(ws, "_audio_output_state",
                                                return_value={"output": "bluetooth"}):
                    data = ws.app.test_client().get("/api/status").get_json()["data"]
        self.assertIn("stream", data)
        self.assertFalse(data["stream"]["available"])

    def virtual_sink(self):
        return {"name": "rukebox_output", "description": "Rukebox output",
                "kind": "docker", "codec": None, "address": None}

    def test_the_virtual_output_is_offered_as_a_choice(self):
        """It is what a container plays to: the card has to be able to say so,
        and to name the sink PipeWire reports."""
        client = ws.app.test_client()
        with unittest.mock.patch.object(ws.audio_output, "list_sinks",
                                        return_value=[self.virtual_sink()]):
            with unittest.mock.patch.object(ws, "_user_session_env", return_value={}):
                data = client.get("/api/audio/outputs").get_json()["data"]
        self.assertIn("docker", [item["kind"] for item in data["outputs"]])

    def test_testing_the_virtual_output_needs_a_listener_not_a_chime(self):
        """Playing the test sound into it would stop in the void: what the
        button can answer is whether the stream is there at all."""
        client = ws.app.test_client()
        with unittest.mock.patch.object(ws, "_user_session_env", return_value={}):
            with unittest.mock.patch.object(ws.audio_output, "list_sinks",
                                            return_value=[self.virtual_sink()]):
                with unittest.mock.patch.object(ws, "_stream_status",
                                                return_value={"available": False}):
                    answer = client.post("/api/audio/test", json={"output": "docker"})
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.get_json()["error"], "stream_unavailable")

    def test_testing_the_virtual_output_when_the_stream_is_up(self):
        client = ws.app.test_client()
        with unittest.mock.patch.object(ws, "_user_session_env", return_value={}):
            with unittest.mock.patch.object(ws.audio_output, "list_sinks",
                                            return_value=[self.virtual_sink()]):
                with unittest.mock.patch.object(ws, "_stream_status",
                                                return_value={"available": True,
                                                              "listeners": 2}):
                    answer = client.post("/api/audio/test", json={"output": "docker"})
        self.assertTrue(answer.get_json()["ok"])
        self.assertEqual(answer.get_json()["data"]["listeners"], 2)


@unittest.skipUnless(flask, "Flask is not installed")
class UpnpRouteTest(unittest.TestCase):
    """The UPnP doors: what VLC browses without ever being told an address."""

    BROWSE = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        '<s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        "<ObjectID>0</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter>"
        "<StartingIndex>0</StartingIndex><RequestedCount>5000</RequestedCount>"
        "<SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>"
    )
    BROWSE_ACTION = '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"'

    def setUp(self):
        self.addCleanup(unittest.mock.patch.stopall)
        self.addCleanup(ws.forget_stream)
        ws.forget_stream()
        was = {key: ws.cfg().get(key) for key in ("STREAM_ENABLED", "UPNP_NAME")}
        self.addCleanup(ws.update_config_file, was)
        self.client = ws.app.test_client()

    def ready_stream(self):
        with unittest.mock.patch.object(ws.stream_mod, "encoders_available",
                                        return_value=["opus"]):
            return ws.stream_mod.build({"STREAM_ENABLED": True, "STREAM_SOURCE": "s.monitor"},
                                       probe=False)

    def browse(self):
        return self.client.post("/upnp/ContentDirectory/control", data=self.BROWSE,
                                headers={"SOAPAction": self.BROWSE_ACTION})

    def test_the_device_description_is_a_media_server(self):
        answer = self.client.get("/upnp/rootDesc.xml")
        self.assertEqual(answer.status_code, 200)
        self.assertIn("urn:schemas-upnp-org:device:MediaServer:1", answer.get_data(as_text=True))
        self.assertIn("<UDN>uuid:", answer.get_data(as_text=True))

    def test_the_stream_is_offered_with_the_address_the_player_used(self):
        with unittest.mock.patch.object(ws, "stream_server", return_value=self.ready_stream()):
            answer = self.browse()
        text = answer.get_data(as_text=True)
        self.assertEqual(answer.status_code, 200)
        self.assertIn("<TotalMatches>1</TotalMatches>", text)
        self.assertIn("http://localhost/stream.opus", text)
        self.assertIn("http-get:*:audio/ogg:*", text)
        self.assertIn("&lt;DIDL-Lite", text)

    def test_nothing_is_offered_while_the_stream_is_off(self):
        with unittest.mock.patch.object(ws, "stream_server", return_value=None):
            text = self.browse().get_data(as_text=True)
        self.assertIn("<TotalMatches>0</TotalMatches>", text)
        self.assertNotIn("stream.opus", text)

    def test_a_player_never_has_to_log_in(self):
        """VLC cannot type a password: the browse is not behind one."""
        real = ws.cfg

        def fake_cfg():
            return dict(real(), WEB_PASSWORD_HASH="x")

        with unittest.mock.patch.object(ws, "cfg", side_effect=fake_cfg):
            self.assertEqual(self.client.get("/upnp/rootDesc.xml").status_code, 200)
            self.assertEqual(self.browse().status_code, 200)
            self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_a_client_that_watches_for_changes_is_answered(self):
        """Cling-based players subscribe before browsing, and stop if refused."""
        answer = self.client.open("/upnp/ContentDirectory/event", method="SUBSCRIBE")
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.headers["SID"].startswith("uuid:"))
        self.assertEqual(answer.headers["TIMEOUT"], "Second-1800")

    def test_a_service_description_is_there_and_an_unknown_one_is_not(self):
        self.assertEqual(self.client.get("/upnp/ContentDirectory/scpd.xml").status_code, 200)
        self.assertEqual(self.client.get("/upnp/ConnectionManager/scpd.xml").status_code, 200)
        self.assertEqual(self.client.get("/upnp/Nonsense/scpd.xml").status_code, 404)
        self.assertEqual(self.client.post("/upnp/Nonsense/control").status_code, 404)

    def test_the_device_only_exists_while_the_stream_is_on(self):
        """An empty folder in a player stays until the player is restarted: the
        announcement comes and goes with the stream instead."""
        with unittest.mock.patch.object(ws.upnp, "start") as started:
            with unittest.mock.patch.object(ws.upnp, "stop") as stopped:
                with unittest.mock.patch.object(ws, "_stream_available", return_value=False):
                    ws._upnp_follow_stream()
                self.assertTrue(stopped.called, "nothing to offer: no device at all")
                self.assertFalse(started.called)
                stopped.reset_mock()
                with unittest.mock.patch.object(ws, "_stream_available", return_value=True):
                    ws._upnp_follow_stream()
                self.assertTrue(started.called, "the entry appears without restarting VLC")
                self.assertFalse(stopped.called)

    def test_the_announcement_has_no_switch_of_its_own(self):
        """Asked for as: the announcement is on exactly when the stream is, so
        an option that always moves with another one is one option too many."""
        with unittest.mock.patch.object(ws, "_stream_available", return_value=True):
            with unittest.mock.patch.object(ws, "cfg", return_value={"WEB_PORT": 80}):
                with unittest.mock.patch.object(ws.upnp, "start") as started:
                    with unittest.mock.patch.object(ws.upnp, "stop") as stopped:
                        ws._upnp_follow_stream()
        self.assertTrue(started.called, "the stream is on: the radio announces itself")
        self.assertFalse(stopped.called)

    def test_the_name_is_drawn_once_with_its_number_and_kept(self):
        """One field, holding the whole name: "Rukebox 4821" is what a player
        lists, and what the owner can rewrite in one go."""
        with unittest.mock.patch.object(ws, "update_config_file") as written:
            name = ws._ensure_upnp_identity({"UPNP_NAME": "", "UPNP_UID": ""})
        self.assertRegex(name, r"^Rukebox \d{4}$")
        written_values = written.call_args[0][0]
        self.assertEqual(written_values["UPNP_NAME"], name)
        self.assertRegex(written_values["UPNP_UID"], r"^[0-9a-f]{8}$")
        self.assertEqual(ws.upnp.device_name(), name, "the whole name, nothing appended")
        self.addCleanup(ws.upnp.configure, ws.upnp.DEFAULT_NAME, "")

    def test_a_name_already_there_is_kept(self):
        with unittest.mock.patch.object(ws, "update_config_file") as written:
            self.assertEqual(
                ws._ensure_upnp_identity({"UPNP_NAME": "Cuisine 0042", "UPNP_UID": "abcd1234"}),
                "Cuisine 0042")
        self.assertFalse(written.called)
        self.addCleanup(ws.upnp.configure, ws.upnp.DEFAULT_NAME, "")

    def test_renaming_relabels_the_device_instead_of_adding_one(self):
        """A player remembers the identity, not the name: renaming must not
        leave it listing the same radio twice."""
        with unittest.mock.patch.object(ws, "update_config_file"):
            ws._ensure_upnp_identity({"UPNP_NAME": "Rukebox 1111", "UPNP_UID": "aaaa1111"})
            first = ws.upnp.udn()
            ws._ensure_upnp_identity({"UPNP_NAME": "Cuisine 2222", "UPNP_UID": "aaaa1111"})
            self.assertEqual(ws.upnp.udn(), first, "same radio, another label")
            self.assertEqual(ws.upnp.device_name(), "Cuisine 2222")
            ws._ensure_upnp_identity({"UPNP_NAME": "Cuisine 2222", "UPNP_UID": "bbbb2222"})
            self.assertNotEqual(ws.upnp.udn(), first, "another radio is another device")
        self.addCleanup(ws.upnp.configure, ws.upnp.DEFAULT_NAME, "")

    def test_the_item_says_when_the_radio_has_nothing_to_send(self):
        """A player meeting a silent stream cannot tell it from a broken one -
        and a Pi whose speaker is away reports "music, not paused" with no track
        loaded at all, which is the state the owner heard nothing in."""
        def status(**fields):
            return unittest.mock.patch.object(ws, "control",
                                              return_value={"ok": True, "data": fields})

        with status(mode="music", paused=False, current_track_path="/m/a.mp3", sound=""):
            self.assertFalse(ws._upnp_title().endswith(")"), "music plays: nothing to say")
        with status(mode="music", paused=True, current_track_path="/m/a.mp3", sound=""):
            self.assertTrue(ws._upnp_title().endswith("(paused)"), ws._upnp_title())
        with status(mode="music", paused=False, current_track_path=None, sound=""):
            self.assertTrue(ws._upnp_title().endswith("(idle)"), ws._upnp_title())
        with status(mode="stopped", paused=False, current_track_path="/m/a.mp3", sound=""):
            self.assertTrue(ws._upnp_title().endswith("(idle)"),
                            "a remembered track is not a track playing: " + ws._upnp_title())
        with status(mode="idle", paused=False, current_track_path=None, sound="/m/jingle.wav"):
            self.assertFalse(ws._upnp_title().endswith(")"),
                             "an announcement is heard, whatever the mode")
        with unittest.mock.patch.object(ws, "control",
                                        return_value={"ok": False,
                                                      "error": "daemon_unreachable"}):
            self.assertFalse(ws._upnp_title().endswith(")"), "no daemon, no excuse")

    def test_a_silent_stream_is_reconnected_when_the_radio_plays(self):
        """Measured on a Pi: an encoder that attached while the sink was silent
        produced its headers and not one audio page afterwards, even once the
        music was back."""
        stalled = unittest.mock.Mock(source="auto_monitor",
                                     stalled_for=unittest.mock.Mock(return_value=99.0))
        ws._stream_stalls["tries"] = 0
        self.addCleanup(ws._stream_stalls.update, tries=0)
        with unittest.mock.patch.object(ws, "stream_server", return_value=stalled):
            with unittest.mock.patch.object(ws, "_daemon_status",
                                            return_value={"mode": "music",
                                                          "current_track_path": "/m/a.mp3"}):
                self.assertTrue(ws._reconnect_a_silent_stream())
        self.assertTrue(stalled.restart.called, "the capture is taken again")
        self.assertFalse(stalled.stop.called, "without ending the players")

    def test_a_young_encoder_is_given_its_time(self):
        """It has written nothing yet, which is not the same as stalled, and the
        first listener of an encoder that has just been started is right there:
        reconnecting under it would end its stream."""
        young = unittest.mock.Mock(source="auto_monitor",
                                   stalled_for=unittest.mock.Mock(return_value=0.5))
        with unittest.mock.patch.object(ws, "stream_server", return_value=young):
            with unittest.mock.patch.object(ws, "_daemon_status",
                                            return_value={"mode": "music",
                                                          "current_track_path": "/m/a.mp3"}):
                self.assertFalse(ws._reconnect_a_silent_stream())
        self.assertFalse(young.restart.called)

    def test_a_stream_that_carries_something_is_left_alone(self):
        busy = unittest.mock.Mock(source="auto_monitor",
                                  stalled_for=unittest.mock.Mock(return_value=1.0))
        with unittest.mock.patch.object(ws, "stream_server", return_value=busy):
            self.assertFalse(ws._reconnect_a_silent_stream())
        self.assertFalse(busy.restart.called)

    def test_a_quiet_radio_is_not_an_encoder_to_reconnect(self):
        stalled = unittest.mock.Mock(source="auto_monitor",
                                     stalled_for=unittest.mock.Mock(return_value=99.0))
        with unittest.mock.patch.object(ws, "stream_server", return_value=stalled):
            with unittest.mock.patch.object(ws, "_daemon_status", return_value={}):
                self.assertFalse(ws._reconnect_a_silent_stream())
                with unittest.mock.patch.object(
                        ws, "_daemon_status",
                        return_value={"mode": "idle", "current_track_path": None,
                                      "paused": False}):
                    self.assertFalse(ws._reconnect_a_silent_stream())
        self.assertFalse(stalled.restart.called)

    def test_it_gives_up_rather_than_restarting_for_ever(self):
        stalled = unittest.mock.Mock(source="auto_monitor",
                                     stalled_for=unittest.mock.Mock(return_value=99.0))
        ws._stream_stalls["tries"] = ws.STREAM_STALL_ATTEMPTS
        self.addCleanup(ws._stream_stalls.update, tries=0)
        with unittest.mock.patch.object(ws, "stream_server", return_value=stalled):
            with unittest.mock.patch.object(ws, "_daemon_status",
                                            return_value={"mode": "music",
                                                          "current_track_path": "/m/a.mp3"}):
                self.assertFalse(ws._reconnect_a_silent_stream())
        self.assertFalse(stalled.restart.called)

    def test_saving_the_stream_switch_announces_it_at_once(self):
        """A save is enough: turning the stream on adds the radio to a player
        that is already open, without restarting anything."""
        with unittest.mock.patch.object(ws, "_upnp_follow_stream") as followed:
            self.client.post("/api/settings", json={"STREAM_ENABLED": True})
        self.assertTrue(followed.called)

    def test_a_stream_that_is_off_is_not_announced_at_all(self):
        """With nothing to stream there is no device to add to a player, which
        is what the owner saw as an empty folder."""
        with unittest.mock.patch.object(ws, "_stream_available", return_value=False):
            with unittest.mock.patch.object(ws.upnp, "start") as started:
                with unittest.mock.patch.object(ws.upnp, "stop") as stopped:
                    self.client.post("/api/settings", json={"STREAM_ENABLED": True})
        self.assertTrue(stopped.called)
        self.assertFalse(started.called)

    def test_the_stream_settings_apply_without_a_restart(self):
        """The encoder is rebuilt on the next status read, so the card says so
        rather than sending the owner to a restart button."""
        with unittest.mock.patch.object(ws, "forget_stream") as forgotten:
            answer = self.client.post("/api/settings", json={"STREAM_ENABLED": True}).get_json()
        self.assertTrue(forgotten.called)
        self.assertFalse(answer["data"]["restart_needed"])



@unittest.skipUnless(flask, "Flask is not installed")
class SpeakerNudgeTest(unittest.TestCase):
    """The page sees the speaker go: the daemon is told at once."""

    def setUp(self):
        ws._speaker_seen.clear()
        self.sent = []
        patcher = unittest.mock.patch.object(
            ws.threading, "Thread",
            side_effect=lambda target, args, daemon: types.SimpleNamespace(
                start=lambda: self.sent.append(args[0])))
        patcher.start()
        self.addCleanup(patcher.stop)

    def link(self, connected, unknown=False):
        return {"connected": connected, "unknown": unknown}

    def test_only_a_change_is_sent(self):
        ws._tell_daemon_if_speaker_changed("M", self.link(True))
        ws._tell_daemon_if_speaker_changed("M", self.link(True))
        self.assertEqual(self.sent, [])
        ws._tell_daemon_if_speaker_changed("M", self.link(False))
        self.assertEqual(self.sent, ["speaker_check"])
        ws._tell_daemon_if_speaker_changed("M", self.link(True))
        self.assertEqual(self.sent, ["speaker_check", "speaker_check"])

    def test_no_answer_is_not_a_change(self):
        ws._tell_daemon_if_speaker_changed("M", self.link(True))
        ws._tell_daemon_if_speaker_changed("M", self.link(False, unknown=True))
        self.assertEqual(self.sent, [])


@unittest.skipUnless(flask, "Flask is not installed")
class RepairAfterRestoreTest(unittest.TestCase):
    """A restored configuration keeps addresses, never pairings: "To finish" says so."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        values = dict(ws.cfg(), SPEAKER_MAC="7C:E9:13:69:66:55", AUDIO_OUTPUT="bluetooth",
                      STATE_DIR=self.dir, WEB_PASSWORD_HASH="", SETUP_HIDDEN="")
        for target, value in (("cfg", lambda: dict(values)), ("_ap_is_open", lambda: False),
                              ("_storages", lambda: []), ("_timezone_name", lambda: "Europe/Paris"),
                              ("control", lambda *a, **k: {"ok": True, "data": {"track_count": 10}})):
            patcher = unittest.mock.patch.object(ws, target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def items(self, paired, flic_count):
        with unittest.mock.patch.object(ws, "_speaker_paired", return_value=paired), \
                unittest.mock.patch.object(ws, "_flic_button_count", return_value=flic_count):
            return ws.app.test_client().get("/api/setup/pending").get_json()["data"]["items"]

    def test_a_speaker_known_by_address_only_asks_to_be_paired(self):
        self.assertIn("speaker_pair", self.items(False, None))
        self.assertNotIn("speaker_pair", self.items(True, None))
        self.assertNotIn("speaker_pair", self.items(None, None), "no answer is not 'unpaired'")

    def test_flic_buttons_are_asked_for_until_one_is_paired(self):
        with open(os.path.join(self.dir, "repair.json"), "w") as f:
            f.write('{"flic_buttons": 1}')
        self.assertIn("flic_pair", self.items(True, None))
        self.assertIn("flic_pair", self.items(True, 0))
        self.assertNotIn("flic_pair", self.items(True, 1))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "repair.json")), "done once and for all")
        self.assertNotIn("flic_pair", self.items(True, 0))


@unittest.skipUnless(flask, "Flask is not installed")
class GuestLockTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(unittest.mock.patch.stopall)
        self.values = {"WEB_PASSWORD_HASH": "x", "GUEST_MODE_ENABLED": True, "GUEST_QUOTA_ENABLED": False,
                       "GUEST_LOCKED": "next, volume, nonsense"}
        real = ws.cfg

        def fake_cfg():
            values = dict(real())
            values.update(self.values)
            return values

        unittest.mock.patch.object(ws, "cfg", side_effect=fake_cfg).start()
        unittest.mock.patch.object(ws, "control", return_value={"ok": True, "data": {}}).start()
        self.client = ws.app.test_client()

    def test_a_locked_command_is_refused_to_a_guest(self):
        self.assertEqual(ws._guest_locked(), ["next", "volume"], "unknown names are ignored")
        r = self.client.post("/api/action/next_track")
        self.assertEqual((r.status_code, r.get_json()["error"]), (403, "guest_locked"))
        self.assertEqual(self.client.post("/api/volume", json={"volume": 50}).status_code, 403)
        self.assertNotEqual(self.client.post("/api/action/previous_track").status_code, 403)

    def test_the_owner_is_never_locked(self):
        unittest.mock.patch.object(ws, "_is_authenticated", return_value=True).start()
        self.assertNotEqual(self.client.post("/api/action/next_track").status_code, 403)

    def test_a_page_hidden_from_guests_refuses_what_it_asks_for(self):
        self.values["GUEST_PAGES_OFF"] = "game, today, nonsense"
        self.assertEqual(ws._guest_pages_off(), ["game", "today"])
        self.assertIn(self.client.get("/api/game").status_code, (401, 403))
        self.assertIn(self.client.get("/api/today").status_code, (401, 403))
        self.assertNotIn(self.client.get("/api/recent").status_code, (401, 403))
        unittest.mock.patch.object(ws, "_is_authenticated", return_value=True).start()
        self.assertNotIn(self.client.get("/api/today").status_code, (401, 403), "the owner sees every page")



@unittest.skipUnless(flask, "Flask is not installed (run these on the Pi)")
class DedicationAndReminderTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(unittest.mock.patch.stopall)
        self.values = {"WEB_PASSWORD_HASH": "", "DEDICATIONS_ENABLED": False, "ACTION_REPEAT_SEC": 0}
        real = ws.cfg

        def fake_cfg():
            values = dict(real())
            values.update(self.values)
            return values

        unittest.mock.patch.object(ws, "cfg", side_effect=fake_cfg).start()
        unittest.mock.patch.object(ws, "_path_for_key", return_value="/music/a.mp3").start()
        unittest.mock.patch.object(ws, "_this_device", return_value={"id": "d1", "person": "p1", "name": "Renard"}).start()
        # The device base lives in /var/lib/rukebox, which a CI runner cannot create.
        unittest.mock.patch.object(ws, "_suggestion_box", return_value=None).start()
        self.control = unittest.mock.patch.object(ws, "control", return_value={"ok": True, "data": {}}).start()
        self.client = ws.app.test_client()

    def test_a_message_needs_dedications_on(self):
        r = self.client.post("/api/library/queue", json={"key": "k", "message": "hello"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (403, "dedications_off"))
        self.client.post("/api/library/queue", json={"key": "k"})
        self.assertEqual(self.control.call_args.kwargs, {"path": "/music/a.mp3", "person": "p1"})

    def test_a_message_goes_with_the_song_and_its_sender(self):
        self.values["DEDICATIONS_ENABLED"] = True
        r = self.client.post("/api/library/queue", json={"key": "k", "message": "  for   you  "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.control.call_args.kwargs["dedication"], {"from": "Renard", "text": "for you"})

    def test_a_reminder_in_minutes_or_at_a_time(self):
        before = time.time()
        self.client.post("/api/reminders", json={"text": "cake", "minutes": 20})
        at = self.control.call_args.kwargs["at"]
        self.assertAlmostEqual(at - before, 1200, delta=5)
        self.client.post("/api/reminders", json={"text": "cake", "time": "07:30"})
        when = datetime.fromtimestamp(self.control.call_args.kwargs["at"])
        self.assertEqual((when.hour, when.minute), (7, 30))
        self.assertGreater(when.timestamp(), time.time())
        self.assertEqual(self.client.post("/api/reminders", json={"text": "x", "minutes": 0}).status_code, 400)
        self.assertEqual(self.client.post("/api/reminders", json={"text": "x", "time": "25:99x"}).status_code, 400)


@unittest.skipUnless(flask, "Flask is not installed (run these on the Pi)")
class RepeatedActionTest(unittest.TestCase):
    def setUp(self):
        ws._repeats.clear()
        self.calls = []
        self.delay = 0.0
        self.addCleanup(unittest.mock.patch.stopall)
        unittest.mock.patch.object(ws, "_quota_applies", return_value=False).start()
        unittest.mock.patch.object(ws, "_repeat_person",
                                   side_effect=lambda: ws.request.headers.get("X-Who", "a")).start()
        unittest.mock.patch.object(ws, "ACTION_REPEAT_SEC", 1.0).start()

        def control(cmd, **kw):
            self.calls.append((cmd, kw))
            time.sleep(self.delay)
            return {"ok": True, "data": {"cmd": cmd}}
        unittest.mock.patch.object(ws, "control", side_effect=control).start()
        self.client = ws.app.test_client()

    def post(self, path, who="a", **body):
        return self.client.post(path, json=body or None, headers={"X-Who": who})

    def cmds(self):
        return [c for c, _ in self.calls]

    def test_next_previous_and_back_to_the_start_are_one_family(self):
        self.post("/api/action/next_track", "a")
        r = self.post("/api/action/previous_track", "b")
        self.assertEqual(self.cmds(), ["next_track"], "someone else's Previous right after a Next is not run")
        self.assertEqual(r.get_json()["data"]["cmd"], "next_track", "it gets the answer of what did happen")
        self.post("/api/action/previous_track", "a")
        self.assertEqual(self.cmds(), ["next_track", "previous_track"],
                         "the one who just acted may go on (a second Previous goes further back)")

    def test_the_window_runs_from_the_end_of_a_fade(self):
        self.delay = 1.3
        first = threading.Thread(target=lambda: self.post("/api/action/next_track", "a"))
        first.start()
        time.sleep(1.1)
        self.post("/api/action/next_track", "b")
        first.join()
        self.delay = 0
        self.post("/api/action/next_track", "c")
        self.assertEqual(self.cmds(), ["next_track"], "pressed during the fade, and just after it: one song")

    def test_a_volume_is_one_hand_on_the_dial(self):
        for value in (40, 41, 43):
            self.post("/api/volume", "a", value=value)
        self.post("/api/volume", "b", value=42)
        self.assertEqual([kw["value"] for c, kw in self.calls if c == "set_volume"], [40, 41, 43],
                         "a slider drag goes through, a near value from someone else does not")

    def test_a_double_tap_on_a_toggle_is_one_tap(self):
        self.post("/api/action/toggle_pause", "a")
        self.post("/api/action/toggle_pause", "a")
        self.post("/api/mute", "a", on="toggle")
        self.post("/api/mute", "a", on="toggle")
        self.assertEqual(self.cmds(), ["toggle_pause", "set_mute"])

    def test_three_presses_at_once_run_once(self):
        self.delay = 0.3
        threads = [threading.Thread(target=lambda w=w: self.post("/api/action/toggle_pause", w)) for w in "abc"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.cmds(), ["toggle_pause"])

    def test_two_songs_queued_by_two_people_are_both_queued(self):
        unittest.mock.patch.object(ws, "_path_for_key", side_effect=lambda key: "/music/" + key).start()
        self.post("/api/library/queue", "a", key="k1")
        self.post("/api/library/queue", "b", key="k2")
        self.post("/api/library/queue", "c", key="k1")
        self.assertEqual([kw["path"] for c, kw in self.calls if c == "queue_track"],
                         ["/music/k1", "/music/k2"], "the same song asked twice is queued once")

    def test_a_failure_is_not_repeated(self):
        results = iter([{"ok": False, "error": "x"}, {"ok": True}])
        ws.control.side_effect = lambda *a, **k: next(results)
        self.assertEqual(self.post("/api/action/next_track", "a").status_code, 400)
        self.assertEqual(self.post("/api/action/next_track", "b").status_code, 200)


@unittest.skipUnless(flask, "Flask is not installed (run these on the Pi)")
class TransferLimitTest(unittest.TestCase):
    def limit(self, bus, **settings):
        values = {"TRANSFER_LIMIT_MODE": "auto", "TRANSFER_LIMIT_KBPS": 200, "AUDIO_OUTPUT": "bluetooth",
                  "SPEAKER_BT_ADAPTER": "AA:AA:AA:AA:AA:AA"}
        values.update(settings)
        playing = {"ok": True, "data": {"speaker_connected": True, "paused": False, "mode": "music"}}
        with unittest.mock.patch.object(ws, "cfg", return_value=values), \
                unittest.mock.patch.object(ws, "_bt_controllers",
                                           return_value=[{"address": "AA:AA:AA:AA:AA:AA", "bus": bus, "name": "hci0"}]), \
                unittest.mock.patch.object(ws, "_resolve_controller", side_effect=lambda value, found: found[0]), \
                unittest.mock.patch.object(ws, "control", return_value=playing):
            return ws._transfer_limit()

    def test_a_speaker_on_a_dongle_is_limited_only_when_asked(self):
        self.assertEqual(self.limit("uart"), 200 * 1024, "the built-in chip: limited, as before")
        self.assertIsNone(self.limit("usb"), "a dongle: left alone by default")
        self.assertEqual(self.limit("usb", TRANSFER_LIMIT_USB=True), 200 * 1024, "unless the option says so")


@unittest.skipUnless(flask, "Flask is not installed (run these on the Pi)")
class RepeatWindowSettingTest(unittest.TestCase):
    def test_the_window_comes_from_the_settings_and_zero_turns_it_off(self):
        ws._repeats.clear()
        calls = []
        with unittest.mock.patch.object(ws, "ACTION_REPEAT_SEC", None), \
                unittest.mock.patch.object(ws, "cfg", return_value=dict(ws.cfg(), ACTION_REPEAT_SEC=0)), \
                unittest.mock.patch.object(ws, "_quota_applies", return_value=False), \
                unittest.mock.patch.object(ws, "_require_auth", return_value=None), \
                unittest.mock.patch.object(ws, "control",
                                           side_effect=lambda cmd, **kw: calls.append(cmd) or {"ok": True}):
            self.assertEqual(ws._repeat_window(), 0)
            client = ws.app.test_client()
            client.post("/api/action/toggle_pause")
            client.post("/api/action/toggle_pause")
        self.assertEqual(calls.count("toggle_pause"), 2, "with 0, every press runs")


class GameRouteTest(unittest.TestCase):
    def setUp(self):
        ws._game = None
        self.addCleanup(setattr, ws, "_game", None)
        self.addCleanup(unittest.mock.patch.stopall)
        patch = unittest.mock.patch.object
        patch(ws, "_require_auth", return_value=None).start()
        patch(ws, "_game_player", side_effect=lambda: (ws.request.headers.get("X-Who", "a"), "Name")).start()
        rows = [{"path": "/m/%d" % i, "title": "Song %d" % i, "artist": "A%d" % i, "duration": 200}
                for i in range(12)]
        patch(ws, "_get_library", return_value=unittest.mock.Mock(quiz_tracks=lambda: rows)).start()
        self.started = patch(ws.threading, "Thread").start()
        patch(ws, "stats").start()
        self.client = ws.app.test_client()

    def test_a_game_is_started_answered_and_stopped(self):
        self.assertEqual(self.client.get("/api/game").get_json()["data"]["state"], "none")
        self.assertEqual(self.client.post("/api/game/start", json={"rounds": 7}).status_code, 400)
        self.assertTrue(self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20}).get_json()["ok"])
        self.started.return_value.start.assert_called_once()
        self.assertEqual(self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20}).get_json()["error"],
                         "game_running")
        question = ws._game.next_question()
        r = self.client.post("/api/game/answer", json={"choice": question["answer"]}, headers={"X-Who": "b"})
        self.assertEqual(r.get_json()["data"]["mine"], question["answer"])
        self.assertNotIn("answer", r.get_json()["data"], "the answer stays hidden until the reveal")
        self.client.post("/api/game/stop")
        self.assertTrue(ws._game.stopped)

    def test_a_library_too_small_is_refused(self):
        ws._get_library.return_value = unittest.mock.Mock(quiz_tracks=lambda: [])
        r = self.client.post("/api/game/start", json={"rounds": 5, "seconds": 20})
        self.assertEqual(r.get_json()["error"], "game_not_enough_tracks")


class SkipVoteTest(unittest.TestCase):
    def setUp(self):
        ws._skip_votes.update(key=None, persons=set())
        ws._repeats.clear()
        self.calls = []
        self.present = {"a": 1.0, "b": 2.0, "c": 3.0}
        self.addCleanup(unittest.mock.patch.stopall)
        patch = unittest.mock.patch.object
        patch(ws, "_require_auth", return_value=None).start()
        patch(ws, "_quota_applies", return_value=False).start()
        patch(ws, "_repeat_person", side_effect=lambda: ws.request.headers.get("X-Who", "a")).start()
        patch(ws, "_recently_seen", side_effect=lambda: dict(self.present)).start()
        patch(ws.track_media, "track_key", side_effect=lambda path: path and "key:" + path).start()
        patch(ws, "stats").start()
        self.track = "/m/a.mp3"

        def control(cmd, **kw):
            self.calls.append((cmd, kw))
            return {"ok": True, "data": {"mode": "music", "current_track_path": self.track}}
        patch(ws, "control", side_effect=control).start()
        self.client = ws.app.test_client()

    def vote(self, who):
        return self.client.post("/api/vote/skip", json={}, headers={"X-Who": who})

    def test_a_majority_skips_the_song_once(self):
        first = self.vote("a").get_json()
        self.assertEqual((first["data"]["votes"], first["data"]["needed"]), (1, 2))
        self.assertFalse(first["data"]["skipped"])
        self.assertEqual(self.vote("a").get_json()["data"]["votes"], 1, "one vote per person")
        second = self.vote("b").get_json()
        self.assertTrue(second["data"]["skipped"])
        self.assertIn(("next_track", {"source": "vote"}), self.calls)

    def test_a_new_song_starts_a_new_vote(self):
        self.vote("a")
        self.track = "/m/b.mp3"
        self.assertEqual(self.vote("b").get_json()["data"]["votes"], 1)

    def test_alone_there_is_nothing_to_vote(self):
        self.present = {}
        r = self.vote("a")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "vote_unavailable")

    def test_the_share_is_more_than_the_setting(self):
        self.present = {k: 1.0 for k in "abcd"}
        with unittest.mock.patch.object(ws, "cfg", return_value=dict(ws.cfg(), SKIP_VOTE_SHARE=50)):
            self.assertEqual(ws._skip_vote_state("k", "a")["needed"], 3, "more than half of four")
        with unittest.mock.patch.object(ws, "cfg", return_value=dict(ws.cfg(), SKIP_VOTE_ENABLED=False)):
            self.assertIsNone(ws._skip_vote_state("k", "a"))


@unittest.skipUnless(flask, "Flask is not installed")
class ExcludedTracksTest(unittest.TestCase):
    """The excluded page's routes: the radio stops picking what is excluded,
    nothing is deleted, and a list still gets its own tracks back."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.music = os.path.join(self.dir, "music")
        self.db_file = os.path.join(self.dir, "library.db")
        self.hidden_file = os.path.join(self.dir, "hidden.json")
        self.lists_file = os.path.join(self.dir, "music_lists.json")
        self.state_dir = os.path.join(self.dir, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        lib = library.Library(self.db_file, track_media.track_key)
        self.tracks = {}
        for name in ("Alpha/one.mp3", "Alpha/two.mp3", "Beta/three.mp3"):
            path = os.path.join(self.music, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x" * 10)
            self.tracks[name] = path
        lib.sync(list(self.tracks.values()), self.music)
        self.keys = {name: lib.item_for_path(path)["key"] for name, path in self.tracks.items()}

        self.addCleanup(unittest.mock.patch.stopall)
        self.calls = []
        values = dict(ws.cfg(), WEB_PASSWORD_HASH=web_auth.hash_password("secret"),
                      GUEST_MODE_ENABLED=True, STATE_DIR=self.state_dir,
                      MUSIC_DIR=self.music, MUSIC_CACHE_FILE=os.path.join(self.dir, "cache.json"),
                      LIBRARY_DB_FILE=self.db_file, HIDDEN_FILE=self.hidden_file,
                      MUSIC_LISTS_FILE=self.lists_file,
                      LIKES_FILE=os.path.join(self.dir, "likes.json"))
        for target, value in (
                ("cfg", lambda: dict(values)),
                ("_get_library", lambda: lib),
                ("control", lambda cmd, **kw: self.calls.append((cmd, kw)) or {"ok": True}),
                ("stats", unittest.mock.Mock()),
                ("_warm_track_media", lambda *a, **k: None)):
            patcher = unittest.mock.patch.object(ws, target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lib._db.close)

    def owner(self):
        client = ws.app.test_client()
        client.post("/api/auth/login", json={"password": "secret"})
        return client

    def test_nothing_is_excluded_at_first(self):
        data = self.owner().get("/api/excluded").get_json()["data"]
        self.assertEqual((data["items"], data["count"]), ([], 0))

    def test_a_track_is_excluded_and_the_daemon_is_told(self):
        owner = self.owner()
        r = owner.post("/api/excluded", json={"keys": [self.keys["Alpha/one.mp3"]]}).get_json()
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["data"]["count"], r["data"]["total"]), (1, 1))
        self.assertIn(("reload_hidden", {}), self.calls)
        item = owner.get("/api/excluded").get_json()["data"]["items"][0]
        self.assertEqual((item["title"], item["artist"], item["origin"], item["missing"]),
                         ("one", "Alpha", "manual", False))
        self.assertGreater(item["excluded_at"], 0)

    def test_a_whole_filter_is_excluded_in_one_go(self):
        owner = self.owner()
        r = owner.post("/api/excluded/filter", json={"artist": "Alpha"}).get_json()
        self.assertEqual(r["data"]["count"], 2, "both tracks of the artist, not one page of them")
        items = owner.get("/api/excluded").get_json()["data"]["items"]
        self.assertEqual({item["title"] for item in items}, {"one", "two"})
        self.assertEqual({item["origin"] for item in items}, {"filter"})

    def test_a_filter_needs_something_to_filter_on(self):
        r = self.owner().post("/api/excluded/filter", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "excluded_filter_required")

    def test_a_filter_that_matches_nothing_excludes_nothing(self):
        r = self.owner().post("/api/excluded/filter", json={"artist": "Nobody"}).get_json()
        self.assertEqual(r["data"]["count"], 0)
        self.assertEqual(self.owner().get("/api/excluded").get_json()["data"]["count"], 0)

    def test_a_track_the_library_does_not_know_excludes_nothing(self):
        for body in ({}, {"keys": []}, {"keys": ["nonsense"]}):
            r = self.owner().post("/api/excluded", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.get_json()["error"], "excluded_key_required")

    def test_the_list_comes_a_page_at_a_time(self):
        owner = self.owner()
        owner.post("/api/excluded", json={"keys": list(self.keys.values())})
        first = owner.get("/api/excluded?offset=0&limit=2").get_json()["data"]
        self.assertEqual((len(first["items"]), first["count"]), (2, 3),
                         "count is the whole list, not the page")
        rest = owner.get("/api/excluded?offset=2&limit=2").get_json()["data"]
        self.assertEqual(len(rest["items"]), 1)
        keys = [item["key"] for item in first["items"] + rest["items"]]
        self.assertEqual(len(set(keys)), 3, "no track twice, none missing")
        for query in ("offset=x&limit=y", "offset=-5", "limit=0"):
            self.assertEqual(owner.get("/api/excluded?" + query).get_json()
                             ["data"]["items"][0]["key"] in self.keys.values(), True, query)

    def test_putting_one_back_and_then_every_one(self):
        owner = self.owner()
        owner.post("/api/excluded", json={"keys": list(self.keys.values())})
        self.assertEqual(owner.get("/api/excluded").get_json()["data"]["count"], 3)
        r = owner.post("/api/excluded/restore", json={"keys": [self.keys["Beta/three.mp3"]]}).get_json()
        self.assertEqual((r["data"]["count"], r["data"]["total"]), (1, 2))
        r = owner.post("/api/excluded/restore", json={"all": True}).get_json()
        self.assertEqual((r["data"]["count"], r["data"]["total"]), (2, 0))
        self.assertEqual(owner.post("/api/excluded/restore", json={}).status_code, 400)

    def test_the_library_marks_what_is_excluded(self):
        owner = self.owner()
        owner.post("/api/excluded", json={"keys": [self.keys["Alpha/one.mp3"]]})
        found = owner.get("/api/library?q=Alpha").get_json()["data"]["items"]
        marked = {item["title"]: item["excluded"] for item in found}
        self.assertEqual(marked, {"one": True, "two": False})

    def test_up_next_and_recently_played_carry_the_mark(self):
        owner = self.owner()
        owner.post("/api/excluded", json={"keys": [self.keys["Alpha/one.mp3"]]})
        path = self.tracks["Alpha/one.mp3"]
        with unittest.mock.patch.object(ws, "control", return_value={"ok": True, "data": {
                "paths": [path], "requested": []}}):
            items = owner.get("/api/queue").get_json()["data"]["items"]
        self.assertEqual([item["excluded"] for item in items], [True])
        with open(os.path.join(self.state_dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"recent": [{"path": path, "at": 1.0}]}, f)
        recent = owner.get("/api/recent").get_json()["data"]["items"]
        self.assertEqual([item["excluded"] for item in recent], [True])

    def test_a_list_keeps_its_track_and_says_it_is_excluded(self):
        owner = self.owner()
        list_id = owner.post("/api/lists", json={"name": "Soir", "kind": "manual"}).get_json()["data"]["id"]
        owner.post("/api/lists/%s/tracks" % list_id, json={"key": self.keys["Alpha/one.mp3"]})
        owner.post("/api/excluded", json={"keys": [self.keys["Alpha/one.mp3"]]})
        contents = owner.get("/api/lists/%s/tracks" % list_id).get_json()["data"]
        self.assertEqual([(item["title"], item["excluded"]) for item in contents["items"]],
                         [("one", True)], "nothing is taken out of a list")

    def test_a_guest_cannot_exclude_anything(self):
        owner = self.owner()
        owner.post("/api/excluded", json={"keys": [self.keys["Alpha/one.mp3"]]})
        guest = ws.app.test_client()
        self.assertEqual(guest.get("/api/excluded").status_code, 401)
        self.assertEqual(guest.post("/api/excluded", json={"keys": []}).status_code, 401)
        self.assertEqual(guest.post("/api/excluded/restore", json={"all": True}).status_code, 401)
        self.assertEqual(owner.get("/api/excluded").get_json()["data"]["count"], 1,
                         "and the guest changed nothing")

    def test_the_duplicates_card_still_uses_the_same_list(self):
        owner = self.owner()
        key = self.keys["Alpha/one.mp3"]
        owner.post("/api/library/hide", json={"key": key, "hidden": True,
                                              "path": self.tracks["Alpha/one.mp3"]})
        items = owner.get("/api/excluded").get_json()["data"]["items"]
        self.assertEqual([item["origin"] for item in items], ["duplicate"])


if __name__ == "__main__":
    unittest.main()

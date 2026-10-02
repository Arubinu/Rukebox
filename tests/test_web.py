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
import os
import shutil
import tempfile
import time
import types
import unittest
import unittest.mock

import _path  # noqa: F401

try:
    import flask  # noqa: F401
except ImportError:
    flask = None

TMP = tempfile.mkdtemp()
if flask:
    # Read by web_server at import time; nothing of the real installation is opened for writing.
    os.environ["STATS_DB_FILE"] = os.path.join(TMP, "stats.db")
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

    def owner(self):
        client = ws.app.test_client()
        client.post("/api/auth/login", json={"password": "secret"})
        return client

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


if __name__ == "__main__":
    unittest.main()

"""Accounts and roles: who may log in, and what each role reaches.

The store is the `people` block of rukebox.yaml, the ladder is src/roles.py,
and the gate that reads them is the web server's. src/web_auth.py holds the
hashing, unchanged.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import accounts
import music_lists
import config_document
import roles
import web_auth

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


class AccountStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)

    def test_an_account_is_a_name_a_role_and_a_password(self):
        made = accounts.add({"name": "Marie", "role": "member", "password": "un-mot-de-passe"},
                            self.path)
        self.assertEqual((made["id"], made["name"], made["role"]), ("marie", "Marie", "member"))
        self.assertTrue(web_auth.is_hash(made["hash"]), "the password is stored as a hash")
        self.assertNotIn("un-mot-de-passe", str(made))
        stored = accounts.by_id("marie", self.path)
        self.assertTrue(accounts.verify(stored, "un-mot-de-passe"))
        self.assertFalse(accounts.verify(stored, "autre-chose"))

    def test_a_name_is_free_but_not_twice(self):
        accounts.add({"name": "Marie", "role": "member", "password": "un-mot-de-passe"}, self.path)
        with self.assertRaises(ValueError) as caught:
            accounts.add({"name": "marie", "role": "member", "password": "un-mot-de-passe"},
                         self.path)
        self.assertEqual(str(caught.exception), "account_name_taken")

    def test_names_and_roles_are_checked(self):
        for data, code in (({"name": "", "role": "member", "password": "un-mot-de-passe"},
                            "account_name_required"),
                           ({"name": "x" * 41, "role": "member", "password": "un-mot-de-passe"},
                            "account_name_too_long"),
                           ({"name": "Marie", "role": "guest", "password": "un-mot-de-passe"},
                            "account_bad_role"),
                           ({"name": "Marie", "role": "member", "password": "court"},
                            "password_too_short"),
                           ({"name": "Marie", "role": "member", "hash": "pas-un-hash"},
                            "account_bad_hash")):
            with self.assertRaises(ValueError) as caught:
                accounts.add(data, self.path)
            self.assertEqual(str(caught.exception), code, data)

    def test_only_the_owner_may_hold_the_open_door(self):
        # An account without a password would let anyone in by typing its name.
        with self.assertRaises(ValueError) as caught:
            accounts.add({"name": "Marie", "role": "member"}, self.path)
        self.assertEqual(str(caught.exception), "account_password_required")
        owner = accounts.add({"name": "Le patron", "role": "owner"}, self.path)
        self.assertEqual(owner["hash"], "")
        self.assertTrue(accounts.owner_has_password(self.path) is False,
                        "an owner with no password leaves the door open")

    def test_the_owner_is_the_one_that_closes_the_door(self):
        self.assertFalse(accounts.owner_has_password(self.path))
        accounts.add({"name": "Le patron", "role": "owner", "password": "un-mot-de-passe"},
                     self.path)
        self.assertTrue(accounts.owner_has_password(self.path))
        self.assertEqual(accounts.owner(self.path)["name"], "Le patron")

    def test_a_password_can_be_changed_and_cleared(self):
        accounts.add({"name": "Marie", "role": "member", "password": "un-mot-de-passe"}, self.path)
        accounts.set_password("marie", "un-autre-mot-de-passe", self.path)
        self.assertTrue(accounts.verify(accounts.by_id("marie", self.path),
                                        "un-autre-mot-de-passe"))
        # Changing the role leaves the password alone.
        accounts.update("marie", {"role": "operator"}, self.path)
        self.assertEqual(accounts.by_id("marie", self.path)["role"], "operator")
        self.assertTrue(accounts.verify(accounts.by_id("marie", self.path),
                                        "un-autre-mot-de-passe"))
        accounts.delete("marie", self.path)
        self.assertIsNone(accounts.by_id("marie", self.path))

    def test_the_last_owner_is_neither_demoted_nor_removed(self):
        accounts.add({"name": "Le patron", "role": "owner", "password": "mot-de-passe-owner"},
                     self.path)
        accounts.add({"name": "Marie", "role": "member", "password": "mot-de-passe-marie"},
                     self.path)
        for change in (lambda: accounts.update("le-patron", {"role": "operator"}, self.path),
                       lambda: accounts.delete("le-patron", self.path)):
            with self.assertRaises(ValueError) as caught:
                change()
            self.assertEqual(str(caught.exception), "last_owner")
        self.assertTrue(accounts.owner_has_password(self.path), "the door stays closed")

    def test_an_unknown_account_is_a_key_error(self):
        for call in (lambda: accounts.update("nobody", {"role": "member"}, self.path),
                     lambda: accounts.delete("nobody", self.path)):
            with self.assertRaises(KeyError):
                call()

    def test_the_accounts_are_a_block_of_the_document(self):
        accounts.add({"name": "Marie", "role": "member", "password": "un-mot-de-passe"}, self.path)
        block = config_document.read("people", self.path)
        self.assertEqual([one["name"] for one in block["items"]], ["Marie"])
        with open(self.path, encoding="utf-8") as handle:
            self.assertIn("people:", handle.read())

    def test_the_password_this_radio_had_becomes_the_owner(self):
        legacy = web_auth.hash_password("l-ancien-mot-de-passe")
        self.assertTrue(accounts.migrate_legacy(legacy, self.path))
        owner = accounts.owner(self.path)
        self.assertEqual(owner["role"], "owner")
        self.assertTrue(accounts.verify(owner, "l-ancien-mot-de-passe"),
                        "the same password still opens the interface")
        self.assertFalse(accounts.migrate_legacy(legacy, self.path), "only once")
        self.assertFalse(accounts.migrate_legacy("", self.path), "nothing to take")

    def test_an_unreadable_document_is_never_written_over(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("people:\n  items: [ oops\n")
        with self.assertLogs("config_document", level="ERROR"):
            self.assertIsNone(accounts.read_items(self.path))
        with self.assertRaises(ValueError) as caught:
            accounts.add({"name": "Marie", "role": "member", "password": "un-mot-de-passe"},
                         self.path)
        self.assertEqual(str(caught.exception), "file_unreadable")


class RoleLadderTest(unittest.TestCase):
    """The table itself: what each role reaches, and the owner by default."""

    def test_the_ladder_goes_from_a_visitor_to_the_owner(self):
        self.assertEqual(roles.ROLES, ("guest", "member", "operator", "owner"))
        self.assertTrue(roles.allows("owner", "operator"))
        self.assertTrue(roles.allows("operator", "member"))
        self.assertTrue(roles.allows("member", "guest"))
        self.assertFalse(roles.allows("guest", "member"))
        self.assertFalse(roles.allows("member", "operator"))
        self.assertFalse(roles.allows("operator", "owner"))

    def test_a_route_nobody_listed_needs_the_owner(self):
        self.assertEqual(roles.needed_role("/api/settings", None), "operator")
        self.assertEqual(roles.needed_role("/api/status", None), "guest")
        self.assertEqual(roles.needed_role("/api/likes", None), "member")
        self.assertEqual(roles.needed_role("/api/system/reboot", None), "owner")
        self.assertEqual(roles.needed_role("/api/a_route_added_tomorrow", None), "owner")

    def test_a_rule_with_an_id_is_found_by_its_rule(self):
        self.assertEqual(roles.needed_role("/api/lists/soir", "/api/lists/<list_id>"), "member")
        self.assertEqual(roles.needed_role("/api/schedules/<id>", None), "owner")
        self.assertEqual(roles.needed_role("/api/announcements", None, "GET"), "guest")
        self.assertEqual(roles.needed_role("/api/announcements", None, "POST"), "operator")
        self.assertEqual(roles.needed_role("/api/stations/x", "/api/stations/<station_id>", "DELETE"), "operator")
        self.assertEqual(roles.needed_role("/api/schedules/abc",
                                           "/api/schedules/<schedule_id>"), "operator")

    def test_a_guest_gets_the_player_and_nothing_of_the_house(self):
        for path in ("/api/status", "/api/queue", "/api/volume", "/api/action/next_track"):
            self.assertEqual(roles.needed_role(path, None), "guest", path)
        for path in ("/api/settings", "/api/lists", "/api/likes", "/api/action/poweroff",
                     "/api/system/reboot", "/api/auth/accounts"):
            self.assertNotEqual(roles.needed_role(path, None), "guest", path)

    def test_the_guest_credits_stay_a_guest_thing(self):
        # A member lives here: the prices are not theirs.
        self.assertEqual(roles.needed_role("/api/likes/toggle", None), "member")
        self.assertFalse(roles.allows("member", "operator"))


@unittest.skipUnless(ws is not None, "Flask is not installed (run these on the Pi)")
class GateTest(unittest.TestCase):
    """The gate: the same interface, answered as four different roles."""

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)
        self.values = {"WEB_PASSWORD_HASH": "", "GUEST_MODE_ENABLED": True,
                       "GUEST_QUOTA_ENABLED": False, "ACTION_REPEAT_SEC": 0,
                       # Devices and names of its own: a name taken by another
                       # test must not follow this one.
                       "SUGGESTIONS_DB_FILE": os.path.join(self.dir, "suggestions.db")}
        real = ws.cfg

        def fake_cfg():
            values = dict(real())
            values.update(self.values)
            return values

        mock.patch.object(ws, "cfg", side_effect=fake_cfg).start()
        mock.patch.object(ws, "control", return_value={"ok": True, "data": {}}).start()
        # The audio output is asked for on every status read, and reading it
        # needs a Linux user session.
        mock.patch.object(ws, "_audio_output_state", lambda: {}).start()
        self.client = ws.app.test_client()

    def people(self):
        accounts.add({"name": "Le patron", "role": "owner", "password": "mot-de-passe-owner"},
                     self.path)
        accounts.add({"name": "Marie", "role": "member", "password": "mot-de-passe-marie"},
                     self.path)
        accounts.add({"name": "Paul", "role": "operator", "password": "mot-de-passe-paul"},
                     self.path)

    def login(self, name, password):
        return self.client.post("/api/auth/login", json={"name": name, "password": password})

    def test_with_no_account_the_whole_interface_is_open(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 200)
        self.assertEqual(self.client.get("/api/system/info").status_code, 200)
        self.assertEqual(self.client.post("/api/action/poweroff").status_code, 200)

    def test_the_owner_account_closes_it(self):
        accounts.add({"name": "Le patron", "role": "owner", "password": "mot-de-passe-owner"},
                     self.path)
        self.assertEqual(self.client.get("/api/settings").status_code, 401)
        self.assertEqual(self.client.get("/api/status").status_code, 200, "a guest still plays")

    def test_a_member_plays_and_the_house_is_not_theirs(self):
        self.people()
        self.assertEqual(self.login("Marie", "mot-de-passe-marie").status_code, 200)
        for path in ("/api/status", "/api/queue", "/api/likes", "/api/lists"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        for path in ("/api/settings", "/api/journal/summary", "/api/system/info"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/api/action/poweroff").status_code, 401)

    def test_a_member_keeps_their_own_lists_and_plays_one_only_when_allowed(self):
        self.people()
        accounts.add({"name": "Luc", "role": "member", "password": "mot-de-passe-luc"}, self.path)
        music_lists.add({"name": "De la radio", "kind": "manual"}, self.path)
        self.assertEqual(self.login("Marie", "mot-de-passe-marie").status_code, 200)
        mine = self.client.post("/api/lists", json={"name": "Marie", "kind": "manual"}).get_json()["data"]
        self.client.post("/api/auth/logout")
        self.assertEqual(self.login("Luc", "mot-de-passe-luc").status_code, 200)
        self.client.post("/api/lists", json={"name": "Luc", "kind": "manual"})
        listed = {one["name"]: one for one in self.client.get("/api/lists").get_json()["data"]["lists"]}
        self.assertEqual(sorted(listed), ["De la radio", "Luc"], "another member's list is theirs")
        self.assertEqual((listed["Luc"]["editable"], listed["De la radio"]["editable"]), (True, False))
        self.assertEqual(self.client.post("/api/lists/" + mine["id"], json={"name": "x"}).status_code, 404)
        self.assertEqual(self.client.post("/api/lists/de-la-radio", json={"name": "x"}).get_json()["error"],
                         "list_not_yours")
        self.assertEqual(self.client.get("/api/lists/de-la-radio/tracks").status_code, 200,
                         "the radio's lists are there to be listened to")
        self.assertFalse(self.client.get("/api/lists").get_json()["data"]["may_play"])
        refused = self.client.post("/api/lists/active", json={"id": listed["Luc"]["id"], "start": True})
        self.assertEqual((refused.status_code, refused.get_json()["error"]), (403, "lists_play_not_allowed"))
        self.assertEqual(self.client.post("/api/lists/from_genres", json={"genres": ["Rock"]}).status_code, 403)

        luc = accounts.by_name("Luc", self.path)
        accounts.update(luc["id"], {"play_lists": True}, self.path)
        self.assertTrue(self.client.get("/api/lists").get_json()["data"]["may_play"],
                        "granted, without logging in again")
        self.assertEqual(self.client.post("/api/lists/active", json={"id": listed["Luc"]["id"]}).status_code, 200)
        self.assertEqual(self.client.post("/api/lists/active", json={"id": mine["id"]}).status_code, 404)
        self.assertEqual(self.client.post("/api/lists/active", json={
            "play": [listed["Luc"]["id"], "de-la-radio"]}).status_code, 200, "several of the lists it sees")
        refused = self.client.post("/api/lists/active", json={"exclude": ["de-la-radio"]})
        self.assertEqual((refused.status_code, refused.get_json()["error"]), (403, "lists_exclude_not_allowed"),
                         "leaving songs out is for everyone listening: the keepers decide it")
        self.assertFalse(self.client.get("/api/lists").get_json()["data"]["may_exclude"])

        self.client.post("/api/auth/logout")
        self.assertEqual(self.login("Paul", "mot-de-passe-paul").status_code, 200)
        listed = {one["name"]: one for one in self.client.get("/api/lists").get_json()["data"]["lists"]}
        self.assertEqual(listed["Marie"]["owner_name"], "Marie", "the radio's keepers see whose it is")
        self.assertTrue(listed["Marie"]["editable"])
        self.assertEqual(self.client.post("/api/lists/active", json={"id": mine["id"]}).status_code, 200,
                         "and may put a member's list on")
        edited = self.client.post("/api/lists/" + mine["id"], json={"name": "Marie bis"}).get_json()["data"]
        self.assertEqual(music_lists.get(edited["id"], self.path)["owner"], "marie", "an edit keeps the owner")

    def test_what_reads_below_the_operator_is_not_written_from_there(self):
        self.people()
        self.values.update({"SINGLE_CLICK_ACTION": "poweroff", "DOUBLE_CLICK_ACTION": "next"})
        clicked = []
        mock.patch.object(ws, "control", side_effect=lambda cmd, **kw: clicked.append(cmd) or {"ok": True}).start()
        self.assertEqual(self.client.post("/api/action/single_click").status_code, 403,
                         "a guest never switches the radio off through its button's setting")
        self.assertEqual(self.client.post("/api/action/double_click").status_code, 200)
        self.assertEqual(self.login("Marie", "mot-de-passe-marie").status_code, 200)
        self.assertEqual(self.client.post("/api/action/single_click").get_json()["error"],
                         "click_action_not_allowed")
        self.values["SINGLE_CLICK_ACTION"] = "sleep"
        self.assertEqual(self.client.post("/api/action/single_click").status_code, 403)
        self.assertEqual(self.client.get("/api/announcements").status_code, 200)
        self.assertEqual(self.client.post("/api/announcements", json={"name": "x"}).status_code, 401,
                         "reading the announcements is not making them")
        self.assertEqual(self.client.get("/api/stations").status_code, 200)
        self.assertEqual(self.client.post("/api/stations", json={"name": "x", "url": "https://x"}).status_code, 401)
        self.assertEqual(self.client.delete("/api/stations/x").status_code, 401)
        self.assertEqual(self.client.post("/api/stations/x/play").get_json()["error"], "lists_play_not_allowed")
        self.assertEqual(self.client.post("/api/excluded/restore", json={"all": True}).status_code, 401,
                         "everyone's exclusions are not undone by one member")
        self.client.post("/api/auth/logout")
        self.assertEqual(self.login("Paul", "mot-de-passe-paul").status_code, 200)
        clicked.clear()
        self.assertEqual(self.client.post("/api/action/single_click").status_code, 200)
        self.assertEqual(clicked, ["single_click"], "the radio's keepers press the button as it is set")
        self.assertEqual(self.client.post("/api/stations/x/play").status_code, 200)
        self.assertTrue(self.client.get("/api/stations").get_json()["data"]["editable"])

    def test_an_operator_keeps_the_radio_but_not_the_machine(self):
        self.people()
        self.assertEqual(self.login("Paul", "mot-de-passe-paul").status_code, 200)
        for path in ("/api/status", "/api/likes", "/api/settings", "/api/lists",
                     "/api/schedules", "/api/announcements"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        for path in ("/api/journal/summary", "/api/system/info", "/api/wifi/status"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/api/action/poweroff").status_code, 401)
        self.assertEqual(self.client.post("/api/system/reboot").status_code, 401)

    def test_an_operator_leaves_the_machines_settings_to_the_owner(self):
        self.people()
        self.assertEqual(self.login("Paul", "mot-de-passe-paul").status_code, 200)
        for key in ("network.ap_gateway", "AP_GATEWAY_ENABLED", "updates.github_repo",
                    "paths.state_dir", "hardware.usb_port_mode"):
            answer = self.client.post("/api/settings", json={key: "x"})
            self.assertEqual(answer.status_code, 401, key)
            self.assertEqual(answer.get_json()["needs"], "owner")

    def test_an_operators_export_leaves_the_secrets_out(self):
        self.people()
        with mock.patch.object(ws.config_bundle, "export_bundle",
                               side_effect=lambda *a, **k: {"settings": {
                                   "home_assistant.password": "s3cret",
                                   "home_assistant.host": "ha.local"}}):
            self.assertEqual(self.login("Paul", "mot-de-passe-paul").status_code, 200)
            settings = self.client.get("/api/config/export").get_json()["settings"]
            self.assertNotIn("home_assistant.password", settings)
            self.assertEqual(settings["home_assistant.host"], "ha.local")
            self.client.post("/api/auth/logout")
            self.assertEqual(self.login("Le patron", "mot-de-passe-owner").status_code, 200)
            settings = self.client.get("/api/config/export").get_json()["settings"]
            self.assertEqual(settings["home_assistant.password"], "s3cret", "a backup the owner can restore")

    def test_the_owner_reaches_everything(self):
        self.people()
        self.assertEqual(self.login("Le patron", "mot-de-passe-owner").status_code, 200)
        for path in ("/api/status", "/api/settings", "/api/system/info", "/api/journal/summary"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_a_wrong_password_is_told_apart_from_an_unknown_name(self):
        self.people()
        self.assertEqual(self.login("Marie", "pas-le-bon").status_code, 401)
        self.assertEqual(self.login("Personne", "mot-de-passe-marie").status_code, 401)
        self.assertFalse(self.client.get("/api/auth/status").get_json()["data"]["authenticated"])
        self.assertEqual(self.client.get("/api/likes").status_code, 401,
                         "a guest reaches the player, not the house")

    def test_the_single_password_still_opens_the_interface(self):
        # What an installation that updates has: the password, no account yet.
        legacy = web_auth.hash_password("l-ancien-mot-de-passe")
        self.values["WEB_PASSWORD_HASH"] = legacy
        r = self.client.post("/api/auth/login", json={"password": "l-ancien-mot-de-passe"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").get_json()["data"]["role"], "owner")

    def test_an_account_holding_the_password_takes_the_key_over(self):
        self.people()
        self.values["WEB_PASSWORD_HASH"] = web_auth.hash_password("l-ancien-mot-de-passe")
        # The account is now the password that counts: the old key is inert.
        self.assertEqual(self.client.post("/api/auth/login",
                                          json={"password": "l-ancien-mot-de-passe"}).status_code, 401)
        self.assertEqual(self.login("Le patron", "mot-de-passe-owner").status_code, 200)

    def test_a_browser_is_logged_out_when_its_account_is_deleted(self):
        self.people()
        self.login("Marie", "mot-de-passe-marie")
        self.assertEqual(self.client.get("/api/auth/status").get_json()["data"]["role"], "member")
        accounts.delete("marie", self.path)
        data = self.client.get("/api/auth/status").get_json()["data"]
        self.assertEqual((data["role"], data["authenticated"]), ("guest", False))
        self.assertEqual(self.client.get("/api/likes").status_code, 401,
                         "and what a member had is closed again")

    def test_the_status_says_who_this_browser_is(self):
        self.people()
        data = self.client.get("/api/auth/status").get_json()["data"]
        self.assertTrue(data["auth_required"])
        self.assertFalse(data["authenticated"])
        self.assertEqual([one["name"] for one in data["accounts"]],
                         ["Le patron", "Marie", "Paul"])
        self.assertEqual([one["name"] for one in data["accounts"] if "hash" in one], [])
        self.login("Marie", "mot-de-passe-marie")
        data = self.client.get("/api/auth/status").get_json()["data"]
        self.assertEqual((data["role"], data["account"]["name"]), ("member", "Marie"))

    def test_the_accounts_are_the_owners_to_see_and_change(self):
        self.people()
        self.login("Marie", "mot-de-passe-marie")
        self.assertEqual(self.client.get("/api/auth/accounts").status_code, 401)
        self.client.post("/api/auth/logout")
        self.login("Le patron", "mot-de-passe-owner")
        listed = self.client.get("/api/auth/accounts").get_json()["data"]
        self.assertEqual([one["name"] for one in listed["accounts"]],
                         ["Le patron", "Marie", "Paul"])
        self.assertEqual(listed["roles"], ["member", "operator", "owner"],
                         "weakest first, as the ladder is written")
        self.assertTrue(all("hash" not in one for one in listed["accounts"]))

    def test_an_account_is_added_changed_and_removed(self):
        self.people()
        self.login("Le patron", "mot-de-passe-owner")
        made = self.client.post("/api/auth/accounts", json={
            "name": "Lucie", "role": "member", "password": "mot-de-passe-lucie"})
        self.assertEqual(made.status_code, 200)
        self.assertEqual(made.get_json()["data"]["id"], "lucie")
        changed = self.client.post("/api/auth/accounts", json={"id": "lucie", "role": "operator"})
        self.assertEqual(changed.get_json()["data"]["role"], "operator")
        self.assertEqual(self.client.delete("/api/auth/accounts/lucie").status_code, 200)
        self.assertEqual(self.client.delete("/api/auth/accounts/lucie").status_code, 404)

    def test_the_owner_cannot_delete_itself(self):
        self.people()
        self.login("Le patron", "mot-de-passe-owner")
        r = self.client.delete("/api/auth/accounts/le-patron")
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "cannot_delete_self"))
        # Another one goes, and the one logged in stays.
        self.assertEqual(self.client.delete("/api/auth/accounts/marie").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").get_json()["data"]["role"], "owner")

    def name_the_visitor(self, name="Renard bleu"):
        """What the page does for a visitor that has no name yet."""
        r = self.client.post("/api/suggestions/name", json={"name": name, "generated": True})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_the_browser_goes_by_the_name_of_the_account(self):
        self.people()
        self.name_the_visitor()
        self.assertEqual(self.client.get("/api/suggestions").get_json()["data"]["me"]["name"],
                         "Renard bleu")
        self.login("Marie", "mot-de-passe-marie")
        me = self.client.get("/api/suggestions").get_json()["data"]["me"]
        self.assertEqual(me["name"], "Marie", "the account is the name it goes by now")

    def test_logging_out_gives_the_device_its_own_name_back(self):
        self.people()
        self.name_the_visitor()
        self.login("Marie", "mot-de-passe-marie")
        self.client.post("/api/auth/logout")
        me = self.client.get("/api/suggestions").get_json()["data"]["me"]
        self.assertEqual(me["name"], "Renard bleu")

    def test_an_account_does_not_rename_itself_from_the_page(self):
        self.people()
        self.login("Marie", "mot-de-passe-marie")
        r = self.client.post("/api/suggestions/name", json={"name": "Autre chose"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "name_is_account"))

    def test_a_signed_in_browser_does_not_link_devices(self):
        self.people()
        self.login("Marie", "mot-de-passe-marie")
        for path in ("/api/devices/link_code", "/api/devices/link_join"):
            r = self.client.post(path, json={"code": "123456"})
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "account_signed_in"),
                             path)
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.post("/api/devices/link_code").status_code, 200,
                         "a visitor still does")

    def test_a_member_pays_no_credits(self):
        self.values["GUEST_QUOTA_ENABLED"] = True
        self.values["GUEST_COST_NEXT"] = 3
        self.people()
        self.login("Marie", "mot-de-passe-marie")
        data = self.client.get("/api/status").get_json()["data"]
        self.assertIsNone(data["quota"], "the prices are a guest's")
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/status").get_json()["data"]["quota"]["tokens"], 3)


if __name__ == "__main__":
    unittest.main()

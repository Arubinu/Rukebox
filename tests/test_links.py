"""Several devices, one person: what linking shares and what it leaves alone."""

import os
import tempfile
import unittest

import _path  # noqa: F401
import suggestions


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.box = suggestions.SuggestionBox(os.path.join(self.dir.name, "s.db"))
        self.addCleanup(self.box._db.close)

    def device(self, mac, name=None):
        device, token = self.box.resolve_device(None, mac, "10.42.0.9")
        if name:
            self.box.set_name(device, name)
        return device, token

    def again(self, token):
        return self.box.resolve_device(token, None, "10.42.0.9")[0]

    def test_a_linked_device_goes_by_the_name_of_the_other(self):
        (phone, _), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02", "Loutre verte")
        self.box.link(laptop["id"], phone["id"])
        self.assertEqual(self.again(laptop_token)["name"], "Renard bleu")
        self.assertEqual(self.box.device_by_id(laptop["id"])["name"], "Renard bleu")
        self.assertEqual([d["device_id"] for d in self.box.linked_devices(phone["id"])], [laptop["id"]])
        with self.assertRaises(suggestions.SuggestionError) as refused:
            self.box.link(phone["id"], laptop["id"])
        self.assertEqual(refused.exception.code, "already_linked")

    def test_the_name_left_behind_stays_reserved_to_the_person(self):
        (phone, _), (laptop, _) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02", "Loutre verte")
        stranger, _ = self.device("aa:00:00:00:00:03")
        self.box.link(laptop["id"], phone["id"])
        with self.assertRaises(suggestions.SuggestionError) as refused:
            self.box.set_name(stranger, "Loutre verte")
        self.assertEqual(refused.exception.code, "name_taken")

    def test_a_rename_on_either_device_renames_the_person(self):
        (phone, phone_token), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02")
        self.box.link(laptop["id"], phone["id"])
        self.box.set_name(self.again(laptop_token), "Hibou gris")
        self.assertEqual(self.again(phone_token)["name"], "Hibou gris")

    def test_one_person_has_one_vote_and_none_on_its_own_suggestion(self):
        (phone, _), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02", "Loutre verte")
        other, _ = self.device("aa:00:00:00:00:03", "Hibou gris")
        theirs = self.box.add(other, "music", "Daft Punk - Around the World")
        mine = self.box.add(phone, "music", "Air - Sexy Boy")
        self.box.vote(phone, theirs, 1)
        self.box.vote(laptop, theirs, 1)
        self.box.vote(laptop, mine, 1)
        self.box.link(laptop["id"], phone["id"])

        items = {item["id"]: item for item in self.box.list(self.again(laptop_token))}
        self.assertEqual(items[theirs]["up"], 1, "two devices of one person are one vote")
        self.assertEqual(items[mine]["up"], 0, "and no vote on what the person suggested")
        self.assertTrue(items[mine]["mine"], "the laptop sees the phone's suggestion as its own")
        with self.assertRaises(suggestions.SuggestionError):
            self.box.vote(self.again(laptop_token), mine, 1)

    def test_a_ban_and_the_credits_are_the_persons(self):
        (phone, _), (laptop, _) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02", "Loutre verte")
        self.box.set_ban(laptop["id"], -1)
        self.box.link(laptop["id"], phone["id"])
        self.assertEqual(self.box.ban_until(phone["id"]), -1, "linking never loses a ban")
        banned = self.box.banned_devices()
        self.assertEqual(len(banned), 1)
        self.assertEqual(set(banned[0]["macs"]), {"aa:00:00:00:00:01", "aa:00:00:00:00:02"})
        self.box.set_ban(laptop["id"], None)
        self.assertIsNone(self.box.ban_until(phone["id"]))

        self.box.set_free_credits(laptop["id"], True)
        self.assertTrue(self.box.has_free_credits(phone["id"]))
        self.box.set_name_locked(phone["id"], True)
        self.assertTrue(self.box.name_locked(laptop["id"]))

    def test_the_portal_stays_each_devices_own(self):
        (phone, _), (laptop, _) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02")
        self.box.link(laptop["id"], phone["id"])
        self.box.set_portal(laptop["id"], "never")
        self.box.note_portal_release("aa:00:00:00:00:02")
        self.assertIsNone(self.box.portal_for_mac("aa:00:00:00:00:01"))
        self.assertIsNone(self.box.portal_released_at("aa:00:00:00:00:01"))
        self.assertEqual(self.box.portal_for_mac("aa:00:00:00:00:02"), "never")

    def test_a_device_that_leaves_leaves_with_nothing(self):
        (phone, phone_token), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02")
        self.box.link(laptop["id"], phone["id"])
        self.box.unlink(laptop["id"])
        self.assertIsNone(self.again(laptop_token)["name"])
        self.assertEqual(self.again(phone_token)["name"], "Renard bleu")
        with self.assertRaises(suggestions.SuggestionError) as refused:
            self.box.unlink(laptop["id"])
        self.assertEqual(refused.exception.code, "not_linked")

    def test_the_others_keep_everything_when_the_first_device_leaves(self):
        (phone, phone_token), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02")
        tablet, tablet_token = self.device("aa:00:00:00:00:03")
        self.box.link(laptop["id"], phone["id"])
        self.box.link(tablet["id"], phone["id"])
        idea = self.box.add(phone, "music", "Air - Sexy Boy")
        self.box.set_free_credits(phone["id"], True)

        self.box.unlink(phone["id"])
        self.assertIsNone(self.again(phone_token)["name"])
        self.assertFalse(self.box.has_free_credits(phone["id"]))
        for token in (laptop_token, tablet_token):
            device = self.again(token)
            self.assertEqual(device["name"], "Renard bleu")
            self.assertTrue(self.box.has_free_credits(device["id"]))
            self.assertTrue({i["id"]: i for i in self.box.list(device)}[idea]["mine"])
        self.assertEqual(len(self.box.linked_devices(laptop["id"])), 1)

    def test_forgetting_a_device_does_not_forget_the_person(self):
        (phone, _), (laptop, laptop_token) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02")
        self.box.link(laptop["id"], phone["id"])
        self.assertEqual(self.box.forget_unnamed(), 0, "a linked device goes by its person's name")
        self.box.forget_device(phone["id"])
        self.assertIsNone(self.box.device_by_id(phone["id"]))
        self.assertEqual(self.again(laptop_token)["name"], "Renard bleu")

    def test_linking_two_people_brings_all_their_devices(self):
        (phone, _), (laptop, _) = self.device("aa:00:00:00:00:01", "Renard bleu"), \
            self.device("aa:00:00:00:00:02", "Loutre verte")
        tablet, tablet_token = self.device("aa:00:00:00:00:03")
        self.box.link(tablet["id"], laptop["id"])
        self.box.link(tablet["id"], phone["id"])
        self.assertEqual(self.again(tablet_token)["name"], "Renard bleu")
        self.assertEqual(self.box.device_by_id(laptop["id"])["name"], "Renard bleu")
        self.assertEqual(len(self.box.linked_devices(phone["id"])), 2)

    def test_an_older_database_gains_the_column(self):
        path = os.path.join(self.dir.name, "old.db")
        import sqlite3
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE device_state (device_id TEXT PRIMARY KEY, banned_until REAL, portal TEXT,"
                   " generated_name INTEGER NOT NULL DEFAULT 0)")
        db.commit()
        db.close()
        box = suggestions.SuggestionBox(path)
        self.addCleanup(box._db.close)
        device, _ = box.resolve_device(None, "aa:00:00:00:00:09", "10.42.0.9")
        self.assertEqual(box.person_id(device["id"]), device["id"])


if __name__ == "__main__":
    unittest.main()

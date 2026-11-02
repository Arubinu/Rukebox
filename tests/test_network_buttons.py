"""The buttons on other devices of the network (src/network_buttons.py)."""
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import network_buttons as nb

ID, SECRET = "ab" * 8, "cd" * 16


class NetworkButtonsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "buttons.json")

    def test_a_button_waits_until_it_is_approved_and_only_its_secret_answers(self):
        self.assertEqual(nb.press(self.path, ID, SECRET, "Rayon", "10.0.0.2"), "waiting")
        self.assertEqual(nb.press(self.path, ID, SECRET, "Rayon", "10.0.0.2"), "waiting")
        self.assertEqual(len(nb.load(self.path)), 1, "one entry however often it is pressed")
        self.assertTrue(nb.update(self.path, ID, approved=True))
        self.assertEqual(nb.press(self.path, ID, SECRET, "Rayon", "10.0.0.3"), "approved")
        self.assertEqual(nb.load(self.path)[0]["ip"], "10.0.0.3")
        self.assertEqual(nb.press(self.path, ID, "ee" * 16, "Rayon", "10.0.0.9"), "refused")
        self.assertEqual(nb.load(self.path)[0]["ip"], "10.0.0.3")

    def test_what_is_not_an_identity_is_refused_and_never_kept(self):
        for bad_id, bad_secret in (("", SECRET), (ID, ""), ("../../etc", SECRET), (ID, "zz" * 16), (None, None)):
            self.assertEqual(nb.press(self.path, bad_id, bad_secret, "x", "ip"), "refused")
        self.assertEqual(nb.load(self.path), [])

    def test_strangers_cannot_fill_the_list(self):
        for number in range(nb.MAX_WAITING):
            self.assertEqual(nb.press(self.path, "%016x" % number, SECRET, "x", "ip"), "waiting")
        self.assertEqual(nb.press(self.path, ID, SECRET, "x", "ip"), "full")
        self.assertTrue(nb.delete(self.path, "%016x" % 0))
        self.assertEqual(nb.press(self.path, ID, SECRET, "x", "ip"), "waiting")

    def test_those_waiting_come_first_and_a_name_is_kept_clean(self):
        nb.press(self.path, ID, SECRET, "Zèbre", "ip1")
        nb.press(self.path, "12" * 8, SECRET, "", "ip2")
        nb.update(self.path, "12" * 8, approved=True, name=" Caisse\x07 " + "x" * 60)
        names = [button["name"] for button in nb.load(self.path)]
        self.assertEqual(names[0], "Zèbre")
        self.assertTrue(names[1].startswith("Caisse x"))
        self.assertEqual(len(names[1]), nb.NAME_MAX)
        nb.update(self.path, ID, name="   ")
        self.assertEqual(nb.load(self.path)[0]["name"], "Zèbre", "an empty name keeps the one it had")
        self.assertFalse(nb.update(self.path, "ff" * 8, approved=True))
        self.assertFalse(nb.delete(self.path, "ff" * 8))


if __name__ == "__main__":
    unittest.main()

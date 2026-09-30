"""Version strings (src/version.py): is a release newer than what is installed?

A tree pushed from a repository records `git describe`, so the installed
release reads "v1.2.0-5-g5622dcf" - five commits AFTER v1.2.0. Comparing the
two as plain strings made the interface offer v1.2.0 as an update to a Pi that
already had more than v1.2.0 (reported as: the push says "already runs this
exact version" while the Update card offers v1.2.0).
"""
import unittest

import _path  # noqa: F401
from version import is_newer, tag_parts


class TagPartsTest(unittest.TestCase):
    def test_a_plain_tag(self):
        self.assertEqual(tag_parts("v1.2.0"), (1, 2, 0, 0))
        self.assertEqual(tag_parts("v1.2"), (1, 2, 0, 0))
        self.assertEqual(tag_parts("1.3.10"), (1, 3, 10, 0))

    def test_a_describe_says_how_far_past_the_tag(self):
        self.assertEqual(tag_parts("v1.2.0-5-g5622dcf"), (1, 2, 0, 5))
        self.assertEqual(tag_parts("v1.2.0-5-g5622dcf-dirty"), (1, 2, 0, 5))
        self.assertEqual(tag_parts("v1.0.0-1-gabcdef0"), (1, 0, 0, 1))

    def test_what_is_not_a_version(self):
        for text in ("", None, "5622dcf", "main", "-dirty"):
            self.assertIsNone(tag_parts(text), text)


class IsNewerTest(unittest.TestCase):
    def test_a_push_past_the_release_is_not_offered_it(self):
        self.assertFalse(is_newer("v1.2.0", "v1.2.0-5-g5622dcf"))
        self.assertFalse(is_newer("v1.2.0", "v1.2.0-5-g5622dcf-dirty"))
        self.assertFalse(is_newer("v1.2.0", "v1.2.0"))

    def test_a_later_release_still_is(self):
        self.assertTrue(is_newer("v1.2.1", "v1.2.0-5-g5622dcf"))
        self.assertTrue(is_newer("v1.3.0", "v1.2.0"))
        self.assertTrue(is_newer("v2.0.0", "v1.9.9"))

    def test_an_older_release_never_is(self):
        self.assertFalse(is_newer("v1.1.0", "v1.2.0"))
        self.assertFalse(is_newer("v1.1.0", "v1.2.0-5-g5622dcf"))

    def test_nothing_installed_yet(self):
        self.assertTrue(is_newer("v1.2.0", ""))
        self.assertTrue(is_newer("v1.2.0", None))

    def test_a_bare_commit_is_not_ordered(self):
        self.assertTrue(is_newer("v1.2.0", "5622dcf"),
                        "there is nothing to compare a hash against")

    def test_no_release_to_offer(self):
        self.assertFalse(is_newer("", "v1.2.0"))
        self.assertFalse(is_newer(None, "v1.2.0"))


if __name__ == "__main__":
    unittest.main()

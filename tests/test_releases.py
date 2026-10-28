"""Which release a radio is offered: never past the newest of its own major, and the
next major only once it is there, with what it implies."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _path  # noqa: F401
import releases
import version


def release(tag, body="", draft=False, prerelease=False, notes_url=""):
    found = {"tag_name": tag, "name": tag, "body": body, "draft": draft, "prerelease": prerelease,
             "published_at": "2026-10-01T00:00:00Z", "html_url": "https://example/" + tag}
    if notes_url:
        found["assets"] = [{"name": "notes.json", "browser_download_url": notes_url}]
    return found


LISTED = [release("v2.1.0"), release("v1.19.2"), release("v2.0.0"), release("v1.19.1"),
          release("v3.0.0-rc1"), release("v1.18.0"), release("v4.0.0", draft=True),
          release("v3.1.0", prerelease=True), release("nightly")]


def tag(found):
    return found["tag_name"] if found else None


class TargetTest(unittest.TestCase):
    def test_an_older_release_of_the_same_major_is_offered_the_newest_of_it(self):
        self.assertEqual((tag(releases.target(LISTED, "v1.18.0")[0]), releases.target(LISTED, "v1.18.0")[1]),
                         ("v1.19.2", False), "the newest 1.x, never 2.x from there")

    def test_the_newest_of_a_major_is_offered_the_next_one_as_a_crossing(self):
        found, crosses = releases.target(LISTED, "v1.19.2")
        self.assertEqual((tag(found), crosses), ("v2.1.0", True))

    def test_nothing_past_the_next_major(self):
        self.assertEqual(releases.target(LISTED, "v2.1.0"), (None, False),
                         "no v3 published as a release, and drafts or pre-releases do not count")
        listed = LISTED + [release("v3.0.0"), release("v4.0.0")]
        self.assertEqual(tag(releases.target(listed, "v2.1.0")[0]), "v3.0.0", "one major at a time")
        self.assertEqual(tag(releases.target(listed, "v2.0.0")[0]), "v2.1.0", "the newest 2.x first")

    def test_a_tree_without_a_release_name_starts_its_own_major(self):
        with mock.patch.object(version, "MAJOR", 1):
            self.assertEqual(tag(releases.target(LISTED, "")[0]), "v1.19.2")
        with mock.patch.object(version, "MAJOR", 2):
            self.assertEqual(tag(releases.target(LISTED, "abc123")[0]), "v2.1.0")

    def test_commits_past_the_newest_tag_count_as_being_on_it(self):
        self.assertEqual(releases.target(LISTED, "v1.19.2-3-gabcdef")[1], True)


class PreReleaseTest(unittest.TestCase):
    def test_a_pre_release_is_offered_to_no_one_and_its_radios_move_to_the_release(self):
        listed = [release("v2.0.0-rc.1", prerelease=True), release("v1.19.3")]
        self.assertEqual(releases.target(listed, "v1.19.3"), (None, False), "a test is never offered")
        self.assertIsNone(releases.refusal(listed, "v1.19.3", "v2.0.0-rc.1", major=True),
                          "but it may be installed on purpose")
        listed.insert(0, release("v2.0.0"))
        found, crosses = releases.target(listed, "v2.0.0-rc.1")
        self.assertEqual((found["tag_name"], crosses), ("v2.0.0", False), "the release follows its test")


class RefusalTest(unittest.TestCase):
    def test_a_step_is_refused_with_its_reason(self):
        listed = LISTED + [release("v3.0.0")]
        cases = {
            ("v1.18.0", "v1.19.1", False): None,
            ("v1.19.1", "v1.18.0", False): None,
            ("v1.19.1", "v2.0.0", True): "newest_first",
            ("v1.19.2", "v2.0.0", False): "major_needs_confirmation",
            ("v1.19.2", "v2.0.0", True): None,
            ("v1.19.2", "v3.0.0", True): "major_skipped",
            ("v2.0.0", "v1.19.2", False): "major_backwards",
            ("v2.0.0", "nightly", False): "release_unknown",
        }
        for (installed, wanted, major), why in cases.items():
            self.assertEqual(releases.refusal(listed, installed, wanted, major), why, (installed, wanted))


class ChangesTest(unittest.TestCase):
    def test_the_releases_on_the_way_newest_first(self):
        self.assertEqual([tag(found) for found in releases.changes(LISTED, "v1.19.1", "v2.1.0")],
                         ["v2.1.0", "v2.0.0", "v1.19.2"])
        self.assertEqual(releases.changes(LISTED, "v1.19.1", "nightly"), [])

    def test_the_text_is_in_the_readers_language_or_english(self):
        texts = {"en": "Fixes", "fr": "Correctifs", "de": " "}
        self.assertEqual(releases.in_language(texts, "fr"), "Correctifs")
        self.assertEqual(releases.in_language(texts, "de"), "Fixes", "a blank translation is none")
        self.assertEqual(releases.in_language(None, "fr", "body"), "body")


class RecognisedTest(unittest.TestCase):
    def test_a_tree_the_notes_declare_is_that_release(self):
        listed = [release("v1.19.2", "# x ![](https://img.shields.io/badge/tree--hash-cc6da6fbc80d-blue)")]
        self.assertEqual(releases.recognised(listed, "v1.19.1", "cc6da6fbc80d18d4"), "v1.19.2")
        self.assertEqual(releases.recognised(listed, "v1.19.1", "0000"), "v1.19.1")


class CommandTest(unittest.TestCase):
    def run_main(self, *args, listed=LISTED, installed="v1.19.2"):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, True))
        path = os.path.join(folder, "version.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"release": installed, "tree_hash": "ffff"}, f)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(releases, "fetch", return_value=listed), redirect_stdout(out), redirect_stderr(err):
            code = releases._main(["releases.py", args[0], "o/r", path] + list(args[1:]))
        return code, out.getvalue().strip(), err.getvalue().strip()

    def test_the_updater_is_told_what_to_install_or_why_not(self):
        self.assertEqual(self.run_main("target", installed="v1.18.0")[:2], (0, "v1.19.2"))
        code, out, err = self.run_main("target")
        self.assertEqual((code, out), (4, ""), "a new major waits for --major")
        self.assertIn("v2.1.0", err)
        self.assertEqual(self.run_main("target", "--major")[:2], (0, "v2.1.0"))
        self.assertEqual(self.run_main("target", installed="v2.1.0")[0], 3, "nothing newer")
        self.assertEqual(self.run_main("check", "v2.0.0", installed="v1.19.1", )[0], 5)
        self.assertEqual(self.run_main("check", "v1.19.1")[:2], (0, "v1.19.1"))

    def test_a_lookup_that_fails_says_so(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(releases, "fetch", side_effect=releases.LookupFailed("no_internet")), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(releases._main(["releases.py", "target", "o/r", "/nope"]), 2)
        self.assertIn("no_internet", err.getvalue())


class FetchTest(unittest.TestCase):
    def test_only_a_repository_name_is_asked_for(self):
        for bad in ("", "a", "a/b/c", "../x", "a/..", "a b/c"):
            with self.assertRaises(releases.LookupFailed):
                releases.fetch(bad)

    def test_notes_are_read_only_from_https(self):
        self.assertEqual(releases.fetch_notes(release("v1", notes_url="http://example/notes.json")), {})
        self.assertEqual(releases.fetch_notes(release("v1")), {})


if __name__ == "__main__":
    unittest.main()

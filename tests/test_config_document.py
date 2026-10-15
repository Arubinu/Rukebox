"""The lists live in rukebox.yaml: one block per list, comments kept.

The stores (announcements, music_lists, schedules, cards, track_order) write
through src/config_document.py, so a save touches its own lines and nothing
else. A list an older version kept in its own file is taken in once.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
import announcements
import cards
import config_document
import config_file
import config_schema
import json_file
import music_lists
import schedules
import track_order


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# a comment of mine\n" + config_file.render_template())

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_a_block_is_written_and_read_back(self):
        config_document.write("cards", {"cards": [{"id": "abcd1234", "name": "Bleue"}]}, self.path)
        self.assertEqual(config_document.read("cards", self.path),
                         {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        self.assertIn("  - id: abcd1234\n", self.text(), "written as YAML, not on one line")

    def test_writing_a_block_leaves_everything_else_alone(self):
        before = self.text()
        config_document.write("music_lists", {"lists": [{"id": "soir", "name": "Soir"}]}, self.path)
        after = self.text()
        self.assertIn("# a comment of mine", after)
        self.assertIn("base_volume:", after)
        self.assertEqual(len(after), len(before) + len(after) - len(before))
        # The settings section and its comments are the same document as before.
        self.assertEqual(config_file.read_values(self.path), config_file.read_values(self.path))

    def test_a_settings_save_keeps_the_lists(self):
        announcements.save_all([{"id": "matin", "name": "Matin", "folder": self.dir,
                                 "hour": 7, "minute": 0, "trigger": "time"}], self.path)
        schedules.add({"name": "Weekend", "days": [5], "start": "09:00"}, self.path)
        config_file.write_values({"playback.base_volume": "42"}, self.path)
        self.assertEqual([item["id"] for item in announcements.load(self.path)], ["matin"])
        self.assertEqual([item["id"] for item in schedules.load(self.path)], ["weekend"])
        self.assertEqual(config_file.read_values(self.path)["BASE_VOLUME"], "42")
        self.assertIn("# a comment of mine", self.text())

    def test_two_blocks_do_not_stand_on_each_other(self):
        cards.save({"id": "abcd1234", "name": "Bleue", "action": "list", "target": "soir"},
                   self.path)
        track_order.save_order("meme", ["b.mp3", "a.mp3"], self.path)
        self.assertEqual(sorted(cards.load(self.path)), ["abcd1234"])
        self.assertEqual(track_order.get("meme", self.path), ["b.mp3", "a.mp3"])

    def test_a_block_the_document_does_not_have_is_added_at_the_end(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# only a comment\nplayback:\n  base_volume: 35\n")
        music_lists.save_all([{"id": "soir", "name": "Soir", "kind": "manual"}], self.path)
        text = self.text()
        self.assertIn("# The lists built in the interface, and the genres they hold.", text)
        self.assertTrue(text.index("playback:") < text.index("music_lists:"))
        self.assertEqual([item["id"] for item in music_lists.load(self.path)], ["soir"])

    def test_a_store_handed_the_file_it_used_to_keep_reads_the_document(self):
        # A caller of one's own passes /etc/rukebox/announcements.json still.
        beside = os.path.join(self.dir, "announcements.json")
        announcements.save_all([{"id": "matin", "name": "Matin", "folder": self.dir,
                                 "hour": 7, "minute": 0, "trigger": "time"}], beside)
        self.assertEqual([item["id"] for item in announcements.load(self.path)], ["matin"])
        self.assertFalse(os.path.exists(beside), "nothing is written beside the document")

    def test_an_unreadable_document_reads_as_none_and_is_not_written(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("cards:\n  cards: [ oops\n")
        with self.assertLogs("config_document", level="ERROR"):
            self.assertIsNone(config_document.read("cards", self.path))
        # A store reads an unreadable document as empty, so that a page holds.
        self.assertEqual(cards.load(self.path), {})

    def test_a_block_that_is_not_a_mapping_reads_as_empty(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("cards: nonsense\n")
        self.assertEqual(config_document.read("cards", self.path), {})

    def test_the_document_is_read_again_only_when_it_changed(self):
        config_document.write("cards", {"cards": []}, self.path)
        first = config_document.read("cards", self.path)
        # Same file: the cached document, not another parse.
        self.assertIs(config_document.read("cards", self.path), first)
        config_document.write("cards", {"cards": [{"id": "abcd1234", "name": "Bleue"}]}, self.path)
        self.assertEqual(len(config_document.read("cards", self.path)["cards"]), 1)

    def test_an_unknown_block_is_refused(self):
        for name in ("settings", "nothing"):
            with self.assertRaises(ValueError):
                config_document.read(name, self.path)


class ImportTest(unittest.TestCase):
    """What an installation that updated takes in, once."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)
        # The environment file a test syncs is its own too: the sandbox one is
        # what every other test reads when a document is missing.
        self.env = os.path.join(self.dir, "rukebox.env")
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(config_file.render_template(config_schema.DEFAULTS))

    def legacy(self, name, doc):
        path = os.path.join(self.dir, name)
        json_file.write(path, doc)
        return path

    def test_the_five_lists_move_in_and_their_files_are_kept(self):
        contents = {
            "announcements.json": {"items": [{"id": "matin", "name": "Matin"}],
                                   "volumes": {"meme": {"on": True, "volume": 80}}},
            "music_lists.json": {"lists": [{"id": "soir", "name": "Soir"}]},
            "schedules.json": {"schedules": [{"id": "weekend", "name": "Weekend"}]},
            "cards.json": {"cards": [{"id": "abcd1234", "name": "Bleue"}]},
            "track_order.json": {"meme": ["b.mp3", "a.mp3"]},
        }
        paths = {name: self.legacy(name, doc) for name, doc in contents.items()}
        moved = config_document.import_legacy(self.path)
        self.assertEqual(sorted(moved), ["announcements", "cards", "music_lists", "schedules",
                                         "track_order"])
        self.assertEqual([item["id"] for item in announcements.load(self.path)], ["matin"])
        self.assertEqual(announcements.volumes(self.path)["meme"], {"on": True, "volume": 80})
        self.assertEqual([item["id"] for item in music_lists.load(self.path)], ["soir"])
        self.assertEqual([item["id"] for item in schedules.load(self.path)], ["weekend"])
        self.assertEqual(sorted(cards.load(self.path)), ["abcd1234"])
        self.assertEqual(track_order.get("meme", self.path), ["b.mp3", "a.mp3"])
        for name, path in paths.items():
            self.assertFalse(os.path.exists(path), "%s was taken" % name)

    def test_the_whole_folder_is_copied_before_anything_moves(self):
        self.legacy("cards.json", {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        env_line = "BASE_VOLUME='30'"
        with open(os.path.join(self.dir, "rukebox.env"), "w", encoding="utf-8") as f:
            f.write(env_line + "\n")
        before = open(self.path, encoding="utf-8").read()
        self.assertEqual(config_document.import_legacy(self.path), ["cards"])

        kept = os.path.join(self.dir, config_document.BACKUP_DIR)
        self.assertTrue(os.path.isdir(kept))
        # The document as it was, before the block was written into it.
        with open(os.path.join(kept, "rukebox.yaml"), encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        with open(os.path.join(kept, "rukebox.env"), encoding="utf-8") as f:
            self.assertEqual(f.read(), env_line + "\n")
        with open(os.path.join(kept, "cards.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        # And it says what it is, for whoever finds it in a year.
        with open(os.path.join(kept, config_document.README_NAME), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("before the lists", text)
        self.assertIn("cp -a", text)

    def test_the_copy_is_taken_once_and_never_overwritten(self):
        self.legacy("cards.json", {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        config_document.import_legacy(self.path)
        kept = os.path.join(self.dir, config_document.BACKUP_DIR)
        with open(os.path.join(kept, "cards.json"), "w", encoding="utf-8") as f:
            f.write('{"cards": []}')
        # A later run must not write over the copy a person may be relying on.
        config_document.write("cards", {"cards": []}, self.path)
        self.legacy("schedules.json", {"schedules": [{"id": "weekend", "name": "Weekend"}]})
        self.assertEqual(config_document.import_legacy(self.path), ["schedules"])
        with open(os.path.join(kept, "cards.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"cards": []}')

    def test_a_folder_no_copy_can_be_made_of_moves_nothing(self):
        path = self.legacy("cards.json", {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        with mock.patch.object(config_document, "_keep_the_folder", side_effect=OSError("no room")):
            with self.assertLogs("config_document", level="ERROR"):
                self.assertEqual(config_document.import_legacy(self.path), [])
        self.assertTrue(os.path.exists(path), "without a copy the file is not removed")
        self.assertEqual(cards.load(self.path), {}, "and nothing was written either")

    def test_the_copy_is_not_copied_into_itself(self):
        self.legacy("cards.json", {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        config_document.import_legacy(self.path)
        kept = os.path.join(self.dir, config_document.BACKUP_DIR)
        self.assertNotIn(config_document.BACKUP_DIR, os.listdir(kept))
        # A half-written document is not worth keeping either.
        with open(os.path.join(self.dir, "rukebox.yaml.tmp"), "w", encoding="utf-8") as f:
            f.write("half a document")
        os.remove(os.path.join(kept, "cards.json"))
        config_document.write("cards", {"cards": []}, self.path)
        self.legacy("schedules.json", {"schedules": [{"id": "weekend", "name": "Weekend"}]})
        config_document.import_legacy(self.path)
        self.assertNotIn("rukebox.yaml.tmp", os.listdir(kept))

    def test_a_file_that_cannot_be_read_is_left_where_it_is(self):
        path = os.path.join(self.dir, "cards.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(config_document.import_legacy(self.path), [])
        self.assertTrue(os.path.exists(path), "a file we could not read is not thrown away")
        self.assertEqual(cards.load(self.path), {})

    def test_an_empty_file_is_left_where_it_is(self):
        path = self.legacy("cards.json", {})
        self.assertEqual(config_document.import_legacy(self.path), [])
        self.assertTrue(os.path.exists(path), "there was nothing to take")

    def test_a_list_the_document_already_holds_is_not_taken(self):
        path = self.legacy("cards.json", {"cards": [{"id": "abcd1234", "name": "Ancienne"}]})
        cards.save({"id": "1234abcd", "name": "Nouvelle", "action": "list", "target": "soir"},
                   self.path)
        self.assertEqual(config_document.import_legacy(self.path), [])
        self.assertEqual(sorted(cards.load(self.path)), ["1234abcd"])
        self.assertTrue(os.path.exists(path))

    def test_the_document_keeps_a_file_the_old_key_names(self):
        # A document this version did not write named the file itself.
        elsewhere = os.path.join(self.dir, "somewhere", "cards.json")
        os.makedirs(os.path.dirname(elsewhere), exist_ok=True)
        json_file.write(elsewhere, {"cards": [{"id": "abcd1234", "name": "Bleue"}]})
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            # Single quotes: a Windows path is full of backslashes, which YAML
            # reads as escapes inside double quotes.
            f.write("paths:\n  cards_file: '%s'\n"
                    "  hidden_file: '/var/lib/rukebox/hidden.json'\n" % elsewhere)
        self.assertEqual(config_document.legacy_path("cards", self.path), elsewhere)
        self.assertEqual(config_document.import_legacy(self.path), ["cards"])
        self.assertEqual(sorted(cards.load(self.path)), ["abcd1234"])
        self.assertFalse(os.path.exists(elsewhere))

    def test_ensure_takes_the_lists_in_before_seeding(self):
        # A brand new radio gets its two announcement types, but an updated one
        # must keep the list waiting in its own file.
        self.legacy("announcements.json", {"items": [{"id": "matin", "name": "Matin"}]})
        config_file.ensure_file(self.path, self.env)
        self.assertEqual([item["id"] for item in announcements.load(self.path)], ["matin"],
                         "the list in the file, not the two a new radio is offered")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "announcements.json")))

    def test_ensure_offers_a_new_radio_the_two_types(self):
        config_file.ensure_file(self.path, self.env)
        self.assertEqual([item["id"] for item in announcements.load(self.path)],
                         ["morning", "doubleclick"])


class StoreTest(unittest.TestCase):
    """Each store, through the document, with its own shape."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _path.document(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = _path.document(self.dir)

    def test_the_announcements_block_holds_items_and_volumes(self):
        announcements.set_volume("meme", {"on": True, "volume": 30}, self.path)
        self.assertEqual(config_document.read("announcements", self.path),
                         {"items": [], "volumes": {"meme": {"on": True, "volume": 30}}})

    def test_the_music_lists_block_holds_lists(self):
        music_lists.add({"name": "Soir", "kind": "manual"}, self.path)
        self.assertEqual(list(config_document.read("music_lists", self.path)), ["lists"])

    def test_the_schedules_block_holds_schedules(self):
        schedules.add({"name": "Weekend", "days": [5], "start": "09:00"}, self.path)
        self.assertEqual(list(config_document.read("schedules", self.path)), ["schedules"])

    def test_the_cards_block_holds_cards(self):
        cards.save({"id": "abcd1234", "name": "Bleue", "action": "list", "target": "soir"},
                   self.path)
        self.assertEqual(list(config_document.read("cards", self.path)), ["cards"])

    def test_the_track_order_block_holds_one_order_per_source(self):
        track_order.save_order("meme", ["b.mp3"], self.path)
        self.assertEqual(config_document.read("track_order", self.path), {"meme": ["b.mp3"]})

    def test_a_block_reads_as_its_own_document(self):
        # What each store used to read from its JSON file, it reads from the block.
        cards.save({"id": "abcd1234", "name": "Bleue", "action": "list", "target": "soir"},
                   self.path)
        self.assertEqual(json.dumps(config_document.read("cards", self.path), sort_keys=True),
                         json.dumps({"cards": [{"id": "abcd1234", "name": "Bleue",
                                                "action": "list", "target": "soir"}]},
                                    sort_keys=True))


if __name__ == "__main__":
    unittest.main()

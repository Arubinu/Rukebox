"""The configuration as one portable file (src/config_bundle.py), and the saved
order of the announcement folders it carries (src/track_order.py)."""
import json
import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401
import announcements
import config_bundle
import config_file
import track_order


class TrackOrderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "track_order.json")

    def test_nothing_ordered_yet(self):
        self.assertEqual(track_order.load(self.path), {})
        self.assertIsNone(track_order.get(self.path, "meme"))

    def test_an_order_is_kept_per_source_and_cleared_alone(self):
        track_order.save_order(self.path, "meme", ["b.mp3", "a.mp3"])
        track_order.save_order(self.path, "custom:lunch", ["2.mp3", "1.mp3"])
        self.assertEqual(track_order.get(self.path, "meme"), ["b.mp3", "a.mp3"])
        track_order.clear(self.path, "meme")
        self.assertIsNone(track_order.get(self.path, "meme"))
        self.assertEqual(track_order.get(self.path, "custom:lunch"), ["2.mp3", "1.mp3"])
        track_order.clear(self.path, "never-saved")

    def test_an_order_is_a_list_of_names(self):
        for wrong in ("a.mp3", None, ["a.mp3", 3], {"a.mp3": 1}):
            with self.assertRaises(ValueError) as refused:
                track_order.save_order(self.path, "meme", wrong)
            self.assertEqual(str(refused.exception), "bad_order")
        self.assertEqual(track_order.load(self.path), {})

    def test_what_is_not_an_order_in_the_file_is_left_out(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"meme": ["a.mp3"], "broken": "a.mp3", "mixed": ["a.mp3", 3]}, f)
        self.assertEqual(track_order.load(self.path), {"meme": ["a.mp3"]})

    def test_an_unreadable_file_is_no_order_rather_than_a_crash(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertLogs("json_file", level="ERROR"):
            self.assertEqual(track_order.load(self.path), {})


def announcement(**fields):
    item = {"name": "Lunch", "folder": "/home/pi/audio/lunch", "hour": 12, "minute": 30}
    item.update(fields)
    return item


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.yaml = os.path.join(self.dir, "rukebox.yaml")
        with open(self.yaml, "w", encoding="utf-8", newline="\n") as f:
            f.write(config_file.render_template())
        before = (config_file.YAML_FILE_CANDIDATES, config_file.ENV_FILE)
        config_file.YAML_FILE_CANDIDATES = [self.yaml]
        config_file.ENV_FILE = os.path.join(self.dir, "rukebox.env")
        self.addCleanup(lambda: setattr(config_file, "YAML_FILE_CANDIDATES", before[0]))
        self.addCleanup(lambda: setattr(config_file, "ENV_FILE", before[1]))
        self.cfg = {"ANNOUNCEMENTS_FILE": os.path.join(self.dir, "announcements.json"),
                    "TRACK_ORDER_FILE": os.path.join(self.dir, "track_order.json")}

    def bundle(self, **parts):
        data = {"format": config_bundle.BUNDLE_FORMAT, "version": config_bundle.BUNDLE_VERSION,
                "settings": {}}
        data.update(parts)
        return data

    def yaml_text(self):
        with open(self.yaml, encoding="utf-8") as f:
            return f.read()

    def test_the_export_carries_everything_but_the_two_secrets(self):
        config_file.write_values({"BASE_VOLUME": "42", "WEB_PASSWORD_HASH": "pbkdf2$secret",
                                  "WEB_SESSION_SECRET": "signing-key"})
        announcements.save_all(self.cfg["ANNOUNCEMENTS_FILE"], [dict(announcement(), id="lunch")])
        track_order.save_order(self.cfg["TRACK_ORDER_FILE"], "meme", ["b.mp3", "a.mp3"])

        bundle = config_bundle.export_bundle(self.cfg, {"tree_hash_short": "abc123"})
        self.assertEqual((bundle["format"], bundle["version"], bundle["app_version"]),
                         ("rukebox-config", 1, "abc123"))
        self.assertEqual(bundle["settings"]["BASE_VOLUME"], "42")
        self.assertNotIn("WEB_PASSWORD_HASH", bundle["settings"])
        self.assertNotIn("WEB_SESSION_SECRET", bundle["settings"])
        self.assertNotIn("pbkdf2$secret", json.dumps(bundle))
        self.assertEqual([item["id"] for item in bundle["announcements"]], ["lunch"])
        self.assertEqual(bundle["track_order"], {"meme": ["b.mp3", "a.mp3"]})

    def test_what_was_exported_comes_back_and_changes_nothing(self):
        config_file.write_values({"BASE_VOLUME": "42"})
        bundle = json.loads(json.dumps(config_bundle.export_bundle(self.cfg)))
        before = self.yaml_text()
        summary = config_bundle.import_bundle(bundle, self.cfg)
        self.assertEqual(summary["settings_changed"], 0)
        self.assertEqual(self.yaml_text(), before)

    def test_settings_are_written_line_by_line_and_counted_honestly(self):
        comments = self.yaml_text().count("#")
        data = self.bundle(settings={"BASE_VOLUME": 55, "MUSIC_LOOP": "false"})
        first = config_bundle.import_bundle(data, self.cfg)
        self.assertEqual((first["settings"], first["settings_changed"]), (2, 2))
        self.assertEqual(config_file.read_values()["BASE_VOLUME"], "55")
        self.assertEqual(self.yaml_text().count("#"), comments, "every comment is still there")
        again = config_bundle.import_bundle(data, self.cfg)
        self.assertEqual((again["settings"], again["settings_changed"]), (2, 0))

    def test_an_unknown_setting_is_reported_and_a_secret_is_never_written(self):
        config_file.write_values({"WEB_PASSWORD_HASH": "pbkdf2$mine"})
        summary = config_bundle.import_bundle(
            self.bundle(settings={"BASE_VOLUME": "30", "NOT_A_SETTING": "1",
                                  "WEB_PASSWORD_HASH": "pbkdf2$theirs"}), self.cfg)
        self.assertEqual(summary["unknown_settings"], ["NOT_A_SETTING"])
        self.assertEqual(summary["settings"], 1)
        self.assertEqual(config_file.read_values()["WEB_PASSWORD_HASH"], "pbkdf2$mine")

    def test_what_is_not_a_bundle_is_refused_with_a_code(self):
        for data, code in (("text", "not_a_bundle"),
                           ({"format": "something-else", "version": 1, "settings": {}}, "not_a_bundle"),
                           (self.bundle(version=0), "not_a_bundle"),
                           (self.bundle(version="x"), "not_a_bundle"),
                           (self.bundle(version=config_bundle.BUNDLE_VERSION + 1), "newer_bundle"),
                           (self.bundle(settings=None), "no_settings")):
            before = self.yaml_text()
            with self.assertRaises(ValueError) as refused:
                config_bundle.import_bundle(data, self.cfg)
            self.assertEqual(str(refused.exception), code)
            self.assertEqual(self.yaml_text(), before, "a refusal writes nothing")
        self.assertIn("newer version", config_bundle._refusal_message("newer_bundle"))
        self.assertEqual(config_bundle._refusal_message("anything"), "anything")

    def test_announcements_keep_their_ids_and_a_bad_one_costs_only_itself(self):
        summary = config_bundle.import_bundle(self.bundle(announcements=[
            dict(announcement(), id="lunch"),
            dict(announcement(name="Lunch again"), id="lunch"),
            announcement(name=""),
            "not an object",
            announcement(name="No id"),
        ]), self.cfg)
        self.assertEqual(summary["announcements"], 3)
        self.assertEqual(summary["announcements_rejected"], ["announcement_name_required", "not an object"])
        saved = announcements.load(self.cfg["ANNOUNCEMENTS_FILE"])
        self.assertEqual([item["id"] for item in saved], ["lunch", "lunch-2", "no-id"])

    def test_a_bundle_without_announcements_leaves_them_alone(self):
        announcements.save_all(self.cfg["ANNOUNCEMENTS_FILE"], [dict(announcement(), id="lunch")])
        track_order.save_order(self.cfg["TRACK_ORDER_FILE"], "meme", ["a.mp3"])
        summary = config_bundle.import_bundle(self.bundle(), self.cfg)
        self.assertEqual((summary["announcements"], summary["track_order"]), (0, 0))
        self.assertEqual(len(announcements.load(self.cfg["ANNOUNCEMENTS_FILE"])), 1)
        self.assertEqual(track_order.get(self.cfg["TRACK_ORDER_FILE"], "meme"), ["a.mp3"])

    def test_the_orders_come_back_without_what_is_not_one(self):
        summary = config_bundle.import_bundle(
            self.bundle(track_order={"meme": ["b.mp3", "a.mp3"], "broken": "a.mp3"}), self.cfg)
        self.assertEqual(summary["track_order"], 1)
        self.assertEqual(track_order.load(self.cfg["TRACK_ORDER_FILE"]), {"meme": ["b.mp3", "a.mp3"]})

    def test_a_file_that_cannot_be_read_is_none(self):
        path = os.path.join(self.dir, "bundle.json")
        with self.assertLogs("config_bundle", level="ERROR"):
            self.assertIsNone(config_bundle.load_bundle_file(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertLogs("config_bundle", level="ERROR"):
            self.assertIsNone(config_bundle.load_bundle_file(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.bundle(), f)
        self.assertEqual(config_bundle.load_bundle_file(path)["format"], "rukebox-config")


if __name__ == "__main__":
    unittest.main()

"""Configuration (src/config_schema.py, src/config_file.py): writes keep
every comment, unknown keys are refused, the generated env file cannot be
split by a line break, and config/rukebox.yaml is the template's output."""
import os
import shutil
import tempfile
import unittest

import _path
import config_file
import config_schema


class ConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.yaml = os.path.join(self.dir, "rukebox.yaml")
        self.env = os.path.join(self.dir, "rukebox.env")
        with open(self.yaml, "w", encoding="utf-8", newline="\n") as f:
            f.write(config_file.render_template())

    def tearDown(self):
        shutil.rmtree(self.dir)

    @staticmethod
    def text(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_write_keeps_comments(self):
        before = self.text(self.yaml)
        changed = config_file.write_values({"BASE_VOLUME": "42"}, path=self.yaml, env_path=self.env)
        after = self.text(self.yaml)
        self.assertEqual(changed, ["BASE_VOLUME"])
        self.assertEqual(before.count("#"), after.count("#"))
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        self.assertIn("BASE_VOLUME='42'", self.text(self.env))

    def test_unknown_key_refused(self):
        with self.assertRaises(ValueError):
            config_file.write_values({"NOT_A_SETTING": "1"}, path=self.yaml, env_path=self.env)

    def test_env_value_cannot_start_a_line(self):
        self.assertNotIn("\n", config_file._shell_quote("a\nEVIL=1"))
        self.assertEqual(config_file._shell_quote("it's"), "'it'\\''s'")

    def test_repository_template_is_current(self):
        self.assertEqual(_path.read("config", "rukebox.yaml").replace("\r\n", "\n"),
                         config_file.render_template().replace("\r\n", "\n"),
                         "regenerate: python3 src/config_file.py template > config/rukebox.yaml")


class SchemaTest(unittest.TestCase):
    def test_env_names_unique(self):
        names = [s.env for s in config_schema.SETTINGS]
        self.assertEqual(len(names), len(set(names)))

    def test_restart_required_are_settings(self):
        known = {s.env for s in config_schema.SETTINGS}
        self.assertLessEqual(set(config_schema.RESTART_REQUIRED), known)


if __name__ == "__main__":
    unittest.main()

"""Where Rukebox keeps its files (src/paths.py): the four roots and the
RUKEBOX_*_DIR environment variables that move them.

The container image is the reason this exists - it puts the configuration
under /config, the state under /data and the music under /music without
rewriting a single line of YAML - so the test that matters is that a moved
root really does move every path derived from it, and that the defaults stay
the Pi's own.

The roots are moved by patching the module's own DEFAULT_* values rather than
the environment, because that is what the test suite's sandbox does too: a
RUKEBOX_* variable is also an override load_config() honours, and a test that
passes its own cfg dictionary must keep the last word."""
import os
import unittest
from unittest import mock

import _path  # noqa: F401
import config_schema
import paths

MOVED = {"DEFAULT_CONFIG_DIR": "/config",
         "DEFAULT_STATE_DIR": "/data",
         "DEFAULT_MUSIC_DIR": "/music"}


class DefaultsTest(unittest.TestCase):
    def test_the_pi_layout_is_what_paths_py_ships(self):
        """Read from the source rather than from the module, which the test
        suite's sandbox has already moved."""
        source = _path.read("src", "paths.py")
        for name, value in (("DEFAULT_CONFIG_DIR", "/etc/rukebox"),
                            ("DEFAULT_STATE_DIR", "/var/lib/rukebox"),
                            ("DEFAULT_MUSIC_DIR", "/home/pi/audio"),
                            ("DEFAULT_INSTALL_DIR", "/opt/rukebox")):
            self.assertIn('%s = "%s"' % (name, value), source, name)

    def test_what_is_appended_to_a_root_uses_posix_separators(self):
        """These end up in the YAML and in the env file a Pi reads, so a
        backslash from the machine that rendered them would be a broken path
        there. The roots are set to POSIX values here, and every derived path
        has to stay under them."""
        for name, value in MOVED.items():
            patcher = mock.patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for key, value in config_schema._environment_paths().items():
            self.assertNotIn("\\", value, key)
            self.assertTrue(value.startswith(("/config", "/data", "/music")), (key, value))


class ResolveTest(unittest.TestCase):
    def test_without_the_variable_nothing_moves(self):
        self.assertEqual(paths.resolve("STATS_DB_FILE", "/var/lib/rukebox/stats.db"),
                         "/var/lib/rukebox/stats.db")

    def test_the_variable_wins(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_STATS_DB_FILE": "/data/stats.db"}):
            self.assertEqual(paths.resolve("STATS_DB_FILE", "/var/lib/rukebox/stats.db"),
                             "/data/stats.db")

    def test_an_empty_variable_is_not_a_path(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_STATS_DB_FILE": ""}):
            self.assertEqual(paths.resolve("STATS_DB_FILE", "/var/lib/rukebox/stats.db"),
                             "/var/lib/rukebox/stats.db")

    def test_a_forced_yaml_file_is_used_alone(self):
        with mock.patch.dict(os.environ, {"RUKEBOX_YAML_FILE": "/tmp/mine.yaml"}):
            self.assertEqual(paths.yaml_candidates(), ["/tmp/mine.yaml"])


class MovedRootTest(unittest.TestCase):
    def setUp(self):
        for name, value in MOVED.items():
            patcher = mock.patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_two_configuration_files_follow_the_config_root(self):
        self.assertEqual(paths.env_file(), "/config/rukebox.env")
        self.assertEqual(paths.yaml_candidates(),
                         ["/config/rukebox.yaml", "/config/rukebox.yml"])

    def test_every_derived_path_follows_its_root(self):
        self.assertEqual(paths.config("announcements.json"), "/config/announcements.json")
        self.assertEqual(paths.state("stats.db"), "/data/stats.db")
        self.assertEqual(paths.music("system", "restart.wav"), "/music/system/restart.wav")

    def test_the_generated_defaults_follow_the_roots(self):
        """config_schema builds every path setting from these, so this is the
        whole of "the container needs no rewritten YAML"."""
        defaults = config_schema._environment_paths()
        self.assertEqual(defaults["STATE_DIR"], "/data")
        self.assertEqual(defaults["STATS_DB_FILE"], "/data/stats.db")
        self.assertEqual(defaults["LIKES_FILE"], "/data/likes.json")
        self.assertEqual(defaults["ANNOUNCEMENTS_FILE"], "/config/announcements.json")
        self.assertEqual(defaults["CARDS_FILE"], "/config/cards.json")
        self.assertEqual(defaults["MUSIC_DIR"], "/music/music")
        self.assertEqual(defaults["MEME_DIR"], "/music/memes")
        self.assertEqual(defaults["CUTOFF_ANNOUNCE_DIR"], "/music/cutoff_announcements")
        self.assertEqual(defaults["KEEPALIVE_SOUND"], "/music/system/keepalive.wav")

    def test_the_documented_layout_is_untouched_by_a_move(self):
        """The YAML template documents the Pi's own paths whatever machine
        renders it, or the checkout's file would depend on who generated it."""
        self.assertEqual(config_schema.BY_ENV["MUSIC_DIR"].default, "/home/pi/audio/music")
        self.assertEqual(config_schema.BY_ENV["STATE_DIR"].default, "/var/lib/rukebox")


if __name__ == "__main__":
    unittest.main()

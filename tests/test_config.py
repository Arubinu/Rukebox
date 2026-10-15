"""Configuration (src/config_schema.py, src/config_file.py): writes keep
every comment, unknown keys are refused, the generated env file cannot be
split by a line break, and config/rukebox.yaml is the template's output."""
import glob
import os
import re
import shutil
import tempfile
import unittest
import unittest.mock

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
        changed = config_file.write_values({"playback.base_volume": "42"},
                                           path=self.yaml, env_path=self.env)
        after = self.text(self.yaml)
        self.assertEqual(changed, ["BASE_VOLUME"])
        self.assertEqual(before.count("#"), after.count("#"))
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        self.assertIn("PLAYBACK_BASE_VOLUME='42'", self.text(self.env),
                      "a setting that is not this machine's default is carried")

    def test_a_setting_is_named_by_its_path(self):
        for setting in config_schema.SETTINGS:
            self.assertEqual(setting.variable, setting.path.upper().replace(".", "_"))
        names = [s.variable for s in config_schema.SETTINGS]
        self.assertEqual(len(names), len(set(names)), "two settings, one name")
        self.assertEqual(config_schema.find_setting("audio.output"),
                         config_schema.BY_ENV["AUDIO_OUTPUT"])
        self.assertEqual(config_schema.find_setting("AUDIO_OUTPUT"),
                         config_schema.BY_ENV["AUDIO_OUTPUT"], "the name it had")
        for name in set(config_schema.BY_VARIABLE) & set(config_schema.BY_ENV):
            self.assertIs(config_schema.BY_VARIABLE[name], config_schema.BY_ENV[name],
                          "%s names two settings" % name)

    def test_the_generated_file_carries_what_a_shell_reads(self):
        """A script reads a setting by a name: the file must carry it, or the
        script silently reads nothing."""
        named = set()
        for path in glob.glob(os.path.join(_path.ROOT, "scripts", "*.sh")) \
                + glob.glob(os.path.join(_path.ROOT, "systemd", "*")):
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            for word in re.findall(r"[A-Za-z][A-Za-z0-9_.]{2,}", text):
                setting = config_schema.find_setting(word)
                if setting is not None:
                    named.add(setting.path)
        self.assertEqual(named - set(config_schema.SHELL_SETTINGS), set(),
                         "add them to SHELL_SETTINGS")
        self.assertEqual(set(config_schema.SHELL_SETTINGS) - named, set(),
                         "nothing reads these any more")

    def test_a_name_is_read_whatever_form_it_has(self):
        """The environment file is read back even when a line still carries the
        name a setting had before."""
        with open(self.env, "w", encoding="utf-8", newline="\n") as f:
            f.write("MUSIC_DIR='/srv/audio/music'\nFOLDERS_MEMES='/srv/audio/memes'\n")
        values = config_file.read_env_file(self.env)
        self.assertEqual(values["MUSIC_DIR"], "/srv/audio/music")
        self.assertEqual(values["MEME_DIR"], "/srv/audio/memes")

    def test_unknown_key_refused(self):
        with self.assertRaises(ValueError):
            config_file.write_values({"NOT_A_SETTING": "1"}, path=self.yaml, env_path=self.env)

    def test_env_value_cannot_start_a_line(self):
        self.assertNotIn("\n", config_file._shell_quote("a\nEVIL=1"))
        self.assertEqual(config_file._shell_quote("it's"), "'it'\\''s'")

    def test_repository_template_is_current(self):
        # The template carries the Pi's own paths whatever machine renders it:
        # moving a root applies to the defaults, not to the documented layout.
        self.assertEqual(_path.read("config", "rukebox.yaml").replace("\r\n", "\n"),
                         config_file.render_template().replace("\r\n", "\n"),
                         "regenerate: python3 src/config_file.py template > config/rukebox.yaml")

    def test_a_new_setting_is_added_with_this_machines_own_path(self):
        """A setting a new version introduces is added to a *machine*: a
        container that got the Pi's documented path would keep its sounds
        somewhere that does not exist there (reported as: the music folder
        settings still said /home/pi)."""
        values = dict(config_file.DEFAULTS)
        values["DJ_ANNOUNCE_DIR"] = "/srv/rukebox/audio/dj_announcements"
        lines = [line for line in self.text(self.yaml).splitlines(True)
                 if not line.startswith("  dj_announcements:")]
        with open(self.yaml, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)
        with unittest.mock.patch.dict(config_file.DEFAULTS, values):
            self.assertIn("DJ_ANNOUNCE_DIR",
                          config_file.merge_missing(self.yaml, self.env))
        self.assertIn('dj_announcements: "/srv/rukebox/audio/dj_announcements"',
                      self.text(self.yaml), "the machine's own audio root, not the Pi's")
        self.assertIn("FOLDERS_DJ_ANNOUNCEMENTS='/srv/rukebox/audio/dj_announcements'",
                      self.text(self.env))

    def test_the_repository_template_still_documents_the_pis_own_paths(self):
        self.assertIn('dj_announcements: "/home/pi/audio/dj_announcements"',
                      _path.read("config", "rukebox.yaml"))

    def test_a_folder_settings_left_at_the_pis_root_follows_this_machine(self):
        """An installation whose audio tree is elsewhere gets the folders a new
        version added moved with it: left at the Pi's path, the interface would
        write outside the tree it belongs in and the radio would read nothing."""
        nothing = config_file.retarget_folders("/home/pi/audio", "/home/pi/audio",
                                               self.yaml, self.env)
        self.assertEqual(nothing, [], "a Pi is already there")
        moved = config_file.retarget_folders("/home/pi/audio", "/srv/rukebox/audio",
                                             self.yaml, self.env)
        self.assertIn("DJ_ANNOUNCE_DIR", moved)
        self.assertIn('dj_announcements: "/srv/rukebox/audio/dj_announcements"',
                      self.text(self.yaml))
        self.assertIn("FOLDERS_MUSIC='/srv/rukebox/audio/music'", self.text(self.env),
                      "the environment file follows the same move")

    def point_announcements_at(self, folder):
        """A configuration whose announcements file is the test's own."""
        ann_path = os.path.join(self.dir, "announcements.json")
        config_file.write_values({"ANNOUNCEMENTS_FILE": ann_path},
                                 path=self.yaml, env_path=self.env)
        import announcements

        return ann_path, announcements

    def test_the_announcement_folders_move_to_this_machines_audio_root(self):
        """A container keeps its audio elsewhere: seeded folders left under the
        Pi's root are ones the interface may not write to, and ones the radio
        would play from an empty folder. Reported by the owner, twice."""
        ann_path, announcements = self.point_announcements_at(self.dir)
        announcements.seed_defaults(ann_path, "/home/pi/audio")
        moved = config_file.retarget_announcements("/home/pi/audio", "/srv/rukebox/audio",
                                                   self.yaml, self.env)
        self.assertEqual(moved, 2)
        folders = [item["folder"] for item in announcements.read_items(ann_path)]
        self.assertEqual(folders, ["/srv/rukebox/audio/morning_announcements",
                                   "/srv/rukebox/audio/doubleclick_announcements"])
        self.assertEqual(config_file.retarget_announcements(
            "/home/pi/audio", "/srv/rukebox/audio", self.yaml, self.env), 0,
            "a second run moves nothing")

    def test_the_seeded_folders_follow_the_configured_audio_root(self):
        """The folders a new radio is offered come from this machine's audio
        root, not from the Pi's template - the template is a document."""
        moved = os.path.join(self.dir, "audio")
        ann_path, announcements = self.point_announcements_at(self.dir)
        config_file.write_values({"MEME_DIR": os.path.join(moved, "memes")},
                                 path=self.yaml, env_path=self.env)
        self.assertTrue(config_file.seed_announcements(self.yaml, self.env))
        folders = [item["folder"] for item in announcements.read_items(ann_path)]
        self.assertEqual(folders, [os.path.join(moved, "morning_announcements"),
                                   os.path.join(moved, "doubleclick_announcements")])


class SchemaTest(unittest.TestCase):
    def test_env_names_unique(self):
        names = [s.env for s in config_schema.SETTINGS]
        self.assertEqual(len(names), len(set(names)))

    def test_restart_required_are_settings(self):
        known = {s.env for s in config_schema.SETTINGS}
        self.assertLessEqual(set(config_schema.RESTART_REQUIRED), known)


if __name__ == "__main__":
    unittest.main()

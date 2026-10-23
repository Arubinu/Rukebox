"""A rollback says which version runs again: update.sh's restore_version_file, run in bash."""
import getpass
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _path  # noqa: F401

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "update.sh")


def function_text():
    with open(SCRIPT, encoding="utf-8") as f:
        text = f.read()
    return re.search(r"^restore_version_file\(\) \{\n.*?^\}\n", text, re.S | re.M).group(0)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "update.sh runs in bash")
class RollbackVersionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.version = os.path.join(self.dir, "version.json")
        self.backup = os.path.join(self.dir, "opt-rukebox-20261023-174812.tar.gz")

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def rollback(self):
        script = 'say() { echo "$@"; }\n' + function_text() + 'restore_version_file "$1"\n'
        env = dict(os.environ, VERSION_FILE=self.version, RUKEBOX_USER=getpass.getuser())
        done = subprocess.run(["bash", "-c", script, "rollback", self.backup], env=env,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(self.version, encoding="utf-8") as f:
            return json.load(f)

    def test_the_copy_kept_beside_the_backup_comes_back(self):
        self.write(self.version, {"tree_hash": "new" * 10, "release": "v2.0.0"})
        self.write(self.backup[:-len(".tar.gz")] + ".version.json", {"tree_hash": "old" * 10, "release": "v1.19.3"})
        self.assertEqual(self.rollback()["release"], "v1.19.3")

    def test_an_older_backup_falls_back_on_the_version_before(self):
        self.write(self.version, {"tree_hash": "new" * 10, "previous_tree_hash": "0ba8db2b9ff1" + "a" * 52,
                                  "release": "v2.0.0"})
        info = self.rollback()
        self.assertEqual((info["tree_hash_short"], info.get("release")), ("0ba8db2b9ff1", None),
                         "never the release just left")

    def test_nothing_known_leaves_the_file_as_it_was(self):
        self.write(self.version, {"tree_hash": "new" * 10})
        self.assertEqual(self.rollback(), {"tree_hash": "new" * 10})


if __name__ == "__main__":
    unittest.main()

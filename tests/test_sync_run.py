"""One sync run: the rclone command, its configuration, its log, and a run end to end."""
import json
import os
import shutil
import stat
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import _path  # noqa: F401
import sync

CONN = {"id": "nas", "type": "sftp", "host": "nas.local", "port": 22, "user": "pi", "path": "Music",
        "auth": "password", "tls": "verify", "host_key": "nas.local ssh-ed25519 AAAA"}
JOB = {"id": "music", "connection": "nas", "remote": "Rock", "target": "/srv/music",
       "extensions": ["mp3", "flac", "jpg"], "max_size_mb": 300}


class CommandTest(unittest.TestCase):
    def test_only_the_wanted_files_are_fetched_and_never_deleted(self):
        with mock.patch.object(sync.shutil, "which", return_value=None):
            command = sync.build_command(JOB, CONN, "/tmp/x/rclone.conf", limit_kbps=100)
        self.assertEqual(command[:4], ["rclone", "copy", "source:Music/Rock", "/srv/music"])
        self.assertIn("*.{mp3,flac,jpg}", command)
        self.assertIn("--ignore-case", command)
        self.assertEqual(command[command.index("--max-size") + 1], "300M")
        self.assertEqual(command[command.index("--bwlimit") + 1], "100k")
        for never in ("sync", "--delete-after", "move", "--no-check-certificate"):
            self.assertNotIn(never, command)
        unlimited = sync.build_command(JOB, CONN, "c", 0)
        self.assertNotIn("--bwlimit", unlimited)
        capped = sync.build_command(JOB, CONN, "c", 0, room_mb=700)
        self.assertEqual(capped[capped.index("--max-transfer") + 1], "700M", "never more than the room left")
        self.assertEqual(capped[capped.index("--cutoff-mode") + 1], "soft")

    def test_it_runs_below_the_music(self):
        with mock.patch.object(sync.shutil, "which", return_value="/usr/bin/x"):
            command = sync.build_command(JOB, CONN, "c")
        self.assertEqual(command[:6], ["nice", "-n", "15", "ionice", "-c3", "rclone"])

    def test_the_configuration_never_holds_the_password(self):
        text = sync.remote_config(CONN, key_file=None, known_hosts="/run/k")
        self.assertIn("host = nas.local", text)
        self.assertIn("known_hosts_file = /run/k", text)
        self.assertIn("shell_type = none", text, "nothing is ever run on the other machine")
        self.assertNotIn("pass", text)
        dav = sync.remote_config(dict(CONN, type="webdav", port=443, vendor="nextcloud", host="fe80::1"))
        self.assertIn("url = https://[fe80::1]:443", dav)
        ftp = sync.remote_config(dict(CONN, type="ftp", port=21, tls="self_signed"))
        self.assertIn("explicit_tls = true", ftp)
        self.assertIn("no_check_certificate = true", ftp)
        self.assertNotIn("explicit_tls", sync.remote_config(dict(CONN, type="ftp", port=21, tls="none")))

    def test_the_log_says_what_came_in(self):
        events = [sync.parse_line(json.dumps(line)) for line in (
            {"level": "info", "msg": "Copied (new)", "object": "a.mp3"},
            {"level": "info", "msg": "Copied (replaced existing)", "object": "b.mp3"},
            {"level": "error", "msg": "Failed to copy: denied", "object": "c.mp3"},
            {"level": "notice", "msg": "stats", "stats": {"bytes": 10, "totalBytes": 40, "transfers": 1,
                                                            "totalTransfers": 3, "errors": 0}},
        )] + [sync.parse_line("not json"), sync.parse_line("[]")]
        self.assertEqual([e and e["kind"] for e in events], ["copied", "copied", "error", "stats", None, None])
        self.assertEqual((events[0]["new"], events[1]["new"]), (True, False))
        self.assertEqual((events[3]["done"], events[3]["total"]), (1, 3))


class HalfFileTest(unittest.TestCase):
    def test_the_files_under_way_are_read_from_the_stats(self):
        event = sync.parse_line(json.dumps({"stats": {"transferring": [{"name": "A/b.mp3", "bytes": 5}, {}]}}))
        self.assertEqual(event["transferring"], ["A/b.mp3"])
        self.assertEqual(sync.parse_line(json.dumps({"stats": {}}))["transferring"], [])

    def test_a_stopped_run_leaves_no_broken_song(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        target = os.path.join(folder, "music")
        os.makedirs(os.path.join(target, "A"))
        for name in ("A/half.mp3", "A/done.mp3", "A/old.mp3", "outside.mp3"):
            with open(os.path.join(target if name != "outside.mp3" else folder, name), "w") as f:
                f.write("x")
        os.utime(os.path.join(target, "A/old.mp3"), (1000, 1000))
        run = sync.Run(dict(JOB, target=target), CONN, workdir=folder)
        run.started = time.time() - 10
        run.added = ["A/done.mp3"]
        run._under_way = {"A/half.mp3", "A/done.mp3", "A/old.mp3", "../outside.mp3", "A/gone.mp3"}
        run._clear_half_files()
        self.assertEqual(sorted(os.listdir(os.path.join(target, "A"))), ["done.mp3", "old.mp3"],
                         "the half copy goes; a finished one and one a newer rclone left untouched stay")
        self.assertTrue(os.path.exists(os.path.join(folder, "outside.mp3")), "never outside the target")


class RunsFileTest(unittest.TestCase):
    def test_the_newest_runs_are_kept(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for i in range(sync.RUNS_KEPT + 5):
            sync.add_run(folder, {"job": "m", "started": i, "new_files": ["x"] * 80})
        runs = sync.load_runs(folder)
        self.assertEqual((len(runs), runs[0]["started"], len(runs[0]["new_files"])), (sync.RUNS_KEPT, 54, 50))


FAKE_RCLONE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    log = os.environ["FAKE_LOG"]
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "pass": os.environ.get("RCLONE_CONFIG_SOURCE_PASS"),
                            "config": open(os.environ["RCLONE_CONFIG"]).read()}) + "\\n")
    if sys.argv[1] == "obscure":
        print("OBSCURED-" + sys.stdin.read())
        sys.exit(0)
    target = sys.argv[3]
    open(os.path.join(target, "new.mp3"), "w").close()
    for line in ({"level": "info", "msg": "Copied (new)", "object": "new.mp3"},
                 {"level": "notice", "msg": "x", "stats": {"bytes": 5, "transfers": 1, "totalTransfers": 1}}):
        sys.stderr.write(json.dumps(line) + "\\n")
    """)


@unittest.skipUnless(os.name == "posix", "a fake rclone is a POSIX script")
class RunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(bin_dir)
        fake = os.path.join(bin_dir, "rclone")
        with open(fake, "w") as f:
            f.write(FAKE_RCLONE)
        os.chmod(fake, 0o755)
        self.log = os.path.join(self.dir, "log")
        patcher = mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"],
                                               "FAKE_LOG": self.log, "RCLONE_CONFIG": "/dev/null"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_run_fetches_and_reports_without_showing_the_password(self):
        target = os.path.join(self.dir, "music")
        job = dict(JOB, target=target)
        run = sync.Run(job, CONN, password="s3cret", limit_kbps=50, workdir=self.dir).start()
        self.assertEqual(run.wait(20), "ok")
        record = run.record()
        self.assertEqual((record["added"], record["updated"], record["new_files"]), (1, 0, ["new.mp3"]))
        self.assertTrue(os.path.exists(os.path.join(target, "new.mp3")))
        with open(self.log, encoding="utf-8") as f:
            calls = [json.loads(line) for line in f]
        copy = calls[-1]
        self.assertEqual(copy["pass"], "OBSCURED-s3cret", "through the environment")
        self.assertFalse(any("s3cret" in arg for call in calls for arg in call["argv"]), "never on a command line")
        self.assertIn("known_hosts_file", copy["config"])
        self.assertEqual([name for name in os.listdir(self.dir) if name.startswith("rukebox-sync-")], [],
                         "the run's own configuration is gone with it")

    def test_the_run_folder_is_the_service_accounts_alone(self):
        run = sync.Run(dict(JOB, target=os.path.join(self.dir, "m")), CONN, workdir=self.dir)
        config_path, _env = run._prepare()
        self.addCleanup(shutil.rmtree, run._folder, True)
        self.assertEqual(stat.S_IMODE(os.stat(run._folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

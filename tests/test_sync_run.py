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


class MirrorCommandTest(unittest.TestCase):
    def test_a_mirror_syncs_into_its_trash_after_a_dry_run(self):
        job = dict(JOB, mode="mirror")
        with mock.patch.object(sync.shutil, "which", return_value=None):
            dry = sync.build_command(job, CONN, "c", dry_run=True)
            real = sync.build_command(job, CONN, "c", trash="/var/lib/rukebox/sync-trash/music/x")
        self.assertEqual(dry[:2], ["rclone", "sync"])
        self.assertIn("--dry-run", dry)
        self.assertNotIn("--backup-dir", dry)
        self.assertEqual(real[real.index("--backup-dir") + 1], "/var/lib/rukebox/sync-trash/music/x",
                         "nothing is deleted outright")
        self.assertNotIn("--dry-run", real)
        with self.assertRaises(ValueError):
            sync.build_command(job, CONN, "c")
        self.assertNotIn("--backup-dir", sync.build_command(JOB, CONN, "c", trash="/t"), "a copy removes nothing")

    def test_the_log_says_what_goes(self):
        lines = [{"level": "warning", "msg": "Skipped delete as --dry-run is set (size 2)", "object": "a.mp3",
                  "skipped": "delete"},
                 {"level": "warning", "msg": "Skipped copy as --dry-run is set", "object": "b.mp3", "skipped": "copy"},
                 {"level": "info", "msg": "Moved (server-side)", "object": "c.mp3"},
                 {"level": "info", "msg": "Moved into backup dir", "object": "c.mp3"}]
        self.assertEqual([(e or {}).get("kind") for e in map(sync.parse_line, map(json.dumps, lines))],
                         ["would_remove", None, "moved", "removed"])


class MirrorGuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for i in range(20):
            open(os.path.join(self.dir, "%d.mp3" % i), "w").close()
        open(os.path.join(self.dir, "mine.exe"), "w").close()

    def verdict(self, names, approved=(), max_files=50, max_percent=10):
        run = sync.Run(dict(JOB, target=self.dir, mode="mirror"), CONN,
                       mirror={"trash": "/t", "max_files": max_files, "max_percent": max_percent,
                               "approved": list(approved)})
        run.would_remove = list(names)
        return run._guard()

    def test_a_few_go_many_wait_and_all_never(self):
        self.assertEqual(self.verdict([]), "running")
        self.assertEqual(self.verdict(["0.mp3", "1.mp3"]), "running", "10 % of 20 files")
        self.assertEqual(self.verdict(["0.mp3", "1.mp3", "2.mp3"]), "mirror_confirm", "more than 10 %")
        self.assertEqual(self.verdict(["0.mp3", "1.mp3"], max_files=1), "mirror_confirm", "more than the count")
        self.assertEqual(self.verdict(["%d.mp3" % i for i in range(20)], approved=["%d.mp3" % i for i in range(20)]),
                         "mirror_source_empty", "an empty share never empties the radio, even once allowed")

    def test_what_the_owner_allowed_goes_once_and_nothing_more(self):
        three = ["0.mp3", "1.mp3", "2.mp3"]
        self.assertEqual(self.verdict(three, approved=three), "running")
        self.assertEqual(self.verdict(three + ["3.mp3"], approved=three), "mirror_confirm",
                         "a file not shown to the owner is asked again")


FAKE_MIRROR = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    if sys.argv[1] == "obscure":
        sys.exit(0)
    target = sys.argv[3]
    source = set(os.environ["FAKE_SOURCE"].split(","))
    here = sorted(os.listdir(target))
    def say(**line):
        sys.stderr.write(json.dumps(line) + "\\n")
    if "--dry-run" in sys.argv:
        for name in here:
            if name not in source:
                say(level="warning", msg="Skipped delete as --dry-run is set", object=name, skipped="delete")
        sys.exit(0)
    trash = sys.argv[sys.argv.index("--backup-dir") + 1]
    os.makedirs(trash)
    for name in here:
        if name not in source:
            shutil.move(os.path.join(target, name), os.path.join(trash, name))
            say(level="info", msg="Moved (server-side)", object=name)
            say(level="info", msg="Moved into backup dir", object=name)
    for name in sorted(source - set(here)):
        open(os.path.join(target, name), "w").close()
        say(level="info", msg="Copied (new)", object=name)
    """)


@unittest.skipUnless(os.name == "posix", "a fake rclone is a POSIX script")
class MirrorRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "rclone"), "w") as f:
            f.write(FAKE_MIRROR)
        os.chmod(os.path.join(bin_dir, "rclone"), 0o755)
        self.target = os.path.join(self.dir, "music")
        os.makedirs(self.target)
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            open(os.path.join(self.target, name), "w").close()
        patcher = mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"],
                                               "FAKE_SOURCE": "a.mp3,b.mp3,d.mp3"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_mirror(self, **guard):
        pids = []
        mirror = dict({"trash": os.path.join(self.dir, "trash"), "max_files": 50, "max_percent": 50}, **guard)
        run = sync.Run(dict(JOB, target=self.target, mode="mirror", extensions=["mp3"]), dict(CONN, auth="none"),
                       workdir=self.dir, mirror=mirror, on_process=pids.append).start()
        run.wait(20)
        return run, pids

    def test_a_mirror_moves_what_is_gone_into_its_trash(self):
        run, pids = self.run_mirror()
        self.assertEqual(run.status, "ok")
        self.assertEqual(len(pids), 2, "the dry run, then the run: the daemon sees each")
        self.assertEqual(sorted(os.listdir(self.target)), ["a.mp3", "b.mp3", "d.mp3"])
        (folder,) = os.listdir(os.path.join(self.dir, "trash"))
        self.assertEqual(os.listdir(os.path.join(self.dir, "trash", folder)), ["c.mp3"])
        record = run.record()
        self.assertEqual((record["added"], record["removed"], record["new_files"]), (1, 1, ["d.mp3"]))

    def test_too_many_removals_wait_and_nothing_moves(self):
        run, pids = self.run_mirror(max_percent=10)
        self.assertEqual((run.status, run.would_remove, len(pids)), ("mirror_confirm", ["c.mp3"], 1))
        self.assertEqual(sorted(os.listdir(self.target)), ["a.mp3", "b.mp3", "c.mp3"], "not even the new one")
        self.assertEqual(run.record()["would_remove"], 1)


if __name__ == "__main__":
    unittest.main()

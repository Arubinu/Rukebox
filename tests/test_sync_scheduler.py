"""When syncs run, what holds them, the switch-off that waits, and the owner's routes."""
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

import _path
import accounts
import secret_box
import sync
import sync_scheduler
import syncs
from test_empty_library import DaemonCase

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None

JOB = {"id": "music", "enabled": True, "every_hours": 6, "at_boot": False, "window_start": "",
       "window_end": "", "internet": "any", "wifi": "any", "ssid": "", "playback": "idle",
       "connection": "nas"}


def at(hour, minute=0):
    return datetime(2026, 10, 19, hour, minute)


class ScheduleTest(unittest.TestCase):
    def test_a_sync_comes_back_every_n_hours_after_the_boot_delay(self):
        boot = at(8).timestamp()
        self.assertFalse(sync_scheduler.due(JOB, at(8, 3), None, boot, 5), "not in the first minutes")
        self.assertTrue(sync_scheduler.due(JOB, at(8, 6), None, boot, 5), "never ran: due")
        self.assertFalse(sync_scheduler.due(JOB, at(12), at(8, 6).timestamp(), boot, 5))
        self.assertTrue(sync_scheduler.due(JOB, at(14, 7), at(8, 6).timestamp(), boot, 5))
        self.assertFalse(sync_scheduler.due(dict(JOB, enabled=False), at(14), None, boot, 0))
        self.assertFalse(sync_scheduler.due(dict(JOB, every_hours=0), at(14), None, boot, 0), "by hand only")

    def test_at_boot_runs_once_per_start(self):
        job = dict(JOB, every_hours=0, at_boot=True)
        boot = at(7).timestamp()
        self.assertTrue(sync_scheduler.due(job, at(7, 10), at(6).timestamp(), boot, 5), "the last run was before")
        self.assertFalse(sync_scheduler.due(job, at(9), at(7, 10).timestamp(), boot, 5))

    def test_the_hours_may_cross_midnight(self):
        night = dict(JOB, window_start="23:00", window_end="05:00")
        self.assertTrue(sync_scheduler.in_window(night, at(23, 30)))
        self.assertTrue(sync_scheduler.in_window(night, at(4, 59)))
        self.assertFalse(sync_scheduler.in_window(night, at(12)))
        day = dict(JOB, window_start="02:00", window_end="06:00")
        self.assertTrue(sync_scheduler.in_window(day, at(2)))
        self.assertFalse(sync_scheduler.in_window(day, at(6)))
        self.assertTrue(sync_scheduler.in_window(JOB, at(12)), "no hours: the whole day")


class ConditionTest(unittest.TestCase):
    def test_internet_wifi_and_music_hold_a_sync_as_asked(self):
        net = {"internet": True, "home": False, "ssid": "Box-5G"}
        idle = {"playing": False}
        cases = [
            ({}, net, idle, None),
            ({"internet": "needed"}, dict(net, internet=False), idle, "internet_missing"),
            ({"internet": "forbidden"}, net, idle, "internet_present"),
            ({"internet": "forbidden"}, dict(net, internet=False), idle, None),
            ({"wifi": "home"}, net, idle, "wifi_missing"),
            ({"wifi": "ssid", "ssid": "Box-5G"}, net, idle, None),
            ({"wifi": "ssid", "ssid": "Other"}, net, idle, "wifi_missing"),
            ({}, net, {"playing": True}, "playing"),
            ({"playback": "any"}, net, {"playing": True}, None),
        ]
        for changes, state, play, why in cases:
            self.assertEqual(sync_scheduler.hold_reason(dict(JOB, **changes), state, play, False), why, changes)
        self.assertEqual(sync_scheduler.hold_reason(JOB, net, idle, True), "secret_lost")

    def test_the_speed_follows_the_built_in_bluetooth(self):
        cfg = {"SYNC_LIMIT_KBPS": 500, "SYNC_LIMIT_BT_KBPS": 80, "SYNC_BT_MODE": "limit"}
        self.assertEqual(sync_scheduler.rate_for(cfg, False), 500)
        self.assertEqual(sync_scheduler.rate_for(cfg, True), 80)
        self.assertEqual(sync_scheduler.rate_for(dict(cfg, SYNC_LIMIT_KBPS=0), True), 80, "no limit, then the lower")
        self.assertEqual(sync_scheduler.rate_for(dict(cfg, SYNC_LIMIT_KBPS=50), True), 50, "never faster while it plays")
        self.assertIsNone(sync_scheduler.rate_for(dict(cfg, SYNC_BT_MODE="pause"), True))
        self.assertEqual(sync_scheduler.rate_for(dict(cfg, SYNC_BT_MODE="none"), True), 500)


class FakeRun:
    made = []

    def __init__(self, job, conn, password, key_file, rate, workdir, room_mb=None):
        self.job, self.rate = job, rate
        self.started, self.ended, self.progress = 1.0, None, {}
        self.added, self.updated = [], []
        self.alive = True
        self.done = threading.Event()
        self.process = mock.Mock(pid=os.getpid())
        FakeRun.made.append(self)

    def start(self):
        return self

    def running(self):
        return self.alive

    def stop(self):
        self.alive = False
        self.done.set()

    def wait(self, timeout=None):
        self.done.wait(timeout or 10)
        return "stopped"

    def record(self):
        return {"job": self.job["id"], "started": 1.0, "ended": 2.0, "status": "ok",
                "added": len(self.added), "updated": 0, "new_files": list(self.added)}


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(mock.patch.stopall)
        FakeRun.made = []
        self.cfg = {"SYNC_BOOT_DELAY_MIN": 0, "SYNC_LIMIT_KBPS": 0, "SYNC_LIMIT_BT_KBPS": 80,
                    "SYNC_BT_MODE": "limit"}
        self.play = {"playing": False, "builtin_bt": False}
        self.after = []
        conn = {"id": "nas", "type": "smb", "auth": "none", "host": "nas", "port": 445}
        mock.patch.object(syncs, "load", return_value={"connections": [conn], "jobs": [JOB]}).start()
        mock.patch.object(syncs, "job", return_value=dict(JOB, target=self.dir)).start()
        mock.patch.object(syncs, "connection", return_value=conn).start()
        mock.patch.object(sync, "rclone_available", return_value=True).start()
        mock.patch.object(sync, "Run", FakeRun).start()
        mock.patch.object(sync_scheduler, "network_state",
                          return_value={"internet": True, "home": True, "ssid": ""}).start()
        store = mock.Mock()
        store.lost.return_value = []
        self.manager = sync_scheduler.Manager(lambda: self.cfg, lambda: self.play,
                                              lambda job, record: self.after.append(record),
                                              self.dir, boot_at=0, store=store)
        self.addCleanup(lambda: [run.stop() for run in FakeRun.made])

    def until(self, check):
        deadline = time.monotonic() + 5
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(check())

    def test_one_due_sync_starts_and_says_it_runs(self):
        self.assertEqual(self.manager.tick(at(10)), "music")
        if os.name == "posix":
            self.assertEqual(sync_scheduler.running(self.dir)["job"], "music", "the daemon can see it")
        self.assertIsNone(self.manager.tick(at(10, 1)), "one at a time")
        self.assertEqual(len(FakeRun.made), 1)

    def test_music_starting_sets_the_run_aside_and_it_resumes_later(self):
        self.manager.tick(at(10))
        first = FakeRun.made[0]
        first.added = ["a.mp3"]
        self.play = {"playing": True, "builtin_bt": True}
        self.manager.tick(at(10, 1))
        self.assertFalse(first.alive, "an idle-only sync steps aside for the music")
        self.until(lambda: self.manager.resume == "music")
        self.assertEqual(self.after, [], "no run recorded for a pause")
        self.assertIsNone(self.manager.tick(at(10, 2)), "still playing")
        self.assertEqual(self.manager.held["music"], "playing")
        self.play = {"playing": False, "builtin_bt": False}
        self.assertEqual(self.manager.tick(at(10, 3)), "music")
        FakeRun.made[1].stop()
        self.until(lambda: self.after)
        self.assertEqual(self.after[0]["new_files"], ["a.mp3"], "what the first part brought is kept")

    def test_a_lost_password_holds_the_sync(self):
        self.manager.store.lost.return_value = ["nas"]
        self.assertIsNone(self.manager.tick(at(10)))
        self.assertEqual(self.manager.held, {"music": "secret_lost"})

    def test_a_press_from_another_process_starts_or_stops_a_sync(self):
        sync_scheduler.ask(self.dir, "music")
        self.assertIsNone(self.manager.answer_request())
        self.assertEqual(len(FakeRun.made), 1, "started at once, whatever its hours")
        self.assertFalse(os.path.exists(os.path.join(self.dir, sync_scheduler.REQUEST_FILE)), "taken once")
        self.assertIsNone(self.manager.answer_request(), "nothing asked, nothing done")
        self.assertEqual(len(FakeRun.made), 1)
        sync_scheduler.ask(self.dir, stop=True)
        self.manager.answer_request()
        self.assertFalse(FakeRun.made[0].alive)

    def test_an_old_press_is_forgotten(self):
        sync_scheduler.ask(self.dir, "music")
        with mock.patch.object(sync_scheduler.time, "time",
                               return_value=time.time() + sync_scheduler.REQUEST_MAX_AGE_SEC + 5):
            self.manager.answer_request()
        self.assertEqual(FakeRun.made, [], "a press the web server missed is not run hours later")

    def test_every_start_checks_where_it_writes(self):
        self.manager.refuse = lambda target: "sync_target_usb"
        sync_scheduler.ask(self.dir, "music")
        self.assertEqual(self.manager.answer_request(), "sync_target_usb")
        self.assertEqual(self.manager.tick(at(10)), None)
        self.assertEqual((FakeRun.made, self.manager.held["music"]), ([], "sync_target_usb"))


class ShutdownWaitTest(DaemonCase):
    def test_the_nightly_switch_off_waits_for_a_sync_then_goes(self):
        self.daemon.cfg.update({"SYNC_SHUTDOWN_WAIT_MIN": 10, "SYNC_SHUTDOWN_FALLBACK": "standby",
                                "SHUTDOWN_AFTER_CUTOFF": True})
        running = mock.patch.object(sync_scheduler, "running", return_value={"pid": 1}).start()
        with mock.patch.object(self.daemon, "_cutoff_standby") as standby, \
                mock.patch.object(self.daemon.stats, "record"):
            self.assertTrue(self.daemon._hold_poweroff_for_sync("cutoff"))
        standby.assert_called_once()
        self.daemon.mode = "idle"
        with mock.patch.object(self.daemon, "_do_shutdown_sequence") as off:
            self.daemon._check_sync_poweroff()
            off.assert_not_called()
            running.return_value = None
            self.daemon._check_sync_poweroff()
        off.assert_called_once_with(force=True, reason="cutoff_after_sync")

    def test_at_the_deadline_the_choice_decides(self):
        self.daemon.cfg.update({"SYNC_SHUTDOWN_FALLBACK": "poweroff"})
        mock.patch.object(sync_scheduler, "running", return_value={"pid": 1}).start()
        stop = mock.patch.object(sync_scheduler, "stop_running").start()
        self.daemon.mode = "idle"
        self.daemon._sync_poweroff = (0.0, "cutoff")
        with mock.patch.object(self.daemon, "_do_shutdown_sequence") as off:
            self.daemon._check_sync_poweroff()
        stop.assert_called_once()
        off.assert_called_once()
        self.daemon.cfg["SYNC_SHUTDOWN_FALLBACK"] = "standby"
        self.daemon._sync_poweroff = (0.0, "cutoff")
        with mock.patch.object(self.daemon, "_do_shutdown_sequence") as off, \
                mock.patch.object(self.daemon.stats, "record"):
            self.daemon._check_sync_poweroff()
        off.assert_not_called()
        self.assertIsNone(self.daemon._sync_poweroff)

    def test_music_started_again_drops_the_held_switch_off(self):
        self.daemon.mode = "music"
        self.daemon._sync_poweroff = (10 ** 12, "cutoff")
        self.daemon._check_sync_poweroff()
        self.assertIsNone(self.daemon._sync_poweroff)

    def test_a_switch_off_asked_for_never_waits(self):
        self.daemon.cfg["SYNC_SHUTDOWN_WAIT_MIN"] = 10
        mock.patch.object(sync_scheduler, "running", return_value={"pid": 1}).start()
        with mock.patch.object(self.daemon, "_hold_poweroff_for_sync") as hold, \
                mock.patch.object(rukebox_daemon_system(), "power_off"), \
                mock.patch.object(self.daemon.stats, "record"), mock.patch.object(self.daemon.stats, "end_session"), \
                mock.patch("time.sleep"):
            self.daemon._do_shutdown_sequence(force=True, reason="button")
        hold.assert_not_called()


def rukebox_daemon_system():
    import rukebox_daemon
    return rukebox_daemon.system_actions


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class RouteTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = _path.document(self.dir)
        self.music = os.path.join(self.dir, "music")
        os.makedirs(self.music)
        real = ws.cfg
        self.values = {"WEB_PASSWORD_HASH": "", "MUSIC_DIR": self.music, "STATE_DIR": self.dir,
                       "SUGGESTIONS_DB_FILE": os.path.join(self.dir, "s.db")}
        mock.patch.object(ws, "cfg", side_effect=lambda: dict(real(), **self.values)).start()
        mock.patch.object(ws, "control", return_value={"ok": True, "data": {}}).start()
        mock.patch.object(ws, "_usb_music_in_use", return_value=False).start()
        mock.patch.object(secret_box, "_machine_id", return_value=b"this-one").start()
        mock.patch.object(syncs, "secrets",
                          side_effect=lambda state_dir=None: secret_box.Store(
                              os.path.join(self.dir, "sync-secrets.json"), self.dir)).start()
        accounts.add({"name": "Boss", "role": "owner", "password": "boss-password"}, self.path)
        accounts.add({"name": "Marie", "role": "member", "password": "marie-password"}, self.path)
        self.client = ws.app.test_client()

    def login(self, name, password):
        return self.client.post("/api/auth/login", json={"name": name, "password": password})

    def test_only_the_owner_reaches_the_syncs(self):
        self.assertEqual(self.client.get("/api/sync").status_code, 401)
        self.login("Marie", "marie-password")
        for method, url in (("get", "/api/sync"), ("post", "/api/sync/connections"),
                            ("post", "/api/sync/jobs/x/run"), ("get", "/api/sync/ssh_key")):
            self.assertEqual(getattr(self.client, method)(url, json={}).status_code, 401, url)

    def test_a_password_goes_in_sealed_and_never_comes_back(self):
        self.login("Boss", "boss-password")
        answer = self.client.post("/api/sync/connections", json={
            "name": "NAS", "type": "smb", "host": "nas.local", "user": "pi", "password": "hunter2-secret"})
        text = answer.get_data(as_text=True)
        self.assertNotIn("hunter2", text)
        conn = answer.get_json()["data"]["connections"][0]
        self.assertTrue(conn["secret_set"])
        with open(os.path.join(self.dir, "sync-secrets.json"), encoding="utf-8") as f:
            self.assertNotIn("hunter2", f.read())
        self.assertEqual(syncs.secrets().get(conn["id"]), "hunter2-secret")
        self.assertNotIn("hunter2", self.client.get("/api/config/export").get_data(as_text=True))

    def test_a_sync_writes_only_into_the_radios_folders(self):
        self.login("Boss", "boss-password")
        self.client.post("/api/sync/connections", json={"name": "NAS", "type": "smb", "host": "nas"})
        for target in ("/etc", os.path.join(self.dir, "elsewhere"), self.music + "/../outside"):
            answer = self.client.post("/api/sync/jobs", json={"name": "x", "connection": "nas", "target": target})
            self.assertEqual(answer.get_json()["error"], "sync_bad_target", target)
        if hasattr(os, "symlink"):
            link = os.path.join(self.music, "escape")
            try:
                os.symlink(tempfile.gettempdir(), link)
            except OSError:
                link = None
            if link:
                answer = self.client.post("/api/sync/jobs", json={"name": "x", "connection": "nas", "target": link})
                self.assertEqual(answer.get_json()["error"], "sync_bad_target", "a link leaving the music folder")
        made = self.client.post("/api/sync/jobs", json={"name": "Rock", "connection": "nas",
                                                       "target": os.path.join(self.music, "Rock")})
        self.assertTrue(made.get_json()["ok"])

    def test_a_backup_restored_elsewhere_asks_for_the_password_again(self):
        self.login("Boss", "boss-password")
        self.client.post("/api/sync/connections", json={"name": "NAS", "type": "smb", "host": "nas",
                                                       "password": "pw-on-the-first"})
        bundle = self.client.get("/api/config/export").get_json()
        self.assertIn("syncs", bundle)
        os.remove(os.path.join(self.dir, "sync-secrets.json"))
        os.remove(os.path.join(self.dir, secret_box.KEY_FILE))
        self.client.post("/api/config/import", json=bundle)
        self.assertIn("sync_secret", self.client.get("/api/setup/pending").get_json()["data"]["items"])
        view = self.client.get("/api/sync").get_json()["data"]
        self.assertEqual(view["lost"], ["nas"])
        self.client.post("/api/sync/connections/nas", json={"password": "typed-again"})
        self.assertNotIn("sync_secret", self.client.get("/api/setup/pending").get_json()["data"]["items"])


if __name__ == "__main__":
    unittest.main()

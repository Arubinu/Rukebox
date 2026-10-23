"""When the syncs run: what is due, what the moment allows, one run at a time.

The web server owns the runs (it rescans the library after them); the daemon only
reads RUNNING_FILE to hold the nightly switch-off while one is under way.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime

import secret_box
import sync
import syncs

log = logging.getLogger("sync")

TICK_SEC = 60
# A press from Home Assistant is picked up this fast, and forgotten when the web server missed it.
REQUEST_SEC = 3
REQUEST_FILE = "sync-request.json"
REQUEST_MAX_AGE_SEC = 120
RECENT_LIST_ID = "recent-additions"
RECENT_MAX = 200
# Below this much room left on the target's disk, a sync waits.
FREE_FLOOR_BYTES = 300 * 1024 * 1024
RECENT_NAMES = {"en": "Recent additions", "fr": "Ajouts récents", "de": "Neu hinzugefügt",
                "es": "Añadidos recientes", "it": "Aggiunte recenti", "nl": "Recent toegevoegd"}


def in_window(job, now):
    """Whether `now` lies in the sync's hours (the whole day when none are set; may cross midnight)."""
    start, end = job.get("window_start") or "", job.get("window_end") or ""
    if not start or not end or start == end:
        return True
    current = now.strftime("%H:%M")
    return start <= current < end if start < end else (current >= start or current < end)


def due(job, now, last_end, boot_at, boot_delay_min):
    """Whether a sync should run now, by its own schedule alone."""
    if not job.get("enabled", True):
        return False
    stamp = now.timestamp()
    if stamp < boot_at + max(0, boot_delay_min) * 60:
        return False
    if not in_window(job, now):
        return False
    if job.get("at_boot") and (last_end is None or last_end < boot_at):
        return True
    every = int(job.get("every_hours") or 0)
    if every <= 0:
        return False
    return last_end is None or stamp - last_end >= every * 3600


def hold_reason(job, net, play, secret_lost):
    """Why a sync waits now, as a code, or None when the moment allows it.

    net: {"internet": bool, "home": bool, "ssid": str}; play: {"playing": bool}.
    """
    if secret_lost:
        return "secret_lost"
    internet = job.get("internet", "any")
    if internet == "needed" and not net.get("internet"):
        return "internet_missing"
    if internet == "forbidden" and net.get("internet"):
        return "internet_present"
    wifi = job.get("wifi", "any")
    if wifi == "home" and not net.get("home"):
        return "wifi_missing"
    if wifi == "ssid" and net.get("ssid") != job.get("ssid"):
        return "wifi_missing"
    if job.get("playback", "idle") == "idle" and play.get("playing"):
        return "playing"
    return None


def rate_for(cfg, builtin_bt_playing):
    """The speed limit in KB/s (0 = none), or None when the sync must wait for the music."""
    try:
        normal = max(0, int(cfg.get("SYNC_LIMIT_KBPS") or 0))
        while_bt = max(10, int(cfg.get("SYNC_LIMIT_BT_KBPS") or 100))
    except (TypeError, ValueError):
        normal, while_bt = 0, 100
    if not builtin_bt_playing:
        return normal
    mode = str(cfg.get("SYNC_BT_MODE") or "limit")
    if mode == "pause":
        return None
    if mode == "limit":
        return min(normal, while_bt) if normal else while_bt
    return normal


def has_internet():
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=3):
            return True
    except OSError:
        return False


def network_state(home_conn_name):
    """{"internet", "home", "ssid"} of the moment, each False/"" when it cannot be read."""
    state = {"internet": has_internet(), "home": False, "ssid": ""}
    try:
        active = subprocess.run(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
                                capture_output=True, text=True, timeout=10).stdout.splitlines()
        state["home"] = bool(home_conn_name) and home_conn_name in active
        wifi = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
                              capture_output=True, text=True, timeout=10).stdout.splitlines()
        for line in wifi:
            if line.startswith("yes:"):
                state["ssid"] = line[4:].replace("\\:", ":")
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return state


def running(state_dir):
    """{"pid", "job", "started"} of the run under way, or None."""
    path = os.path.join(state_dir, sync.RUNNING_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        os.kill(int(data["pid"]), 0)
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stop_running(state_dir):
    """Ends the run under way from another process: what was fully copied stays."""
    data = running(state_dir)
    if data is None:
        return False
    try:
        os.killpg(int(data["pid"]), signal.SIGTERM)
    except (AttributeError, OSError):
        return False
    return True


def ask(state_dir, job_id=None, stop=False):
    """Asks the web server, from another process, to start one sync or stop the one running."""
    path = os.path.join(state_dir, REQUEST_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"job": job_id, "stop": bool(stop), "at": time.time()}, f)
    os.replace(tmp, path)


def _take_request(state_dir):
    path = os.path.join(state_dir, REQUEST_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        return None
    if not isinstance(data, dict) or abs(time.time() - float(data.get("at") or 0)) > REQUEST_MAX_AGE_SEC:
        return None
    return data


class Manager:
    """The web server's side: a thread that starts due syncs, and the run of the moment.

    cfg: the settings; play: {"playing", "builtin_bt"} of the moment; after(job, record):
    what follows a run (rescan, list, journal); refuse(target): why a sync may not write there.
    """

    def __init__(self, cfg, play, after, state_dir, boot_at=None, store=None, refuse=None):
        self.cfg = cfg
        self.play = play
        self.after = after
        self.state_dir = state_dir
        self.boot_at = boot_at if boot_at is not None else time.time()
        self.store = store
        self.refuse = refuse
        self.run = None
        self.run_rate = None
        # A run ended for a new speed: started again by the next tick, its files carried over.
        self.resume = None
        self.carried = ([], [])
        self.held = {}
        self.seq = 0
        self.lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()

    def _secrets(self):
        return self.store or syncs.secrets(self.state_dir)

    def last_end(self, job_id):
        return next((run.get("ended") for run in sync.load_runs(self.state_dir)
                     if run.get("job") == job_id and run.get("ended")), None)

    def busy(self):
        return self.run is not None and self.run.running()

    def start(self, job_id, manual=False):
        """Starts one sync now; an error code when it cannot."""
        with self.lock:
            if self.busy():
                return "sync_busy"
            if not sync.rclone_available():
                return "rclone_missing"
            try:
                job = syncs.job(job_id)
                conn = syncs.connection(job["connection"])
            except KeyError:
                return "not_found"
            refused = self.refuse(job["target"]) if self.refuse else None
            if refused:
                return refused
            store = self._secrets()
            try:
                password = store.get(conn["id"]) if conn.get("auth") == "password" else None
            except secret_box.SecretLost:
                return "secret_lost"
            if conn.get("auth") == "password" and not password:
                return "secret_missing"
            if conn["type"] == "sftp" and not conn.get("host_key"):
                return "host_key_unknown"
            play = self.play()
            rate = rate_for(self.cfg(), play.get("builtin_bt"))
            if rate is None and not manual:
                return "bt_playing"
            key_file = None
            if conn.get("auth") == "key":
                key_file, _public = sync.ssh_key(self.state_dir)
            try:
                free = _free_bytes(job["target"])
                if free < FREE_FLOOR_BYTES:
                    return "no_space"
                room_mb = (free - FREE_FLOOR_BYTES) // (1024 * 1024)
                self.run = sync.Run(job, conn, password, key_file, rate or 0, self.state_dir, room_mb).start()
            except (OSError, subprocess.SubprocessError, ValueError):
                log.exception("The sync %s could not start", job_id)
                return "sync_failed"
            self.run_rate = rate
            self.seq += 1
            _write_running(self.state_dir, self.run.process.pid, job_id)
            threading.Thread(target=self._finish, args=(self.run,), name="sync-finish", daemon=True).start()
            return None

    def stop(self):
        with self.lock:
            if not self.busy():
                return False
            self.run.stop()
            return True

    def _finish(self, run):
        run.wait()
        try:
            os.remove(os.path.join(self.state_dir, sync.RUNNING_FILE))
        except OSError:
            pass
        with self.lock:
            if getattr(run, "restarting", False):
                self.resume = run.job["id"]
                self.carried = (self.carried[0] + run.added, self.carried[1] + run.updated)
                return
            run.added[:0], run.updated[:0] = self.carried
            self.carried = ([], [])
        record = sync.add_run(self.state_dir, run.record())
        with self.lock:
            self.seq += 1
        try:
            self.after(run.job, record)
        except Exception:  # noqa: BLE001 - what follows a run never stops the next one
            log.exception("After the sync %s", run.job["id"])

    def tick(self, now=None):
        """Starts the first due sync the moment allows; adjusts the one running to the music."""
        now = now or datetime.now()
        cfg = self.cfg()
        with self.lock:
            if self.busy():
                play = self.play()
                wanted = rate_for(cfg, play.get("builtin_bt"))
                waits = self.run.job.get("playback", "idle") == "idle" and play.get("playing")
                if wanted != self.run_rate or waits:
                    # A new speed means a new rclone: what is complete stays, the rest resumes.
                    log.info("Sync %s set aside (speed %s, music %s)", self.run.job["id"], wanted, bool(waits))
                    self.run.restarting = True
                    self.run.stop()
                return None
            if self.resume is not None:
                resumed = self.resume
                play = self.play()
                try:
                    job = syncs.job(resumed)
                except KeyError:
                    self.resume = None
                    return None
                why = hold_reason(job, network_state(cfg.get("HOME_WIFI_CONN_NAME") or ""), play,
                                  job["connection"] in set(self._secrets().lost()))
                if why is None and rate_for(cfg, play.get("builtin_bt")) is None:
                    why = "bt_playing"
                if why is not None:
                    self.held[resumed] = why
                    return None
                error = self.start(resumed, manual=True)
                if error is None:
                    self.resume = None
                    self.held.pop(resumed, None)
                    return resumed
                self.held[resumed] = error
                if error in ("not_found", "secret_lost", "secret_missing", "host_key_unknown"):
                    self.resume = None
                return None
            lost = set(self._secrets().lost())
            net = play = None
            try:
                boot_delay = int(cfg.get("SYNC_BOOT_DELAY_MIN") or 0)
            except (TypeError, ValueError):
                boot_delay = 0
            for job in syncs.load()["jobs"]:
                if not due(job, now, self.last_end(job["id"]), self.boot_at, boot_delay):
                    self.held.pop(job["id"], None)
                    continue
                if net is None:
                    net = network_state(cfg.get("HOME_WIFI_CONN_NAME") or "")
                    play = self.play()
                why = hold_reason(job, net, play, job["connection"] in lost)
                if why is None:
                    error = self.start(job["id"])
                    if error is None:
                        self.held.pop(job["id"], None)
                        return job["id"]
                    why = error
                self.held[job["id"]] = why
            return None

    def status(self):
        with self.lock:
            current = None
            if self.run is not None and (self.busy() or self.run.ended is None):
                current = {"job": self.run.job["id"], "started": self.run.started,
                           "progress": self.run.progress, "rate": self.run_rate,
                           "added": len(self.run.added), "updated": len(self.run.updated)}
            return {"running": current, "seq": self.seq, "held": dict(self.held),
                    "runs": sync.load_runs(self.state_dir)[:20]}

    def answer_request(self):
        """Runs what another process asked through ask(): an error code, or None."""
        asked = _take_request(self.state_dir)
        if asked is None:
            return None
        if asked.get("stop"):
            self.stop()
            return None
        job_id = str(asked.get("job") or "")
        error = self.start(job_id, manual=True)
        if error:
            log.info("The sync %s asked from outside did not start: %s", job_id, error)
            with self.lock:
                self.held[job_id] = error
                self.seq += 1
        return error

    def loop(self):
        last = time.monotonic()
        while not self._stop.wait(REQUEST_SEC):
            try:
                self.answer_request()
                if time.monotonic() - last >= TICK_SEC:
                    last = time.monotonic()
                    self.tick()
            except Exception:  # noqa: BLE001 - the scheduler outlives a bad tick
                log.exception("Sync scheduler")

    def start_thread(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self.loop, name="sync-scheduler", daemon=True)
            self._thread.start()


def _free_bytes(folder):
    probe = folder
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    try:
        disk = os.statvfs(probe or "/")
        return disk.f_bavail * disk.f_frsize
    except (AttributeError, OSError):
        return FREE_FLOOR_BYTES * 10


def _write_running(state_dir, pid, job_id):
    path = os.path.join(state_dir, sync.RUNNING_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "job": job_id, "started": time.time()}, f)
    os.replace(tmp, path)


def recent_list_name(lang):
    return RECENT_NAMES.get(str(lang or "")[:2], RECENT_NAMES["en"])

"""One sync run: rclone fetching a folder of another machine into one of the radio.

Copy only: files are added and updated, never deleted. The rclone configuration
is written for the run alone (mode 600) and the password reaches rclone through
its environment, obscured, never on a command line another account could read.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

log = logging.getLogger("sync")

REMOTE = "source"
RUNS_FILE = "sync-runs.json"
RUNNING_FILE = "sync-running.json"
RUNS_KEPT = 50
SSH_KEY_FILE = "sync_ssh_key"
CONNECT_TIMEOUT_SEC = 15
IO_TIMEOUT_SEC = 120


def rclone_available():
    return shutil.which("rclone") is not None


def obscure(password):
    """rclone's own reversible form of a password, made through stdin, never argv."""
    done = subprocess.run(["rclone", "obscure", "-"], input=str(password).encode("utf-8"),
                          capture_output=True, timeout=20)
    if done.returncode != 0:
        raise OSError("rclone obscure failed")
    return done.stdout.decode("ascii").strip()


def remote_config(conn, key_file=None, known_hosts=None):
    """The [source] section rclone reads for this connection (no password in it)."""
    lines = ["[%s]" % REMOTE, "type = %s" % conn["type"]]
    kind = conn["type"]
    if kind == "webdav":
        scheme = "http" if conn.get("tls") == "none" else "https"
        lines.append("url = %s://%s:%d" % (scheme, _url_host(conn["host"]), conn["port"]))
        lines.append("vendor = %s" % conn.get("vendor", "other"))
    else:
        lines.append("host = %s" % conn["host"])
        lines.append("port = %d" % conn["port"])
    if conn.get("user"):
        lines.append("user = %s" % conn["user"])
    if kind == "sftp":
        if conn.get("auth") == "key" and key_file:
            lines.append("key_file = %s" % key_file)
        if known_hosts:
            lines.append("known_hosts_file = %s" % known_hosts)
        # Only the files are wanted: no command is ever run on the other side.
        lines.append("disable_hashcheck = true")
        lines.append("shell_type = none")
    if kind == "ftp":
        if conn.get("tls") != "none":
            lines.append("explicit_tls = true")
        if conn.get("tls") == "self_signed":
            lines.append("no_check_certificate = true")
    return "\n".join(lines) + "\n"


def _url_host(host):
    return "[%s]" % host if ":" in host else host


def remote_path(conn, job):
    """`source:<base>/<sub>`, the folder of the other machine this sync reads."""
    parts = [part for part in (conn.get("path"), job.get("remote")) if part]
    return "%s:%s" % (REMOTE, "/".join(parts))


def build_command(job, conn, config_path, limit_kbps=0, room_mb=None):
    """The argument list (never a shell string) for one run; `room_mb` caps what it brings."""
    extensions = ",".join(job["extensions"])
    command = [
        "rclone", "copy", remote_path(conn, job), job["target"],
        "--config", config_path,
        "--include", "*.{%s}" % extensions,
        "--ignore-case",
        "--max-size", "%dM" % job["max_size_mb"],
        "--transfers", "1", "--checkers", "2",
        "--retries", "2", "--low-level-retries", "5",
        "--contimeout", "%ds" % CONNECT_TIMEOUT_SEC, "--timeout", "%ds" % IO_TIMEOUT_SEC,
        "--use-json-log", "--log-level", "INFO", "--stats", "2s", "--stats-log-level", "NOTICE",
    ]
    if limit_kbps and limit_kbps > 0:
        command += ["--bwlimit", "%dk" % int(limit_kbps)]
    if room_mb is not None:
        # The file under way is finished, then the run ends: the card never fills up.
        command += ["--max-transfer", "%dM" % max(1, int(room_mb)), "--cutoff-mode", "soft"]
    if conn.get("tls") == "self_signed" and conn["type"] == "webdav":
        command.append("--no-check-certificate")
    # Below the music: nice for the processor, idle class for the card.
    prefix = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "15"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c3"]
    return prefix + command


def parse_line(line):
    """One line of rclone's JSON log as an event, or None."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    stats = data.get("stats")
    if isinstance(stats, dict):
        return {"kind": "stats", "bytes": int(stats.get("bytes") or 0),
                "total_bytes": int(stats.get("totalBytes") or 0),
                "done": int(stats.get("transfers") or 0), "total": int(stats.get("totalTransfers") or 0),
                "errors": int(stats.get("errors") or 0), "eta": stats.get("eta"),
                "transferring": [str(item.get("name") or "") for item in stats.get("transferring") or []
                                 if isinstance(item, dict) and item.get("name")]}
    message = str(data.get("msg") or "")
    if message.startswith("Copied (new)"):
        return {"kind": "copied", "new": True, "name": str(data.get("object") or "")}
    if message.startswith("Copied ("):
        return {"kind": "copied", "new": False, "name": str(data.get("object") or "")}
    if str(data.get("level") or "") == "error":
        return {"kind": "error", "message": message[:300]}
    return None


class Run:
    """One rclone process and what it reports; started by start(), ended by itself or stop()."""

    def __init__(self, job, conn, password=None, key_file=None, limit_kbps=0, workdir=None, room_mb=None):
        self.job = job
        self.room_mb = room_mb
        self.conn = conn
        self.password = password
        self.key_file = key_file
        self.limit_kbps = limit_kbps
        self.workdir = workdir or tempfile.gettempdir()
        self.process = None
        self.thread = None
        self.started = None
        self.ended = None
        self.status = "starting"
        self.added = []
        self.updated = []
        self.errors = []
        self.progress = {}
        self._folder = None
        self._stopping = False
        self._under_way = set()

    def _prepare(self):
        self._folder = tempfile.mkdtemp(prefix="rukebox-sync-", dir=self.workdir)
        os.chmod(self._folder, 0o700)
        known_hosts = None
        if self.conn["type"] == "sftp" and self.conn.get("host_key"):
            known_hosts = os.path.join(self._folder, "known_hosts")
            _private(known_hosts, self.conn["host_key"] + "\n")
        config_path = os.path.join(self._folder, "rclone.conf")
        _private(config_path, remote_config(self.conn, self.key_file, known_hosts))
        env = dict(os.environ, RCLONE_CONFIG=config_path)
        if self.password:
            env["RCLONE_CONFIG_%s_PASS" % REMOTE.upper()] = obscure(self.password)
        return config_path, env

    def start(self):
        config_path, env = self._prepare()
        os.makedirs(self.job["target"], exist_ok=True)
        command = build_command(self.job, self.conn, config_path, self.limit_kbps, self.room_mb)
        self.started = time.time()
        self.status = "running"
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        self.thread = threading.Thread(target=self._read, name="sync-run", daemon=True)
        self.thread.start()
        return self

    def _read(self):
        try:
            for raw in self.process.stderr:
                event = parse_line(raw.decode("utf-8", "replace"))
                if event is None:
                    continue
                if event["kind"] == "stats":
                    self.progress = event
                    self._under_way.update(event["transferring"])
                elif event["kind"] == "copied":
                    (self.added if event["new"] else self.updated).append(event["name"])
                elif event["kind"] == "error" and len(self.errors) < 20:
                    self.errors.append(event["message"])
            code = self.process.wait()
        finally:
            self.process.stderr.close()
            shutil.rmtree(self._folder, ignore_errors=True)
        self.ended = time.time()
        if self._stopping:
            self.status = "stopped"
        else:
            self.status = "ok" if code == 0 else "failed"
        if self.status != "ok":
            self._clear_half_files()

    def _clear_half_files(self):
        """Removes what was cut off mid-copy: rclone before 1.63 writes under the final name,
        so a stopped run would leave a broken song in the library."""
        done = set(self.added) | set(self.updated)
        target = os.path.realpath(self.job["target"])
        for name in self._under_way - done:
            path = os.path.realpath(os.path.join(target, name))
            if not path.startswith(target.rstrip(os.sep) + os.sep):
                continue
            try:
                # Written during this run: a newer rclone leaves the old copy untouched, and it stays.
                if os.path.getmtime(path) >= self.started - 1:
                    os.remove(path)
                    log.info("Removed the half-copied %s", name)
            except OSError:
                pass

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """Ends the run; what was fully copied stays, a half file is removed."""
        self._stopping = True
        if self.running():
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except (AttributeError, OSError):
                self.process.terminate()

    def wait(self, timeout=None):
        if self.thread is not None:
            self.thread.join(timeout)
        return self.status

    def record(self):
        """What the run did, for the journal and the page (no password, no address)."""
        return {"job": self.job["id"], "started": self.started, "ended": self.ended,
                "status": self.status, "added": len(self.added), "updated": len(self.updated),
                "errors": self.errors[:5], "bytes": int(self.progress.get("bytes") or 0),
                "new_files": [name for name in self.added[:200]]}


def test(conn, password=None, key_file=None, workdir=None, timeout=45):
    """(True, "") when the connection lists its folder, else (False, rclone's last word on it)."""
    probe = Run({"id": "test", "target": workdir or tempfile.gettempdir()}, conn, password, key_file,
                workdir=workdir)
    config_path, env = probe._prepare()
    try:
        done = subprocess.run(
            ["rclone", "lsf", "--max-depth", "1", remote_path(conn, {}), "--config", config_path,
             "--contimeout", "%ds" % CONNECT_TIMEOUT_SEC, "--timeout", "%ds" % CONNECT_TIMEOUT_SEC,
             "--retries", "1", "--low-level-retries", "1"],
            capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False, "timeout"
    finally:
        shutil.rmtree(probe._folder, ignore_errors=True)
    if done.returncode == 0:
        return True, ""
    lines = [line for line in done.stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    return False, (lines[-1] if lines else "failed")[-300:]


def _private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def load_runs(state_dir):
    try:
        with open(os.path.join(state_dir, RUNS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def add_run(state_dir, record):
    """Keeps the newest RUNS_KEPT runs, newest first."""
    runs = [dict(record, new_files=record.get("new_files", [])[:50])] + load_runs(state_dir)
    path = os.path.join(state_dir, RUNS_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs[:RUNS_KEPT], f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return runs[0]


def ssh_key(state_dir):
    """(private key path, public key text) of the radio's own SSH key, made once."""
    path = os.path.join(state_dir, SSH_KEY_FILE)
    if not os.path.exists(path):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "rukebox-sync", "-f", path],
                       capture_output=True, timeout=30, check=True)
        os.chmod(path, 0o600)
    with open(path + ".pub", encoding="ascii") as f:
        return path, f.read().strip()


def scan_host_key(host, port):
    """(every key line the server offers, their SHA256 fingerprints), or (None, []).

    All of them are pinned: rclone's SSH client may settle on any type the server has."""
    try:
        done = subprocess.run(["ssh-keyscan", "-T", "5", "-p", str(int(port)), "--", host],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None, []
    lines = sorted(line for line in done.stdout.splitlines() if line and not line.startswith("#"))
    if not lines:
        return None, []
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pub") as f:
        f.write("\n".join(lines) + "\n")
        name = f.name
    try:
        printed = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", name],
                                 capture_output=True, text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        printed = []
    finally:
        os.remove(name)
    # "256 SHA256:abc... host (ED25519)": the type, then the fingerprint.
    fingerprints = sorted("%s %s" % (row.split()[-1].strip("()"), row.split()[1])
                          for row in printed if len(row.split()) >= 3)
    return "\n".join(lines), fingerprints

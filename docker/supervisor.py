#!/usr/bin/env python3
"""The container's own supervisor: what systemd does on a Pi.

A container has no systemd, so nothing would restart the daemon after it ends
by itself - which is how "restart the service" works there, and how "switch
off" ends the container. This runs the two processes, restarts them when they
stop asking to, and stops everything on SIGTERM.

It is deliberately small and uses nothing but the standard library: the project
has no dependencies to install (see CLAUDE.md), and a supervisor is not worth
being the first one."""

import logging
import os
import signal
import socket
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("supervisor")

SRC = os.environ.get("RUKEBOX_SRC") or "/opt/rukebox/src"
POLL_SEC = 2.0
# Left by the daemon when "switch off" meant the container and not a restart
# (see src/system_actions.py): without it, this supervisor would start the
# radio again a second later.
STOP_REQUEST = "switch-off-request"
# How long a process waits for the sound server's socket before giving up and
# starting anyway (it then fails on its own, and is restarted).
READY_TIMEOUT_SEC = 20.0
# A process that dies at once must not spin: wait longer each time.
BACKOFF_MAX_SEC = 30.0
# Long enough for a clean exit (the daemon closes its statistics session).
STOP_GRACE_SEC = 10.0

_stopping = False


class Child:
    """One process, restarted whenever it stops.

    `ready` is what the sound server has to have produced before this one is
    started: PipeWire takes a moment to create its socket, and anything that
    connects to it - the Pulse layer, mpv, the web server's `pw-dump` - dies
    at once if it is not there yet."""

    def __init__(self, name, argv, ready=None):
        self.name = name
        self.argv = argv
        self.ready = ready
        self.process = None
        self.started_at = 0.0
        self.failures = 0

    def wait_ready(self, timeout=READY_TIMEOUT_SEC):
        if self.ready is None:
            return True
        deadline = time.monotonic() + timeout
        while not self.ready():
            if _stopping or time.monotonic() > deadline:
                log.warning("%s: waited %.0fs and its prerequisite is still not here",
                            self.name, timeout)
                return False
            time.sleep(0.2)
        return True

    def start(self):
        if not self.wait_ready():
            return
        log.info("Starting %s: %s", self.name, " ".join(self.argv))
        try:
            self.process = subprocess.Popen(self.argv, env=os.environ)
        except OSError:
            log.exception("Could not start %s", self.name)
            self.process = None
            self.failures += 1
            return
        self.started_at = time.monotonic()

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def reap(self):
        """Restarts it if it is gone. Returns the seconds to wait before the
        next turn."""
        if self.alive():
            return POLL_SEC
        if self.process is None:
            # Never started: nothing to reap, retry on the normal beat.
            self.start()
            return POLL_SEC
        code = self.process.returncode
        lasted = time.monotonic() - self.started_at
        self.process = None
        if lasted < 5.0:
            self.failures += 1
        else:
            self.failures = 0
        wait = min(BACKOFF_MAX_SEC, POLL_SEC * (2 ** min(self.failures, 4))) if self.failures else POLL_SEC
        log.warning("%s stopped (code %s) after %.1fs: starting it again in %.0fs",
                    self.name, code, lasted, wait)
        time.sleep(wait)
        self.start()
        return POLL_SEC

    def stop(self):
        if not self.alive():
            return
        log.info("Stopping %s", self.name)
        try:
            self.process.terminate()
        except OSError:
            pass


def _on_signal(signum, _frame):
    global _stopping
    log.info("Signal %s received: stopping", signum)
    _stopping = True


def children():
    """What this container runs, in order.

    PipeWire is only started when nothing else provides a sound server: a
    container given the host's PipeWire socket, or a sound card of its own,
    must not have a second one started on top of it.

    `pipewire-pulse` is the PulseAudio compatibility layer. The project talks
    to PipeWire with `pw-dump`, but the layer is what gives the virtual output
    a MONITOR - the stream has nothing to encode without it."""
    started = []
    if _needs_local_pipewire():
        started.append(Child("pipewire", ["pipewire"]))
        for name, argv in (("wireplumber", ["wireplumber"]),
                           ("pipewire-pulse", ["pipewire-pulse"])):
            started.append(Child(name, argv, ready=_pipewire_socket))
    started.append(Child("daemon", [sys.executable, os.path.join(SRC, "rukebox_daemon.py")],
                         ready=_audio_ready))
    started.append(Child("web", [sys.executable, os.path.join(SRC, "web_server.py")]))
    return started


def _runtime_dir():
    return os.environ.get("PIPEWIRE_RUNTIME_DIR") or os.environ.get(
        "XDG_RUNTIME_DIR") or "/run/rukebox"


_stale_sockets = set()


def _pipewire_socket():
    path = os.path.join(_runtime_dir(), "pipewire-0")
    return path not in _stale_sockets and os.path.exists(path)


def _socket_answers(path):
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _drop_stale_socket():
    """Forgets a socket file nothing listens on.

    /run survives a restart of this container, so the sound server of the run
    before is still there as a file: it would stop us starting our own, and the
    daemon would wait on a socket nobody ever answers on. A socket that was
    given to us (a bind mount, see the third compose variant) is reported
    rather than deleted - it is not ours."""
    path = os.path.join(_runtime_dir(), "pipewire-0")
    if not os.path.exists(path) or _socket_answers(path):
        return False
    if os.path.ismount(path):
        _stale_sockets.add(path)
        log.warning("The sound server's socket (%s) is there but nobody answers on it", path)
        return False
    try:
        os.unlink(path)
    except OSError:
        log.warning("Could not remove the sound server's socket from the run before (%s)", path)
        return False
    log.info("Removed the sound server's socket from the run before (%s)", path)
    return True


def _audio_ready():
    """PipeWire has its socket AND something to play to.

    The socket comes first by a moment: a daemon started on the socket alone
    finds no sink, gives mpv "auto", and only corrects itself on its next
    output check 30 seconds later - which is what made the virtual sink work
    on a first start and not after a restart. Waiting for one Audio/Sink here
    is the whole fix; if none ever appears the daemon starts anyway and says
    so itself."""
    if not _pipewire_socket():
        return False
    try:
        done = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return '"media.class": "Audio/Sink"' in (done.stdout or "")


def _needs_local_pipewire():
    if os.environ.get("RUKEBOX_PIPEWIRE") == "off":
        return False
    if _pipewire_socket():
        log.info("A PipeWire socket is already there (%s): not starting another",
                 _runtime_dir())
        return False
    if not os.path.isdir("/dev/snd"):
        log.warning("No sound card here: the container plays to its own virtual output, "
                    "and the network stream is what you hear")
    return True


def _state_dir():
    return os.environ.get("RUKEBOX_STATE_DIR") or "/data"


def _switch_off_request():
    """The path of the daemon's request, or None once it has been read."""
    path = os.path.join(_state_dir(), STOP_REQUEST)
    if not os.path.exists(path):
        return None
    try:
        os.unlink(path)
    except OSError:
        log.warning("Could not remove %s", path)
    return path


def main():
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)

    _drop_stale_socket()
    # A request from the run before: the container was started again, so it is
    # spent.
    _switch_off_request()
    running = children()
    for child in running:
        child.start()

    while not _stopping:
        time.sleep(POLL_SEC)
        if _switch_off_request():
            log.info("The radio asked to be switched off: stopping the container")
            break
        for child in running:
            if _stopping:
                break
            child.reap()

    for child in running:
        child.stop()
    deadline = time.monotonic() + STOP_GRACE_SEC
    for child in running:
        if child.process is None:
            continue
        try:
            child.process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            log.warning("%s did not stop in time: killing it", child.name)
            child.process.kill()
    log.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

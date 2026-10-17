"""systemd's watchdog: the daemon is restarted when its scheduler loop stops turning."""

import logging
import os
import socket
import threading
import time

log = logging.getLogger("radio")

PING_EVERY_SEC = 30
# Under WatchdogSec (300) in the unit: a rescan holding the command lock a while is not a hang.
STALE_AFTER_SEC = 240
# Starting up (a first scan of a large library on a Pi Zero) before the loop has turned once.
STARTUP_GRACE_SEC = 900


def notify(state, environ=None):
    """Sends one sd_notify line; False where there is no systemd to tell (Docker, Windows)."""
    address = (environ if environ is not None else os.environ).get("NOTIFY_SOCKET", "")
    if not address or not hasattr(socket, "AF_UNIX"):
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode("utf-8"), address)
        return True
    except OSError:
        return False


class Watchdog:
    """`beat()` from the loop; the thread tells systemd only while the beats are recent."""

    def __init__(self, send=notify, clock=time.monotonic):
        self._send = send
        self._clock = clock
        self._started = clock()
        self._last_beat = None
        self._stop = threading.Event()

    def beat(self):
        self._last_beat = self._clock()

    def alive(self):
        now = self._clock()
        if self._last_beat is None:
            return now - self._started < STARTUP_GRACE_SEC
        return now - self._last_beat < STALE_AFTER_SEC

    def ping(self):
        """One check: True when systemd was told the daemon is alive."""
        if not self.alive():
            return False
        return self._send("WATCHDOG=1")

    def start(self):
        if not os.environ.get("NOTIFY_SOCKET"):
            return False

        def run():
            warned = False
            while not self._stop.wait(PING_EVERY_SEC):
                if not self.ping() and not warned and not self.alive():
                    warned = True
                    log.error("The scheduler loop has stopped turning: systemd will restart the daemon")
        threading.Thread(target=run, name="watchdog", daemon=True).start()
        return True

    def stop(self):
        self._stop.set()

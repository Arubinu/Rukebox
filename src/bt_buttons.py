#!/usr/bin/env python3
"""Bridge from Bluetooth remotes (selfie shutters, media remotes) to the daemon: single, double and long press."""

import logging
import os
import re
import select
import struct
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_client import send_control_command  # noqa: E402
from config_and_scan import load_config  # noqa: E402
from gpio_click import ButtonWatcher  # noqa: E402

log = logging.getLogger("bt_buttons")

EV_KEY = 1
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EVIOCGRAB = 0x40044590
RESCAN_SEC = 3.0
# Some remotes release one key and press another for a single press.
MERGE_SEC = 0.06
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def remotes(cfg):
    """The addresses of the remotes to listen to, upper case and valid only."""
    found = []
    for part in re.split(r"[\s,;]+", str(cfg.get("BT_BUTTONS") or "")):
        mac = part.strip().upper()
        if MAC_RE.match(mac) and mac not in found:
            found.append(mac)
    return found


def parse_devices(text):
    """[(name, uniq, [event handlers])] from /proc/bus/input/devices."""
    devices = []
    name, uniq, handlers = None, "", []
    for line in text.splitlines() + [""]:
        if not line.strip():
            if name is not None:
                devices.append((name, uniq, [h for h in handlers if h.startswith("event")]))
            name, uniq, handlers = None, "", []
        elif line.startswith("N: Name="):
            name = line[len("N: Name="):].strip().strip('"')
        elif line.startswith("U: Uniq="):
            uniq = line[len("U: Uniq="):].strip().upper()
        elif line.startswith("H: Handlers="):
            handlers = line[len("H: Handlers="):].split()
    return devices


def find_devices(macs, text=None):
    """{"/dev/input/eventN": mac} for every input device of these remotes."""
    if text is None:
        try:
            with open("/proc/bus/input/devices") as f:
                text = f.read()
        except OSError:
            return {}
    wanted = set(macs)
    found = {}
    for name, uniq, events in parse_devices(text):
        # A speaker's AVRCP keys carry its address too: they belong to speaker_buttons.
        if uniq in wanted and "AVRCP" not in name.upper():
            for event in events:
                found["/dev/input/" + event] = uniq
    return found


def key_events(chunk):
    """(code, value) of every key event in a raw read."""
    for i in range(0, len(chunk) - EVENT_SIZE + 1, EVENT_SIZE):
        _sec, _usec, ev_type, code, value = struct.unpack(EVENT_FORMAT, chunk[i:i + EVENT_SIZE])
        if ev_type == EV_KEY:
            yield code, value


class Remote:
    """One remote, whatever its keys: held while any of them is down."""

    def __init__(self, mac, window, long_press, send, clock=time.monotonic, timer=None):
        self.mac = mac
        self._clock = clock
        kwargs = {"clock": clock, "send": send}
        if timer is not None:
            kwargs["timer"] = timer
        self.watcher = ButtonWatcher(0.0, window, long_press, **kwargs)
        self.down = set()
        self.pressed = False
        self.release_at = None

    def tune(self, window, long_press):
        self.watcher.double_click_window_sec = window
        self.watcher.long_press_sec = long_press

    def key(self, path, code, value):
        if value == 1:
            self.down.add((path, code))
            self.release_at = None
            if not self.pressed:
                self.pressed = True
                self.watcher.on_change(True)
        elif value == 0:
            self.down.discard((path, code))
            if not self.down and self.pressed:
                self.release_at = self._clock() + MERGE_SEC

    def tick(self):
        if self.release_at is not None and self._clock() >= self.release_at:
            self.release_at = None
            self.pressed = False
            self.watcher.on_change(False)


def _open(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        # Grabbed, so a remote that is a keyboard never types into a console.
        if fcntl is not None:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
    except OSError:
        pass
    return fd


def watch_forever():
    open_fds = {}
    remotes_by_mac = {}
    while True:
        cfg = load_config(env_overrides=False)
        window = float(cfg.get("BT_BUTTON_DOUBLE_CLICK_WINDOW_SEC") or 0.4)
        long_press = float(cfg.get("BT_BUTTON_LONG_PRESS_SEC") or 1.0)
        sock = cfg["CONTROL_SOCKET"]
        macs = remotes(cfg)
        wanted = find_devices(macs) if macs else {}

        def send(cmd, sock=sock):
            answer = send_control_command(sock, cmd, source="remote")
            if not answer.get("ok"):
                log.warning("Daemon refused %s from a remote: %s", cmd, answer.get("error"))

        for mac in macs:
            if mac not in remotes_by_mac:
                remotes_by_mac[mac] = Remote(mac, window, long_press, send)
            remotes_by_mac[mac].tune(window, long_press)
        for path in [one for one in open_fds if one not in wanted]:
            os.close(open_fds.pop(path)[0])
        for path, mac in wanted.items():
            if path in open_fds:
                continue
            try:
                open_fds[path] = (_open(path), mac)
            except OSError as exc:
                log.warning("Could not open %s: %s", path, exc)
                continue
            log.info("Listening to the remote %s on %s", mac, path)
        deadline = time.monotonic() + RESCAN_SEC
        while time.monotonic() < deadline:
            pending = [r.release_at for r in remotes_by_mac.values() if r.release_at is not None]
            wait = max(0.0, min([deadline - time.monotonic()] + [p - time.monotonic() for p in pending]))
            if not open_fds:
                time.sleep(wait)
                continue
            ready, _, _ = select.select([fd for fd, _ in open_fds.values()], [], [], wait)
            for path, (fd, mac) in list(open_fds.items()):
                if fd not in ready:
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 32)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    log.info("The remote %s went away (%s)", mac, path)
                    os.close(open_fds.pop(path)[0])
                    continue
                remote = remotes_by_mac.get(mac)
                if remote is None:
                    continue
                for code, value in key_events(chunk):
                    remote.key(path, code, value)
            for remote in remotes_by_mac.values():
                remote.tick()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [bt_buttons] %(message)s")
    try:
        watch_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

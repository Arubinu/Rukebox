#!/usr/bin/env python3
"""A rotary encoder decoded by the kernel (scripts/rotary.sh): its turns and its push go to the daemon."""

import glob
import logging
import os
import select
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_and_scan import load_config  # noqa: E402
from control_client import send_control_command  # noqa: E402
from gpio_click import ButtonWatcher  # noqa: E402

log = logging.getLogger("rotary")

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY, EV_REL = 1, 2
REL_X = 0
# The push's key in the overlay: one no keyboard sends, so no other key is taken for it.
PUSH_KEYCODE = 203
# Turns closer together than this go to the daemon as one.
BATCH_SEC = 0.06
RESCAN_SEC = 30


def find_devices(root="/sys/class/input"):
    """(the encoder's /dev/input path or None, the push's or None), by the names the kernel gives them."""
    rotary = push = None
    for folder in sorted(glob.glob(os.path.join(root, "event*"))):
        try:
            with open(os.path.join(folder, "device", "name"), encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            continue
        path = "/dev/input/" + os.path.basename(folder)
        if name.startswith("rotary@") and rotary is None:
            rotary = path
        elif name.startswith("button@") and push is None:
            push = path
    return rotary, push


def parse(data):
    """[(type, code, value)] of the whole events in `data`."""
    out = []
    for start in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        _sec, _usec, kind, code, value = struct.unpack(EVENT_FORMAT, data[start:start + EVENT_SIZE])
        out.append((kind, code, value))
    return out


class Rotary:
    """Turns into batched steps, the push into the button's gestures."""

    def __init__(self, send, watcher, clock=time.monotonic):
        self._send = send
        self._watcher = watcher
        self._clock = clock
        self.pending = 0
        self._since = None

    def on_events(self, events):
        for kind, code, value in events:
            if kind == EV_REL and code == REL_X and value:
                if not self.pending:
                    self._since = self._clock()
                self.pending += value
            elif kind == EV_KEY and code == PUSH_KEYCODE and value in (0, 1):
                self._watcher.on_change(value == 1)

    def flush(self, force=False):
        """Sends what was turned, once the batch is old enough."""
        if not self.pending or (not force and self._clock() - self._since < BATCH_SEC):
            return False
        steps, self.pending = self.pending, 0
        self._send("rotary_turn", steps=steps)
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [rotary] %(message)s")
    cfg = load_config()
    sock = cfg["CONTROL_SOCKET"]

    def send(cmd, **fields):
        answer = send_control_command(sock, cmd, source="rotary", **fields)
        if not answer.get("ok"):
            log.warning("Daemon refused %s: %s", cmd, answer.get("error"))

    watcher = ButtonWatcher(float(cfg.get("GPIO_BUTTON_DEBOUNCE_SEC") or 0.03),
                            float(cfg.get("GPIO_BUTTON_DOUBLE_CLICK_WINDOW_SEC") or 0.4),
                            float(cfg.get("GPIO_BUTTON_LONG_PRESS_SEC") or 1.5),
                            send=lambda cmd: send(cmd))
    rotary = Rotary(send, watcher)
    while True:
        paths = [path for path in find_devices() if path]
        if not paths:
            log.info("No encoder yet (the overlay takes a restart of the radio): looking again soon")
            time.sleep(RESCAN_SEC)
            continue
        fds = []
        try:
            fds = [os.open(path, os.O_RDONLY | os.O_NONBLOCK) for path in paths]
            log.info("Reading %s", ", ".join(paths))
            while True:
                ready, _, _ = select.select(fds, [], [], BATCH_SEC)
                for fd in ready:
                    rotary.on_events(parse(os.read(fd, EVENT_SIZE * 64)))
                rotary.flush()
        except OSError as error:
            log.warning("The encoder went away (%s): looking again", error)
            time.sleep(2)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bridge from a USB RFID reader to the daemon.

Such a reader is a keyboard: it types the card's number and Enter. The device
is grabbed, so those keys never reach anything else."""

import logging
import os
import select
import struct
import sys
import time

try:
    import fcntl
except ImportError:  # not on Linux: nothing to read anyway
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_and_scan import load_config  # noqa: E402
from control_client import send_control_command  # noqa: E402
from speaker_buttons import EVENT_FORMAT, EVENT_SIZE, EV_KEY, parse_input_devices  # noqa: E402

log = logging.getLogger("card_reader")

EVIOCGRAB = 0x40044590
RESCAN_SEC = 5.0
SAME_CARD_SEC = 3.0
# Names the usual cheap readers announce themselves with.
READER_HINTS = ("rfid", "id&ic", "id reader", "card reader", "sycreader")

KEY_CHARS = {2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
             79: "1", 80: "2", 81: "3", 75: "4", 76: "5", 77: "6", 71: "7", 72: "8", 73: "9", 82: "0",
             30: "A", 48: "B", 46: "C", 32: "D", 18: "E", 33: "F"}
KEY_ENTER = (28, 96)


def find_reader(devices_text, wanted=""):
    """/dev/input/eventN of the reader: the device whose name holds `wanted`,
    else one whose name says it is a card reader."""
    wanted = " ".join(str(wanted or "").split()).lower()
    for name, handlers in parse_input_devices(devices_text):
        events = [h for h in handlers if h.startswith("event")]
        lowered = " ".join(name.split()).lower()
        if not events:
            continue
        if (wanted and wanted in lowered) or (not wanted and any(h in lowered for h in READER_HINTS)):
            return "/dev/input/" + events[0]
    return None


class CardDecoder:
    """Turns key presses into card numbers."""

    def __init__(self):
        self.typed = ""

    def feed(self, data):
        """The card number when this event ends one, else None."""
        if len(data) < EVENT_SIZE:
            return None
        _sec, _usec, ev_type, code, value = struct.unpack(EVENT_FORMAT, data[:EVENT_SIZE])
        if ev_type != EV_KEY or value != 1:
            return None
        if code in KEY_ENTER:
            typed, self.typed = self.typed, ""
            return typed if len(typed) >= 4 else None
        char = KEY_CHARS.get(code)
        if char:
            self.typed = (self.typed + char)[-32:]
        return None


def watch_forever():
    last = (None, 0.0)
    while True:
        cfg = load_config(env_overrides=False)
        try:
            with open("/proc/bus/input/devices") as f:
                path = find_reader(f.read(), cfg.get("RFID_READER", ""))
        except OSError:
            path = None
        if not path:
            time.sleep(RESCAN_SEC)
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            log.warning("Could not open %s: %s", path, exc)
            time.sleep(RESCAN_SEC)
            continue
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
        except (OSError, AttributeError):
            log.warning("Could not take the reader for itself: its numbers may reach the console")
        log.info("Listening to the card reader on %s", path)
        decoder = CardDecoder()
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], RESCAN_SEC)
                if not ready:
                    if not os.path.exists(path):
                        break
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                for i in range(0, len(chunk) - EVENT_SIZE + 1, EVENT_SIZE):
                    card = decoder.feed(chunk[i:i + EVENT_SIZE])
                    if not card:
                        continue
                    now = time.monotonic()
                    if card == last[0] and now - last[1] < SAME_CARD_SEC:
                        continue
                    last = (card, now)
                    log.info("Card %s", card)
                    answer = send_control_command(cfg["CONTROL_SOCKET"], "card", id=card, source="card")
                    if not answer.get("ok"):
                        log.info("Card %s: %s", card, answer.get("error"))
        finally:
            os.close(fd)
        log.info("Card reader gone, looking for it again")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        watch_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bridge from the Bluetooth speaker's own buttons (AVRCP) to the daemon."""

import logging
import os
import select
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_client import send_control_command  # noqa: E402
from config_and_scan import load_config  # noqa: E402

log = logging.getLogger("speaker_buttons")

EV_KEY = 1
KEY_GESTURES = {
    164: "playpause",
    200: "playpause",
    201: "playpause",
    207: "playpause",
    166: "playpause",
    163: "next",
    165: "previous",
}
GESTURES = ("playpause", "next", "previous")

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

REPEAT_GUARD_SEC = 0.4
RESCAN_SEC = 3.0


def parse_input_devices(text):
    """[(name, [handlers])] from /proc/bus/input/devices."""
    devices = []
    name, handlers = None, []
    for line in text.splitlines() + [""]:
        if not line.strip():
            if name is not None:
                devices.append((name, handlers))
            name, handlers = None, []
        elif line.startswith("N: Name="):
            name = line[len("N: Name="):].strip().strip('"')
        elif line.startswith("H: Handlers="):
            handlers = line[len("H: Handlers="):].split()
    return devices


def find_event_device(mac, devices_text=None, alias=None):
    """/dev/input/eventN of the speaker's AVRCP device, or None."""
    if devices_text is None:
        try:
            with open("/proc/bus/input/devices") as f:
                devices_text = f.read()
        except OSError:
            return None
    wanted = (mac or "").strip().upper()
    wanted_name = " ".join((alias or "").split()).upper()
    avrcp = []
    for name, handlers in parse_input_devices(devices_text):
        events = [h for h in handlers if h.startswith("event")]
        if not events:
            continue
        upper = " ".join(name.split()).upper()
        if wanted and wanted in upper:
            return "/dev/input/" + events[0]
        if "AVRCP" in upper:
            if wanted_name and upper.startswith(wanted_name):
                return "/dev/input/" + events[0]
            avrcp.append("/dev/input/" + events[0])
    return avrcp[0] if len(avrcp) == 1 else None


def speaker_alias(mac):
    """The speaker's Bluetooth name."""
    if not mac:
        return None
    try:
        out = subprocess.run(["bluetoothctl"], input="info %s\n" % mac, capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    name = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Alias:"):
            return line[len("Alias:"):].strip() or name
        if line.startswith("Name:"):
            name = line[len("Name:"):].strip()
    return name


def gesture_of(data):
    """The gesture carried by one raw input event."""
    if len(data) < EVENT_SIZE:
        return None
    _sec, _usec, ev_type, code, value = struct.unpack(EVENT_FORMAT, data[:EVENT_SIZE])
    if ev_type != EV_KEY or value != 1:
        return None
    return KEY_GESTURES.get(code)


def send(control_socket, gesture):
    response = send_control_command(control_socket, "speaker_button", gesture=gesture, source="speaker")
    if not response.get("ok"):
        log.warning("Daemon refused speaker gesture %s: %s", gesture, response.get("error"))


def watch_forever():
    last = {}
    while True:
        cfg = load_config(env_overrides=False)
        mac = cfg.get("SPEAKER_MAC", "")
        path = find_event_device(mac)
        if not path:
            path = find_event_device(mac, alias=speaker_alias(mac))
        if not path:
            time.sleep(RESCAN_SEC)
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            log.warning("Could not open %s: %s", path, exc)
            time.sleep(RESCAN_SEC)
            continue
        log.info("Listening to the speaker's buttons on %s", path)
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], RESCAN_SEC)
                if not ready:
                    if not os.path.exists(path):
                        break
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 16)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                for i in range(0, len(chunk) - EVENT_SIZE + 1, EVENT_SIZE):
                    gesture = gesture_of(chunk[i:i + EVENT_SIZE])
                    if not gesture:
                        continue
                    now = time.monotonic()
                    if now - last.get(gesture, 0) < REPEAT_GUARD_SEC:
                        continue
                    last[gesture] = now
                    log.info("Speaker button: %s", gesture)
                    send(cfg["CONTROL_SOCKET"], gesture)
        finally:
            os.close(fd)
        log.info("Speaker input device gone, looking for it again")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        watch_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bridge from the buttons the radio hears to the daemon: the Bluetooth
speaker's own keys (AVRCP), and the media keys of a USB sound card."""

import glob
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
    115: "volumeup",
    114: "volumedown",
}
GESTURES = ("playpause", "next", "previous", "volumeup", "volumedown")

SYSFS_SOUND = "/sys/class/sound"
SYSFS_INPUT = "/sys/class/input"
BUS_USB = "3"
SOURCE_LABELS = {"speaker": "speaker", "usb": "sound card"}

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


def uevent_field(text, name):
    """One field of a sysfs uevent file, "" when it is not there."""
    for line in text.splitlines():
        if line.startswith(name + "="):
            return line[len(name) + 1:].strip().strip('"')
    return ""


def _uevent(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def input_usb_ids(uevent):
    """(vendor, product) of a USB input device, read from its uevent; None for
    a device on another bus."""
    parts = uevent_field(uevent, "PRODUCT").split("/")
    if len(parts) < 3 or parts[0] != BUS_USB:
        return None
    return (parts[1].lower(), parts[2].lower())


def sound_card_usb_ids(uevent):
    """(vendor, product) of a USB sound card's audio interface; None when the
    card is not a USB one."""
    if "DEVTYPE=usb_interface" not in uevent:
        return None
    parts = uevent_field(uevent, "PRODUCT").split("/")
    if len(parts) < 2 or not parts[0]:
        return None
    return (parts[0].lower(), parts[1].lower())


def find_usb_button_device(input_root=SYSFS_INPUT, sound_root=SYSFS_SOUND):
    """/dev/input/eventN of the buttons a USB sound card carries - those of the
    headphones plugged into it. Only a card the kernel lists as a USB sound
    card is matched, so another USB device's keys are never read."""
    cards = set()
    for card in sorted(glob.glob(os.path.join(sound_root, "card*"))):
        found = sound_card_usb_ids(_uevent(os.path.join(card, "device", "uevent")))
        if found:
            cards.add(found)
    if not cards:
        return None
    for event in sorted(glob.glob(os.path.join(input_root, "event*"))):
        if input_usb_ids(_uevent(os.path.join(event, "device", "uevent"))) in cards:
            return "/dev/input/" + os.path.basename(event)
    return None


def gesture_of(data):
    """The gesture carried by one raw input event."""
    if len(data) < EVENT_SIZE:
        return None
    _sec, _usec, ev_type, code, value = struct.unpack(EVENT_FORMAT, data[:EVENT_SIZE])
    if ev_type != EV_KEY or value != 1:
        return None
    return KEY_GESTURES.get(code)


def send(control_socket, gesture, source="speaker"):
    response = send_control_command(control_socket, "speaker_button", gesture=gesture, source=source)
    if not response.get("ok"):
        log.warning("Daemon refused %s gesture %s: %s", source, gesture, response.get("error"))


class Listener:
    """One open input device: its raw events, or the news that it is gone."""

    def __init__(self, path, source):
        self.path = path
        self.source = source
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    def read(self):
        """The events ready right now: b"" when there is none, None once the
        device has gone away."""
        try:
            return os.read(self.fd, EVENT_SIZE * 16)
        except BlockingIOError:
            return b""
        except OSError:
            return None

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def targets(cfg):
    """{path: where its presses come from} of the devices to read."""
    mac = cfg.get("SPEAKER_MAC", "")
    found = {}
    path = find_event_device(mac) or find_event_device(mac, alias=speaker_alias(mac))
    if path:
        found[path] = "speaker"
    path = find_usb_button_device()
    if path:
        found.setdefault(path, "usb")
    return found


def watch_forever():
    last = {}
    listeners = {}
    while True:
        cfg = load_config(env_overrides=False)
        wanted = targets(cfg)
        for path in [one for one in listeners if one not in wanted]:
            listeners.pop(path).close()
        for path, source in wanted.items():
            if path in listeners:
                continue
            try:
                listeners[path] = Listener(path, source)
            except OSError as exc:
                log.warning("Could not open %s: %s", path, exc)
                continue
            log.info("Listening to the %s's buttons on %s", SOURCE_LABELS[source], path)
        if not listeners:
            time.sleep(RESCAN_SEC)
            continue
        ready, _, _ = select.select([one.fd for one in listeners.values()], [], [], RESCAN_SEC)
        if not ready:
            for path in [one for one in listeners if not os.path.exists(one)]:
                log.info("The %s's input device is gone, looking for it again",
                         SOURCE_LABELS[listeners.pop(path).source])
            continue
        for listener in [one for one in listeners.values() if one.fd in ready]:
            chunk = listener.read()
            if chunk is None:
                log.info("The %s's input device (%s) is gone", SOURCE_LABELS[listener.source],
                         listener.path)
                listeners.pop(listener.path).close()
                continue
            for i in range(0, len(chunk) - EVENT_SIZE + 1, EVENT_SIZE):
                gesture = gesture_of(chunk[i:i + EVENT_SIZE])
                if not gesture:
                    continue
                now = time.monotonic()
                if now - last.get((listener.source, gesture), 0) < REPEAT_GUARD_SEC:
                    continue
                last[(listener.source, gesture)] = now
                log.info("%s button: %s", SOURCE_LABELS[listener.source], gesture)
                send(cfg["CONTROL_SOCKET"], gesture, listener.source)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        watch_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

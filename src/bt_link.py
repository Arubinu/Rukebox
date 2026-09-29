"""Which controller actually carries a Bluetooth device.

BlueZ exports a device per controller: the same speaker can be paired on two
of them, connected on one, and unknown to the other - and `bluetoothctl info`
run against the wrong one answers "not available", which reads exactly like a
device that is switched off. Everything here asks every controller before
answering, and says which one carries it."""

import logging
import re
import subprocess

log = logging.getLogger("bt_link")

_CONTROLLER_RE = re.compile(r"^Controller ([0-9A-Fa-f:]{17})\s*(.*)$")
_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
PLACEHOLDER = "XX:XX:XX:XX:XX:XX"
INFO_TIMEOUT_SEC = 8
CONNECT_TIMEOUT_SEC = 25


def bluetoothctl(script, timeout=INFO_TIMEOUT_SEC):
    """Runs bluetoothctl with these commands on stdin; None when it failed.

    The whole script goes to ONE session: `select` only applies to the process
    that runs it, so a separate call would answer about another controller."""
    try:
        done = subprocess.run(["bluetoothctl"], input=script, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        log.debug("bluetoothctl did not answer", exc_info=True)
        return None
    return done.stdout


def parse_controllers(out):
    """[{"address", "name", "default"}] from `bluetoothctl list`."""
    found = []
    for line in str(out or "").splitlines():
        match = _CONTROLLER_RE.match(line.strip())
        if not match:
            continue
        rest = match.group(2)
        found.append({
            "address": match.group(1).upper(),
            "name": rest.replace("[default]", "").strip(),
            "default": "[default]" in rest,
        })
    return found


def parse_info(out):
    """What one controller says about a device."""
    text = str(out or "")
    # An unpaired controller answers "Device AA:BB:.. not available".
    known = bool(text.strip()) and "not available" not in text
    info = {"known": known, "connected": False, "paired": False, "name": ""}
    if not known:
        return info
    for line in text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key == "Connected":
            info["connected"] = value == "yes"
        elif key == "Paired":
            info["paired"] = value == "yes"
        elif key in ("Name", "Alias") and value and not info["name"]:
            info["name"] = value
    return info


def controllers():
    """Every controller BlueZ knows, in its own order."""
    return parse_controllers(bluetoothctl("list\n", timeout=5))


def default_controller(found=None):
    """The address of BlueZ's default controller, or the first one."""
    found = controllers() if found is None else found
    if not found:
        return None
    return next((c["address"] for c in found if c["default"]), found[0]["address"])


def device_info(mac, adapter=None):
    """What `adapter` (None: the default controller) says about `mac`."""
    script = ("select %s\n" % adapter if adapter else "") + "info %s\n" % mac
    out = bluetoothctl(script)
    return None if out is None else parse_info(out)


def locate(mac, adapter=""):
    """Whether the device is connected, and which controller carries it.

    The controller `adapter` names (empty: BlueZ's default) is asked first, so
    the usual case stays one call; the others are asked only when it answers
    no. `unknown` means no controller answered at all, which must not be read
    as "disconnected"."""
    mac = str(mac or "").strip().upper()
    state = {"mac": mac, "connected": False, "controller": None, "expected": None,
             "paired_here": False, "known_here": False, "name": "", "unknown": False}
    if not _MAC_RE.match(mac) or mac == PLACEHOLDER:
        return state

    found = controllers()
    expected = str(adapter or "").strip().upper() or default_controller(found)
    state["expected"] = expected
    others = [c["address"] for c in found if c["address"] != expected]

    answered = False
    here = device_info(mac, expected) if expected else None
    if here is not None:
        answered = True
        state["known_here"] = here["known"]
        state["paired_here"] = here["paired"]
        state["name"] = here["name"]
        if here["connected"]:
            state["connected"] = True
            state["controller"] = expected
            return state

    for address in others:
        there = device_info(mac, address)
        if there is None:
            continue
        answered = True
        if there["connected"]:
            state.update(connected=True, controller=address,
                         name=state["name"] or there["name"])
            return state

    state["unknown"] = not answered
    return state


def connect_here(mac, adapter):
    """Asks that controller to connect the device; True when it reports it.

    This is what moves the sound from one radio to the other: the audio
    follows the controller the speaker is connected to."""
    mac = str(mac or "").strip().upper()
    if not adapter or not _MAC_RE.match(mac):
        return False
    out = bluetoothctl("select %s\nconnect %s\n" % (adapter, mac),
                       timeout=CONNECT_TIMEOUT_SEC)
    if out is None:
        return False
    return "Connected: yes" in out or "Connection successful" in out

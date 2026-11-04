#!/usr/bin/env python3
"""Bridge from the Flic button (flicd) to the daemon's control socket."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_client import send_control_command  # noqa: E402
import audio_diag  # noqa: E402

FLICLIB_PATH = "/opt/fliclib-linux-hci/clientlib/python"
if os.path.isdir(FLICLIB_PATH):
    sys.path.insert(0, FLICLIB_PATH)

import fliclib  # noqa: E402  (provided by the Flic SDK, see path above)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [flic] %(message)s")
log = logging.getLogger("flic")

# PATHS_CONTROL_SOCKET is what rukebox.env writes; CONTROL_SOCKET was its older name.
CONTROL_SOCKET = (os.environ.get("PATHS_CONTROL_SOCKET") or os.environ.get("CONTROL_SOCKET")
                  or "/tmp/rukebox_control.sock")
FLICD_HOST = os.environ.get("FLICD_HOST", "localhost")
STATE_DIR = os.environ.get("PATHS_STATE_DIR") or os.environ.get("STATE_DIR") or "/var/lib/rukebox"


# A button that connected by itself looks asleep to whoever holds it: its first press wakes it, however late.
_link = {"connected": None, "woken": False}
_buttons = set()


def send_command(cmd: str, **extra):
    response = send_control_command(CONTROL_SOCKET, cmd, source="flic", **extra)
    if not response.get("ok"):
        log.warning("Command '%s' rejected or failed: %s", cmd, response.get("error"))


def mark_linked(linked):
    try:
        audio_diag.set_flic_linked(STATE_DIR, linked)
    except OSError:
        log.warning("Could not note the button's link", exc_info=True)


def on_button_event(channel, click_type, was_queued, time_diff):
    waking = bool(_link["connected"]) and not _link["woken"]
    if waking:
        # The first gesture is the waking one, even a queued one dropped below.
        _link["woken"] = True
        mark_linked(True)
        log.info("Flic button woken by a press")
        send_control_command(CONTROL_SOCKET, "flic_connected", source="flic")
    if was_queued:
        return
    extra = {"waking": True} if waking else {}
    if click_type == fliclib.ClickType.ButtonSingleClick:
        log.info("Single click detected -> single_click")
        send_command("single_click", **extra)
    elif click_type == fliclib.ClickType.ButtonDoubleClick:
        log.info("Double click detected -> double_click")
        send_command("double_click", **extra)
    elif click_type == fliclib.ClickType.ButtonHold:
        log.info("Long press detected -> long_press")
        send_command("long_press", **extra)


def note_link(status, first=False):
    linked = status in (fliclib.ConnectionStatus.Connected, fliclib.ConnectionStatus.Ready)
    if linked == _link["connected"]:
        return
    was = _link["connected"]
    _link["connected"] = linked
    # A button found connected as the bridge starts was not woken by a press, nor waits for one.
    _link["woken"] = linked and (first or was is not False)
    log.info("Flic button %s", "connected" if linked else "waiting for a press to connect")
    if not linked or _link["woken"]:
        mark_linked(linked)


def note_battery(bd_addr, percent):
    try:
        audio_diag.set_flic_battery(STATE_DIR, bd_addr, percent)
    except OSError:
        log.warning("Could not note the button's battery", exc_info=True)


def got_button(bd_addr):
    if bd_addr in _buttons:
        return
    _buttons.add(bd_addr)
    log.info("Flic button detected: %s", bd_addr)
    # flicd sends the level at once, then every few hours while the button connects.
    if hasattr(fliclib, "BatteryStatusListener"):
        listener = fliclib.BatteryStatusListener(bd_addr)
        listener.on_battery_status = lambda _listener, percent, _at: note_battery(bd_addr, percent)
        client.add_battery_status_listener(listener)
    cc = fliclib.ButtonConnectionChannel(bd_addr)
    cc.on_button_single_or_double_click_or_hold = on_button_event
    cc.on_create_connection_channel_response = lambda channel, error, status: note_link(status, first=True)
    cc.on_connection_status_changed = lambda channel, status, reason: note_link(status)
    client.add_connection_channel(cc)


# A link noted by an earlier bridge says nothing of this one's.
try:
    audio_diag.set_flic_linked(STATE_DIR, False)
except OSError:
    log.warning("Could not clear the button's link", exc_info=True)


def got_info(items):
    log.info("flicd lists these buttons: %s", items["bd_addr_of_verified_buttons"])
    for addr in items["bd_addr_of_verified_buttons"]:
        got_button(addr)


def list_buttons():
    client.get_info(got_info)


def controller_changed(state):
    log.info("Bluetooth controller: %s", state)
    # A start may hear of no button at all: the list is asked again once the controller is up.
    if state == fliclib.BluetoothControllerState.Attached:
        list_buttons()


client = fliclib.FlicClient(FLICD_HOST)
client.on_bluetooth_controller_state_change = controller_changed
client.on_new_verified_button = got_button
list_buttons()

log.info("Flic bridge started, listening for clicks...")
client.handle_events()

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


def send_command(cmd: str):
    response = send_control_command(CONTROL_SOCKET, cmd, source="flic")
    if not response.get("ok"):
        log.warning("Command '%s' rejected or failed: %s", cmd, response.get("error"))


def on_button_event(channel, click_type, was_queued, time_diff):
    if was_queued:
        return
    if click_type == fliclib.ClickType.ButtonSingleClick:
        log.info("Single click detected -> single_click")
        send_command("single_click")
    elif click_type == fliclib.ClickType.ButtonDoubleClick:
        log.info("Double click detected -> double_click")
        send_command("double_click")
    elif click_type == fliclib.ClickType.ButtonHold:
        log.info("Long press detected -> long_press")
        send_command("long_press")


def note_link(status):
    linked = status in (fliclib.ConnectionStatus.Connected, fliclib.ConnectionStatus.Ready)
    try:
        audio_diag.set_flic_linked(STATE_DIR, linked)
    except OSError:
        log.warning("Could not note the button's link", exc_info=True)
    log.info("Flic button %s", "connected" if linked else "waiting for a press to connect")


def got_button(bd_addr):
    log.info("Flic button detected: %s", bd_addr)
    cc = fliclib.ButtonConnectionChannel(bd_addr)
    cc.on_button_single_or_double_click_or_hold = on_button_event
    cc.on_create_connection_channel_response = lambda channel, error, status: note_link(status)
    cc.on_connection_status_changed = lambda channel, status, reason: note_link(status)
    client.add_connection_channel(cc)


# A link noted by an earlier bridge says nothing of this one's.
try:
    audio_diag.set_flic_linked(STATE_DIR, False)
except OSError:
    log.warning("Could not clear the button's link", exc_info=True)


client = fliclib.FlicClient(FLICD_HOST)
client.get_info(
    lambda items: [got_button(addr) for addr in items["bd_addr_of_verified_buttons"]]
)
client.on_new_verified_button = got_button

log.info("Flic bridge started, listening for clicks...")
client.handle_events()

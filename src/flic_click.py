#!/usr/bin/env python3
"""Bridge from the Flic button (flicd) to the daemon's control socket."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_client import send_control_command  # noqa: E402

FLICLIB_PATH = "/opt/fliclib-linux-hci/clientlib/python"
if os.path.isdir(FLICLIB_PATH):
    sys.path.insert(0, FLICLIB_PATH)

import fliclib  # noqa: E402  (provided by the Flic SDK, see path above)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [flic] %(message)s")
log = logging.getLogger("flic")

CONTROL_SOCKET = os.environ.get("CONTROL_SOCKET", "/tmp/rukebox_control.sock")
FLICD_HOST = os.environ.get("FLICD_HOST", "localhost")


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


def got_button(bd_addr):
    log.info("Flic button detected: %s", bd_addr)
    cc = fliclib.ButtonConnectionChannel(bd_addr)
    cc.on_button_single_or_double_click_or_hold = on_button_event
    client.add_connection_channel(cc)


client = fliclib.FlicClient(FLICD_HOST)
client.get_info(
    lambda items: [got_button(addr) for addr in items["bd_addr_of_verified_buttons"]]
)
client.on_new_verified_button = got_button

log.info("Flic bridge started, listening for clicks...")
client.handle_events()

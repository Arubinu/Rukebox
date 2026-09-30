"""Audio outputs by kind (Bluetooth, jack, USB, HDMI), found with pw-dump."""

import json
import logging
import subprocess

log = logging.getLogger("audio_output")

KINDS = ("bluetooth", "jack", "usb", "hdmi")


def classify(props):
    """The kind of one Audio/Sink node, from its PipeWire properties."""
    name = str(props.get("node.name") or "")
    low = (name + " " + str(props.get("alsa.card_name") or "") + " "
           + str(props.get("node.description") or "")).lower()
    if props.get("device.api") == "bluez5" or name.startswith("bluez_"):
        return "bluetooth"
    if props.get("device.bus") == "usb" or name.startswith("alsa_output.usb-"):
        return "usb"
    if "hdmi" in low:
        return "hdmi"
    if props.get("device.api") == "alsa" or name.startswith("alsa_output."):
        return "jack"
    return "other"


def parse_sinks(dump):
    """[{"name", "description", "kind", "codec", "address"}] from pw-dump's JSON.

    codec and address are only filled in for a Bluetooth output, and are what
    the audio diagnostic reports quality questions with."""
    sinks = []
    for obj in dump if isinstance(dump, list) else []:
        props = ((obj or {}).get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink" or not props.get("node.name"):
            continue
        sinks.append({
            "name": props["node.name"],
            "description": props.get("node.description") or props["node.name"],
            "kind": classify(props),
            "codec": props.get("api.bluez5.codec"),
            "address": (props.get("api.bluez5.address") or "").upper() or None,
        })
    return sinks


def list_sinks(env=None, timeout=5):
    """Every output PipeWire can play to right now."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=timeout, env=env).stdout
        return parse_sinks(json.loads(out or "[]"))
    except (subprocess.SubprocessError, OSError, ValueError):
        log.debug("Could not list the audio outputs", exc_info=True)
        return []


def find(kind, sinks):
    """The first sink of that kind, or None."""
    for sink in sinks:
        if sink["kind"] == kind:
            return sink
    return None


def mpv_device(kind, sinks):
    """(mpv audio-device, found)."""
    if kind not in KINDS or kind == "bluetooth":
        return "auto", True
    sink = find(kind, sinks)
    if sink is None:
        return "auto", False
    return "pipewire/" + sink["name"], True

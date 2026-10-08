"""Music on a USB key: what is plugged in, what is mounted, and how much of a library it holds."""

import json
import logging
import os
import shutil
import subprocess

import announcements

log = logging.getLogger("usb_storage")

MOUNT_POINT = "/media/rukebox-usb"
# What a walk of the key stops at, so a lying 2 TB disk cannot stall a tick.
MAX_SCAN_FILES = 20000

_LSBLK_FIELDS = "NAME,PATH,LABEL,FSTYPE,SIZE,RM,TYPE,UUID,MOUNTPOINT,TRAN,MODEL"


def mount_point():
    """Where a key is mounted, read at every call (a test moves it)."""
    return os.environ.get("RUKEBOX_USB_MOUNT") or MOUNT_POINT


def is_mounted(path=None):
    """Whether something is mounted there right now."""
    return os.path.ismount(path or mount_point())


def key_of(entry):
    """What identifies that key from one plug to the next: its filesystem
    UUID when it has one, else its label, else where the kernel put it."""
    entry = entry or {}
    if entry.get("uuid"):
        return "uuid:" + str(entry["uuid"])
    if entry.get("label"):
        return "label:" + str(entry["label"])
    if entry.get("device"):
        return "path:" + str(entry["device"])
    return None


def _lsblk():
    command = ["lsblk", "-J", "-o", _LSBLK_FIELDS]
    if os.name == "posix":
        command = ["nice", "-n", "19"] + command
    try:
        out = subprocess.run(command, capture_output=True, timeout=10).stdout
        return json.loads(out.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        log.debug("lsblk could not be read: %s", exc)
        return {}


def _entry(raw, hotplug, model=""):
    return {
        "device": raw.get("path") or ("/dev/" + str(raw.get("name") or "")),
        "label": raw.get("label") or "",
        # A partition has no model of its own: it takes the disk's.
        "model": (raw.get("model") or model or "").strip(),
        "fstype": (raw.get("fstype") or "").lower(),
        "size": raw.get("size") or "",
        "uuid": raw.get("uuid") or "",
        "mountpoint": raw.get("mountpoint") or "",
        "removable": bool(hotplug),
    }


def _hotplug(raw):
    """A disk you can pull out: flagged removable, or simply on the USB bus -
    an SSD in a USB caddy reports `rm` false but `tran` usb."""
    return bool(raw.get("rm")) or str(raw.get("tran") or "").lower() == "usb"


def _usable(entry):
    return (entry["fstype"] not in ("", "swap") and entry["device"]
            and entry["device"].startswith("/dev/"))


def devices(disks=None):
    """Every partition a music key can be: on a hot-pluggable disk, holding a
    filesystem. The SD card the Pi booted from is never one of them, and
    neither is a partition with no filesystem or a swap one."""
    tree = disks if disks is not None else _lsblk().get("blockdevices") or []
    found = []
    for disk in tree:
        if not isinstance(disk, dict):
            continue
        hotplug = _hotplug(disk)
        children = [c for c in (disk.get("children") or []) if isinstance(c, dict)]
        if not children:
            # A key with no partition table of its own (`/dev/sda` is the
            # filesystem).
            entry = _entry(disk, hotplug)
            if hotplug and _usable(entry):
                found.append(entry)
            continue
        for child in children:
            entry = _entry(child, hotplug or bool(child.get("rm")), disk.get("model") or "")
            if entry["removable"] and _usable(entry):
                found.append(entry)
    found.sort(key=lambda e: e["device"])
    return found


def find(devices_list, key):
    """The device a remembered key names, or None."""
    if not key:
        return None
    for entry in devices_list or []:
        if key_of(entry) == key:
            return entry
    return None


def space(path):
    """How full the file system holding a path is: total, used, free, percent.
    None when there is nothing there to measure."""
    if not path or not os.path.isdir(path):
        return None
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        log.debug("No space to report for %s: %s", path, exc)
        return None
    if not usage.total:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free,
            "percent": int(round(usage.used * 100.0 / usage.total))}


def count_music(root):
    """(how many audio files, how many bytes) under a mounted key, walking no
    further than MAX_SCAN_FILES."""
    tracks = 0
    total = 0
    if not root or not os.path.isdir(root):
        return 0, 0
    for folder, directories, names in os.walk(root):
        directories[:] = [d for d in directories if not d.startswith(".")]
        for name in names:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in announcements.AUDIO_EXTENSIONS:
                continue
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                continue
            tracks += 1
            if tracks >= MAX_SCAN_FILES:
                return tracks, total
    return tracks, total

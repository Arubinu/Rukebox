"""What this machine can do: the platform, and the features it allows.

Everything that only exists on a Raspberry Pi - the access point, GPIO, the
hardware clock, the USB gadget, powering the board off - is asked for here
rather than guessed at from a missing file somewhere else. The web interface
hides what is missing and the routes answer `unsupported_here`.

RUKEBOX_PLATFORM forces the answer (`pi`, `lxc`, `docker`, `host`), which is
what the test suite uses: the same routes must answer politely on a machine
that has none of it.

This module is named `platform` like the standard library's; nothing here
imports that one, and the tests run with `src/` first on the path."""

import glob
import os

import paths  # noqa: F401 - the roots are read by system_actions

PI = "pi"
LXC = "lxc"
DOCKER = "docker"
HOST = "host"

KNOWN = (PI, LXC, DOCKER, HOST)

# Features that belong to the machine rather than to a piece of hardware a
# probe could find: no PCI-less container has them, and they are exactly what
# the Pi profile keeps and the others drop. `rtc` is in the list because a
# container shares the host's clock and may not write it, so a /dev/rtc0
# bind-mounted in would still not be a hardware clock of our own.
BY_PLATFORM = {
    PI: frozenset({"access_point", "captive_portal", "gpio", "power", "rtc",
                   "self_update", "set_clock", "usb_gadget", "usb_storage", "wireless"}),
    DOCKER: frozenset(),
    LXC: frozenset({"power", "self_update"}),
    HOST: frozenset({"access_point", "captive_portal", "gpio", "power", "rtc",
                     "self_update", "set_clock", "usb_gadget", "usb_storage", "wireless"}),
}

PLATFORM_ONLY = frozenset(
    key for keys in BY_PLATFORM.values() for key in keys
)

CAPABILITY_NAMES = (
    "access_point", "bluetooth", "captive_portal", "gpio", "local_audio",
    "media_upload", "power", "rtc", "self_update", "set_clock", "usb_gadget",
    "usb_storage", "wireless",
)

_detected = None
_overrides = {}


def _read_first(*candidate_paths):
    for path in candidate_paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            continue
    return ""


def _cgroup_text():
    text = _read_first("/proc/1/cgroup", "/proc/self/cgroup")
    if text:
        return text
    try:
        for directory, _dirs, files in os.walk("/sys/fs/cgroup"):
            if "cgroup.procs" in files:
                return directory
    except OSError:
        pass
    return ""


def _is_raspberry_pi():
    model = _read_first("/proc/device-tree/model", "/sys/firmware/devicetree/base/model")
    return "raspberry pi" in model.lower()


def _container_kind():
    """What the container manager calls itself, or "" outside one.

    `/run/systemd/container` is systemd's own answer, and the only sign that
    reaches everywhere: LXC creates no `/dev/lxc`, and puts `container=lxc` in
    the environment of PID 1 alone - so neither is visible to a service, an SSH
    session, or a script run by hand. Without systemd, PID 1's environment is
    the last place it is written down."""
    declared = _read_first("/run/systemd/container").strip().lower()
    if declared:
        return declared
    for entry in _read_first("/proc/1/environ").split("\0"):
        if entry.startswith("container="):
            return entry.split("=", 1)[1].strip().lower()
    return ""


def detect():
    """`docker`, `lxc`, `pi` or `host`, in that order of certainty."""
    forced = (os.environ.get("RUKEBOX_PLATFORM") or "").strip().lower()
    if forced in KNOWN:
        return forced
    kind = _container_kind()
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return DOCKER
    if kind in ("docker", "podman", "containerd") \
            or os.environ.get("container") in ("docker", "podman", "containerd"):
        return DOCKER
    # LXC names itself too, so the two are told apart after Docker.
    if kind in ("lxc", "lxc-libvirt") or os.path.isdir("/dev/lxc") \
            or os.environ.get("container") == "lxc":
        return LXC
    if ":/lxc/" in _cgroup_text():
        return LXC
    if _is_raspberry_pi():
        return PI
    return HOST


def name():
    """The platform, worked out once."""
    global _detected
    if _detected is None:
        _detected = detect()
    return _detected


def reset():
    """Forgets the detection: the tests and a forced environment need this."""
    global _detected
    _detected = None
    _overrides.clear()


def _system_actions():
    import system_actions

    return system_actions


def _has_bluetooth():
    return bool(glob.glob("/sys/class/bluetooth/hci*"))


def _has_local_audio():
    if os.path.isdir("/dev/snd"):
        return True
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % \
        getattr(os, "getuid", lambda: 0)()
    return os.path.exists(os.path.join(runtime, "pipewire-0"))


def _has_media_upload():
    """A music folder this process may write to. Everything the interface adds
    goes there - a song, an announcement's sound, a system sound - and a
    container's usually arrives read-only (`:ro` in the compose file), where
    music is added from the host instead."""
    return os.access(_music_dir(), os.W_OK)


def _music_dir():
    """The folder the library is read from, resolved as the rest of the project
    resolves it: RUKEBOX_MUSIC_DIR, then the environment the units carry
    (MUSIC_DIR, which is what the generated rukebox.env writes), then the root a
    first start defaults to. `paths.music_dir()` alone answers /home/pi/audio on
    an LXC, whose music is under /srv/rukebox/audio/music and named in the YAML."""
    return (os.environ.get("RUKEBOX_MUSIC_DIR")
            or os.environ.get("MUSIC_DIR")
            or paths.music_dir())


def _has_rtc():
    # The two names the kernel gives a Pi's RTC first, then anything else.
    if os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"):
        return True
    return bool(glob.glob("/dev/rtc*"))


def _has_set_clock():
    return _system_actions().can_set_clock()


def _has_self_update():
    return _system_actions().can_self_update()


def _has_power():
    return _system_actions().can_power_off()


def _has_wireless():
    return bool(glob.glob("/sys/class/net/*/wireless"))


def _has_gpio():
    return os.path.exists("/dev/gpiochip0")


def _has_usb_gadget():
    return os.path.isdir("/sys/class/udc")


def _has_usb_storage():
    """A USB bus a music key can be plugged into. Only its own machine has one:
    a container sees the host's devices through nothing, and the helper that
    mounts the key read-only is installed beside the units, on a host."""
    return name() in (PI, HOST)


def _has_access_point():
    """A wireless card AND NetworkManager, which is what creates and shares
    `rukebox-ap`."""
    return _has_wireless() and os.path.exists("/run/NetworkManager")


def _has_captive_portal():
    return _has_access_point() and os.path.exists("/run/NetworkManager/dnsmasq-shared.d")


HARDWARE = {
    "access_point": _has_access_point,
    "bluetooth": _has_bluetooth,
    "captive_portal": _has_captive_portal,
    "gpio": _has_gpio,
    "local_audio": _has_local_audio,
    "media_upload": _has_media_upload,
    "power": _has_power,
    "rtc": _has_rtc,
    "self_update": _has_self_update,
    "set_clock": _has_set_clock,
    "usb_gadget": _has_usb_gadget,
    "usb_storage": _has_usb_storage,
    "wireless": _has_wireless,
}


def override(**capabilities):
    """Pins named capabilities, for a caller that knows better than the probe
    (the tests, and the setup page before anything is configured). A None
    forgets the pin."""
    for key, value in capabilities.items():
        if value is None:
            _overrides.pop(key, None)
        else:
            _overrides[key] = bool(value)


def has(capability):
    """Whether the feature is there."""
    if capability in _overrides:
        return _overrides[capability]
    if capability in PLATFORM_ONLY and capability not in BY_PLATFORM.get(name(), frozenset()):
        return False
    probe = HARDWARE.get(capability)
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - a probe must never take the radio down
        return False


def caps():
    """Every capability, as the interface reads them."""
    return dict({key: has(key) for key in CAPABILITY_NAMES}, platform=name())

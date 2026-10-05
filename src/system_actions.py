"""Everything that asks the machine to do something: services, power, the clock.

One function per verb, so the daemon and the web server call `reboot()` rather
than spelling out `sudo systemctl reboot` in six places - and so a platform
with no systemd, no RTC and no way to switch itself off can answer for itself.

On a Pi these are the commands that were always there. In a container there is
no machine to switch off: "off" ends the container's own processes, and Docker
brings them back according to the restart policy, which is what the image's
compose file sets. Nothing here ever tries to reach the host."""

import logging
import os
import subprocess
import sys
import threading
import time

import paths
import platform as platform_mod

log = logging.getLogger("system_actions")

DAEMON_UNIT = "rukebox-daemon.service"
WEB_UNIT = "rukebox-web.service"

# systemd's own answer for a unit that is not there at all.
NO_UNIT = 4
# Long enough for an HTTP answer or a journal line to leave before the process does.
EXIT_DELAY_SEC = 1.5


def platform_name():
    return platform_mod.name()


def is_pi():
    return platform_mod.name() == platform_mod.PI

def is_container():
    return platform_mod.name() in (platform_mod.DOCKER, platform_mod.LXC)


def _systemctl_available():
    if sys.platform.startswith("win"):
        return False
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory and os.path.exists(os.path.join(directory, "systemctl")):
            return True
    return os.path.exists("/run/systemd/system")


def can_power_off():
    """Whether switching this machine off means anything.

    True on a Pi and on any host that runs systemd. False in a container:
    there is no machine to switch off, only this process to end, and the
    platform profile says so rather than the filesystem."""
    return platform_mod.name() == platform_mod.PI or _systemctl_available()


def can_set_clock():
    """The clock is the host's everywhere except on the Pi, which has no NTP
    and a hardware clock of its own to write."""
    if sys.platform.startswith("win"):
        return False
    return platform_mod.name() == platform_mod.PI


def can_self_update():
    """Whether an update can replace the installed tree in place."""
    if sys.platform.startswith("win"):
        return False
    if platform_mod.name() == platform_mod.DOCKER:
        # The image is the unit of update: `docker pull`.
        return False
    return os.path.isdir(paths.install_dir())


def read_uptime():
    """Seconds since this machine booted, or None."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------

def _run(command, timeout=15, sudo=False):
    full = (["sudo"] + command) if sudo else list(command)
    if not _systemctl_available():
        return None
    try:
        return subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        log.debug("Could not run %s", " ".join(full), exc_info=True)
        return None


def service_action(action, unit, sudo=True, timeout=20):
    """`start`, `stop`, `restart`, `enable`, `disable` on one unit."""
    suffix = unit if unit.endswith((".service", ".target")) else unit + ".service"
    result = _run(["systemctl", action, suffix], timeout=timeout, sudo=sudo)
    return bool(result is not None and result.returncode == 0)


def service_is_active(unit, timeout=10):
    result = _run(["systemctl", "is-active", "--quiet", unit], timeout=timeout)
    return bool(result is not None and result.returncode == 0)


def service_is_enabled(unit, timeout=10):
    result = _run(["systemctl", "is-enabled", "--quiet", unit], timeout=timeout)
    return bool(result is not None and result.returncode == 0)


def service_show(units, props=("LoadState", "ActiveState", "SubState", "UnitFileState", "Type"),
                 timeout=5):
    """The raw `systemctl show` output for several units, or ""."""
    result = _run(["systemctl", "show", "--no-pager", "-p", ",".join(props)] + list(units),
                  timeout=timeout)
    return result.stdout if result is not None and result.returncode == 0 else ""


def local_service_units():
    """The unit names this installation runs.

    On a Pi they are installed systemd units. In a container systemd is not
    there at all, and the supervisor's own children are the services - the
    interface then shows nothing rather than a list of units that cannot
    exist."""
    if _systemctl_available():
        return [DAEMON_UNIT, WEB_UNIT]
    return []


# --------------------------------------------------------------------------
# The daemon and the web server themselves
# --------------------------------------------------------------------------

_exit_hooks = []
_exiting = threading.Event()


def on_exit(hook):
    """Registers a hook run when the process ends by our own request: the
    daemon closes its statistics session there before the supervisor kills
    it."""
    _exit_hooks.append(hook)


def _end_this_process(code, delay=EXIT_DELAY_SEC):
    """Leaves the process, which is what a container's supervisor watches."""

    def _leave():
        time.sleep(delay)
        for hook in list(_exit_hooks):
            try:
                hook()
            except Exception:  # noqa: BLE001 - a failed hook must not keep us alive
                log.exception("A shutdown hook failed")
        log.info("Ending this process (code %s)", code)
        os._exit(code)

    if _exiting.is_set():
        return True
    _exiting.set()
    threading.Thread(target=_leave, name="exit", daemon=True).start()
    return True


def restart_daemon():
    """Restarts the daemon, from inside the daemon: it exits and systemd (or
    the container's supervisor) starts it again."""
    if is_container() or not can_power_off():
        return _end_this_process(0)
    result = _run(["systemctl", "restart", DAEMON_UNIT], sudo=True)
    return bool(result is not None and result.returncode == 0)


def restart_web_server():
    if is_container() or not can_power_off():
        return _end_this_process(0)
    result = _run(["systemctl", "restart", WEB_UNIT], sudo=True)
    return bool(result is not None and result.returncode == 0)


# --------------------------------------------------------------------------
# Power
# --------------------------------------------------------------------------

def power_off(delay=None):
    """"Off" on a Pi; end of the container's processes anywhere else."""
    if not can_power_off() or is_container():
        return _end_this_process(0, EXIT_DELAY_SEC if delay is None else delay)
    result = _run(["systemctl", "poweroff"], sudo=True)
    return bool(result is not None and result.returncode == 0)


def reboot(delay=None):
    if not can_power_off() or is_container():
        return _end_this_process(0, EXIT_DELAY_SEC if delay is None else delay)
    result = _run(["systemctl", "reboot"], sudo=True)
    return bool(result is not None and result.returncode == 0)


def going_down():
    """"poweroff" / "reboot" when systemd has that job queued, else None."""
    if not can_power_off():
        return None
    result = _run(["systemctl", "list-jobs", "--no-legend", "--no-pager"], timeout=10)
    if result is None or result.returncode != 0:
        return None
    targets = {"poweroff.target": "poweroff", "halt.target": "poweroff",
               "reboot.target": "reboot", "kexec.target": "reboot"}
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[-1] in targets:
            return targets[parts[-1]]
    return None


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------

def set_clock(value, utc=False):
    """Sets the system clock. Returns (ok, detail)."""
    if not can_set_clock():
        return False, "unsupported_here"
    command = ["sudo", "date"]
    if utc:
        command.append("-u")
    command += ["-s", value]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    return result.returncode == 0, result.stderr.strip()


def has_rtc():
    return platform_mod.has("rtc")


def write_rtc():
    """Copies the system clock into the hardware clock, when there is one."""
    if not has_rtc():
        return False
    try:
        result = subprocess.run(["sudo", "hwclock", "-w"], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        # hwclock is in util-linux-extra on recent Raspberry Pi OS: it can be missing.
        log.warning("The clock module could not be written (hwclock missing or refused)")
    return result.returncode == 0


def timezone_name():
    result = _run(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=5)
    name = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if name:
        return name
    try:
        with open("/etc/timezone", "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def set_timezone(name):
    return bool(_run(["timedatectl", "set-timezone", name], sudo=True, timeout=10)
                is not None)


def list_timezones():
    try:
        with open("/usr/share/zoneinfo/zone.tab", "r", encoding="utf-8") as handle:
            zones = []
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 3 and parts[2].strip():
                    zones.append(parts[2].strip())
            return sorted(set(zones))
    except OSError:
        return []

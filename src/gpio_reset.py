#!/usr/bin/env python3
"""Clears the web interface password if a GPIO pin is grounded at boot."""

import logging
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_file  # noqa: E402
from config_and_scan import load_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [gpio_reset] %(message)s")
log = logging.getLogger("gpio_reset")


def is_raspberry_pi():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return b"Raspberry Pi" in f.read()
    except OSError:
        return False


def pinctrl_available():
    return subprocess.run(
        ["sh", "-c", "command -v pinctrl"],
        capture_output=True, text=True,
    ).returncode == 0


def pin_is_grounded(pin):
    """Configures `pin` as an input with its internal pull-up, then reads it
    back."""
    set_result = subprocess.run(
        ["pinctrl", "set", str(pin), "ip", "pu"],
        capture_output=True, text=True, timeout=5,
    )
    if set_result.returncode != 0:
        log.warning("pinctrl set failed: %s", set_result.stderr.strip())
        return False
    get_result = subprocess.run(
        ["pinctrl", "get", str(pin)],
        capture_output=True, text=True, timeout=5,
    )
    if get_result.returncode != 0:
        log.warning("pinctrl get failed: %s", get_result.stderr.strip())
        return False
    return " lo " in (" " + get_result.stdout.strip() + " ")


def main():
    cfg = load_config()
    if not cfg.get("GPIO_RESET_ENABLED", True):
        log.info("GPIO_RESET_ENABLED=false, skipping.")
        return 0

    if not is_raspberry_pi():
        log.info("Not a Raspberry Pi (or /proc/device-tree/model unreadable), skipping.")
        return 0

    if not pinctrl_available():
        log.info("pinctrl not found, skipping (install raspi-utils to enable this feature).")
        return 0

    pin = cfg.get("GPIO_RESET_PIN", 21)
    if not cfg.get("WEB_PASSWORD_HASH"):
        log.info("No web password currently set, nothing to reset (pin %s not checked).", pin)
        return 0

    try:
        grounded = pin_is_grounded(pin)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("Could not read GPIO %s (%s), leaving the password untouched.", pin, e)
        return 0

    if not grounded:
        log.info("GPIO %s not grounded, web password left untouched.", pin)
        return 0

    log.warning("GPIO %s grounded at boot - clearing the web interface password.", pin)
    config_file.write_values({"WEB_PASSWORD_HASH": ""})
    return 0


if __name__ == "__main__":
    sys.exit(main())

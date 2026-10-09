#!/usr/bin/env python3
"""Bridge from a GPIO push-button to the daemon: single, double and long press."""

import logging
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_client import send_control_command  # noqa: E402
from config_and_scan import load_config  # noqa: E402
from gpio_reset import is_raspberry_pi, pinctrl_available, pin_is_grounded  # noqa: E402

log = logging.getLogger("gpio_click")

CONTROL_SOCKET = os.environ.get("CONTROL_SOCKET", "/tmp/rukebox_control.sock")


def send_command(cmd):
    response = send_control_command(CONTROL_SOCKET, cmd, source="gpio")
    if not response.get("ok"):
        log.warning("Command '%s' rejected or failed: %s", cmd, response.get("error"))


class ButtonWatcher:
    """Turns raw grounded/not-grounded transitions on one pin into single_click
    / double_click / long_press commands."""

    def __init__(self, debounce_sec, double_click_window_sec, long_press_sec,
                 timer=threading.Timer, clock=time.monotonic, send=None):
        # The timer and the clock are arguments so that a test can own the time.
        self._timer = timer
        self._clock = clock
        self.debounce_sec = debounce_sec
        self.double_click_window_sec = double_click_window_sec
        self.long_press_sec = long_press_sec
        self._lock = threading.Lock()
        self._grounded = False
        self._last_edge = 0.0
        self._long_press_timer = None
        self._double_click_timer = None
        self._long_press_fired = False
        self._click_count = 0
        self._send = send

    def _emit(self, cmd):
        (self._send or send_command)(cmd)

    def on_change(self, grounded):
        now = self._clock()
        with self._lock:
            if grounded == self._grounded:
                return
            if now - self._last_edge < self.debounce_sec:
                return
            self._last_edge = now
            self._grounded = grounded
            if grounded:
                self._on_press()
            else:
                self._on_release()

    def _on_press(self):
        if self._double_click_timer:
            self._double_click_timer.cancel()
            self._double_click_timer = None
        self._long_press_fired = False
        self._long_press_timer = self._timer(self.long_press_sec, self._on_long_press)
        self._long_press_timer.daemon = True
        self._long_press_timer.start()

    def _on_release(self):
        if self._long_press_timer:
            self._long_press_timer.cancel()
            self._long_press_timer = None
        if self._long_press_fired:
            return
        self._click_count += 1
        if self._click_count == 1:
            self._double_click_timer = self._timer(
                self.double_click_window_sec, self._on_single_click_confirmed,
            )
            self._double_click_timer.daemon = True
            self._double_click_timer.start()
        else:
            self._double_click_timer = None
            self._click_count = 0
            log.info("Double click detected -> double_click")
            self._emit("double_click")

    def _on_long_press(self):
        with self._lock:
            if not self._grounded:
                return
            self._long_press_fired = True
            self._click_count = 0
        log.info("Long press detected -> long_press")
        self._emit("long_press")

    def _on_single_click_confirmed(self):
        with self._lock:
            self._click_count = 0
            self._double_click_timer = None
        log.info("Single click detected -> single_click")
        self._emit("single_click")


def watch_forever(pin, watcher):
    """Runs `pinctrl poll <pin>` and calls watcher.on_change() on every line it
    prints."""
    proc = subprocess.Popen(
        ["pinctrl", "poll", str(pin)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    for line in proc.stdout:
        if not line.strip():
            continue
        try:
            grounded = pin_is_grounded(pin)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("Could not read GPIO %s (%s), ignoring this change.", pin, e)
            continue
        watcher.on_change(grounded)

    stderr = proc.stderr.read() if proc.stderr else ""
    log.error(
        "pinctrl poll %s exited unexpectedly (exit code %s)%s",
        pin, proc.poll(), (": " + stderr.strip()) if stderr.strip() else "",
    )
    return 1


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [gpio_click] %(message)s")
    cfg = load_config()

    if not is_raspberry_pi():
        log.error("Not a Raspberry Pi (or /proc/device-tree/model unreadable) - nothing to watch.")
        return 1
    if not pinctrl_available():
        log.error("pinctrl not found - install raspi-utils, or check your PATH.")
        return 1

    pin = cfg.get("GPIO_BUTTON_PIN", 20)
    debounce_sec = cfg.get("GPIO_BUTTON_DEBOUNCE_SEC", 0.03)
    double_click_window_sec = cfg.get("GPIO_BUTTON_DOUBLE_CLICK_WINDOW_SEC", 0.4)
    long_press_sec = cfg.get("GPIO_BUTTON_LONG_PRESS_SEC", 1.5)

    set_result = subprocess.run(
        ["pinctrl", "set", str(pin), "ip", "pu"],
        capture_output=True, text=True, timeout=5,
    )
    if set_result.returncode != 0:
        log.error("Could not configure GPIO %s as an input: %s", pin, set_result.stderr.strip())
        return 1

    log.info(
        "Watching GPIO %s (debounce %.2fs, double-click window %.2fs, long press %.2fs)",
        pin, debounce_sec, double_click_window_sec, long_press_sec,
    )
    watcher = ButtonWatcher(debounce_sec, double_click_window_sec, long_press_sec)
    return watch_forever(pin, watcher)


if __name__ == "__main__":
    sys.exit(main())

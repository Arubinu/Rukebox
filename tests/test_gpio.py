"""The header's pins and which may be used (src/gpio_pins.py), and the pin that
clears a forgotten web password at boot (src/gpio_reset.py)."""
import subprocess
import types
import unittest
from unittest import mock

import _path  # noqa: F401
import config_schema
import gpio_pins
import gpio_reset


class PinsTest(unittest.TestCase):
    def test_the_header_has_forty_pins_in_order(self):
        pins = gpio_pins.pinout()
        self.assertEqual([pin["physical"] for pin in pins], list(range(1, 41)))
        numbers = [pin["bcm"] for pin in pins if pin["kind"] == gpio_pins.GPIO]
        self.assertEqual(sorted(numbers), list(range(0, 28)), "every GPIO once")

    def test_what_the_project_needs_is_refused_with_its_reason(self):
        reserved = {pin["bcm"]: pin["reserved"] for pin in gpio_pins.pinout() if pin["reserved"]}
        self.assertEqual(reserved, {2: "rtc", 3: "rtc", 14: "uart", 15: "uart", 0: "eeprom", 1: "eeprom"})
        self.assertEqual(len(gpio_pins.selectable_bcm()), 22)
        for bcm in (2, 3, 14, 15, 0, 1):
            self.assertFalse(gpio_pins.is_selectable(bcm))

    def test_only_a_free_gpio_is_selectable(self):
        self.assertTrue(gpio_pins.is_selectable(17))
        self.assertTrue(gpio_pins.is_selectable("17"), "the form sends text")
        for wrong in (28, -1, "abc", None, ""):
            self.assertFalse(gpio_pins.is_selectable(wrong), wrong)
        self.assertFalse(any(pin["selectable"] for pin in gpio_pins.pinout() if pin["kind"] != gpio_pins.GPIO))

    def test_a_number_is_found_on_the_board(self):
        self.assertEqual(gpio_pins.physical_for_bcm(21), 40)
        self.assertEqual(gpio_pins.physical_for_bcm(17), 11)
        self.assertIsNone(gpio_pins.physical_for_bcm(99))

    def test_the_two_default_pins_are_usable_and_not_the_same(self):
        defaults = {s.env: int(s.default) for s in config_schema.SETTINGS
                    if s.env in ("GPIO_BUTTON_PIN", "GPIO_RESET_PIN")}
        self.assertEqual(len(set(defaults.values())), 2, "one wire cannot be both")
        for bcm in defaults.values():
            self.assertTrue(gpio_pins.is_selectable(bcm))


def ran(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class ReadingThePinTest(unittest.TestCase):
    def read(self, *answers):
        with mock.patch.object(gpio_reset.subprocess, "run", side_effect=list(answers)) as run:
            grounded = gpio_reset.pin_is_grounded(21)
        return grounded, [call.args[0] for call in run.call_args_list]

    def test_the_pin_is_pulled_up_then_read(self):
        grounded, commands = self.read(ran(), ran(stdout="21: ip    pu | lo // GPIO21 = input\n"))
        self.assertTrue(grounded)
        self.assertEqual(commands, [["pinctrl", "set", "21", "ip", "pu"], ["pinctrl", "get", "21"]])

    def test_a_pin_left_alone_reads_high(self):
        grounded, _ = self.read(ran(), ran(stdout="21: ip    pu | hi // GPIO21 = input\n"))
        self.assertFalse(grounded)

    def test_a_pin_that_cannot_be_set_or_read_is_not_grounded(self):
        with self.assertLogs("gpio_reset", level="WARNING"):
            self.assertEqual(self.read(ran(1, stderr="no such pin"))[0], False)
        with self.assertLogs("gpio_reset", level="WARNING"):
            self.assertEqual(self.read(ran(), ran(1, stderr="busy"))[0], False)


class ResetTest(unittest.TestCase):
    def run_main(self, cfg, pi=True, pinctrl=True, grounded=False):
        settings = {"GPIO_RESET_ENABLED": True, "GPIO_RESET_PIN": 21, "WEB_PASSWORD_HASH": "pbkdf2$x"}
        settings.update(cfg)
        patches = [
            mock.patch.object(gpio_reset, "load_config", return_value=settings),
            mock.patch.object(gpio_reset, "is_raspberry_pi", return_value=pi),
            mock.patch.object(gpio_reset, "pinctrl_available", return_value=pinctrl),
            mock.patch.object(gpio_reset.config_file, "write_values"),
        ]
        if isinstance(grounded, Exception):
            patches.append(mock.patch.object(gpio_reset, "pin_is_grounded", side_effect=grounded))
        else:
            patches.append(mock.patch.object(gpio_reset, "pin_is_grounded", return_value=grounded))
        started = [p.start() for p in patches]
        self.addCleanup(mock.patch.stopall)
        code = gpio_reset.main()
        return code, started[3], started[4]

    def test_a_grounded_pin_clears_the_password(self):
        code, write, read = self.run_main({}, grounded=True)
        self.assertEqual(code, 0)
        read.assert_called_once_with(21)
        write.assert_called_once_with({"WEB_PASSWORD_HASH": ""})

    def test_a_free_pin_leaves_it(self):
        code, write, _ = self.run_main({}, grounded=False)
        self.assertEqual(code, 0)
        write.assert_not_called()

    def test_the_pin_is_not_even_read_when_there_is_nothing_to_do(self):
        for cfg, kwargs in (({"GPIO_RESET_ENABLED": False}, {}),
                            ({"WEB_PASSWORD_HASH": ""}, {}),
                            ({}, {"pi": False}),
                            ({}, {"pinctrl": False})):
            code, write, read = self.run_main(cfg, grounded=True, **kwargs)
            self.assertEqual(code, 0, "the boot never fails for it")
            read.assert_not_called()
            write.assert_not_called()
            mock.patch.stopall()

    def test_a_pin_that_cannot_be_read_never_clears_anything(self):
        for failure in (subprocess.TimeoutExpired("pinctrl", 5), OSError("gone")):
            code, write, _ = self.run_main({}, grounded=failure)
            self.assertEqual(code, 0)
            write.assert_not_called()
            mock.patch.stopall()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The 40-pin header, and which pins may be used."""

POWER = "power"
GROUND = "ground"
GPIO = "gpio"

_PINOUT = [
    (1, "3V3", POWER, None, None),
    (2, "5V", POWER, None, None),
    (3, "GPIO2", GPIO, 2, "rtc"),
    (4, "5V", POWER, None, None),
    (5, "GPIO3", GPIO, 3, "rtc"),
    (6, "GND", GROUND, None, None),
    (7, "GPIO4", GPIO, 4, None),
    (8, "GPIO14", GPIO, 14, "uart"),
    (9, "GND", GROUND, None, None),
    (10, "GPIO15", GPIO, 15, "uart"),
    (11, "GPIO17", GPIO, 17, None),
    (12, "GPIO18", GPIO, 18, None),
    (13, "GPIO27", GPIO, 27, None),
    (14, "GND", GROUND, None, None),
    (15, "GPIO22", GPIO, 22, None),
    (16, "GPIO23", GPIO, 23, None),
    (17, "3V3", POWER, None, None),
    (18, "GPIO24", GPIO, 24, None),
    (19, "GPIO10", GPIO, 10, None),
    (20, "GND", GROUND, None, None),
    (21, "GPIO9", GPIO, 9, None),
    (22, "GPIO25", GPIO, 25, None),
    (23, "GPIO11", GPIO, 11, None),
    (24, "GPIO8", GPIO, 8, None),
    (25, "GND", GROUND, None, None),
    (26, "GPIO7", GPIO, 7, None),
    (27, "GPIO0", GPIO, 0, "eeprom"),
    (28, "GPIO1", GPIO, 1, "eeprom"),
    (29, "GPIO5", GPIO, 5, None),
    (30, "GND", GROUND, None, None),
    (31, "GPIO6", GPIO, 6, None),
    (32, "GPIO12", GPIO, 12, None),
    (33, "GPIO13", GPIO, 13, None),
    (34, "GND", GROUND, None, None),
    (35, "GPIO19", GPIO, 19, None),
    (36, "GPIO16", GPIO, 16, None),
    (37, "GPIO26", GPIO, 26, None),
    (38, "GPIO20", GPIO, 20, None),
    (39, "GND", GROUND, None, None),
    (40, "GPIO21", GPIO, 21, None),
]


def pinout():
    """The header as a list of dicts, in physical order."""
    return [
        {
            "physical": physical,
            "label": label,
            "kind": kind,
            "bcm": bcm,
            "reserved": reserved,
            "selectable": kind == GPIO and reserved is None,
        }
        for physical, label, kind, bcm, reserved in _PINOUT
    ]


def selectable_bcm():
    """Every BCM number a button may be wired to, ascending."""
    return sorted(p["bcm"] for p in pinout() if p["selectable"])


def is_selectable(bcm):
    try:
        return int(bcm) in selectable_bcm()
    except (TypeError, ValueError):
        return False


def physical_for_bcm(bcm):
    for pin in pinout():
        if pin["bcm"] == bcm:
            return pin["physical"]
    return None


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "selectable":
        print(" ".join(str(n) for n in selectable_bcm()))
    else:
        print(json.dumps(pinout(), indent=2))

"""Sparing the SD card (systemd/rukebox-sd-care.service, scripts/install.sh, scripts/update.sh):
a remount at boot rather than an edited fstab, and only on a Pi, never in a container."""
import re
import unittest

import _path


class SdCareTest(unittest.TestCase):
    def test_the_card_is_remounted_not_rewritten(self):
        unit = _path.read("systemd", "rukebox-sd-care.service")
        command = re.search(r"^ExecStart=(.*)$", unit, re.MULTILINE).group(1)
        self.assertEqual(command, "/bin/mount -o remount,noatime,commit=60 /")
        for script in ("install.sh", "update.sh"):
            self.assertNotIn("/etc/fstab", _path.read("scripts", script), "fstab is never edited")

    def test_only_a_pi_spares_its_card(self):
        install = _path.read("scripts", "install.sh")
        lxc = re.search(r'LXC_UNITS="([^"]*)"', install).group(1).split()
        pi = re.search(r'PI_UNITS="([^"]*)"', install).group(1).split()
        self.assertIn("rukebox-sd-care.service", lxc, "a container's disk is the host's")
        self.assertIn("rukebox-sd-care.service", pi)
        update = _path.read("scripts", "update.sh")
        pi_block = update[update.index('if [ "$PROFILE" = "pi" ]; then'):]
        self.assertIn("rukebox-sd-care.service", pi_block.split("\n    fi\n")[0])


if __name__ == "__main__":
    unittest.main()

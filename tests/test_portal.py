"""Who the captive portal holds: the devices of the access point, also while
the access point is restarting."""
import ipaddress
import unittest
from unittest import mock

import _path  # noqa: F401
import captive_portal


class ApClientTest(unittest.TestCase):
    def setUp(self):
        captive_portal._last_ap_network.clear()
        captive_portal._interface_networks.clear()
        self.addCleanup(mock.patch.stopall)
        self.network = mock.patch.object(captive_portal, "interface_network").start()

    def test_a_device_of_the_access_point_is_one(self):
        self.network.return_value = ipaddress.ip_network("10.42.0.0/24")
        self.assertTrue(captive_portal.is_ap_client("10.42.0.52"))
        self.assertFalse(captive_portal.is_ap_client("192.168.42.10"))

    def test_it_still_is_while_the_access_point_restarts(self):
        self.network.return_value = ipaddress.ip_network("10.42.0.0/24")
        captive_portal.is_ap_client("10.42.0.52")
        self.network.return_value = None
        self.assertTrue(captive_portal.is_ap_client("10.42.0.52"))
        self.assertFalse(captive_portal.is_ap_client("192.168.42.10"))

    def test_an_access_point_never_seen_holds_nobody(self):
        self.network.return_value = None
        self.assertFalse(captive_portal.is_ap_client("10.42.0.52"))


class NetworkCacheTest(unittest.TestCase):
    def test_no_address_is_asked_again_soon(self):
        captive_portal._interface_networks.clear()
        answers = [mock.Mock(stdout=""), mock.Mock(stdout="3: uap0    inet 10.42.0.1/24 brd ...")]
        with mock.patch.object(captive_portal.subprocess, "run", side_effect=answers), \
                mock.patch.object(captive_portal.time, "monotonic", side_effect=[100.0, 104.0, 105.0]):
            self.assertIsNone(captive_portal.interface_network("uap0"))
            self.assertEqual(str(captive_portal.interface_network("uap0")), "10.42.0.0/24")
            self.assertEqual(str(captive_portal.interface_network("uap0")), "10.42.0.0/24", "cached now")


if __name__ == "__main__":
    unittest.main()

"""What a review found in the network-facing parts: the UPnP routes answer only under the
radio's own name, XML with entities is not read, and a volume that is no number is refused."""
import os
import shutil
import socket
import tempfile
import unittest

import _path  # noqa: F401
import control_client
import upnp
from test_empty_library import DaemonCase

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None


class XmlTest(unittest.TestCase):
    def test_a_document_with_entities_is_not_read(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                '<s:Envelope xmlns:s="x"><s:Body><u:Browse xmlns:u="y"><ObjectID>&b;</ObjectID>'
                '</u:Browse></s:Body></s:Envelope>')
        self.assertEqual(upnp.arguments_of(bomb), {})
        self.assertEqual(upnp.action_of("", bomb), "")


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class HostTest(unittest.TestCase):
    def test_a_foreign_name_is_refused_like_the_api(self):
        client = ws.app.test_client()
        self.assertEqual(client.get("/upnp/rootDesc.xml", headers={"Host": "attacker.example"}).status_code, 403)
        self.assertEqual(client.get("/upnp/rootDesc.xml", headers={"Host": "192.168.1.5"}).status_code, 200)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "the control socket is a Unix socket")
class VolumeNumberTest(DaemonCase):
    def test_a_volume_that_is_no_number_is_refused(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "control.sock")
        self.daemon.cfg["CONTROL_SOCKET"] = path
        self.daemon._start_control_socket()
        self.addCleanup(self.daemon._stop_event.set)
        for value in ("nan", "inf", "-inf"):
            answer = control_client.send_control_command(path, "set_volume", value=value)
            self.assertEqual(answer.get("error"), "invalid_value", value)


if __name__ == "__main__":
    unittest.main()

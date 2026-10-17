"""What a review found in the network-facing parts: the UPnP routes answer only under the
radio's own name, XML with entities is not read, and a volume that is no number is refused."""
import os
import shutil
import socket
import tempfile
import unittest
from unittest import mock

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


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class GuestClickTest(unittest.TestCase):
    def test_a_guest_never_switches_the_radio_off_through_the_button(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        # Guest credits off, and a device store of its own: what is checked is the click alone.
        values = dict(ws.cfg(), WEB_PASSWORD_HASH="x", GUEST_MODE_ENABLED=True, GUEST_QUOTA_ENABLED=False,
                      SUGGESTIONS_DB_FILE=os.path.join(folder, "suggestions.db"),
                      SINGLE_CLICK_ACTION="poweroff", DOUBLE_CLICK_ACTION="next")
        clicked = []
        with mock.patch.object(ws, "cfg", return_value=values), \
                mock.patch.object(ws, "control", side_effect=lambda cmd, **kw: clicked.append(cmd) or {"ok": True}):
            client = ws.app.test_client()
            answer = client.post("/api/action/single_click")
            self.assertEqual((answer.status_code, answer.get_json()["error"]), (403, "click_action_not_allowed"))
            self.assertEqual(client.post("/api/action/double_click").status_code, 200)
            with mock.patch.object(ws, "_is_authenticated", return_value=True):
                self.assertEqual(client.post("/api/action/single_click").status_code, 200)
        self.assertEqual(clicked, ["double_click", "single_click"])


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

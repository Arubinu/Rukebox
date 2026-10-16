"""The DLNA renderer (src/dlna_renderer.py): what a controller such as Home
Assistant reads of the radio, what its buttons ask the daemon, and the events
that keep it up to date without polling."""
import time
import unittest
import xml.etree.ElementTree as ElementTree
from unittest import mock

import _path  # noqa: F401
import dlna_renderer as dr
import upnp

try:
    import flask  # noqa: F401
    import web_server as ws
except ImportError:  # the Pi always has Flask; a checkout may not
    ws = None

BASE = "http://192.168.1.20"
STATUS = {"mode": "music", "paused": False, "current_track_path": "/m/LMFAO/03 - Party.opus",
          "duration": 262.4, "position": 61.2, "volume": 55, "muted": False}
TAGS = {"title": "Party Rock Anthem", "artist": "LMFAO", "album": "Sorry for Party Rocking"}


def now(**changes):
    return dr.state(dict(STATUS, **changes), TAGS, BASE, "k1")


class StateTest(unittest.TestCase):
    def test_the_daemons_modes_become_a_transport_state(self):
        self.assertEqual(now()["TransportState"], "PLAYING")
        self.assertEqual(now(paused=True)["TransportState"], "PAUSED_PLAYBACK")
        self.assertEqual(now(mode="idle")["TransportState"], "STOPPED")
        self.assertEqual(now(mode="stopped")["TransportState"], "STOPPED")
        self.assertEqual(now(mode="custom:morning")["TransportState"], "PLAYING", "an announcement plays")
        self.assertEqual(now(mode="restarting")["TransportState"], "TRANSITIONING")

    def test_what_plays_is_described_with_its_cover(self):
        state = now()
        self.assertEqual((state["CurrentTrackDuration"], state["RelativeTimePosition"]), ("0:04:22", "0:01:01"))
        meta = ElementTree.fromstring(state["CurrentTrackMetaData"])
        text = {el.tag.rsplit("}", 1)[-1]: el.text for el in meta.iter()}
        self.assertEqual((text["title"], text["artist"], text["album"]),
                         ("Party Rock Anthem", "LMFAO", "Sorry for Party Rocking"))
        self.assertEqual(text["albumArtURI"], BASE + "/dlna/cover.jpg?k=k1")
        self.assertEqual((state["Volume"], state["Mute"]), ("55", "0"))
        self.assertEqual(now(mode="idle", current_track_path="")["CurrentTrackMetaData"], "")

    def test_the_actions_offered_follow_the_state(self):
        self.assertEqual(now()["CurrentTransportActions"], "Pause,Stop,Next,Previous")
        self.assertEqual(now(paused=True)["CurrentTransportActions"], "Play,Stop,Next,Previous")
        self.assertEqual(now(mode="idle")["CurrentTransportActions"], "Play,Next,Previous")


class ControlTest(unittest.TestCase):
    def call(self, name, action, state, **arguments):
        sent = []

        def send(command, **kwargs):
            sent.append((command, kwargs))
            return {"ok": True}
        status, body = dr.handle(name, action, arguments, state, send)
        return status, body, sent

    def test_the_buttons_become_the_daemons_commands(self):
        self.assertEqual(self.call("AVTransport", "Pause", now())[2], [("toggle_pause", {})])
        self.assertEqual(self.call("AVTransport", "Stop", now())[2], [("toggle_pause", {})])
        self.assertEqual(self.call("AVTransport", "Play", now(paused=True))[2], [("toggle_pause", {})])
        self.assertEqual(self.call("AVTransport", "Play", now(mode="idle"))[2], [("start_music", {})])
        self.assertEqual(self.call("AVTransport", "Play", now())[2], [], "already playing")
        self.assertEqual(self.call("AVTransport", "Pause", now(paused=True))[2], [], "already paused")
        self.assertEqual(self.call("AVTransport", "Next", now())[2], [("next_track", {})])
        self.assertEqual(self.call("RenderingControl", "SetVolume", now(), DesiredVolume="140")[2],
                         [("set_volume", {"value": 100.0})])
        self.assertEqual(self.call("RenderingControl", "SetMute", now(), DesiredMute="1")[2],
                         [("set_mute", {"on": True})])

    def test_the_questions_are_answered_from_the_state(self):
        status, body, _sent = self.call("AVTransport", "GetTransportInfo", now(paused=True))
        self.assertEqual(status, 200)
        self.assertIn("<CurrentTransportState>PAUSED_PLAYBACK</CurrentTransportState>", body)
        _status, body, _sent = self.call("RenderingControl", "GetVolume", now())
        self.assertIn("<CurrentVolume>55</CurrentVolume>", body)
        _status, body, _sent = self.call("AVTransport", "GetPositionInfo", now())
        self.assertIn("<RelTime>0:01:01</RelTime>", body)
        self.assertIn("&lt;dc:title&gt;Party Rock Anthem&lt;/dc:title&gt;", body, "the DIDL escaped inside")
        ElementTree.fromstring(body)

    def test_nothing_can_be_sent_to_it(self):
        status, body, _sent = self.call("AVTransport", "SetAVTransportURI", now(),
                                        CurrentURI="http://elsewhere/song.mp3")
        self.assertEqual(status, 500)
        self.assertIn("<errorCode>401</errorCode>", body)
        self.assertNotIn("SetAVTransportURI", dr.service_description("AVTransport"))


class DescriptionTest(unittest.TestCase):
    def test_the_device_and_its_services_are_valid_xml(self):
        root = ElementTree.fromstring(dr.device_description())
        ns = "{urn:schemas-upnp-org:device-1-0}"
        self.assertEqual(root.find(ns + "device/" + ns + "deviceType").text, dr.DEVICE_TYPE)
        services = [el.text for el in root.iter(ns + "serviceType")]
        self.assertEqual(services, [dr.AV_TRANSPORT, dr.RENDERING_CONTROL, dr.CONNECTION_MANAGER])
        for name, _kind, _id in dr.SERVICES:
            scpd = ElementTree.fromstring(dr.service_description(name))
            variables = {el.text for el in scpd.iter("{urn:schemas-upnp-org:service-1-0}name")}
            related = {el.text for el in scpd.iter("{urn:schemas-upnp-org:service-1-0}relatedStateVariable")}
            self.assertEqual(related - variables, set(), name + ": every argument has its variable")
        self.assertIsNone(dr.service_description("ContentDirectory"))

    def test_it_is_a_device_of_its_own_beside_the_media_server(self):
        self.assertNotEqual(dr.DEVICE.udn(), upnp.SERVER.udn())
        search = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                  b"MAN: \"ssdp:discover\"\r\nMX: 1\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n")
        replies = upnp.search_replies(search, "192.168.1.30", 80, addresses=["192.168.1.20"], device=dr.DEVICE)
        self.assertEqual(len(replies), 1)
        self.assertIn(b"LOCATION: http://192.168.1.20/dlna/rootDesc.xml", replies[0])
        self.assertEqual(upnp.search_replies(search, "192.168.1.30", 80, addresses=["192.168.1.20"]), [],
                         "the media server is no renderer")


class EventsTest(unittest.TestCase):
    def test_a_watcher_is_told_everything_first_then_only_what_changed(self):
        told = []
        events = dr.Events(post=lambda url, sid, seq, body: told.append((url, seq, body)) or True)
        sid = events.subscribe("AVTransport", ["http://ha:8123/notify"], 1800)
        events.welcome(sid, now())
        self.assertEqual(told[0][1], 0)
        self.assertIn('TransportState val="PLAYING"', told[0][2])
        events.publish(now())
        events.publish(now())
        self.assertEqual(len(told), 1, "nothing changed")
        events.publish(now(paused=True))
        self.assertEqual(told[1][1], 1)
        self.assertIn("PAUSED_PLAYBACK", told[1][2])
        self.assertNotIn("CurrentTrackMetaData", told[1][2], "only what changed")
        volume = events.subscribe("RenderingControl", ["http://ha:8123/notify"], 1800)
        events.publish(now(paused=True, volume=30))
        self.assertIn('Volume channel="Master" val="30"', told[-1][2])
        self.assertTrue(events.unsubscribe(volume))
        self.assertFalse(events.renew(volume, 1800))

    def test_an_expired_subscription_is_forgotten(self):
        events = dr.Events(post=lambda *a: True)
        sid = events.subscribe("AVTransport", ["http://ha/notify"], 60)
        self.assertTrue(events.watched())
        events.subscriptions[sid]["expires"] = time.monotonic() - 1
        self.assertFalse(events.watched())

    def test_the_headers_are_read(self):
        self.assertEqual(dr.parse_callbacks("<http://a:1/x><http://b/y>"), ["http://a:1/x", "http://b/y"])
        self.assertEqual(dr.parse_timeout("Second-300"), 300)
        self.assertEqual(dr.parse_timeout("infinite"), dr.SUBSCRIPTION_SEC)


@unittest.skipUnless(ws, "Flask is not installed (run these on the Pi)")
class RouteTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ws, "control", return_value={"ok": True, "data": dict(STATUS)}).start()
        mock.patch.object(ws.track_media, "tags", return_value=dict(TAGS)).start()
        self.client = ws.app.test_client()

    def test_nothing_answers_while_the_setting_is_off(self):
        with mock.patch.object(dr, "running", return_value=False):
            self.assertEqual(self.client.get("/dlna/rootDesc.xml").status_code, 404)

    def test_a_controller_subscribes_and_asks(self):
        with mock.patch.object(dr, "running", return_value=True), \
                mock.patch.object(dr.events, "welcome") as welcome, \
                mock.patch.object(ws.threading, "Timer") as timer:
            self.assertEqual(self.client.get("/dlna/rootDesc.xml").status_code, 200)
            answer = self.client.open("/dlna/AVTransport/event", method="SUBSCRIBE", headers={
                "CALLBACK": "<http://ha:8123/api/notify>", "NT": "upnp:event", "TIMEOUT": "Second-300"})
            self.assertEqual(answer.status_code, 200)
            sid = answer.headers["SID"]
            self.assertTrue(sid.startswith("uuid:"))
            self.assertEqual(answer.headers["TIMEOUT"], "Second-300")
            timer.assert_called_once()
            renewed = self.client.open("/dlna/AVTransport/event", method="SUBSCRIBE", headers={"SID": sid})
            self.assertEqual(renewed.status_code, 200)
            gone = self.client.open("/dlna/AVTransport/event", method="UNSUBSCRIBE", headers={"SID": sid})
            self.assertEqual(gone.status_code, 200)
            body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><u:GetTransportInfo xmlns:u="%s"><InstanceID>0</InstanceID>'
                    '</u:GetTransportInfo></s:Body></s:Envelope>' % dr.AV_TRANSPORT)
            info = self.client.post("/dlna/AVTransport/control", data=body, headers={
                "SOAPAction": '"%s#GetTransportInfo"' % dr.AV_TRANSPORT, "Content-Type": "text/xml"})
            self.assertIn("<CurrentTransportState>PLAYING</CurrentTransportState>", info.get_data(as_text=True))
        welcome.assert_not_called()


if __name__ == "__main__":
    unittest.main()

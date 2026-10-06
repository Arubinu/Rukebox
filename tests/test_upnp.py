"""UPnP discovery (src/upnp.py): how a player finds the stream by itself.

What matters here is that a search for a media server is answered with the right
device, that the DIDL a player reads carries the stream's own plain HTTP URL,
and that a browse says "nothing at all" rather than offering an address that
would play silence. The socket is exercised for real on a throwaway port:
multicast is not something a fake would prove anything about."""
import socket
import unittest
import xml.etree.ElementTree as ElementTree
from unittest import mock

import _path  # noqa: F401
import upnp

def search(st=upnp.DEVICE_TYPE, man="ssdp:discover"):
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "%s"\r\n'
        "MX: 3\r\n"
        "ST: %s\r\n"
        "\r\n"
    ) % (man, st)


SEARCH = search().encode("ascii")

STREAM = {"title": "Rukebox", "url": "http://10.42.0.1/stream.opus", "mime": "audio/ogg"}


def text_of(xml, tag):
    """The text of the first element of that name, namespaces ignored."""
    root = ElementTree.fromstring(xml)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == tag:
            return element.text or ""
    return ""


class UdnTest(unittest.TestCase):
    """What a player remembers this radio by, and what tells two apart."""

    def setUp(self):
        self.addCleanup(upnp.configure, upnp.DEFAULT_NAME, "")

    def test_it_is_a_uuid_and_it_does_not_move(self):
        upnp.configure("Rukebox", "")
        first = upnp.udn()
        self.assertTrue(first.startswith("uuid:"), first)
        self.assertEqual(first, upnp.udn(), "a player that saw it once must not see two radios")

    def test_the_name_is_what_a_player_shows(self):
        upnp.configure("Cuisine", "4821")
        self.assertEqual(upnp.device_name(), "Cuisine 4821")
        self.assertEqual(upnp.device_name(), upnp.device_name())

    def test_the_serial_is_what_tells_two_radios_apart(self):
        upnp.configure("Rukebox", "4821")
        one = upnp.udn()
        upnp.configure("Rukebox", "9137")
        self.assertNotEqual(one, upnp.udn(), "same name, another serial: another device")

    def test_renaming_keeps_the_device(self):
        upnp.configure("Rukebox", "9137")
        one = upnp.udn()
        upnp.configure("Cuisine", "9137")
        self.assertEqual(upnp.udn(), one, "renaming relabels the device a player already has")
        self.assertEqual(upnp.device_name(), "Cuisine 9137")

    def test_a_radio_without_a_serial_still_has_a_name(self):
        upnp.configure("", "")
        self.assertEqual(upnp.device_name(), upnp.DEFAULT_NAME)
        self.assertTrue(upnp.udn().startswith("uuid:"))


class DescriptionTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(upnp.configure, upnp.DEFAULT_NAME, "")

    def test_it_describes_a_media_server_with_a_content_directory(self):
        upnp.configure("Rukebox", "")
        text = upnp.device_description()
        root = ElementTree.fromstring(text)
        self.assertEqual(root.tag.rsplit("}", 1)[-1], "root")
        self.assertEqual(text_of(text, "deviceType"), upnp.DEVICE_TYPE)
        self.assertEqual(text_of(text, "UDN"), upnp.udn())
        self.assertEqual(text_of(text, "friendlyName"), "Rukebox")
        self.assertEqual(text_of(text, "controlURL"), "/upnp/ContentDirectory/control")
        services = [element.text for element in root.iter()
                    if element.tag.rsplit("}", 1)[-1] == "serviceType"]
        self.assertEqual(services, [upnp.CONTENT_DIRECTORY, upnp.CONNECTION_MANAGER])

    def test_the_description_carries_the_serial(self):
        upnp.configure("Cuisine", "4821")
        text = upnp.device_description()
        self.assertEqual(text_of(text, "friendlyName"), "Cuisine 4821")

    def test_both_service_descriptions_are_readable_xml(self):
        for name in ("ContentDirectory", "ConnectionManager"):
            root = ElementTree.fromstring(upnp.service_description(name))
            self.assertEqual(root.tag.rsplit("}", 1)[-1], "scpd")
        self.assertIn("Browse", upnp.service_description("ContentDirectory"))
        self.assertIn("GetProtocolInfo", upnp.service_description("ConnectionManager"))

    def test_an_unknown_service_has_no_description(self):
        self.assertIsNone(upnp.service_description("Nonsense"))
        self.assertIsNone(upnp.service_of("Nonsense"))


class SearchTest(unittest.TestCase):
    """What a player sends, and what it must get back."""

    def test_only_a_search_with_the_discover_header_is_answered(self):
        self.assertIsNotNone(upnp.parse_search(SEARCH))
        self.assertIsNone(upnp.parse_search(b"NOTIFY * HTTP/1.1\r\nNT: upnp:rootdevice\r\n\r\n"))
        self.assertIsNone(upnp.parse_search(b"GET / HTTP/1.1\r\n\r\n"))
        self.assertIsNone(upnp.parse_search(search(man="ssdp:all").encode()),
                          "a search that does not say discover is not one")

    def replies(self, st, sender="10.42.0.52", port=80, addresses=("10.42.0.1", "192.168.1.10")):
        return upnp.search_replies(search(st).encode("ascii"), sender, port,
                                   addresses=list(addresses))

    def test_the_answer_names_the_device_and_where_it_lives(self):
        replies = self.replies(upnp.DEVICE_TYPE)
        self.assertEqual(len(replies), 1)
        text = replies[0].decode("ascii")
        self.assertTrue(text.startswith("HTTP/1.1 200 OK\r\n"))
        self.assertIn("ST: " + upnp.DEVICE_TYPE + "\r\n", text)
        self.assertIn("USN: %s::%s\r\n" % (upnp.udn(), upnp.DEVICE_TYPE), text)
        self.assertIn("LOCATION: http://10.42.0.1/upnp/rootDesc.xml\r\n", text)
        self.assertIn("EXT:\r\n", text)
        self.assertIn("CACHE-CONTROL: max-age=1800\r\n", text)
        self.assertIn("CONFIGID.UPNP.ORG: 1\r\n", text)
        self.assertTrue(text.endswith("\r\n\r\n"), "the message ends with a blank line")

    def test_the_address_offered_is_the_one_on_the_senders_own_network(self):
        """A device of the access point must be pointed at 10.42.0.1, never at
        the address of the home network the Pi is also on."""
        self.assertIn("LOCATION: http://10.42.0.1/", self.replies(upnp.DEVICE_TYPE)[0].decode())
        self.assertIn("LOCATION: http://192.168.1.10/",
                      self.replies(upnp.DEVICE_TYPE, sender="192.168.1.44")[0].decode())
        self.assertIn("LOCATION: http://10.42.0.1/",
                      self.replies(upnp.DEVICE_TYPE, sender="172.16.9.4")[0].decode(),
                      "a network we are not on falls back to the first address")

    def test_the_port_is_left_out_when_it_is_the_default_one(self):
        self.assertIn("http://10.42.0.1/upnp/rootDesc.xml",
                      self.replies(upnp.DEVICE_TYPE, port=80)[0].decode())
        self.assertIn("http://10.42.0.1:8080/upnp/rootDesc.xml",
                      self.replies(upnp.DEVICE_TYPE, port=8080)[0].decode())

    def test_a_search_for_everything_gets_every_service(self):
        replies = self.replies("ssdp:all")
        self.assertEqual(len(replies), len(upnp.search_targets(None)))
        targets = "".join(reply.decode("ascii") for reply in replies)
        self.assertIn("ST: upnp:rootdevice", targets)
        self.assertIn("ST: " + upnp.DEVICE_TYPE, targets)

    def test_a_search_for_something_we_do_not_have_is_left_alone(self):
        self.assertEqual(self.replies("urn:schemas-upnp-org:device:Printer:1"), [])

    def test_a_search_is_not_answered_when_this_machine_has_no_address(self):
        self.assertEqual(self.replies(upnp.DEVICE_TYPE, addresses=()), [])

    def test_no_address_at_all_is_an_empty_answer_not_a_crash(self):
        self.assertEqual(upnp.pick_address("10.42.0.52", []), "")
        self.assertEqual(upnp.pick_address("10.42.0.52", ["10.42.0.1"]), "10.42.0.1")


class NotifyTest(unittest.TestCase):
    """A player that was already open only ever learns about us this way."""

    def test_an_announcement_says_where_and_for_how_long(self):
        messages = upnp.notify_messages(8080, addresses=["10.42.0.1"])
        self.assertTrue(messages)
        text = messages[0].decode("ascii")
        self.assertTrue(text.startswith("NOTIFY * HTTP/1.1\r\n"))
        self.assertIn("HOST: 239.255.255.250:1900\r\n", text)
        self.assertIn("NTS: ssdp:alive\r\n", text)
        self.assertIn("LOCATION: http://10.42.0.1:8080/upnp/rootDesc.xml\r\n", text)

    def test_one_announcement_per_address_and_per_service(self):
        messages = upnp.notify_messages(80, addresses=["10.42.0.1", "192.168.1.10"])
        self.assertEqual(len(messages), 2 * len(upnp.search_targets(None)))

    def test_going_away_says_so_without_an_address(self):
        text = upnp.notify_messages(80, alive=False, addresses=["10.42.0.1"])[0].decode()
        self.assertIn("NTS: ssdp:byebye\r\n", text)
        self.assertNotIn("LOCATION:", text)


class BrowseTest(unittest.TestCase):
    """The ContentDirectory call: what a player reads to play something."""

    def browse(self, items, object_id="0", flag="BrowseDirectChildren", service=None,
               action="Browse"):
        body = (
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            '<s:Body><u:Browse xmlns:u="%s"><ObjectID>%s</ObjectID>'
            "<BrowseFlag>%s</BrowseFlag><Filter>*</Filter><StartingIndex>0</StartingIndex>"
            "<RequestedCount>5000</RequestedCount><SortCriteria></SortCriteria>"
            "</u:Browse></s:Body></s:Envelope>" % (upnp.CONTENT_DIRECTORY, object_id, flag)
        )
        status, text = upnp.handle(service or upnp.CONTENT_DIRECTORY, action, body, items)
        return status, text, ElementTree.fromstring(text)

    def test_the_stream_is_offered_with_its_own_http_url(self):
        status, text, _root = self.browse([STREAM])
        self.assertEqual(status, 200)
        self.assertIn("<NumberReturned>1</NumberReturned>", text)
        self.assertIn("<TotalMatches>1</TotalMatches>", text)
        didl = text_of(text, "Result")
        self.assertIn('protocolInfo="http-get:*:audio/ogg:*"', didl)
        self.assertIn(">http://10.42.0.1/stream.opus</res>", didl)
        self.assertIn("object.item.audioItem.audioBroadcast", didl)
        self.assertIn("<dc:title>Rukebox</dc:title>", didl)

    def test_the_didl_is_escaped_inside_the_result(self):
        """VLC reads the Result's text and parses it: raw XML there would make
        the whole answer unreadable."""
        _status, text, _root = self.browse([STREAM])
        self.assertIn("&lt;DIDL-Lite", text)
        self.assertIsNotNone(ElementTree.fromstring(text_of(text, "Result")),
                             "what a player parses out of Result is XML")

    def test_a_url_with_an_ampersand_survives_the_escaping(self):
        item = dict(STREAM, url="http://10.42.0.1:8080/stream.opus?a=1&b=2")
        _status, text, _root = self.browse([item])
        self.assertIn("&amp;b=2", text_of(text, "Result"))

    def test_nothing_is_offered_when_the_stream_is_off(self):
        """An entry that plays silence is worse than no entry."""
        status, text, _root = self.browse([])
        self.assertEqual(status, 200)
        self.assertIn("<NumberReturned>0</NumberReturned>", text)
        self.assertIn("<TotalMatches>0</TotalMatches>", text)
        self.assertNotIn("<res", text_of(text, "Result"))

    def test_the_root_says_how_many_things_are_in_it(self):
        _status, text, _root = self.browse([STREAM], flag="BrowseMetadata")
        self.assertIn("<upnp:class>object.container.storageFolder</upnp:class>",
                      text_of(text, "Result"))
        self.assertIn('childCount="1"', text_of(text, "Result"))

    def test_the_item_itself_can_be_asked_for(self):
        status, text, _root = self.browse([STREAM], object_id="1", flag="BrowseMetadata")
        self.assertEqual(status, 200)
        self.assertIn("stream.opus", text_of(text, "Result"))

    def test_an_object_that_is_not_there_is_a_fault(self):
        status, text, _root = self.browse([STREAM], object_id="99")
        self.assertEqual(status, 500)
        self.assertIn("<errorCode>701</errorCode>", text)

    def test_a_look_at_what_this_player_can_play(self):
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                "<s:Body><u:GetProtocolInfo xmlns:u=\"%s\"/></s:Body></s:Envelope>"
                % upnp.CONNECTION_MANAGER)
        status, text = upnp.handle(upnp.CONNECTION_MANAGER, "GetProtocolInfo", body, [STREAM])
        self.assertEqual(status, 200)
        self.assertIn("http-get:*:audio/ogg:*", text)
        self.assertIn("<Sink></Sink>", text)

    def test_a_call_we_do_not_know_is_refused_in_the_expected_shape(self):
        status, text = upnp.handle(upnp.CONTENT_DIRECTORY, "Nonsense", "", [])
        self.assertEqual(status, 500)
        self.assertIn("<errorCode>401</errorCode>", text)


class ActionTest(unittest.TestCase):
    def test_the_name_comes_from_the_header_a_player_sends(self):
        self.assertEqual(
            upnp.action_of('"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"', ""),
            "Browse")

    def test_the_name_is_read_from_the_body_when_the_header_is_missing(self):
        body = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                '<u:Browse xmlns:u="%s"><ObjectID>0</ObjectID></u:Browse></s:Body></s:Envelope>'
                % upnp.CONTENT_DIRECTORY)
        self.assertEqual(upnp.action_of("", body), "Browse")

    def test_the_arguments_are_read_whatever_prefixes_are_used(self):
        body = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                '<u:Browse xmlns:u="%s"><ObjectID>0</ObjectID>'
                "<BrowseFlag>BrowseMetadata</BrowseFlag></u:Browse></s:Body></s:Envelope>"
                % upnp.CONTENT_DIRECTORY)
        arguments = upnp.arguments_of(body)
        self.assertEqual(arguments["ObjectID"], "0")
        self.assertEqual(arguments["BrowseFlag"], "BrowseMetadata")

    def test_a_body_that_is_not_xml_is_no_arguments_not_a_crash(self):
        self.assertEqual(upnp.arguments_of("not xml at all"), {})
        self.assertEqual(upnp.action_of("", "not xml at all"), "")


class ResponderTest(unittest.TestCase):
    """The socket itself, on a throwaway port: this is what a player talks to."""

    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(upnp, "local_addresses", return_value=["127.0.0.1"]).start()
        self.responders = []
        self.addCleanup(self.stop_all)
        self.responder = self.listen()

    def stop_all(self):
        for responder in self.responders:
            responder.stop()

    def listen(self, **kwargs):
        responder = upnp.Responder(8080, ssdp_port=0, interval=3600, **kwargs)
        self.responders.append(responder)
        self.assertTrue(responder.start())
        return responder

    def ask(self, data=SEARCH):
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(2)
        try:
            client.sendto(data, ("127.0.0.1", self.responder.socket.getsockname()[1]))
            return client.recvfrom(2048)[0]
        finally:
            client.close()

    def test_a_search_gets_an_answer_over_the_real_socket(self):
        answer = self.ask().decode("ascii")
        self.assertIn("ST: " + upnp.DEVICE_TYPE, answer)
        self.assertIn("LOCATION: http://127.0.0.1:8080/upnp/rootDesc.xml", answer)
        self.assertEqual(self.responder.answers, 1)

    def test_something_that_is_not_a_search_is_ignored(self):
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(0.4)
        try:
            client.sendto(b"hello?", ("127.0.0.1", self.responder.socket.getsockname()[1]))
            with self.assertRaises(socket.timeout):
                client.recvfrom(2048)
        finally:
            client.close()
        self.assertEqual(self.responder.answers, 0)

    def test_it_announces_itself_without_being_asked(self):
        """A player opened before us never searches twice: it has to be told."""
        sent = []
        with mock.patch.object(self.responder, "_send",
                               side_effect=lambda message, address: sent.append((message, address))):
            self.responder._announce(alive=True)
        self.assertTrue(sent)
        self.assertEqual(sent[0][1], (upnp.SSDP_ADDRESS, self.responder.ssdp_port))
        self.assertIn(b"NTS: ssdp:alive", sent[0][0])

    def test_it_says_goodbye_when_it_is_stopped(self):
        sent = []
        with mock.patch.object(self.responder, "_send",
                               side_effect=lambda message, address: sent.append(message)):
            self.responder.stop()
        self.assertTrue(sent)
        self.assertIn(b"NTS: ssdp:byebye", sent[0])
        self.assertIsNone(self.responder.socket)

    def test_it_can_be_started_again(self):
        self.responder.stop()
        self.responder = self.listen()
        self.assertIn("ST: " + upnp.DEVICE_TYPE, self.ask().decode("ascii"))


class StartTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(upnp.stop)

    def test_starting_twice_keeps_one_listener(self):
        mock.patch.object(upnp, "local_addresses", return_value=["127.0.0.1"]).start()
        first = upnp.start(8080, ssdp_port=0)
        self.assertIsNotNone(first)
        self.assertTrue(upnp.running())
        self.assertIs(upnp.start(8080, ssdp_port=0), first)
        upnp.stop()
        self.assertFalse(upnp.running())

    def test_a_port_that_cannot_be_taken_is_not_fatal(self):
        with mock.patch.object(upnp.socket, "socket", side_effect=OSError("no sockets here")):
            self.assertIsNone(upnp.start(8080, ssdp_port=0))
        self.assertFalse(upnp.running())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""UPnP MediaServer: what makes a player find the network stream by itself."""

import logging
import re
import socket
import struct
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ElementTree
from email.utils import formatdate
from xml.sax.saxutils import escape, quoteattr

log = logging.getLogger("web")

SSDP_ADDRESS = "239.255.255.250"
SSDP_PORT = 1900
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"
CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:1"
CONNECTION_MANAGER = "urn:schemas-upnp-org:service:ConnectionManager:1"
SERVICES = (
    ("ContentDirectory", CONTENT_DIRECTORY, "urn:upnp-org:serviceId:ContentDirectory"),
    ("ConnectionManager", CONNECTION_MANAGER, "urn:upnp-org:serviceId:ConnectionManager"),
)
SERVER_NAME = "Rukebox/1.0 UPnP/1.0"
FRIENDLY_NAME = "Rukebox"
MAX_AGE = 1800
NOTIFY_INTERVAL = 900
ANNOUNCE_AGAIN_SEC = 1.0
PATH = "/upnp"
ROOT_ID = "0"
ROOT_TITLE = "Rukebox"
AUDIO_CLASS = "object.item.audioItem.audioBroadcast"
CONTAINER_CLASS = "object.container.storageFolder"
DEFAULT_MIME = "audio/ogg"
# Read by a client to tell one run of this radio from the next one.
BOOT_ID = int(time.time()) & 0x7FFFFFFF
SOAP_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body>%s</s:Body></s:Envelope>"
)
DIDL_HEADER = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
)


def udn():
    """A name that survives a restart, so a player that has seen this radio
    once does not end up listing it twice."""
    return "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    "rukebox-upnp-" + socket.gethostname()))


def base_url(address, port):
    """The address a client reaches the web interface on, without a port when
    it is the default one."""
    port = int(port or 80)
    return "http://%s%s" % (address, "" if port == 80 else ":%d" % port)


def description_url(address, port):
    return base_url(address, port) + PATH + "/rootDesc.xml"


def device_description():
    """The XML a player downloads to learn what is here and where to browse."""
    services = "".join(
        "<service><serviceType>%s</serviceType><serviceId>%s</serviceId>"
        "<SCPDURL>%s/%s/scpd.xml</SCPDURL>"
        "<controlURL>%s/%s/control</controlURL>"
        "<eventSubURL>%s/%s/event</eventSubURL></service>"
        % (service_type, service_id, PATH, name, PATH, name, PATH, name)
        for name, service_type, service_id in SERVICES
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion>"
        "<device>"
        "<deviceType>%s</deviceType>"
        "<friendlyName>%s</friendlyName>"
        "<manufacturer>Rukebox</manufacturer>"
        "<modelName>Rukebox</modelName>"
        "<modelDescription>Rukebox radio</modelDescription>"
        "<UDN>%s</UDN>"
        "<serviceList>%s</serviceList>"
        "</device></root>"
    ) % (DEVICE_TYPE, escape(FRIENDLY_NAME), udn(), services)


def service_of(name):
    """The service type of one of the two services, from its URL name."""
    for service_name, service_type, _service_id in SERVICES:
        if service_name == name:
            return service_type
    return None


def service_description(name):
    """The SCPD of a service: what a strict client reads before calling it."""
    if name == "ContentDirectory":
        return CONTENT_DIRECTORY_SCPD
    if name == "ConnectionManager":
        return CONNECTION_MANAGER_SCPD
    return None


def _header(name, value):
    return "%s:%s\r\n" % (name, "" if value == "" else " " + str(value))


def _message(start_line, headers):
    return start_line + "\r\n" + "".join(_header(*pair) for pair in headers) + "\r\n"


def parse_search(data):
    """The headers of an M-SEARCH datagram, or None for anything else."""
    text = data.decode("latin-1", "replace")
    lines = text.split("\n")
    if not lines or not lines[0].strip().upper().startswith("M-SEARCH"):
        return None
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if value:
            headers[name.strip().lower()] = value.strip()
    if "ssdp:discover" not in headers.get("man", "").lower():
        return None
    return headers


def search_targets(st):
    """The (NT, USN) pairs this machine answers a given search target with."""
    name = udn()
    pairs = [
        (name, name),
        ("upnp:rootdevice", name + "::upnp:rootdevice"),
        (DEVICE_TYPE, name + "::" + DEVICE_TYPE),
    ]
    pairs += [(service_type, name + "::" + service_type)
              for _service, service_type, _id in SERVICES]
    if not st or st == "ssdp:all":
        return pairs
    return [pair for pair in pairs if pair[0] == st]


def search_replies(data, sender, port, addresses=None):
    """What to answer one datagram with: one message per service searched for."""
    headers = parse_search(data)
    if headers is None:
        return []
    address = pick_address(sender, addresses)
    if not address:
        return []
    location = description_url(address, port)
    return [
        _message("HTTP/1.1 200 OK", [
            ("CACHE-CONTROL", "max-age=%d" % MAX_AGE),
            ("DATE", formatdate(usegmt=True)),
            ("EXT", ""),
            ("LOCATION", location),
            ("SERVER", SERVER_NAME),
            ("ST", nt),
            ("USN", usn),
            ("BOOTID.UPNP.ORG", BOOT_ID),
            ("CONFIGID.UPNP.ORG", 1),
        ]).encode("ascii")
        for nt, usn in search_targets(headers.get("st"))
    ]


def notify_messages(port, alive=True, addresses=None):
    """The announcements sent without being asked, one per local address: a
    player that was already open learns about us no other way."""
    if addresses is None:
        addresses = local_addresses()
    messages = []
    for address in addresses:
        for nt, usn in search_targets(None):
            headers = [("HOST", "%s:%d" % (SSDP_ADDRESS, SSDP_PORT))]
            if alive:
                headers += [
                    ("CACHE-CONTROL", "max-age=%d" % MAX_AGE),
                    ("LOCATION", description_url(address, port)),
                    ("SERVER", SERVER_NAME),
                ]
            headers += [
                ("NT", nt),
                ("NTS", "ssdp:alive" if alive else "ssdp:byebye"),
                ("USN", usn),
                ("BOOTID.UPNP.ORG", BOOT_ID),
                ("CONFIGID.UPNP.ORG", 1),
            ]
            messages.append(_message("NOTIFY * HTTP/1.1", headers).encode("ascii"))
    return messages


_addresses_cache = {"at": 0.0, "value": []}


def route_address():
    """The address the default route goes out of, or ""."""
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((SSDP_ADDRESS, SSDP_PORT))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        if probe is not None:
            probe.close()


def local_addresses(cache_seconds=30):
    """Every IPv4 address of this machine, the routing one first."""
    now = time.monotonic()
    if _addresses_cache["value"] and now - _addresses_cache["at"] < cache_seconds:
        return list(_addresses_cache["value"])
    found = [route_address()]
    try:
        result = subprocess.run(["ip", "-4", "-brief", "addr"],
                                capture_output=True, text=True, timeout=5)
        found += re.findall(r"(\d+\.\d+\.\d+\.\d+)/\d+", result.stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    ordered = []
    for address in found:
        if address and not address.startswith("127.") and address not in ordered:
            ordered.append(address)
    _addresses_cache.update(at=now, value=ordered)
    return list(ordered)


def pick_address(sender, addresses=None):
    """The address a client should be pointed at: the one on its own network
    first, since the access point and the home network are two different ones."""
    addresses = local_addresses() if addresses is None else list(addresses)
    if not addresses:
        return ""
    network = str(sender).rsplit(".", 1)[0]
    for address in addresses:
        if address.rsplit(".", 1)[0] == network:
            return address
    return addresses[0]


class Responder:
    """The SSDP side: answers searches, and says who it is without being asked."""

    def __init__(self, port, ssdp_port=SSDP_PORT, interval=NOTIFY_INTERVAL):
        self.port = int(port)
        self.ssdp_port = int(ssdp_port)
        self.interval = interval
        self.socket = None
        self.thread = None
        self.answers = 0
        self._stop = threading.Event()

    def start(self):
        """Binds the SSDP port and starts answering; False when it is taken."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", self.ssdp_port))
            # One membership per interface: a device of the access point sends
            # its search on an interface the kernel would otherwise drop it on.
            for address in local_addresses() or ["0.0.0.0"]:
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                    struct.pack("4s4s", socket.inet_aton(SSDP_ADDRESS),
                                                socket.inet_aton(address)))
                except OSError:
                    continue
            sock.settimeout(1.0)
        except OSError as e:
            log.warning("UPnP: no discovery, cannot listen on port %s (%s)",
                        self.ssdp_port, e)
            return False
        self.socket = sock
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="upnp-ssdp", daemon=True)
        self.thread.start()
        log.info("UPnP: listening for players on port %s", self.ssdp_port)
        return True

    def stop(self):
        if self.socket is None:
            return
        self._stop.set()
        self._announce(alive=False)
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.socket.close()
        self.socket = None
        self.thread = None

    def _send(self, message, address):
        try:
            self.socket.sendto(message, address)
        except OSError as e:
            log.debug("UPnP: could not answer %s (%s)", address, e)

    def _announce(self, alive):
        for message in notify_messages(self.port, alive=alive):
            self._send(message, (SSDP_ADDRESS, self.ssdp_port))

    def _answer(self, data, sender):
        replies = search_replies(data, sender[0], self.port)
        for reply in replies:
            self._send(reply, sender)
        self.answers += len(replies)
        return len(replies)

    def _run(self):
        self._announce(alive=True)
        next_alive = time.monotonic() + ANNOUNCE_AGAIN_SEC
        while not self._stop.is_set():
            try:
                data, sender = self.socket.recvfrom(2048)
            except socket.timeout:
                data, sender = None, None
            except OSError:
                break
            if data:
                self._answer(data, sender)
            if time.monotonic() >= next_alive:
                self._announce(alive=True)
                next_alive = time.monotonic() + self.interval


_responder = None
_responder_lock = threading.Lock()


def start(port, ssdp_port=SSDP_PORT):
    """Starts the discovery listener once; a second call keeps the first."""
    global _responder
    with _responder_lock:
        if _responder is not None:
            return _responder
        responder = Responder(port, ssdp_port=ssdp_port)
        _responder = responder if responder.start() else None
        return _responder


def stop():
    global _responder
    with _responder_lock:
        responder, _responder = _responder, None
    if responder is not None:
        responder.stop()


def running():
    return _responder is not None


def arguments_of(body):
    """{name: text} of a SOAP request's action arguments, whatever prefixes it uses."""
    try:
        root = ElementTree.fromstring(body or "")
    except ElementTree.ParseError:
        return {}
    found = {}
    for element in root.iter():
        if list(element):
            continue
        found.setdefault(element.tag.rsplit("}", 1)[-1], element.text or "")
    return found


def action_of(header, body):
    """The action asked for: the SOAPAction header, or the body's own name."""
    if header:
        return header.split("#")[-1].strip().strip('"').strip("'")
    try:
        root = ElementTree.fromstring(body or "")
    except ElementTree.ParseError:
        return ""
    for element in root.iter():
        if list(element) and element.tag.rsplit("}", 1)[-1] not in ("Body", "Envelope"):
            return element.tag.rsplit("}", 1)[-1]
    return ""


def didl(items, start=0):
    """The DIDL-Lite list of the given items - what a player reads to play one."""
    parts = [DIDL_HEADER]
    for index, item in enumerate(items):
        mime = str(item.get("mime") or DEFAULT_MIME).split(";")[0].strip()
        parts.append(
            '<item id="%d" parentID="%s" restricted="1">'
            "<dc:title>%s</dc:title><upnp:class>%s</upnp:class>"
            '<res protocolInfo="http-get:*:%s:*">%s</res></item>'
            % (start + index + 1, ROOT_ID, escape(str(item.get("title") or FRIENDLY_NAME)),
               escape(str(item.get("class") or AUDIO_CLASS)), escape(mime),
               escape(str(item.get("url") or "")))
        )
    return "".join(parts) + "</DIDL-Lite>"


def container_xml(children):
    """The root container, which is what a player browses first."""
    return (
        '<container id="%s" parentID="-1" restricted="1" searchable="0" childCount="%d">'
        "<dc:title>%s</dc:title><upnp:class>%s</upnp:class></container>"
        % (ROOT_ID, children, escape(ROOT_TITLE), CONTAINER_CLASS)
    )


def fault(code, description):
    """(500, XML) for a call we will not answer, in the shape clients expect."""
    inner = (
        "<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>"
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        "<errorCode>%d</errorCode><errorDescription>%s</errorDescription>"
        "</UPnPError></detail></s:Fault>" % (code, escape(description))
    )
    return 500, SOAP_ENVELOPE % inner


def _envelope(service_type, action, inner):
    return SOAP_ENVELOPE % ("<%s xmlns:u=%s>%s</%s>"
                            % (action, quoteattr(service_type), inner, action))


def _browse_response(result, returned, total):
    return _envelope(CONTENT_DIRECTORY, "BrowseResponse",
                     "<Result>%s</Result><NumberReturned>%d</NumberReturned>"
                     "<TotalMatches>%d</TotalMatches><UpdateID>1</UpdateID>"
                     % (escape(result), returned, total))


def _number(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def browse(arguments, items):
    """The ContentDirectory Browse answer: the stream when it is on, nothing
    when it is not - an entry that plays silence is worse than none."""
    object_id = str(arguments.get("ObjectID") or ROOT_ID).strip()
    flag = str(arguments.get("BrowseFlag") or "BrowseDirectChildren").strip()
    if object_id == ROOT_ID:
        if flag == "BrowseMetadata":
            return 200, _browse_response(container_xml(len(items)), 1, 1)
        start = max(_number(arguments.get("StartingIndex")), 0)
        count = _number(arguments.get("RequestedCount"))
        page = items[start:] if count <= 0 else items[start:start + count]
        return 200, _browse_response(didl(page, start=start), len(page), len(items))
    if object_id == str(int(ROOT_ID) + 1) and flag == "BrowseMetadata" and items:
        return 200, _browse_response(didl(items[:1]), 1, 1)
    return fault(701, "No such object")


def handle(service_type, action, body, items):
    """(status, XML) for one control request; `items` is what is worth offering."""
    arguments = arguments_of(body)
    if service_type == CONTENT_DIRECTORY:
        if action == "Browse":
            return browse(arguments, items)
        if action == "GetSortCapabilities":
            return 200, _envelope(service_type, "GetSortCapabilitiesResponse",
                                  "<SortCaps>dc:title</SortCaps>")
        if action == "GetSearchCapabilities":
            return 200, _envelope(service_type, "GetSearchCapabilitiesResponse",
                                  "<SearchCaps></SearchCaps>")
        if action == "GetSystemUpdateID":
            return 200, _envelope(service_type, "GetSystemUpdateIDResponse", "<Id>1</Id>")
    if service_type == CONNECTION_MANAGER:
        if action == "GetProtocolInfo":
            source = list(dict.fromkeys("http-get:*:%s:*"
                                        % str(item.get("mime") or DEFAULT_MIME).split(";")[0].strip()
                                        for item in items))
            return 200, _envelope(service_type, "GetProtocolInfoResponse",
                                  "<Source>%s</Source><Sink></Sink>"
                                  % escape(",".join(source or ["http-get:*:*:*"])))
        if action == "GetCurrentConnectionIDs":
            return 200, _envelope(service_type, "GetCurrentConnectionIDsResponse",
                                  "<ConnectionIDs>0</ConnectionIDs>")
        if action == "GetCurrentConnectionInfo":
            return 200, _envelope(
                service_type, "GetCurrentConnectionInfoResponse",
                "<RcsID>0</RcsID><AVTransportID>0</AVTransportID>"
                "<ProtocolInfo></ProtocolInfo><PeerConnectionManager></PeerConnectionManager>"
                "<PeerConnectionID>-1</PeerConnectionID><Direction>Output</Direction>"
                "<Status>OK</Status>")
    return fault(401, "Invalid Action")


CONTENT_DIRECTORY_SCPD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
    "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>"
    "<action><name>Browse</name><argumentList>"
    "<argument><name>ObjectID</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>"
    "<argument><name>BrowseFlag</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>"
    "<argument><name>Filter</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>"
    "<argument><name>StartingIndex</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>"
    "<argument><name>RequestedCount</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>"
    "<argument><name>SortCriteria</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>"
    "<argument><name>Result</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>"
    "<argument><name>NumberReturned</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>"
    "<argument><name>TotalMatches</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>"
    "<argument><name>UpdateID</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>"
    "</argumentList></action>"
    "<action><name>GetSortCapabilities</name><argumentList>"
    "<argument><name>SortCaps</name><direction>out</direction>"
    "<relatedStateVariable>SortCapabilities</relatedStateVariable></argument>"
    "</argumentList></action>"
    "<action><name>GetSearchCapabilities</name><argumentList>"
    "<argument><name>SearchCaps</name><direction>out</direction>"
    "<relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>"
    "</argumentList></action>"
    "<action><name>GetSystemUpdateID</name><argumentList>"
    "<argument><name>Id</name><direction>out</direction>"
    "<relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>"
    "</argumentList></action>"
    "</actionList><serviceStateTable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name>'
    "<dataType>string</dataType><allowedValueList>"
    "<allowedValue>BrowseMetadata</allowedValue>"
    "<allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name>'
    "<dataType>ui4</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name>'
    "<dataType>ui4</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name>'
    "<dataType>ui4</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>SortCapabilities</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>SearchCapabilities</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="yes"><name>SystemUpdateID</name>'
    "<dataType>ui4</dataType></stateVariable>"
    "</serviceStateTable></scpd>"
)

CONNECTION_MANAGER_SCPD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
    "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>"
    "<action><name>GetProtocolInfo</name><argumentList>"
    "<argument><name>Source</name><direction>out</direction>"
    "<relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>"
    "<argument><name>Sink</name><direction>out</direction>"
    "<relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>"
    "</argumentList></action>"
    "<action><name>GetCurrentConnectionIDs</name><argumentList>"
    "<argument><name>ConnectionIDs</name><direction>out</direction>"
    "<relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>"
    "</argumentList></action>"
    "<action><name>GetCurrentConnectionInfo</name><argumentList>"
    "<argument><name>ConnectionID</name><direction>in</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>"
    "<argument><name>RcsID</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>"
    "<argument><name>AVTransportID</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>"
    "<argument><name>ProtocolInfo</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>"
    "<argument><name>PeerConnectionManager</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>"
    "<argument><name>PeerConnectionID</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>"
    "<argument><name>Direction</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>"
    "<argument><name>Status</name><direction>out</direction>"
    "<relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>"
    "</argumentList></action>"
    "</actionList><serviceStateTable>"
    '<stateVariable sendEvents="yes"><name>SourceProtocolInfo</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="yes"><name>SinkProtocolInfo</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="yes"><name>CurrentConnectionIDs</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name>'
    "<dataType>string</dataType><allowedValueList><allowedValue>OK</allowedValue>"
    "<allowedValue>ContentFormatMismatch</allowedValue>"
    "<allowedValue>InsufficientBandwidth</allowedValue>"
    "<allowedValue>UnreliableChannel</allowedValue>"
    "<allowedValue>Unknown</allowedValue></allowedValueList></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name>'
    "<dataType>string</dataType><allowedValueList><allowedValue>Input</allowedValue>"
    "<allowedValue>Output</allowedValue></allowedValueList></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name>'
    "<dataType>string</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name>'
    "<dataType>i4</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name>'
    "<dataType>i4</dataType></stateVariable>"
    '<stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name>'
    "<dataType>i4</dataType></stateVariable>"
    "</serviceStateTable></scpd>"
)

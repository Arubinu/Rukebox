"""UPnP MediaRenderer: Home Assistant and any DLNA controller see the radio as a media player."""
# SetAVTransportURI exists only while `receiving` is on; changes reach controllers as GENA events.

import http.client
import logging
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ElementTree
from xml.sax.saxutils import escape, quoteattr

import upnp

log = logging.getLogger("web")

PATH = "/dlna"
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"
AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
CONNECTION_MANAGER = upnp.CONNECTION_MANAGER
SERVICES = (
    ("AVTransport", AV_TRANSPORT, "urn:upnp-org:serviceId:AVTransport"),
    ("RenderingControl", RENDERING_CONTROL, "urn:upnp-org:serviceId:RenderingControl"),
    ("ConnectionManager", CONNECTION_MANAGER, "urn:upnp-org:serviceId:ConnectionManager"),
)
DEVICE = upnp.Device(DEVICE_TYPE, SERVICES, PATH, "rukebox-dlna-")
SUBSCRIPTION_SEC = 1800
SUBSCRIPTIONS_MAX = 32
CALLBACKS_MAX = 3
# What mpv plays, said the way a controller checks a file before it sends it.
SINK_PROTOCOLS = ",".join("http-get:*:%s:*" % mime for mime in (
    "audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac", "audio/ogg", "audio/opus", "audio/wav",
    "audio/x-wav", "audio/aac", "audio/mp4", "audio/x-m4a", "audio/L16", "audio/webm",
    "application/ogg"))
# Whether what is sent is played (DLNA_RENDERER's companion setting), and what was
# sent but not played yet: the web server sets one, a controller the other.
options = {"receiving": False}
_pending = {}
_pending_lock = threading.Lock()
NOTIFY_TIMEOUT_SEC = 3
TRACK_CLASS = "object.item.audioItem.musicTrack"
# What the radio plays by itself rather than music: still "playing" to a controller.
SOUND_MODES = ("meme", "cutoff_announce", "speech", "game")


def service_type(name):
    for service_name, kind, _id in SERVICES:
        if service_name == name:
            return kind
    return None


def device_description():
    services = "".join(
        "<service><serviceType>%s</serviceType><serviceId>%s</serviceId>"
        "<SCPDURL>%s/%s/scpd.xml</SCPDURL><controlURL>%s/%s/control</controlURL>"
        "<eventSubURL>%s/%s/event</eventSubURL></service>"
        % (kind, service_id, PATH, name, PATH, name, PATH, name)
        for name, kind, service_id in SERVICES)
    name = escape(upnp.device_name())
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
        "<deviceType>%s</deviceType><friendlyName>%s</friendlyName>"
        "<manufacturer>Rukebox</manufacturer><modelName>Rukebox</modelName>"
        "<modelDescription>Rukebox radio</modelDescription>"
        "<dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>"
        "<UDN>%s</UDN><serviceList>%s</serviceList></device></root>"
    ) % (DEVICE_TYPE, name, DEVICE.udn(), services)


AVT_ACTIONS = [
    ("GetMediaInfo", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                      ("NrTracks", "out", "NumberOfTracks"),
                      ("MediaDuration", "out", "CurrentMediaDuration"),
                      ("CurrentURI", "out", "AVTransportURI"),
                      ("CurrentURIMetaData", "out", "AVTransportURIMetaData"),
                      ("NextURI", "out", "NextAVTransportURI"),
                      ("NextURIMetaData", "out", "NextAVTransportURIMetaData"),
                      ("PlayMedium", "out", "PlaybackStorageMedium"),
                      ("RecordMedium", "out", "RecordStorageMedium"),
                      ("WriteStatus", "out", "RecordMediumWriteStatus")]),
    ("GetTransportInfo", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                          ("CurrentTransportState", "out", "TransportState"),
                          ("CurrentTransportStatus", "out", "TransportStatus"),
                          ("CurrentSpeed", "out", "TransportPlaySpeed")]),
    ("GetPositionInfo", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                         ("Track", "out", "CurrentTrack"),
                         ("TrackDuration", "out", "CurrentTrackDuration"),
                         ("TrackMetaData", "out", "CurrentTrackMetaData"),
                         ("TrackURI", "out", "CurrentTrackURI"),
                         ("RelTime", "out", "RelativeTimePosition"),
                         ("AbsTime", "out", "AbsoluteTimePosition"),
                         ("RelCount", "out", "RelativeCounterPosition"),
                         ("AbsCount", "out", "AbsoluteCounterPosition")]),
    ("GetDeviceCapabilities", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                               ("PlayMedia", "out", "PossiblePlaybackStorageMedia"),
                               ("RecMedia", "out", "PossibleRecordStorageMedia"),
                               ("RecQualityModes", "out", "PossibleRecordQualityModes")]),
    ("GetTransportSettings", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                              ("PlayMode", "out", "CurrentPlayMode"),
                              ("RecQualityMode", "out", "CurrentRecordQualityMode")]),
    ("GetCurrentTransportActions", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                                    ("Actions", "out", "CurrentTransportActions")]),
    ("SetAVTransportURI", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                           ("CurrentURI", "in", "AVTransportURI"),
                           ("CurrentURIMetaData", "in", "AVTransportURIMetaData")]),
    ("Play", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"), ("Speed", "in", "TransportPlaySpeed")]),
    ("Pause", [("InstanceID", "in", "A_ARG_TYPE_InstanceID")]),
    ("Stop", [("InstanceID", "in", "A_ARG_TYPE_InstanceID")]),
    ("Next", [("InstanceID", "in", "A_ARG_TYPE_InstanceID")]),
    ("Previous", [("InstanceID", "in", "A_ARG_TYPE_InstanceID")]),
]
AVT_VARIABLES = [
    ("TransportState", "string", False, ("STOPPED", "PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING",
                                         "NO_MEDIA_PRESENT")),
    ("TransportStatus", "string", False, ("OK", "ERROR_OCCURRED")),
    ("PlaybackStorageMedium", "string", False, ("NETWORK", "NONE")),
    ("RecordStorageMedium", "string", False, ("NOT_IMPLEMENTED",)),
    ("PossiblePlaybackStorageMedia", "string", False, None),
    ("PossibleRecordStorageMedia", "string", False, None),
    ("CurrentPlayMode", "string", False, ("NORMAL",)),
    ("TransportPlaySpeed", "string", False, ("1",)),
    ("RecordMediumWriteStatus", "string", False, ("NOT_IMPLEMENTED",)),
    ("PossibleRecordQualityModes", "string", False, None),
    ("CurrentRecordQualityMode", "string", False, ("NOT_IMPLEMENTED",)),
    ("NumberOfTracks", "ui4", False, None),
    ("CurrentTrack", "ui4", False, None),
    ("CurrentTrackDuration", "string", False, None),
    ("CurrentMediaDuration", "string", False, None),
    ("CurrentTrackMetaData", "string", False, None),
    ("CurrentTrackURI", "string", False, None),
    ("AVTransportURI", "string", False, None),
    ("AVTransportURIMetaData", "string", False, None),
    ("NextAVTransportURI", "string", False, None),
    ("NextAVTransportURIMetaData", "string", False, None),
    ("RelativeTimePosition", "string", False, None),
    ("AbsoluteTimePosition", "string", False, None),
    ("RelativeCounterPosition", "i4", False, None),
    ("AbsoluteCounterPosition", "i4", False, None),
    ("CurrentTransportActions", "string", False, None),
    ("LastChange", "string", True, None),
    ("A_ARG_TYPE_InstanceID", "ui4", False, None),
]
RC_ACTIONS = [
    ("ListPresets", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                     ("CurrentPresetNameList", "out", "PresetNameList")]),
    ("SelectPreset", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"),
                      ("PresetName", "in", "A_ARG_TYPE_PresetName")]),
    ("GetMute", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"), ("Channel", "in", "A_ARG_TYPE_Channel"),
                 ("CurrentMute", "out", "Mute")]),
    ("SetMute", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"), ("Channel", "in", "A_ARG_TYPE_Channel"),
                 ("DesiredMute", "in", "Mute")]),
    ("GetVolume", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"), ("Channel", "in", "A_ARG_TYPE_Channel"),
                   ("CurrentVolume", "out", "Volume")]),
    ("SetVolume", [("InstanceID", "in", "A_ARG_TYPE_InstanceID"), ("Channel", "in", "A_ARG_TYPE_Channel"),
                   ("DesiredVolume", "in", "Volume")]),
]
RC_VARIABLES = [
    ("PresetNameList", "string", False, None),
    ("Mute", "boolean", False, None),
    ("Volume", "ui2", False, "range"),
    ("LastChange", "string", True, None),
    ("A_ARG_TYPE_Channel", "string", False, ("Master",)),
    ("A_ARG_TYPE_InstanceID", "ui4", False, None),
    ("A_ARG_TYPE_PresetName", "string", False, ("FactoryDefaults",)),
]
CM_ACTIONS = [
    ("GetProtocolInfo", [("Source", "out", "SourceProtocolInfo"), ("Sink", "out", "SinkProtocolInfo")]),
    ("GetCurrentConnectionIDs", [("ConnectionIDs", "out", "CurrentConnectionIDs")]),
    ("GetCurrentConnectionInfo", [("ConnectionID", "in", "A_ARG_TYPE_ConnectionID"),
                                  ("RcsID", "out", "A_ARG_TYPE_RcsID"),
                                  ("AVTransportID", "out", "A_ARG_TYPE_AVTransportID"),
                                  ("ProtocolInfo", "out", "A_ARG_TYPE_ProtocolInfo"),
                                  ("PeerConnectionManager", "out", "A_ARG_TYPE_ConnectionManager"),
                                  ("PeerConnectionID", "out", "A_ARG_TYPE_ConnectionID"),
                                  ("Direction", "out", "A_ARG_TYPE_Direction"),
                                  ("Status", "out", "A_ARG_TYPE_ConnectionStatus")]),
]
CM_VARIABLES = [
    ("SourceProtocolInfo", "string", True, None),
    ("SinkProtocolInfo", "string", True, None),
    ("CurrentConnectionIDs", "string", True, None),
    ("A_ARG_TYPE_ConnectionStatus", "string", False, ("OK", "ContentFormatMismatch",
                                                      "InsufficientBandwidth", "UnreliableChannel", "Unknown")),
    ("A_ARG_TYPE_ConnectionManager", "string", False, None),
    ("A_ARG_TYPE_Direction", "string", False, ("Input", "Output")),
    ("A_ARG_TYPE_ProtocolInfo", "string", False, None),
    ("A_ARG_TYPE_ConnectionID", "i4", False, None),
    ("A_ARG_TYPE_AVTransportID", "i4", False, None),
    ("A_ARG_TYPE_RcsID", "i4", False, None),
]
SCPD = {"AVTransport": (AVT_ACTIONS, AVT_VARIABLES), "RenderingControl": (RC_ACTIONS, RC_VARIABLES),
        "ConnectionManager": (CM_ACTIONS, CM_VARIABLES)}


def service_description(name):
    if name not in SCPD:
        return None
    actions, variables = SCPD[name]
    if not options["receiving"]:
        actions = [one for one in actions if one[0] != "SetAVTransportURI"]
    out = ['<?xml version="1.0" encoding="utf-8"?><scpd xmlns="urn:schemas-upnp-org:service-1-0">'
           "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>"]
    for action, arguments in actions:
        out.append("<action><name>%s</name><argumentList>" % action)
        out += ["<argument><name>%s</name><direction>%s</direction>"
                "<relatedStateVariable>%s</relatedStateVariable></argument>" % argument
                for argument in arguments]
        out.append("</argumentList></action>")
    out.append("</actionList><serviceStateTable>")
    for variable, kind, events, allowed in variables:
        out.append('<stateVariable sendEvents="%s"><name>%s</name><dataType>%s</dataType>'
                   % ("yes" if events else "no", variable, kind))
        if allowed == "range":
            out.append("<allowedValueRange><minimum>0</minimum><maximum>100</maximum>"
                       "<step>1</step></allowedValueRange>")
        elif allowed:
            out.append("<allowedValueList>%s</allowedValueList>"
                       % "".join("<allowedValue>%s</allowedValue>" % value for value in allowed))
        out.append("</stateVariable>")
    out.append("</serviceStateTable></scpd>")
    return "".join(out)


def clock(seconds):
    """H:MM:SS, the only time format a controller reads."""
    seconds = max(0, int(round(float(seconds or 0))))
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def transport_state(status):
    mode = str(status.get("mode") or "idle")
    if mode in ("music", "cast"):
        return "PAUSED_PLAYBACK" if status.get("paused") else "PLAYING"
    if mode == "restarting":
        return "TRANSITIONING"
    if mode in SOUND_MODES or mode.startswith(("custom:", "button_announce:")):
        return "PLAYING"
    return "STOPPED"


def metadata(title, artist, album, cover_url, duration, track_url):
    """The DIDL-Lite of the song playing: what a controller shows."""
    if not title:
        return ""
    parts = [upnp.DIDL_HEADER, '<item id="0" parentID="-1" restricted="1">',
             "<dc:title>%s</dc:title>" % escape(title)]
    if artist:
        parts.append("<dc:creator>%s</dc:creator><upnp:artist>%s</upnp:artist>"
                     % (escape(artist), escape(artist)))
    if album:
        parts.append("<upnp:album>%s</upnp:album>" % escape(album))
    if cover_url:
        parts.append("<upnp:albumArtURI>%s</upnp:albumArtURI>" % escape(cover_url))
    parts.append("<upnp:class>%s</upnp:class>" % TRACK_CLASS)
    parts.append('<res protocolInfo="http-get:*:audio/mpeg:*" duration=%s>%s</res>'
                 % (quoteattr(clock(duration) + ".000"), escape(track_url)))
    parts.append("</item></DIDL-Lite>")
    return "".join(parts)


def state(status, tags, base, key):
    """{variable: value} of the three services, from the daemon's status."""
    transport = transport_state(status)
    path = status.get("current_track_path") or ""
    tags, key = (tags or {}, key) if path else ({}, "")
    title = tags.get("title") or (path.rsplit("/", 1)[-1].rsplit(".", 1)[0] if path else "")
    sound = status.get("sound_announcement") or status.get("sound")
    if transport == "PLAYING" and status.get("mode") != "music" and sound:
        title = str(sound)
    duration = status.get("duration") if path else 0
    # The key only tells one song's picture from the next one's, for a controller that caches it.
    mark = urllib.parse.quote(key or "now")
    track_url = "%s%s/track?k=%s" % (base, PATH, mark) if path else ""
    cover_url = "%s%s/cover.jpg?k=%s" % (base, PATH, mark) if path else ""
    artist, album = tags.get("artist") or "", tags.get("album") or ""
    cast = status.get("cast") if status.get("mode") == "cast" else None
    if cast:
        title = cast.get("title") or cast.get("url", "").rsplit("/", 1)[-1]
        artist, album = cast.get("artist") or "", cast.get("album") or ""
        track_url, cover_url = cast.get("url") or "", cast.get("art") or ""
        duration = status.get("duration") or 0
        path = track_url
    meta = metadata(title, artist, album, cover_url, duration, track_url)
    if cast:
        actions = "Pause,Stop" if transport == "PLAYING" else "Play,Stop"
    elif transport == "PLAYING":
        actions = "Pause,Stop,Next,Previous"
    elif transport == "PAUSED_PLAYBACK":
        actions = "Play,Stop,Next,Previous"
    else:
        actions = "Play,Next,Previous"
    if pending() and "Play" not in actions:
        # Something was sent: a controller waits for Play before it asks for it.
        actions = "Play," + actions
    return {
        "TransportState": transport,
        "TransportStatus": "OK",
        "TransportPlaySpeed": "1",
        "CurrentPlayMode": "NORMAL",
        "PlaybackStorageMedium": "NETWORK" if meta else "NONE",
        "NumberOfTracks": "1" if meta else "0",
        "CurrentTrack": "1" if meta else "0",
        "CurrentTrackDuration": clock(duration),
        "CurrentMediaDuration": clock(duration),
        "CurrentTrackMetaData": meta,
        "CurrentTrackURI": track_url,
        "AVTransportURI": track_url,
        "AVTransportURIMetaData": meta,
        "CurrentTransportActions": actions,
        "RelativeTimePosition": clock(status.get("position") if path else 0),
        "Volume": str(int(round(float(status.get("volume") or 0)))),
        "Mute": "1" if status.get("muted") else "0",
        "Casting": bool(cast),
    }


# What each service carries in its LastChange, in the order a controller reads it.
EVENTED = {
    "AVTransport": ("TransportState", "TransportStatus", "TransportPlaySpeed", "CurrentPlayMode",
                    "PlaybackStorageMedium", "NumberOfTracks", "CurrentTrack", "CurrentTrackDuration",
                    "CurrentMediaDuration", "CurrentTrackMetaData", "CurrentTrackURI", "AVTransportURI",
                    "AVTransportURIMetaData", "CurrentTransportActions"),
    "RenderingControl": ("Volume", "Mute"),
}
EVENT_NAMESPACE = {"AVTransport": "urn:schemas-upnp-org:metadata-1-0/AVT/",
                   "RenderingControl": "urn:schemas-upnp-org:metadata-1-0/RCS/"}


def last_change(service, values):
    """The LastChange document of one service for these variables."""
    inner = []
    for name, value in values.items():
        channel = ' channel="Master"' if service == "RenderingControl" else ""
        inner.append("<%s%s val=%s/>" % (name, channel, quoteattr(str(value))))
    return '<Event xmlns="%s"><InstanceID val="0">%s</InstanceID></Event>' % (
        EVENT_NAMESPACE[service], "".join(inner))


def property_set(variables):
    """The body of a NOTIFY: each variable, its value escaped."""
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">%s</e:propertyset>'
            % "".join("<e:property><%s>%s</%s></e:property>" % (name, escape(value), name)
                      for name, value in variables.items()))


def connection_variables():
    sink = SINK_PROTOCOLS if options["receiving"] else ""
    return {"SourceProtocolInfo": "", "SinkProtocolInfo": sink, "CurrentConnectionIDs": "0"}


def pending():
    """Whether something was sent and waits for Play."""
    with _pending_lock:
        return bool(_pending)


def sent_metadata(text):
    """{title, artist, album, art} of the DIDL-Lite a controller sent with its address."""
    found = {}
    root = upnp.parse_xml(text)
    if root is None:
        return found
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        value = (element.text or "").strip()
        if not value:
            continue
        if tag == "title":
            found.setdefault("title", value)
        elif tag in ("artist", "creator"):
            found.setdefault("artist", value)
        elif tag == "album":
            found.setdefault("album", value)
        elif tag == "albumArtURI":
            found.setdefault("art", value)
    return found


def _reply(name, action, values):
    kind = service_type(name)
    inner = "".join("<%s>%s</%s>" % (key, escape(str(value)), key) for key, value in values)
    return 200, upnp.SOAP_ENVELOPE % ("<u:%sResponse xmlns:u=%s>%s</u:%sResponse>"
                                       % (action, quoteattr(kind), inner, action))


def handle(name, action, arguments, now, send):
    """(status, XML) for one control request. `now` is state(); `send(command,
    **arguments)` hands the daemon a command and answers its reply."""
    if name == "AVTransport":
        if action == "GetTransportInfo":
            return _reply(name, action, [("CurrentTransportState", now["TransportState"]),
                                         ("CurrentTransportStatus", "OK"), ("CurrentSpeed", "1")])
        if action == "GetPositionInfo":
            return _reply(name, action, [
                ("Track", now["CurrentTrack"]), ("TrackDuration", now["CurrentTrackDuration"]),
                ("TrackMetaData", now["CurrentTrackMetaData"]), ("TrackURI", now["CurrentTrackURI"]),
                ("RelTime", now["RelativeTimePosition"]), ("AbsTime", now["RelativeTimePosition"]),
                ("RelCount", 2147483647), ("AbsCount", 2147483647)])
        if action == "GetMediaInfo":
            return _reply(name, action, [
                ("NrTracks", now["NumberOfTracks"]), ("MediaDuration", now["CurrentMediaDuration"]),
                ("CurrentURI", now["AVTransportURI"]), ("CurrentURIMetaData", now["AVTransportURIMetaData"]),
                ("NextURI", ""), ("NextURIMetaData", ""), ("PlayMedium", now["PlaybackStorageMedium"]),
                ("RecordMedium", "NOT_IMPLEMENTED"), ("WriteStatus", "NOT_IMPLEMENTED")])
        if action == "GetDeviceCapabilities":
            return _reply(name, action, [("PlayMedia", "NETWORK"), ("RecMedia", "NOT_IMPLEMENTED"),
                                         ("RecQualityModes", "NOT_IMPLEMENTED")])
        if action == "GetTransportSettings":
            return _reply(name, action, [("PlayMode", "NORMAL"), ("RecQualityMode", "NOT_IMPLEMENTED")])
        if action == "GetCurrentTransportActions":
            return _reply(name, action, [("Actions", now["CurrentTransportActions"])])
        if action == "SetAVTransportURI":
            if not options["receiving"]:
                return upnp.fault(401, "Invalid Action")
            url = str(arguments.get("CurrentURI") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                return upnp.fault(714, "Illegal MIME-type")
            with _pending_lock:
                _pending.clear()
                _pending.update(sent_metadata(arguments.get("CurrentURIMetaData")), url=url)
            return _reply(name, action, [])
        if action == "Play" and options["receiving"] and pending():
            with _pending_lock:
                sent = dict(_pending)
                _pending.clear()
            answer = send("cast_play", **sent)
            if not answer.get("ok"):
                return upnp.fault(701, "Transition not available")
            return _reply(name, action, [])
        if action in ("Play", "Pause", "Stop", "Next", "Previous") and now.get("Casting"):
            command = "cast_stop" if action == "Stop" else None
            if action in ("Play", "Pause") and (action == "Play") == (now["TransportState"] == "PAUSED_PLAYBACK"):
                command = "toggle_pause"
            if action in ("Next", "Previous"):
                return upnp.fault(701, "Transition not available")
            if command and not send(command).get("ok"):
                return upnp.fault(701, "Transition not available")
            return _reply(name, action, [])
        if action in ("Play", "Pause", "Stop", "Next", "Previous"):
            command = {"Next": "next_track", "Previous": "previous_track"}.get(action)
            if action == "Play":
                if now["TransportState"] == "PAUSED_PLAYBACK":
                    command = "toggle_pause"
                elif now["TransportState"] == "STOPPED":
                    command = "start_music"
            elif now["TransportState"] == "PLAYING" and action in ("Pause", "Stop"):
                command = "toggle_pause"
            if command:
                answer = send(command)
                if not answer.get("ok"):
                    return upnp.fault(701, "Transition not available")
            return _reply(name, action, [])
    if name == "RenderingControl":
        if action == "GetVolume":
            return _reply(name, action, [("CurrentVolume", now["Volume"])])
        if action == "GetMute":
            return _reply(name, action, [("CurrentMute", now["Mute"])])
        if action == "SetVolume":
            try:
                value = max(0, min(100, int(str(arguments.get("DesiredVolume", "")).strip())))
            except ValueError:
                return upnp.fault(402, "Invalid Args")
            send("set_volume", value=float(value))
            return _reply(name, action, [])
        if action == "SetMute":
            wanted = str(arguments.get("DesiredMute", "")).strip().lower() in ("1", "true", "yes")
            send("set_mute", on=wanted)
            return _reply(name, action, [])
        if action == "ListPresets":
            return _reply(name, action, [("CurrentPresetNameList", "FactoryDefaults")])
        if action == "SelectPreset":
            return _reply(name, action, [])
    if name == "ConnectionManager":
        if action == "GetProtocolInfo":
            return _reply(name, action, [("Source", ""),
                                         ("Sink", SINK_PROTOCOLS if options["receiving"] else "")])
        if action == "GetCurrentConnectionIDs":
            return _reply(name, action, [("ConnectionIDs", "0")])
        if action == "GetCurrentConnectionInfo":
            return _reply(name, action, [("RcsID", 0), ("AVTransportID", 0), ("ProtocolInfo", ""),
                                         ("PeerConnectionManager", ""), ("PeerConnectionID", -1),
                                         ("Direction", "Output"), ("Status", "OK")])
    return upnp.fault(401, "Invalid Action")


def parse_callbacks(header):
    """The URLs of a CALLBACK header: <http://a/b><http://c/d>."""
    return [part for part in (header or "").replace(">", "<").split("<")
            if part.strip().startswith("http://")]


def own_callbacks(header, remote_addr):
    """The callbacks that point back at the controller asking, a few at most."""
    own = str(remote_addr or "")
    own = own[7:] if own.startswith("::ffff:") else own
    return [url for url in parse_callbacks(header)
            if urllib.parse.urlsplit(url.strip()).hostname == own][:CALLBACKS_MAX]


def parse_timeout(header):
    text = str(header or "").strip().lower()
    if text.startswith("second-"):
        try:
            return max(60, min(SUBSCRIPTION_SEC, int(text[7:])))
        except ValueError:
            pass
    return SUBSCRIPTION_SEC


def _post(url, sid, seq, body):
    """One NOTIFY to one controller; False when it did not take it."""
    parsed = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=NOTIFY_TIMEOUT_SEC)
    try:
        connection.request("NOTIFY", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                           body=body.encode("utf-8"),
                           headers={"CONTENT-TYPE": 'text/xml; charset="utf-8"', "NT": "upnp:event",
                                    "NTS": "upnp:propchange", "SID": sid, "SEQ": str(seq)})
        return 200 <= connection.getresponse().status < 300
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


class Events:
    """The controllers watching each service, and what they were last told."""

    def __init__(self, post=_post):
        self.post = post
        self.lock = threading.Lock()
        self.subscriptions = {}
        self.told = {}

    def subscribe(self, service, callbacks, timeout):
        sid = "uuid:%s" % uuid.uuid4()
        with self.lock:
            while len(self.subscriptions) >= SUBSCRIPTIONS_MAX:
                # Full: the one that would lapse first makes room.
                del self.subscriptions[min(self.subscriptions, key=lambda one: self.subscriptions[one]["expires"])]
            self.subscriptions[sid] = {"service": service, "callbacks": callbacks, "seq": 0,
                                       "expires": time.monotonic() + timeout}
        return sid

    def renew(self, sid, timeout):
        with self.lock:
            entry = self.subscriptions.get(sid)
            if entry is None:
                return False
            entry["expires"] = time.monotonic() + timeout
            return True

    def unsubscribe(self, sid):
        with self.lock:
            return self.subscriptions.pop(sid, None) is not None

    def watched(self):
        """Whether any controller is watching, now that the expired ones are gone."""
        now = time.monotonic()
        with self.lock:
            for sid in [sid for sid, entry in self.subscriptions.items() if entry["expires"] < now]:
                del self.subscriptions[sid]
            return bool(self.subscriptions)

    def _send(self, sid, variables):
        with self.lock:
            entry = self.subscriptions.get(sid)
            if entry is None:
                return
            seq = entry["seq"]
            entry["seq"] = (seq + 1) % 4294967296 or 1
            callbacks = list(entry["callbacks"])
        body = property_set(variables)
        for url in callbacks:
            if self.post(url, sid, seq, body):
                return

    def welcome(self, sid, now):
        """The first event of a subscription: everything its service carries."""
        with self.lock:
            entry = self.subscriptions.get(sid)
            service = entry["service"] if entry else None
        if service in EVENTED:
            values = {name: now[name] for name in EVENTED[service]}
            self._send(sid, {"LastChange": last_change(service, values)})
        elif service == "ConnectionManager":
            self._send(sid, connection_variables())

    def publish(self, now):
        """Tells every watcher what changed since it was last told."""
        for service, names in EVENTED.items():
            values = {name: now[name] for name in names}
            before = self.told.get(service)
            changed = {name: value for name, value in values.items()
                       if before is None or before.get(name) != value}
            self.told[service] = values
            if not changed or before is None:
                continue
            with self.lock:
                sids = [sid for sid, entry in self.subscriptions.items() if entry["service"] == service]
            for sid in sids:
                self._send(sid, {"LastChange": last_change(service, changed)})


_responder = None
_responder_lock = threading.Lock()
events = Events()


def start(port, ssdp_port=upnp.SSDP_PORT):
    """Announces the renderer once; a second call keeps the first."""
    global _responder
    with _responder_lock:
        if _responder is not None:
            return _responder
        responder = upnp.Responder(port, ssdp_port=ssdp_port, device=DEVICE)
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

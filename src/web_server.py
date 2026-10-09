#!/usr/bin/env python3
"""The web interface: Flask API and static files."""

import gzip
import ipaddress
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import signal
import shutil
import socket
import subprocess
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import uuid
import zipfile
import sqlite3
import urllib.request
import sys
from datetime import datetime, timedelta

from flask import Flask, Response, g, jsonify, redirect, request, send_file, send_from_directory, session

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import announcements  # noqa: E402
import audio_diag  # noqa: E402
import net_diag  # noqa: E402
import bt_codec  # noqa: E402
import config_bundle  # noqa: E402
import duplicates  # noqa: E402
import hidden_tracks  # noqa: E402
import json_file  # noqa: E402
import playlist  # noqa: E402
import track_order  # noqa: E402
import track_media  # noqa: E402
import captive_portal  # noqa: E402
import config_schema  # noqa: E402
import gpio_pins  # noqa: E402
import gpio_reset  # noqa: E402
import platform as platform_mod  # noqa: E402
import stream as stream_mod  # noqa: E402
import system_actions  # noqa: E402
import upnp  # noqa: E402
import web_auth  # noqa: E402
from config_and_scan import (DEFAULTS, get_music_list, load_config, scan_audio_dir,  # noqa: E402
                             update_config_file)
from control_client import send_control_command  # noqa: E402
from stats import StatsRecorder  # noqa: E402
import suggestions  # noqa: E402
import usb_storage  # noqa: E402
import wled  # noqa: E402
import audio_output  # noqa: E402
import bt_link  # noqa: E402
import library  # noqa: E402
import blind_test  # noqa: E402
import cards  # noqa: E402
import dj_intro  # noqa: E402
import likes  # noqa: E402
import music_lists  # noqa: E402
import prepared_media  # noqa: E402
import schedules  # noqa: E402
from version import is_newer, read_version_file, set_release  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [web] %(message)s")
log = logging.getLogger("web")


class _QuietReads(logging.Filter):
    """The page asks the API several times a minute and the journal is kept on
    the SD card: a read of /api/ that worked is not worth a line. Actions,
    errors, page loads and the phones' connectivity probes still are."""
    _READ = re.compile(r'"GET /api/[^"]*" (200|304) ')

    def filter(self, record):
        return not self._READ.search(record.getMessage())


logging.getLogger("werkzeug").addFilter(_QuietReads())

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

app = Flask(__name__, static_folder=None)


_cfg_cache = {"signature": None, "values": None}
_cfg_lock = threading.Lock()


def _config_signature():
    """What the configuration was read from, as (path, mtime, size)."""
    import config_file
    signature = []
    for path in list(config_file.YAML_FILE_CANDIDATES) + [config_file.ENV_FILE]:
        try:
            st = os.stat(path)
            signature.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            signature.append((path, None, None))
    return tuple(signature)


def cfg():
    # The file, never the environment: rukebox.env is frozen at service start.
    signature = _config_signature()
    with _cfg_lock:
        if _cfg_cache["signature"] == signature and _cfg_cache["values"] is not None:
            return dict(_cfg_cache["values"])
    values = load_config(env_overrides=False)
    with _cfg_lock:
        _cfg_cache.update(signature=signature, values=values)
    return dict(values)


def control(cmd, **kwargs):
    kwargs.setdefault("source", "web")
    return send_control_command(cfg()["CONTROL_SOCKET"], cmd, **kwargs)


def _effective_music_dir():
    """Where the library is right now: the USB key being played from, or the folder the
    settings name."""
    planned = cfg().get("MUSIC_DIR") or ""

    def from_daemon():
        result = control("get_status")
        data = (result.get("data") or {}) if result.get("ok") else {}
        return (data.get("usb_music") or {}).get("dir") or None

    return _status_probe("music_dir", from_daemon) or planned


def _usb_music_in_use():
    """Whether what plays is a key: it is mounted read-only, so nothing can be
    written there, and the interface says so rather than failing a write."""
    return _effective_music_dir() != (cfg().get("MUSIC_DIR") or "")


def notify_daemon(cmd, **kwargs):
    """Best-effort control() call whose result nobody is waiting on."""
    try:
        control(cmd, **kwargs)
    except Exception:  # noqa: BLE001
        log.exception("Could not notify the daemon (%s)", cmd)


_startup_cfg = load_config()


def _ensure_session_secret(initial_cfg):
    secret = initial_cfg.get("WEB_SESSION_SECRET")
    if secret:
        return secret
    secret = secrets.token_hex(32)
    try:
        update_config_file({"WEB_SESSION_SECRET": secret})
    except ValueError:
        log.exception("Could not persist a generated session secret - "
                       "sessions will not survive a restart until this is fixed")
    return secret


def _ensure_upnp_identity(values=None):
    """This radio's name on the network, and what a player remembers it by."""
    values = cfg() if values is None else values
    name = str(values.get("UPNP_NAME") or "").strip()
    uid = str(values.get("UPNP_UID") or "").strip()
    fresh = {}
    if not name:
        name = "%s %04d" % (upnp.DEFAULT_NAME, random.randint(1000, 9999))
        fresh["UPNP_NAME"] = name
    if not uid:
        uid = secrets.token_hex(4)
        fresh["UPNP_UID"] = uid
    if fresh:
        try:
            update_config_file(fresh)
        except ValueError:
            log.exception("Could not keep this radio's identity: it will "
                          "announce itself under another one after a restart")
    upnp.configure(name, uid)
    return name


app.secret_key = _ensure_session_secret(_startup_cfg)
_ensure_upnp_identity(_startup_cfg)
AUTH_MAX_AGE = timedelta(days=8)
app.permanent_session_lifetime = AUTH_MAX_AGE


def _is_authenticated():
    """Logged in, and less than AUTH_MAX_AGE ago."""
    if not session.get("authenticated"):
        return False
    try:
        logged_in_at = float(session.get("auth_at", 0))
    except (TypeError, ValueError):
        logged_in_at = 0
    if time.time() - logged_in_at > AUTH_MAX_AGE.total_seconds():
        session.pop("authenticated", None)
        session.pop("auth_at", None)
        return False
    return True


def _mark_authenticated():
    session.permanent = True
    session["authenticated"] = True
    session["auth_at"] = time.time()

_AUTH_EXEMPT_PREFIX = "/api/auth/"


# Paths a guest may call without the password; long_press (power off) never.
_GUEST_PATHS = frozenset({
    "/api/dedications/voice",
    "/api/status",
    "/api/status/wait",
    "/api/vote/skip",
    "/api/game",
    "/api/game/answer",
    "/api/volume",
    "/api/action/single_click",
    "/api/action/double_click",
    "/api/action/start_music",
    "/api/action/next_track",
    "/api/action/previous_track",
    "/api/action/toggle_pause",
    "/api/action/skip_sound",
    "/api/action/announce",
    "/api/announcements",
    "/api/portal/status",
    "/api/portal/release",
    "/api/now/cover",
    "/api/now/lyrics",
    "/api/recent",
    "/api/queue",
    "/api/today",
    "/api/library",
    "/api/library/facets",
    "/api/library/queue",
    "/api/library/play",
    "/api/audio/fallback",
    "/api/device",
    "/api/suggestions",
    "/api/suggestions/vote",
    "/api/suggestions/delete",
    "/api/suggestions/name",
    "/api/devices/link_code",
    "/api/devices/link_join",
    "/api/devices/link_leave",
})


# A guest page and what it asks for: hiding the page refuses those too.
GUEST_PAGE_PATHS = {
    "upnext": {"/api/queue"},
    "recent": {"/api/recent"},
    "today": {"/api/today"},
    "library": {"/api/library", "/api/library/facets"},
    "game": {"/api/game", "/api/game/answer"},
    "suggest": {"/api/suggestions", "/api/suggestions/vote", "/api/suggestions/delete",
                "/api/suggestions/name"},
}


def _guest_pages_off():
    wanted = {part.strip().lower() for part in str(cfg().get("GUEST_PAGES_OFF") or "").split(",")}
    return sorted(wanted & set(GUEST_PAGE_PATHS))


def _guest_allowed(path, method):
    if not cfg().get("GUEST_MODE_ENABLED"):
        return False
    if path not in _GUEST_PATHS:
        return False
    if any(path in GUEST_PAGE_PATHS[page] for page in _guest_pages_off()):
        return False
    if path == "/api/announcements" and method != "GET":
        return False
    return True


@app.route("/api/stream")
def api_stream():
    """Where the network output is, and whether there is one to offer."""
    return jsonify({"ok": True, "data": _stream_status()})


@app.route("/stream")
@app.route("/stream.<ext>")
def stream_audio(ext=None):
    """The encoded audio itself: one response per listener, never buffered."""
    # direct_passthrough keeps Flask from buffering an endless stream.
    server = stream_server()
    if server is None or not server.source:
        # VLC gets a code rather than silence, and the page says the same thing
        # in words: the stream can simply be off, and that is not a bug.
        why = "off" if server is None else stream_mod.why_unavailable(
            env=stream_mod.audio_env())
        return jsonify({"ok": False, "error": "stream_unavailable", "why": why}), 503
    headers = {
        "Content-Type": server.content_type,
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
    }
    response = Response(server.chunks(), headers=headers, direct_passthrough=True)
    response.timeout = None
    stats.record("stream_listened", label="web", detail={"encoder": server.encoder})
    return response


_PRE_LOGIN_PATHS = frozenset({"/api/portal/status"})


UPNP_SERVICES = {name: upnp.service_of(name) for name, _type, _id in upnp.SERVICES}
# The two modes in which nothing plays by itself: an announcement can still be
# heard in them, which is why the item's own name looks at that too.
UPNP_QUIET_MODES = ("idle", "stopped")


def _stream_available():
    """True when the stream is on and has something to encode."""
    return bool(stream_mod.status(stream_server())["available"])


def _daemon_playing(data):
    """True when something should be coming out of the radio."""
    if data.get("paused"):
        return False
    if data.get("sound"):
        return True
    return bool(data.get("current_track_path")) \
        and str(data.get("mode") or "") not in UPNP_QUIET_MODES


def _upnp_title():
    """What the item is called: the radio's name, and why it is quiet."""
    state = ""
    data = _daemon_status()
    if data:
        if data.get("paused"):
            state = "paused"
        elif not _daemon_playing(data):
            state = "idle"
    return upnp.device_name() + (" (%s)" % state if state else "")


def _daemon_status():
    """The daemon's own answer, or {} when it cannot be asked."""
    try:
        result = control("get_status")
    except Exception:  # noqa: BLE001  (a browse must never fail over this)
        return {}
    return (result.get("data") or {}) if result.get("ok") else {}


def _upnp_items():
    """What a player is offered: the stream, when there is one to hear."""
    server = stream_server()
    if server is None or not server.source:
        return []
    return [{
        "title": _upnp_title(),
        "url": request.host_url.rstrip("/") + "/stream." + server.suffix,
        "mime": server.content_type,
    }]


_upnp_lock = threading.Lock()
UPNP_WATCH_SEC = 5.0
# A capture attached to a silent sink never wakes up: it is reconnected, and the
# listeners are kept over it.
STREAM_STALL_SEC = 4.0
STREAM_STALL_ATTEMPTS = 5
_stream_stalls = {"tries": 0}


def _upnp_follow_stream():
    """Announces this radio exactly while there is a stream to offer."""
    wanted = _stream_available()
    with _upnp_lock:
        if wanted:
            upnp.start(cfg()["WEB_PORT"])
        else:
            upnp.stop()


def _apply_stream_tuning():
    """Hands the running encoder the stream's own volume and title."""
    server = stream_server()
    if server is None or not server.source:
        return False
    values = cfg()
    return server.retune(volume=stream_mod.stream_volume(values),
                         title=stream_mod.stream_title(values))


def _reconnect_a_silent_stream():
    """Starts the encoder again when it is silent while the radio plays."""
    server = stream_server()
    if server is None or not server.source:
        _stream_stalls["tries"] = 0
        return False
    if server.stalled_for() < STREAM_STALL_SEC:
        # A young encoder has not written yet: reconnecting it would end the stream
        # of the player that just joined.
        _stream_stalls["tries"] = 0
        return False
    if not _daemon_playing(_daemon_status()):
        _stream_stalls["tries"] = 0
        return False
    if _stream_stalls["tries"] >= STREAM_STALL_ATTEMPTS:
        return False
    _stream_stalls["tries"] += 1
    log.info("The stream has carried nothing for %.0fs while the radio plays: "
             "reconnecting the encoder (attempt %s)",
             server.stalled_for(), _stream_stalls["tries"])
    # The listeners are kept over it: an encoder reconnected under a player's
    # feet would end its stream, and the player would go silent for good.
    server.restart()
    return True


def _upnp_watch():
    while True:
        time.sleep(UPNP_WATCH_SEC)
        try:
            _reconnect_a_silent_stream()
            _upnp_follow_stream()
        except Exception:  # noqa: BLE001
            log.exception("The stream watchdog failed")


@app.route(upnp.PATH + "/rootDesc.xml")
def api_upnp_description():
    return Response(upnp.device_description(), mimetype="text/xml")


@app.route(upnp.PATH + "/<service>/scpd.xml")
def api_upnp_service_description(service):
    text = upnp.service_description(service)
    if text is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return Response(text, mimetype="text/xml")


@app.route(upnp.PATH + "/<service>/control", methods=["POST"])
def api_upnp_control(service):
    service_type = UPNP_SERVICES.get(service)
    if service_type is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    body = request.get_data(as_text=True)
    status, text = upnp.handle(service_type,
                               upnp.action_of(request.headers.get("SOAPAction", ""), body),
                               body, _upnp_items())
    return Response(text, status=status, mimetype="text/xml")


@app.route(upnp.PATH + "/<service>/event", methods=["SUBSCRIBE", "UNSUBSCRIBE"])
def api_upnp_event(service):
    """Nothing here changes while a player watches it; answering is what keeps
    a strict client from giving up on the browse."""
    if upnp.service_of(service) is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    answer = Response("", status=200, mimetype="text/xml")
    answer.headers["SID"] = request.headers.get("SID") or "uuid:%s" % uuid.uuid4()
    answer.headers["TIMEOUT"] = "Second-1800"
    answer.headers["SERVER"] = upnp.SERVER_NAME
    return answer


JSON_BODY_MAX_BYTES = 1024 * 1024


def _same_origin():
    """False for a request another site's page made the browser send."""
    origin = request.headers.get("Origin")
    if not origin:
        return True

    def bare(host):
        host = host.lower()
        return host[:-3] if host.endswith(":80") else host

    theirs = urllib.parse.urlsplit(origin).netloc
    return bool(theirs) and bare(theirs) == bare(request.host or "")


_LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")
_HOST_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
# The names the API was recently asked under and refused: the owner allows one in a tap.
_refused_hosts = {}
REFUSED_HOSTS_KEEP_SEC = 24 * 3600


def _note_refused_host(host):
    name = (host or "").strip().lower().split(":", 1)[0]
    if not _HOST_NAME_RE.match(name):
        return
    now = time.time()
    _refused_hosts[name] = now
    for old in [h for h, at in _refused_hosts.items() if now - at > REFUSED_HOSTS_KEEP_SEC]:
        _refused_hosts.pop(old, None)
    while len(_refused_hosts) > 10:
        _refused_hosts.pop(min(_refused_hosts, key=_refused_hosts.get))


def _known_host():
    """False when the API is asked for under a name that is not this Pi's: a
    foreign name pointed at our address is how another site reads the API."""
    host = (request.host or "").strip().lower()
    if not host or host.startswith("["):
        return True
    host = host.split(":", 1)[0]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost":
        return True
    # Suffixes nobody can register: "<our name>.evil.example" must not pass.
    own = re.escape(socket.gethostname().split(".", 1)[0].lower())
    named = re.fullmatch(own + r"(?:-\d+)?(\..+)?", host)
    if named and (named.group(1) or "") in _LOCAL_SUFFIXES:
        return True
    extra = str(cfg().get("WEB_EXTRA_HOSTS") or "").lower().replace(",", " ").split()
    return host in extra


@app.before_request
def _refuse_foreign_requests():
    if request.path.startswith("/api/") and not _known_host():
        _note_refused_host(request.host)
        return jsonify({"ok": False, "error": "bad_host", "detail": request.host}), 403
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if not _same_origin():
        return jsonify({"ok": False, "error": "bad_origin"}), 403
    if not (request.mimetype or "").startswith("multipart/") \
            and (request.content_length or 0) > JSON_BODY_MAX_BYTES:
        return jsonify({"ok": False, "error": "too_large"}), 413
    return None


@app.before_request
def _require_auth():
    if not request.path.startswith("/api/") or request.path.startswith(_AUTH_EXEMPT_PREFIX):
        return None
    if request.path in _PRE_LOGIN_PATHS and request.method == "GET":
        return None
    if request.path == "/api/portal/release" and request.method == "POST":
        return None
    if not cfg().get("WEB_PASSWORD_HASH"):
        return None
    if _is_authenticated():
        return None
    if _guest_allowed(request.path, request.method):
        return None
    return jsonify({"ok": False, "error": "auth_required"}), 401


@app.before_request
def _refuse_what_this_machine_cannot_do():
    """A route whose feature is not on this platform answers a code instead of
    failing on a binary that is not installed. The interface hides the card as
    well - this is the door behind it."""
    needed = _ROUTE_CAPABILITIES.get(request.path)
    if needed is None and request.url_rule is not None:
        # A path that carries an id is in the table under its rule
        # ("/api/announce_files/<path:source_id>"), so the id is anything.
        needed = _ROUTE_CAPABILITIES.get(request.url_rule.rule)
    if not needed:
        return None
    missing = [name for name, methods in needed.items()
               if request.method in methods and not platform_mod.has(name)]
    if missing:
        return jsonify({"ok": False, "error": "unsupported_here", "missing": missing}), 501
    return None


@app.route("/api/auth/status")
def api_auth_status():
    password_hash = cfg().get("WEB_PASSWORD_HASH")
    return jsonify({"ok": True, "data": {
        "auth_required": bool(password_hash),
        "authenticated": _is_authenticated() if password_hash else True,
    }})


LOGIN_FREE_FAILS = 3
LOGIN_BASE_DELAY_SEC = 5
LOGIN_MAX_DELAY_SEC = 300
LOGIN_FORGET_SEC = 3600
_login_attempts = {}
_login_attempts_lock = threading.Lock()


def _login_client():
    ip = request.remote_addr or "unknown"
    return suggestions.mac_for_ip(ip) or ip


def _login_wait():
    """Seconds this client must still wait before trying a password."""
    with _login_attempts_lock:
        entry = _login_attempts.get(_login_client())
        if not entry:
            return 0
        return max(0, int(round(entry["until"] - time.time() + 0.49)))


def _login_failed():
    now = time.time()
    with _login_attempts_lock:
        for key in [k for k, v in _login_attempts.items() if now - v["last"] > LOGIN_FORGET_SEC]:
            del _login_attempts[key]
        entry = _login_attempts.setdefault(_login_client(), {"fails": 0, "until": 0, "last": now})
        entry["fails"] += 1
        entry["last"] = now
        extra = entry["fails"] - LOGIN_FREE_FAILS - 1
        if extra >= 0:
            entry["until"] = now + min(LOGIN_BASE_DELAY_SEC * (2 ** extra), LOGIN_MAX_DELAY_SEC)
        wait = max(0, int(round(entry["until"] - now)))
    time.sleep(0.5)
    return wait


def _login_succeeded():
    with _login_attempts_lock:
        _login_attempts.pop(_login_client(), None)


def _too_many_attempts(wait):
    return jsonify({"ok": False, "error": "too_many_attempts", "retry_after": wait}), 429


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    body = request.get_json(silent=True) or {}
    password_hash = cfg().get("WEB_PASSWORD_HASH")
    if not password_hash:
        return jsonify({"ok": True})
    wait = _login_wait()
    if wait > 0:
        return _too_many_attempts(wait)
    if not web_auth.verify_password(body.get("password", ""), password_hash):
        wait = _login_failed()
        return jsonify({"ok": False, "error": "wrong_password", "retry_after": wait}), 401
    _login_succeeded()
    _mark_authenticated()
    stats.record("web_login", label=request.remote_addr or "unknown", detail=_device_detail())
    return jsonify({"ok": True})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("authenticated", None)
    session.pop("auth_at", None)
    return jsonify({"ok": True})


@app.route("/api/auth/set_password", methods=["POST"])
def api_auth_set_password():
    """Sets, changes, or clears (new_password="") the web password."""
    body = request.get_json(silent=True) or {}
    current_hash = cfg().get("WEB_PASSWORD_HASH")
    if current_hash:
        wait = _login_wait()
        if wait > 0:
            return _too_many_attempts(wait)
        if not web_auth.verify_password(body.get("current_password", ""), current_hash):
            wait = _login_failed()
            return jsonify({"ok": False, "error": "wrong_current_password", "retry_after": wait}), 403
        _login_succeeded()

    new_password = body.get("new_password", "")
    if new_password:
        if len(new_password) < web_auth.MIN_PASSWORD_LENGTH:
            return jsonify({"ok": False, "error": "password_too_short"}), 400
        new_hash = web_auth.hash_password(new_password)
    else:
        new_hash = ""
    update_config_file({"WEB_PASSWORD_HASH": new_hash})
    _mark_authenticated()
    stats.record("web_password_changed", label="set" if new_hash else "removed")
    return jsonify({"ok": True, "password_set": bool(new_hash)})


stats = StatsRecorder(
    _startup_cfg["STATS_DB_FILE"],
    retention_days=_startup_cfg["STATS_RETENTION_DAYS"],
    max_events=_startup_cfg["STATS_MAX_EVENTS"],
    enabled=_startup_cfg["STATS_ENABLED"],
)


DEVICE_COOKIE = "rukebox_device"
DEVICE_HEADER = "X-Rukebox-Device"
DEVICE_COOKIE_AGE = 5 * 365 * 86400
_suggestion_boxes = {}
_suggestion_boxes_lock = threading.Lock()


def _suggestion_box():
    path = cfg()["SUGGESTIONS_DB_FILE"]
    with _suggestion_boxes_lock:
        box = _suggestion_boxes.get(path)
        if box is None:
            box = _suggestion_boxes[path] = suggestions.SuggestionBox(path)
        return box


def _is_owner():
    return not cfg().get("WEB_PASSWORD_HASH") or _is_authenticated()


def _this_device(box):
    if "this_device" in g:
        return g.this_device
    ip = request.remote_addr
    header = (request.headers.get(DEVICE_HEADER) or "").strip()[:64] or None
    device, token = box.resolve_device(request.cookies.get(DEVICE_COOKIE), suggestions.mac_for_ip(ip), ip,
                                       alt_token=header)
    if token:
        g.device_cookie = token
    g.this_device = device
    g.this_device_token = token or request.cookies.get(DEVICE_COOKIE) or header
    return device


@app.route("/api/device")
def api_device():
    """This browser's device token, for the page to keep in localStorage."""
    _this_device(_suggestion_box())
    return jsonify({"ok": True, "data": {"token": g.get("this_device_token")}})


# Never 0: the uptime clock starts at boot, and 0 would read as fresh for its first half minute.
NEVER = float("-inf")
_bans_cache = {"at": NEVER, "any": False}
BAN_KICK_INTERVAL_SEC = 10
_ban_thread = None
_MAC_ARG_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _bans_active():
    if time.monotonic() - _bans_cache["at"] > 30:
        try:
            _bans_cache["any"] = bool(_suggestion_box().banned_devices())
        except Exception:  # noqa: BLE001
            _bans_cache["any"] = False
        _bans_cache["at"] = time.monotonic()
    return _bans_cache["any"]


def _bans_changed():
    _bans_cache["at"] = NEVER
    _ensure_ban_thread()
    threading.Thread(target=_kick_banned, daemon=True).start()


@app.before_request
def _refuse_banned():
    if not request.path.startswith("/api/") or request.path.startswith(_AUTH_EXEMPT_PREFIX):
        return None
    if not _bans_active():
        return None
    if cfg().get("WEB_PASSWORD_HASH") and _is_authenticated():
        return None
    box = _suggestion_box()
    device = _this_device(box)
    if box.ban_until(device["id"]) is not None:
        return jsonify({"ok": False, "error": "banned"}), 403
    return None


def _station_command(*args):
    iface = cfg().get("AP_INTERFACE", "uap0")
    base = ["iw", "dev", iface, "station"] + list(args)
    for argv in (base, ["sudo", "-n"] + base):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return result.stdout
    return None


# The page polls every 15s: two minutes is four missed polls, a device that left.
SEEN_CONNECTED_SEC = 120
PREVIOUS_DAYS = 7
PREVIOUS_MAX = 200
_seen_devices = {}
_seen_lock = threading.Lock()


def _stamp_seen(device_id):
    """A request IS the sign of life: a device on somebody else's network never
    shows up in `iw`, and talking to us is the only way to see it."""
    if not device_id:
        return
    with _seen_lock:
        _seen_devices[device_id] = time.monotonic()


def _recently_seen():
    """{device_id: seconds ago}, the stale entries dropped while we are here."""
    now = time.monotonic()
    with _seen_lock:
        stale = [d for d, at in _seen_devices.items() if now - at > SEEN_CONNECTED_SEC]
        for device_id in stale:
            del _seen_devices[device_id]
        return {d: max(0.0, now - at) for d, at in _seen_devices.items()}


@app.before_request
def _note_activity():
    if not request.path.startswith("/api/") or request.path.startswith(_AUTH_EXEMPT_PREFIX):
        return None
    try:
        _stamp_seen(_this_device(_suggestion_box())["id"])
    except Exception:  # noqa: BLE001
        log.exception("Could not note the device behind this request")
    return None


def _stations():
    """The access point's stations: {mac: {connected_sec, inactive_ms,
    signal}}."""
    out = _station_command("dump")
    if out is None:
        return None
    stations, current = {}, None
    for line in out.splitlines():
        if line.startswith("Station "):
            current = line.split()[1].lower()
            stations[current] = {}
            continue
        if current is None or ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip()
        try:
            if key == "connected time":
                stations[current]["connected_sec"] = int(value.split()[0])
            elif key == "inactive time":
                stations[current]["inactive_ms"] = int(value.split()[0])
            elif key == "signal":
                stations[current]["signal"] = int(value.split()[0])
        except (ValueError, IndexError):
            pass
    return stations


def _kick_mac(mac):
    if _MAC_ARG_RE.match(mac or ""):
        return _station_command("del", mac) is not None
    return False


def _kick_banned():
    try:
        banned = _suggestion_box().banned_macs()
    except Exception:  # noqa: BLE001
        return
    if not banned:
        return
    stations = _stations() or {}
    for mac in banned & set(stations):
        log.info("Banned device on the access point, disconnecting %s", mac)
        _kick_mac(mac)


def _ensure_ban_thread():
    global _ban_thread
    if _ban_thread is not None:
        return

    def loop():
        while True:
            time.sleep(BAN_KICK_INTERVAL_SEC)
            if _bans_active():
                _kick_banned()

    _ban_thread = threading.Thread(target=loop, daemon=True)
    _ban_thread.start()


GUEST_QUOTA_ACTIONS = {
    "/api/action/single_click": "sound",
    "/api/action/double_click": "sound",
    "/api/action/start_music": "start",
    "/api/action/next_track": "next",
    "/api/action/previous_track": "previous",
    "/api/action/toggle_pause": "pause",
    "/api/action/skip_sound": "pause",
    "/api/action/announce": "announce",
    "/api/volume": "volume",
    "/api/library/queue": "queue",
    "/api/library/play": "play_now",
    "/api/audio/fallback": "output",
}
QUOTA_VOLUME_BURST_SEC = 10
# Counters are kept on disk, so a restart does not refill everyone; one idle this long is forgotten.
QUOTA_KEEP_SEC = 30 * 86400
_quota = {}
_quota_lock = threading.Lock()
_quota_loaded = False


def _quota_file():
    return os.path.join(cfg()["STATE_DIR"], "guest_credits.json")


def _quota_load(now):
    """Caller holds _quota_lock. Counters already in memory win over the file's."""
    global _quota_loaded
    if _quota_loaded:
        return
    _quota_loaded = True
    doc = json_file.read(_quota_file()) or {}
    for person, entry in (doc.get("people") or {}).items():
        try:
            kept = {"tokens": float(entry["tokens"]), "at": float(entry["at"]),
                    "history": {str(k): [float(t) for t in v] for k, v in (entry.get("history") or {}).items()},
                    "volume_at": float(entry.get("volume_at") or 0.0)}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if now - kept["at"] < QUOTA_KEEP_SEC:
            _quota.setdefault(str(person), kept)


def _quota_save():
    """Caller holds _quota_lock."""
    now = time.time()
    people = {person: entry for person, entry in _quota.items() if now - entry["at"] < QUOTA_KEEP_SEC}
    path = _quota_file()
    try:
        with json_file.lock(path):
            json_file.write(path, {"people": people})
    except OSError:
        log.exception("Could not keep the guest credits")


def _quota_settings():
    c = cfg()
    costs = {}
    for action in set(GUEST_QUOTA_ACTIONS.values()):
        try:
            costs[action] = max(0.0, float(c.get("GUEST_COST_" + action.upper(), 1)))
        except (TypeError, ValueError):
            costs[action] = 1.0
    return {
        "enabled": bool(c.get("GUEST_QUOTA_ENABLED")),
        "max": max(1.0, float(c.get("GUEST_QUOTA_MAX") or 10)),
        "start": _quota_start(c),
        "refill": max(1.0, float(c.get("GUEST_QUOTA_REFILL_SEC") or 120)),
        "window": max(0.0, float(c.get("GUEST_QUOTA_REPEAT_MIN") or 0)) * 60,
        "costs": costs,
    }


def _quota_start(c):
    try:
        start = float(c.get("GUEST_QUOTA_START", 3))
    except (TypeError, ValueError):
        start = 3.0
    return min(max(1.0, float(c.get("GUEST_QUOTA_MAX") or 10)), max(0.0, start))


def _quota_entry(device_id, s, now):
    """Caller holds _quota_lock."""
    _quota_load(now)
    entry = _quota.setdefault(device_id, {"tokens": s["start"], "at": now, "history": {}, "volume_at": 0.0})
    entry["tokens"] = min(s["max"], entry["tokens"] + (now - entry["at"]) / s["refill"])
    entry["at"] = now
    for action, times in list(entry["history"].items()):
        entry["history"][action] = [t for t in times if now - t < s["window"]]
    return entry


def _quota_cost(entry, action, s):
    base = s["costs"].get(action, 1.0)
    return base * (2 ** len(entry["history"].get(action, [])))


def _quota_applies():
    return bool(cfg().get("WEB_PASSWORD_HASH")) and not _is_authenticated()


def _guest_locked():
    known = set(GUEST_QUOTA_ACTIONS.values())
    return sorted({part.strip().lower() for part in str(cfg().get("GUEST_LOCKED") or "").split(",")} & known)


ACTION_REPEAT_SEC = None  # None: the ACTION_REPEAT_SEC setting; a number pins it (tests)
ACTION_REPEAT_WAIT_SEC = 15
# One family per thing that can be changed: within the window, only whoever changed it may act on it again.
ACTION_FAMILIES = {
    "/api/action/next_track": "track",
    "/api/action/previous_track": "track",
    "/api/action/single_click": "track",
    "/api/action/double_click": "track",
    "/api/action/skip_sound": "track",
    "/api/library/play": "track",
    "/api/action/toggle_pause": "playback",
    "/api/action/start_music": "playback",
    "/api/action/timed_pause": "playback",
    "/api/action/standby": "playback",
    "/api/action/poweroff": "playback",
    "/api/action/long_press": "playback",
    "/api/volume": "volume",
    "/api/mute": "mute",
    "/api/loop": "loop",
}
# Asked twice, these are one request whoever asks: the same song queued, the same output chosen.
ACTION_SAME_ONLY = {"/api/library/queue", "/api/audio/fallback", "/api/action/announce",
                    "/api/action/sleep_timer"}
# A second identical tap of a toggle by the same person undoes the first: an accident, not a wish.
ACTION_TOGGLES = {"/api/action/toggle_pause", "/api/action/start_music", "/api/action/skip_sound",
                  "/api/mute", "/api/loop"}
_repeats = {}
_repeats_lock = threading.Lock()

# Per method: /api/time/timezone is readable everywhere, writable only where
# there is a clock to set.
_ROUTE_CAPABILITIES = {
    "/api/gpio/detect": {"gpio": ("POST",)},
    "/api/gpio/pinout": {"gpio": ("GET",)},
    # Everything the interface adds goes into the music folder (a song, an
    # announcement's sound, a system sound), and a container mounts it `:ro`.
    "/api/music/upload": {"media_upload": ("POST",)},
    "/api/announce_files/<path:source_id>": {"media_upload": ("POST", "DELETE")},
    "/api/system_sounds/<key>": {"media_upload": ("POST", "DELETE")},
    # The prepared folders live under the same audio root as the music, so a
    # container that mounts it read-only may read them and not write them.
    "/api/prepared/<kind>/file": {"media_upload": ("POST", "DELETE")},
    "/api/prepared/<kind>/folder": {"media_upload": ("POST",)},
    # A key is mounted from the machine's own USB bus.
    "/api/usb_music": {"usb_storage": ("POST",)},
    # The access point is the one feature whose routes ask nmcli with nothing
    # under them: everywhere else the missing binary answers an empty result.
    "/api/wifi/ap": {"access_point": ("GET", "POST")},
    "/api/wifi/ap/share": {"access_point": ("GET",)},
    "/api/time": {"set_clock": ("POST",)},
    "/api/time/timezone": {"set_clock": ("POST",)},
    "/api/system/reboot": {"power": ("POST",)},
}


def _repeat_request():
    body = request.get_json(silent=True)
    try:
        canon = json.dumps(body, sort_keys=True)
    except (TypeError, ValueError):
        canon = request.get_data(as_text=True)
    return request.path, canon


def _repeat_person():
    try:
        return _this_device(_suggestion_box())["person"]
    except Exception:
        return request.remote_addr


def _repeat_window():
    if ACTION_REPEAT_SEC is not None:
        return ACTION_REPEAT_SEC
    try:
        return max(0.0, min(10.0, float(cfg().get("ACTION_REPEAT_SEC", 1))))
    except (TypeError, ValueError):
        return 1.0


def _repeat_active(state, now):
    return state["busy"] > 0 or now < state["until"]


@app.before_request
def _skip_repeated_action():
    """An action someone else just took, or a double tap, gets the first answer and costs nothing."""
    path = request.path
    family = ACTION_FAMILIES.get(path)
    if request.method != "POST" or not (family or path in ACTION_SAME_ONLY) or _repeat_window() <= 0:
        return None
    action = GUEST_QUOTA_ACTIONS.get(path)
    if action and action in _guest_locked() and _quota_applies():
        return None
    asked = _repeat_request()
    key = ("family", family) if family else ("same",) + asked
    person = _repeat_person() if family else None
    now = time.monotonic()
    with _repeats_lock:
        for old in [k for k, s in _repeats.items() if not _repeat_active(s, now)]:
            del _repeats[old]
        state = _repeats.get(key)
        repeat = state is not None and (
            not family or state["person"] != person
            or (path in ACTION_TOGGLES and state["asked"] == asked))
        if not repeat:
            if state is None:
                state = {"busy": 0, "until": 0.0, "done": threading.Event(), "reply": None}
                _repeats[key] = state
            if state["busy"] == 0:
                state["done"] = threading.Event()
            state.update(busy=state["busy"] + 1, person=person, asked=asked)
            g.repeat_state = state
            return None
        done = state["done"]
    # The window runs to a second after the action ENDS: a fade can outlast it.
    if not done.wait(ACTION_REPEAT_WAIT_SEC):
        return jsonify({"ok": True, "data": {"repeated": True}})
    reply = state["reply"]
    if reply is None:
        return None
    data, status, mimetype, encoding = reply
    response = Response(data, status=status, mimetype=mimetype)
    if encoding:
        response.headers["Content-Encoding"] = encoding
    return response


@app.after_request
def _remember_action_reply(response):
    state = g.pop("repeat_state", None)
    if state is None:
        return response
    with _repeats_lock:
        state["busy"] -= 1
        # A failure opens no window: the next press may try for real.
        if response.status_code < 400 and not response.is_streamed:
            state["reply"] = (response.get_data(), response.status_code, response.mimetype,
                              response.headers.get("Content-Encoding"))
            state["until"] = time.monotonic() + _repeat_window()
        if state["busy"] == 0:
            state["done"].set()
    return response


@app.before_request
def _check_quota():
    action = GUEST_QUOTA_ACTIONS.get(request.path)
    if request.method != "POST" or not action or not _quota_applies():
        return None
    # A lock is not a price: it holds with the credits off, and for a device spared them.
    if action in _guest_locked():
        return jsonify({"ok": False, "error": "guest_locked"}), 403
    s = _quota_settings()
    if not s["enabled"]:
        return None
    box = _suggestion_box()
    device = _this_device(box)
    if box.has_free_credits(device["id"]):
        return None
    now = time.time()
    with _quota_lock:
        entry = _quota_entry(device["person"], s, now)
        if action == "volume" and now - entry["volume_at"] < QUOTA_VOLUME_BURST_SEC:
            return None
        cost = _quota_cost(entry, action, s)
        if entry["tokens"] + 1e-9 < cost:
            wait = int((cost - entry["tokens"]) * s["refill"]) + 1
            return jsonify({"ok": False, "error": "quota_exceeded", "retry_after": wait,
                            "detail": wait}), 429
    g.quota_charge = (device["person"], action, cost)
    return None


@app.after_request
def _charge_quota(response):
    charge = g.pop("quota_charge", None)
    if charge and response.status_code < 400:
        device_id, action, cost = charge
        s = _quota_settings()
        now = time.time()
        with _quota_lock:
            entry = _quota_entry(device_id, s, now)
            entry["tokens"] = max(0.0, entry["tokens"] - cost)
            entry["history"].setdefault(action, []).append(now)
            if action == "volume":
                entry["volume_at"] = now
            _quota_save()
    return response


def _quota_status():
    """For a guest's /api/status."""
    if not _quota_applies():
        return None
    s = _quota_settings()
    if not s["enabled"]:
        return None
    box = _suggestion_box()
    device = _this_device(box)
    if box.has_free_credits(device["id"]):
        return None
    with _quota_lock:
        entry = _quota_entry(device["person"], s, time.time())
        return {
            "tokens": int(entry["tokens"] + 1e-9),
            "max": int(s["max"]),
            "refill_sec": int(s["refill"]),
            "costs": {a: _quota_cost(entry, a, s) for a in set(GUEST_QUOTA_ACTIONS.values())},
        }


@app.after_request
def _set_device_cookie(response):
    token = g.pop("device_cookie", None)
    if token:
        response.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_COOKIE_AGE, httponly=True, samesite="Lax")
    return response


def _suggestion_call(fn, owner_only=False):
    """Runs fn(box, device) and turns its refusals into error codes."""
    if not cfg().get("SUGGESTIONS_ENABLED"):
        return jsonify({"ok": False, "error": "suggestions_disabled"}), 404
    if owner_only and not _is_owner():
        return jsonify({"ok": False, "error": "auth_required"}), 401
    box = _suggestion_box()
    try:
        return jsonify({"ok": True, "data": fn(box, _this_device(box))})
    except suggestions.SuggestionError as e:
        body = {"ok": False, "error": e.code}
        if e.detail is not None:
            body["detail"] = e.detail
        return jsonify(body), 400


@app.route("/api/suggestions", methods=["GET", "POST"])
def api_suggestions():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if body.get("kind") == "music" and not body.get("force") and cfg().get("SUGGESTIONS_ENABLED"):
            found = _get_library().match(body.get("text"))
            if found:
                return jsonify({"ok": False, "error": "suggestion_in_library", "data": found,
                                "detail": " - ".join(filter(None, [found.get("artist"), found.get("title")]))}), 409
        return _suggestion_call(lambda box, dev: {"id": box.add(dev, body.get("kind"), body.get("text"))})
    owner = _is_owner()
    interval = _rename_interval_sec()
    return _suggestion_call(lambda box, dev: {
        "me": {"name": dev["name"], "rename_wait": box.rename_wait(dev, interval),
               "locked": box.name_locked(dev["id"]), "linked": len(box.linked_devices(dev["id"]))},
        "owner": owner,
        "text_max": suggestions.TEXT_MAX,
        "items": _with_library_matches(box.list(dev, admin=owner)),
    })


def _with_library_matches(items):
    """Open music suggestions get `in_library`."""
    try:
        lib = _get_library()
    except Exception:  # noqa: BLE001
        return items
    for item in items:
        if item.get("kind") == "music" and item.get("status") == "open":
            found = lib.match(item.get("text"))
            if found:
                item["in_library"] = found
    return items


def _rename_interval_sec():
    try:
        return max(0, int(cfg().get("SUGGESTIONS_RENAME_INTERVAL_MIN", 60))) * 60
    except (TypeError, ValueError):
        return 3600


@app.route("/api/suggestions/vote", methods=["POST"])
def api_suggestions_vote():
    body = request.get_json(silent=True) or {}
    try:
        value = int(body.get("value", 0))
    except (TypeError, ValueError):
        value = None
    return _suggestion_call(lambda box, dev: box.vote(dev, body.get("id"), value))


@app.route("/api/suggestions/delete", methods=["POST"])
def api_suggestions_delete():
    body = request.get_json(silent=True) or {}
    owner = _is_owner()
    return _suggestion_call(lambda box, dev: box.delete(dev, body.get("id"), admin=owner))


@app.route("/api/suggestions/name", methods=["POST"])
def api_suggestions_name():
    body = request.get_json(silent=True) or {}
    interval = _rename_interval_sec()
    generated = bool(body.get("generated"))
    return _suggestion_call(lambda box, dev: {"name": box.set_name(dev, body.get("name"), interval,
                                                                   generated=generated)})


@app.route("/api/suggestions/status", methods=["POST"])
def api_suggestions_status():
    body = request.get_json(silent=True) or {}
    return _suggestion_call(lambda box, dev: box.set_status(body.get("id"), body.get("status")), owner_only=True)


@app.route("/api/suggestions/names", methods=["GET"])
def api_suggestions_names():
    return _suggestion_call(lambda box, dev: {"people": box.people()}, owner_only=True)


@app.route("/api/suggestions/release_name", methods=["POST"])
def api_suggestions_release_name():
    body = request.get_json(silent=True) or {}
    return _suggestion_call(lambda box, dev: box.release_name(str(body.get("key", ""))), owner_only=True)


WEB_SESSION_WINDOW_SEC = 900
_web_sessions_seen = {}
_web_sessions_lock = threading.Lock()


class _ThrottledInput:
    """A request body read at `rate` bytes per second at most: TCP then slows
    the sender down, and the shared radio keeps room for Bluetooth audio."""

    def __init__(self, raw, rate):
        self._raw = raw
        self._rate = float(rate)
        self._start = time.monotonic()
        self._done = 0

    def _pace(self, n):
        self._done += n
        ahead = self._done / self._rate - (time.monotonic() - self._start)
        if ahead > 0:
            time.sleep(ahead)

    def read(self, size=-1):
        if size is None or size < 0 or size > 16384:
            size = 16384
        data = self._raw.read(size)
        self._pace(len(data))
        return data

    def readline(self, size=-1):
        data = self._raw.readline(size if size and size > 0 else 16384)
        self._pace(len(data))
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def _transfer_limit():
    """The upload rate limit in bytes per second, or None. "auto" limits only
    while something plays to a connected speaker of the built-in chip, or of a
    USB dongle when TRANSFER_LIMIT_USB says so."""
    c = cfg()
    mode = c.get("TRANSFER_LIMIT_MODE") or "auto"
    try:
        rate = max(16, int(c.get("TRANSFER_LIMIT_KBPS") or 200)) * 1024
    except (TypeError, ValueError):
        rate = 200 * 1024
    if mode == "off":
        return None
    if mode == "always":
        return rate
    if (c.get("AUDIO_OUTPUT") or "bluetooth") != "bluetooth":
        return None
    controllers = _bt_controllers()
    speaker = _resolve_controller(c.get("SPEAKER_BT_ADAPTER"), controllers)
    if speaker is None:
        speaker = controllers[0] if controllers else None
    if speaker is None or (speaker["bus"] == "usb" and not c.get("TRANSFER_LIMIT_USB")):
        return None
    status = control("get_status")
    data = (status.get("data") or {}) if status.get("ok") else {}
    # Music and announcements are capped; the keep-alive chime in idle is not worth slowing.
    if not data.get("speaker_connected") or data.get("paused"):
        return None
    if data.get("mode") in (None, "idle", "stopped"):
        return None
    return rate


@app.before_request
def _limit_uploads():
    if request.method != "POST" or (request.content_length or 0) < 256 * 1024:
        return None
    if not request.mimetype.startswith("multipart/"):
        return None
    rate = _transfer_limit()
    if rate:
        request.environ["wsgi.input"] = _ThrottledInput(request.environ["wsgi.input"], rate)
    return None


@app.before_request
def _record_web_session():
    """Records that someone actually opened the admin interface."""
    if not stats.enabled or request.path.startswith("/api/journal"):
        return
    client = request.remote_addr or "unknown"
    now = time.monotonic()
    with _web_sessions_lock:
        last = _web_sessions_seen.get(client)
        if last is not None and now - last < WEB_SESSION_WINDOW_SEC:
            return
        _web_sessions_seen[client] = now
    stats.attach_current_session()
    stats.record(
        "web_session", label=client, detail=_device_detail(),
        counters={"web_sessions": 1}, daily={"web_sessions": 1},
    )


def _device_detail():
    """The event detail naming the device behind this request."""
    try:
        return {"device": _this_device(_suggestion_box())["id"]}
    except Exception:  # noqa: BLE001
        return None


def _name_visitors(events):
    """Adds each visitor's current nickname (`who`) to the events that name
    one."""
    try:
        box = _suggestion_box()
    except Exception:  # noqa: BLE001
        return events
    cache = {}
    for event in events:
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        key = None
        if detail.get("device"):
            key = ("id", str(detail["device"]))
        elif event.get("type") in ("ap_client_connected", "ap_client_disconnected") \
                and _MAC_ARG_RE.match(str(event.get("label") or "").lower()):
            key = ("mac", str(event["label"]).lower())
        if key is None:
            continue
        if key not in cache:
            device = box.device_by_id(key[1]) if key[0] == "id" else box.device_by_mac(key[1])
            cache[key] = device.get("name") if device else None
        if cache[key]:
            event["who"] = cache[key]
    return events


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow:\n", mimetype="text/plain")


GZIP_MIN_BYTES = 1500
_GZIP_TYPES = ("text/", "application/json", "application/javascript",
               "application/manifest+json", "image/svg+xml")
_gzip_static = {}
_gzip_static_lock = threading.Lock()


def _gzip_static_key(response):
    """(path, mtime, size) of a file served from web/, or None."""
    if request.method != "GET" or request.path.startswith("/api/"):
        return None
    name = "index.html" if request.path == "/" else request.path.lstrip("/")
    path = os.path.realpath(os.path.join(WEB_DIR, name))
    if not path.startswith(os.path.realpath(WEB_DIR) + os.sep):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size)


@app.after_request
def _gzip_response(response):
    if (response.status_code != 200
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()
            or response.headers.get("Content-Encoding")
            or not (response.mimetype or "").startswith(_GZIP_TYPES)):
        return response
    response.headers.add("Vary", "Accept-Encoding")
    if response.is_streamed and not response.direct_passthrough:
        return response
    key = _gzip_static_key(response)
    body = None
    if key:
        with _gzip_static_lock:
            body = _gzip_static.get(key)
    if body is None:
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) < GZIP_MIN_BYTES:
            return response
        body = gzip.compress(data, compresslevel=6)
        if key:
            with _gzip_static_lock:
                for old in [k for k in _gzip_static if k[0] == key[0]]:
                    del _gzip_static[old]
                _gzip_static[key] = body
    response.direct_passthrough = False
    response.set_data(body)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(body))
    return response


mimetypes.add_type("application/manifest+json", ".webmanifest")

I18N_LANGS = ("en", "fr", "de", "es", "it", "nl")
LANG_COOKIE = "rukebox_lang"
_i18n_cache = {}
_i18n_lock = threading.Lock()


def _i18n_parts(path):
    """web/i18n.js cut into what comes before the dictionary, one block per language, and after."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start = text.index("const I18N = {\n") + len("const I18N = {\n")
    end = text.index("\n};\n", start)
    blocks = {}
    lines = text[start:end + 1].splitlines(keepends=True)
    current = None
    for line in lines:
        if len(line) > 6 and line.startswith("  ") and line[2:4] in I18N_LANGS and line[4:7] == ": {":
            current = line[2:4]
            blocks[current] = []
        if current:
            blocks[current].append(line)
    return text[:start], {lang: "".join(body) for lang, body in blocks.items()}, text[end + 1:]


def _page_lang():
    asked = (request.args.get("lang") or request.cookies.get(LANG_COOKIE) or "").lower()
    if asked in I18N_LANGS:
        return asked
    best = request.accept_languages.best_match(I18N_LANGS)
    return best or "en"


def _i18n_script(lang, only):
    """English (every missing key falls back to it) and one language; or that language alone, to add."""
    path = os.path.join(WEB_DIR, "i18n.js")
    stamp = os.stat(path).st_mtime_ns
    key = (stamp, lang, only)
    with _i18n_lock:
        hit = _i18n_cache.get(key)
    if hit:
        return hit
    before, blocks, after = _i18n_parts(path)
    if only:
        text = "\"use strict\";\nI18N." + lang + " = {\n" + blocks[lang].split("\n", 1)[1].rstrip().rstrip(",") + ";\n"
    else:
        wanted = ["en"] + ([lang] if lang != "en" else [])
        text = before + "".join(blocks[name] for name in wanted) + after
    body = text.encode("utf-8")
    entry = (body, gzip.compress(body, compresslevel=6), '"%x-%s-%d"' % (stamp, lang, only))
    with _i18n_lock:
        for old in [k for k in _i18n_cache if k[0] != stamp]:
            del _i18n_cache[old]
        _i18n_cache[key] = entry
    return entry


@app.route("/i18n.js")
def i18n_script():
    """Only the languages this page reads: the whole file is two thirds of a first load."""
    lang = _page_lang()
    only = request.args.get("add") == "1"
    body, packed, etag = _i18n_script(lang, only)
    if etag in (request.headers.get("If-None-Match") or ""):
        response = Response(status=304)
    else:
        zipped = "gzip" in request.headers.get("Accept-Encoding", "").lower()
        response = Response(packed if zipped else body, mimetype="text/javascript")
        if zipped:
            response.headers["Content-Encoding"] = "gzip"
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Cookie, Accept-Language, Accept-Encoding"
    return response


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


_portal_released = {}
_portal_lock = threading.Lock()

PORTAL_RELEASE_SECONDS = 12 * 3600


def _client_ip():
    return request.remote_addr or "unknown"


def _release_key(ip, mac=None):
    """A tap is remembered against the device, not the address it happened to
    have: keyed by address, an entry left behind would let the next device
    the DHCP hands that address to through the portal without asking."""
    if mac is None:
        mac = suggestions.mac_for_ip(ip)
    return mac or "ip:" + str(ip)


def _portal_release_held(key):
    with _portal_lock:
        expiry = _portal_released.get(key)
        if expiry is None:
            return False
        if expiry < time.time():
            del _portal_released[key]
            return False
        return True


def _portal_release_remembered(mac):
    """The tap as the device database has it: it outlives the service, which is
    the point - an update or the daily reboot used to ask again a device that
    had already finished."""
    if not mac:
        return False
    try:
        when = _suggestion_box().portal_released_at(mac)
    except Exception:  # noqa: BLE001
        log.exception("Portal: release lookup failed")
        return False
    if when is None or when + PORTAL_RELEASE_SECONDS < time.time():
        return False
    return True


def _portal_tap_remembered(ip, mac):
    """Whether this device still has a tap: the device database first (it
    survives a restart), then the memory, for a device the Pi has no MAC for."""
    return _portal_release_remembered(mac) or _portal_release_held(_release_key(ip, mac))


def _portal_release_time(ip):
    """When this device tapped "Finish connecting", or None. The page confirms
    a tap once and briefly, and this date is what tells a tap made now from one
    made this morning."""
    mac = suggestions.mac_for_ip(ip)
    if mac:
        try:
            when = _suggestion_box().portal_released_at(mac)
        except Exception:  # noqa: BLE001
            log.exception("Portal: release time lookup failed")
            when = None
        if when is not None and when + PORTAL_RELEASE_SECONDS >= time.time():
            return when
    with _portal_lock:
        expiry = _portal_released.get(_release_key(ip, mac))
    if expiry and expiry >= time.time():
        return expiry - PORTAL_RELEASE_SECONDS
    return None


def _portal_is_released(ip):
    """Released by a tap on "Finish connecting" (below), or simply not on the
    access point: the portal only ever holds devices that joined the hotspot,
    so a computer on the home network is not asked to finish anything."""
    if not captive_portal.is_ap_client(ip, cfg().get("AP_INTERFACE", "uap0")):
        return True
    mac = suggestions.mac_for_ip(ip)
    if mac:
        try:
            box = _suggestion_box()
            choice = box.portal_for_mac(mac)
            if choice == "never":
                return True
            if choice == "always":
                return False
            if cfg().get("CAPTIVE_PORTAL_MODE") == "new_only" and box.mac_known(mac):
                return True
        except Exception:  # noqa: BLE001
            log.exception("Portal: device lookup failed")
    return _portal_tap_remembered(ip, mac)


def _portal_release(ip):
    mac = suggestions.mac_for_ip(ip)
    if mac:
        try:
            _suggestion_box().note_portal_release(mac)
        except Exception:  # noqa: BLE001
            log.exception("Portal: could not write the tap down")
    with _portal_lock:
        now = time.time()
        for stale in [k for k, v in _portal_released.items() if v < now]:
            del _portal_released[stale]
        _portal_released[_release_key(ip, mac)] = now + PORTAL_RELEASE_SECONDS


def _portal_forget(mac, ip=None):
    """Undoes a device's tap, so the portal holds it again. True when there
    was a tap to undo."""
    forgotten = False
    if mac:
        try:
            forgotten = _suggestion_box().forget_portal_release(mac)
        except Exception:  # noqa: BLE001
            log.exception("Portal: could not undo the tap")
    with _portal_lock:
        in_memory = _portal_released.pop(_release_key(ip or "", mac), None) is not None
    return forgotten or in_memory


@app.route("/api/portal/status")
def api_portal_status():
    """Tells the page whether this device still has the portal holding it, so
    it can show the "join this network" step only when there is actually
    something to join."""
    c = cfg()
    return jsonify({"ok": True, "data": {
        "enabled": bool(c.get("CAPTIVE_PORTAL_ENABLED")),
        "mode": c.get("CAPTIVE_PORTAL_MODE", "release"),
        "on_ap": captive_portal.is_ap_client(_client_ip(), c.get("AP_INTERFACE", "uap0")),
        "released": _portal_is_released(_client_ip()),
        "released_at": _portal_release_time(_client_ip()),
        "guest_mode": bool(c.get("GUEST_MODE_ENABLED")),
        "auth_required": bool(c.get("WEB_PASSWORD_HASH")),
        "authenticated": _is_authenticated(),
    }})


@app.route("/api/portal/release", methods=["POST"])
def api_portal_release():
    """"I am done with the portal"."""
    if cfg().get("CAPTIVE_PORTAL_MODE", "release") not in ("release", "new_only"):
        return jsonify({"ok": False, "error": "hold_mode"}), 400
    ip = _client_ip()
    _portal_release(ip)
    log.info("Captive portal: released %s", ip)
    stats.record("portal_released", label=ip, detail=_device_detail())
    return jsonify({"ok": True})


@app.errorhandler(404)
def _unknown_path(_error):
    """Anything that isn't a real file or API route goes to the interface
    itself."""
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "not_found"}), 404

    if captive_portal.is_probe_path(request.path):
        # The portal probe is the only sign of life from a device that never opens a page.
        _seen_on_the_network(_suggestion_box(), suggestions.mac_for_ip(_client_ip()), _client_ip())
    if captive_portal.is_probe_path(request.path) and _portal_is_released(_client_ip()):
        status, content_type, body = captive_portal.probe_response(request.path)
        return Response(body, status=status, mimetype=content_type,
                        headers={"Cache-Control": "no-store"})
    answer = redirect(captive_portal.redirect_url_for(_client_ip()) or "/", code=302)
    # A portal window that kept this answer would never see the probe say "no portal".
    answer.headers["Cache-Control"] = "no-store"
    return answer


def _now_playing_path():
    result = control("get_status")
    if not result.get("ok"):
        return None
    return result["data"].get("current_track_path")


def _now_playing_checked():
    path = _now_playing_path()
    key = track_media.track_key(path)
    asked = request.args.get("k")
    if key is None:
        return None, "nothing_playing"
    if asked and asked != key:
        return None, "track_changed"
    return path, None


def _cover_of(path):
    """The cover of a track, looked up the way the settings ask for it."""
    c = cfg()
    return track_media.cover(path, c.get("COVER_DIR"), c.get("MUSIC_DIR"),
                             c.get("COVER_PRIORITY"))


@app.route("/api/now/cover")
def api_now_cover():
    path, error = _now_playing_checked()
    art = _cover_of(path) if path else None
    if not art:
        response = jsonify({"ok": False, "error": error or "no_cover"})
        response.headers["Cache-Control"] = "no-store"
        return response, 404
    data, mime = art
    # Short-lived on purpose: a picture dropped in the covers folder, or the
    # priority changed, is then seen on the next reload instead of tomorrow.
    return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=300"})


@app.route("/api/now/lyrics")
def api_now_lyrics():
    path, error = _now_playing_checked()
    if error == "track_changed":
        return jsonify({"ok": False, "error": error}), 409
    return jsonify({"ok": True, "data": track_media.lyrics(path) if path else None})


_warmed_tracks = set()
_warm_lock = threading.Lock()


_warm_queue = []
_warm_cond = threading.Condition(_warm_lock)
_warm_worker = None


def _warm_track_media(path, full=True):
    global _warm_worker
    if not path:
        return
    with _warm_lock:
        if (path, full) in _warmed_tracks:
            return
        _warmed_tracks.add((path, full))
        if len(_warmed_tracks) > 500:
            _warmed_tracks.clear()
            _warmed_tracks.add((path, full))
        if full:
            _warm_queue.insert(0, (path, full))
        else:
            _warm_queue.append((path, full))
        _warm_cond.notify()
        if _warm_worker is None:
            _warm_worker = threading.Thread(target=_warm_loop, daemon=True)
            _warm_worker.start()


def _warm_loop():
    while True:
        with _warm_lock:
            while not _warm_queue:
                _warm_cond.wait()
            path, full = _warm_queue.pop(0)
        steps = (track_media.tags, _cover_of, track_media.lyrics) if full else (track_media.tags,)
        for fn in steps:
            try:
                fn(path)
            except Exception:  # noqa: BLE001
                log.debug("Could not prepare %s for %s", fn.__name__, path, exc_info=True)
        if track_media.cached_tags(path) is None or not full:
            with _warm_lock:
                _warmed_tracks.discard((path, full))


@app.route("/api/recent")
def api_recent():
    """The last RECENT_TRACKS_COUNT music tracks started, newest first, read
    from the daemon's state file."""
    c = cfg()
    try:
        limit = max(0, int(c.get("RECENT_TRACKS_COUNT", 20)))
    except (TypeError, ValueError):
        limit = 20
    if limit == 0:
        return jsonify({"ok": True, "data": {"enabled": False, "items": []}})
    try:
        with open(os.path.join(c["STATE_DIR"], "state.json"), encoding="utf-8") as f:
            recent = json.load(f).get("recent") or []
    except (OSError, ValueError):
        recent = []
    items, pending = [], False
    excluded = _excluded_keys()
    for entry in recent[:limit]:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not path:
            continue
        tags = track_media.cached_tags(path)
        if tags is None and os.path.exists(path):
            pending = True
            _warm_track_media(path, full=False)
        tags = tags or {}
        key = track_media.track_key(path)
        items.append({
            "title": tags.get("title"),
            "artist": tags.get("artist"),
            "name": os.path.splitext(os.path.basename(path))[0],
            "at": entry.get("at"),
            "key": key,
            "excluded": key in excluded,
        })
    return jsonify({"ok": True, "data": {"enabled": True, "items": items, "pending": pending}})


_library = None
_library_lock = threading.Lock()
_library_wake = threading.Event()
LIBRARY_RESYNC_SEC = 300


def _get_library():
    global _library
    with _library_lock:
        if _library is None:
            _library = library.Library(cfg()["LIBRARY_DB_FILE"], track_media.track_key)
            threading.Thread(target=_library_loop, daemon=True).start()
        return _library


def _library_roots():
    """(the folder the settings name, the storage device being played from or None)."""
    planned = cfg().get("MUSIC_DIR") or ""
    current = _effective_music_dir()
    return planned, (current if current and current != planned else None)


def _library_loop():
    time.sleep(20)
    while True:
        try:
            c = cfg()
            roots = _library_roots()
            planned, device = roots
            # Both folders stay in the catalogue, so the tags of the internal one survive
            # a key; the shared cache file only ever holds the folder the radio plays.
            if device:
                tracks = scan_audio_dir(planned) + get_music_list(device, c["MUSIC_CACHE_FILE"])
            else:
                tracks = get_music_list(planned, c["MUSIC_CACHE_FILE"])
            folders = [r for r in roots if r]
            _library.sync(tracks, folders)
            same = lambda: not _library_wake.is_set() and _library_roots() == roots
            while same():
                batch = _library.unread(20)
                if not batch:
                    break
                for path in batch:
                    _library.store(path, library.read_tags(path), folders)
            # The endings are quick (30 seconds of each file), so before the loudness.
            while same():
                batch = _library.untailed(10)
                if not batch:
                    break
                for path, duration in batch:
                    _library.store_tail(path, library.read_tail(path, duration))
            # After the tags, which the page needs first: one ffmpeg pass per file, once.
            while same():
                batch = _library.unmeasured(5)
                if not batch:
                    break
                for path in batch:
                    _library.store_loudness(path, library.read_loudness(path))
        except Exception:  # noqa: BLE001
            log.exception("Library catalogue: update failed")
        # A storage device that comes or goes is picked up within seconds, not minutes.
        deadline = time.monotonic() + LIBRARY_RESYNC_SEC
        while time.monotonic() < deadline and not _library_wake.is_set():
            if _library_roots() != roots:
                break
            _library_wake.wait(5)
        _library_wake.clear()


@app.route("/api/library")
def api_library():
    """Search the library: words (`q`, in title, artist, album, genre, file
    name) and exact `artist` / `album` / `genre` filters."""
    lib = _get_library()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    found = lib.search(request.args.get("q", ""), request.args.get("artist") or None,
                       request.args.get("album") or None, request.args.get("genre") or None,
                       offset=offset, limit=30)
    found["status"] = lib.status()
    excluded = _excluded_keys()
    for item in found["items"]:
        item["excluded"] = item.get("key") in excluded
    return jsonify({"ok": True, "data": found})


@app.route("/api/library/facets")
def api_library_facets():
    return jsonify({"ok": True, "data": _get_library().facets(request.args.get("artist") or None)})


def _path_for_key(key):
    """The track an opaque key names: from the library catalogue, else from the
    recent list."""
    key = str(key or "")
    if not key:
        return None
    path = _get_library().path_for_key(key)
    if path:
        return path
    try:
        with open(os.path.join(cfg()["STATE_DIR"], "state.json"), encoding="utf-8") as f:
            recent = json.load(f).get("recent") or []
    except (OSError, ValueError):
        recent = []
    return next((e.get("path") for e in recent
                 if isinstance(e, dict) and e.get("path") and track_media.track_key(e["path"]) == key), None)


DEDICATION_MAX = 160
VOICE_MAX_BYTES = 900 * 1024
VOICE_MAX_SEC = 30
VOICE_KEEP_SEC = 6 * 3600
VOICE_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
               "audio/aac": ".aac", "audio/mpeg": ".mp3", "audio/wav": ".wav"}
_VOICE_ID_RE = re.compile(r"^[0-9a-f]{32}\.(webm|ogg|m4a|aac|mp3|wav)$")


def _voice_dir():
    return os.path.join(cfg()["STATE_DIR"], "voice-dedications")


def _voice_path(voice_id):
    """The recording `voice_id` names, or None when it is not one."""
    if not isinstance(voice_id, str) or not _VOICE_ID_RE.match(voice_id):
        return None
    path = os.path.join(_voice_dir(), voice_id)
    return path if os.path.isfile(path) else None


def _voice_seconds(path):
    """The recording's length, or None when ffprobe cannot tell (or is not there)."""
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=15)
        return float(done.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


@app.route("/api/dedications/voice", methods=["POST"])
def api_dedication_voice():
    """A recorded dedication (the request body is the audio): answers its id, to send with the song."""
    c = cfg()
    if not (c.get("DEDICATIONS_ENABLED") and c.get("DEDICATIONS_VOICE")):
        return jsonify({"ok": False, "error": "voice_dedications_off"}), 403
    ext = VOICE_TYPES.get((request.mimetype or "").lower())
    if not ext:
        return jsonify({"ok": False, "error": "voice_bad_type"}), 400
    data = request.get_data(cache=False)
    if not data:
        return jsonify({"ok": False, "error": "voice_empty"}), 400
    if len(data) > VOICE_MAX_BYTES:
        return jsonify({"ok": False, "error": "voice_too_long"}), 413
    folder = _voice_dir()
    os.makedirs(folder, exist_ok=True)
    # A recording never sent with a song is not kept for ever.
    now = time.time()
    for old in os.listdir(folder):
        try:
            if now - os.path.getmtime(os.path.join(folder, old)) > VOICE_KEEP_SEC:
                os.remove(os.path.join(folder, old))
        except OSError:
            pass
    voice_id = secrets.token_hex(16) + ext
    path = os.path.join(folder, voice_id)
    with open(path, "wb") as f:
        f.write(data)
    seconds = _voice_seconds(path)
    if seconds is not None and seconds > VOICE_MAX_SEC:
        os.remove(path)
        return jsonify({"ok": False, "error": "voice_too_long"}), 413
    return jsonify({"ok": True, "data": {"id": voice_id}})


@app.route("/api/dedications/listen")
def api_dedication_listen():
    """The recording waiting with a song of Up next, for the owner to hear before it plays."""
    path = _path_for_key(request.args.get("key"))
    result = control("get_queue", n=50) if path else {}
    entry = ((result.get("data") or {}).get("dedications") or {}).get(path) if path else None
    voice = (entry or {}).get("voice")
    if not voice or os.path.dirname(os.path.realpath(voice)) != os.path.realpath(_voice_dir()) \
            or not os.path.isfile(voice):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return send_file(voice, conditional=True)


@app.route("/api/library/queue", methods=["POST"])
def api_library_queue():
    """{key, message?}: the song waits its turn after the current one and the
    songs asked for (taking turns between people); the message is said just
    before it."""
    body = request.get_json(silent=True) or {}
    path = _path_for_key(body.get("key"))
    if not path:
        return jsonify({"ok": False, "error": "not_found"}), 404
    person, name = None, None
    try:
        device = _this_device(_suggestion_box())
        person, name = device.get("person"), device.get("name")
    except Exception:  # noqa: BLE001 - no device base: first come, first served
        pass
    extra = {}
    message = " ".join(str(body.get("message") or "").split())[:DEDICATION_MAX]
    voice = None
    if body.get("voice"):
        voice = _voice_path(body.get("voice"))
        if not voice:
            return jsonify({"ok": False, "error": "not_found"}), 404
        if not cfg().get("DEDICATIONS_VOICE"):
            return jsonify({"ok": False, "error": "voice_dedications_off"}), 403
    if message or voice:
        if not cfg().get("DEDICATIONS_ENABLED"):
            return jsonify({"ok": False, "error": "dedications_off"}), 403
        extra["dedication"] = {"from": name, "text": message}
        if voice:
            extra["dedication"]["voice"] = voice
    result = control("queue_track", path=path, person=person, **extra)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/dedications/delete", methods=["POST"])
def api_dedication_delete():
    """{key}: the message waiting with this song is not said."""
    path = _path_for_key((request.get_json(silent=True) or {}).get("key"))
    if not path:
        return jsonify({"ok": False, "error": "not_found"}), 404
    result = control("drop_dedication", path=path)
    return jsonify(result), (200 if result.get("ok") else 404)


@app.route("/api/reminders")
def api_reminders():
    result = control("get_reminders")
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 503
    return jsonify(result)


@app.route("/api/reminders", methods=["POST"])
def api_reminder_add():
    """{text, minutes} or {text, time: "HH:MM"} (today, or tomorrow when the
    time has passed)."""
    body = request.get_json(silent=True) or {}
    now = datetime.now()
    if body.get("time"):
        try:
            hour, minute = (int(x) for x in str(body["time"]).split(":"))
            when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "reminder_bad_time"}), 400
        if when <= now:
            when += timedelta(days=1)
        at = when.timestamp()
    else:
        try:
            minutes = float(body.get("minutes"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "reminder_bad_time"}), 400
        if not 1 <= minutes <= 24 * 60:
            return jsonify({"ok": False, "error": "reminder_bad_time"}), 400
        at = time.time() + minutes * 60
    result = control("add_reminder", at=at, text=str(body.get("text") or ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/reminders/<rid>", methods=["DELETE"])
def api_reminder_delete(rid):
    result = control("delete_reminder", id=rid)
    return jsonify(result), (200 if result.get("ok") else 404)


@app.route("/api/library/play", methods=["POST"])
def api_library_play():
    """{key}: plays a song of Up next NOW."""
    path = _path_for_key((request.get_json(silent=True) or {}).get("key"))
    if not path:
        return jsonify({"ok": False, "error": "not_found"}), 404
    upcoming = control("get_queue", n=50)
    if not upcoming.get("ok"):
        return jsonify({"ok": False, "error": upcoming.get("error", "daemon_unreachable")}), 503
    if path not in ((upcoming.get("data") or {}).get("paths") or []):
        return jsonify({"ok": False, "error": "not_up_next"}), 409
    result = control("play_track", path=path)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/queue")
def api_queue():
    """The next UPCOMING_TRACKS_COUNT songs, by name."""
    try:
        count = max(0, min(50, int(cfg().get("UPCOMING_TRACKS_COUNT", 10))))
    except (TypeError, ValueError):
        count = 10
    if count == 0:
        return jsonify({"ok": True, "data": {"enabled": False, "items": []}})
    result = control("get_queue", n=count)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 503
    lib = _get_library()
    items = []
    requested = set((result.get("data") or {}).get("requested") or [])
    dedications = (result.get("data") or {}).get("dedications") or {}
    show_dedications = bool(cfg().get("DEDICATIONS_ENABLED"))
    excluded = _excluded_keys()
    for path in (result.get("data") or {}).get("paths") or []:
        item = lib.item_for_path(path)
        if item is None:
            tags = track_media.cached_tags(path) or {}
            item = {"key": track_media.track_key(path), "title": tags.get("title"),
                    "artist": tags.get("artist"), "album": tags.get("album")}
        item["name"] = os.path.splitext(os.path.basename(path))[0]
        item["requested"] = path in requested
        item["excluded"] = item.get("key") in excluded
        if show_dedications and isinstance(dedications.get(path), dict):
            item["dedication"] = {"from": dedications[path].get("from"),
                                  "text": dedications[path].get("text"),
                                  "voice": bool(dedications[path].get("voice"))}
        items.append(item)
    return jsonify({"ok": True, "data": {"enabled": True, "items": items}})


def _lists_path():
    return cfg()["MUSIC_LISTS_FILE"]


def _with_counts(entries):
    """Lists as the interface shows them: how many tracks each stands for
    right now, which is what the radio would play."""
    lib = _get_library()
    tracks = get_music_list(_effective_music_dir(), cfg()["MUSIC_CACHE_FILE"])
    return [dict(entry, count=len(music_lists.resolved(entry, tracks, lib.paths_for_genres)))
            for entry in entries]


def _active_list_id():
    """The list the daemon is playing, or None - including when it cannot be
    reached, in which case "everything" is what it will play."""
    try:
        status = control("get_status")
    except Exception:  # noqa: BLE001
        log.exception("Could not read the active list from the daemon")
        return None
    return (((status.get("data") or {}).get("active_list") or {}).get("id")
            if status.get("ok") else None)


def _schedules_path():
    return cfg()["SCHEDULES_FILE"]


@app.route("/api/schedules")
def api_schedules():
    """Every schedule, and the settings one may hold for itself."""
    return jsonify({"ok": True, "data": {
        "schedules": schedules.load(_schedules_path()),
        "settings": sorted(schedules.OVERRIDABLE),
    }})


@app.route("/api/schedules", methods=["POST"])
def api_create_schedule():
    body = request.get_json(silent=True) or {}
    try:
        entry = schedules.add(_schedules_path(), body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    notify_daemon("reload_schedules")
    stats.record("schedule_added", label=entry["name"], detail={"id": entry["id"]})
    return jsonify({"ok": True, "data": entry})


@app.route("/api/schedule_order", methods=["POST"])
def api_order_schedules():
    """{order: [id, ...]}: the first of the list wins when two overlap."""
    body = request.get_json(silent=True) or {}
    try:
        items = schedules.reorder(_schedules_path(), body.get("order"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    notify_daemon("reload_schedules")
    stats.record("schedule_changed", label="order", detail={"order": [item["id"] for item in items]})
    return jsonify({"ok": True, "data": {"schedules": items}})


@app.route("/api/schedules/<schedule_id>", methods=["POST"])
def api_update_schedule(schedule_id):
    body = request.get_json(silent=True) or {}
    try:
        entry = schedules.update(_schedules_path(), schedule_id, body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_schedules")
    stats.record("schedule_changed", label=entry["name"],
                 detail={"id": schedule_id, "changes": sorted(body)})
    return jsonify({"ok": True, "data": entry})


@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def api_delete_schedule(schedule_id):
    try:
        schedules.delete(_schedules_path(), schedule_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_schedules")
    stats.record("schedule_removed", label=schedule_id)
    return jsonify({"ok": True})


@app.route("/api/lists")
def api_lists():
    return jsonify({"ok": True, "data": {
        "lists": _with_counts(music_lists.load(_lists_path())),
        "active": _active_list_id(),
    }})


@app.route("/api/lists", methods=["POST"])
def api_create_list():
    body = request.get_json(silent=True) or {}
    try:
        entry = music_lists.add(_lists_path(), body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    notify_daemon("reload_lists")
    stats.record("list_added", label=entry["name"],
                 detail={"id": entry["id"], "kind": entry["kind"]})
    return jsonify({"ok": True, "data": _with_counts([entry])[0]})


@app.route("/api/lists/<list_id>", methods=["POST"])
def api_update_list(list_id):
    body = request.get_json(silent=True) or {}
    try:
        entry = music_lists.update(_lists_path(), list_id, body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_lists")
    stats.record("list_changed", label=entry["name"],
                 detail={"id": list_id, "changes": sorted(body)})
    return jsonify({"ok": True, "data": _with_counts([entry])[0]})


@app.route("/api/lists/<list_id>", methods=["DELETE"])
def api_delete_list(list_id):
    try:
        entry = music_lists.get(_lists_path(), list_id)
        music_lists.delete(_lists_path(), list_id)
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_lists")
    if _active_list_id() == list_id:
        notify_daemon("set_active_list", id=None)
    stats.record("list_removed", label=entry["name"], detail={"id": list_id})
    return jsonify({"ok": True})


@app.route("/api/lists/<list_id>/tracks")
def api_list_tracks(list_id):
    """What a list holds right now, as catalogue rows: the contents of a
    manual list (with the files it kept that are gone from the library), or
    what a genre list resolves to."""
    try:
        entry = music_lists.get(_lists_path(), list_id)
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    lib = _get_library()
    missing = []
    if entry.get("kind") == "genre":
        items = lib.items_for_paths(lib.paths_for_genres(entry.get("genres") or []))
    else:
        stored = list(entry.get("tracks") or [])
        items = lib.items_for_paths(stored)
        known = {item["path"] for item in items}
        missing = [
            {"key": None, "path": path, "title": os.path.splitext(os.path.basename(path))[0],
             "missing": True}
            for path in stored if path not in known
        ]
        items = items + missing
    excluded = _excluded_keys()
    for item in items:
        item["excluded"] = item.get("key") in excluded
    return jsonify({"ok": True, "data": {"items": items, "missing": len(missing)}})


def _stored_track(body):
    """The path a request names: an opaque key from the library, or a path
    already in the list (what removing a file the library no longer knows
    needs)."""
    return str(body.get("path") or "").strip() or _path_for_key(body.get("key"))


@app.route("/api/lists/<list_id>/tracks", methods=["POST"])
def api_add_list_track(list_id):
    body = request.get_json(silent=True) or {}
    path = _stored_track(body)
    if not path:
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        entry = music_lists.add_track(_lists_path(), list_id, path)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_lists")
    stats.record("list_changed", label=entry["name"],
                 detail={"id": list_id, "added": os.path.basename(path)})
    return jsonify({"ok": True, "data": {"name": entry["name"], "count": len(entry["tracks"])}})


@app.route("/api/lists/<list_id>/tracks", methods=["DELETE"])
def api_remove_list_track(list_id):
    body = request.get_json(silent=True) or {}
    path = _stored_track(body)
    if not path:
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        entry = music_lists.remove_track(_lists_path(), list_id, path)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_lists")
    stats.record("list_changed", label=entry["name"],
                 detail={"id": list_id, "removed": os.path.basename(path)})
    return jsonify({"ok": True, "data": {"name": entry["name"], "count": len(entry["tracks"])}})


@app.route("/api/lists/active", methods=["POST"])
def api_active_list():
    """{id: null | "list-id", start: true}: what the radio plays from the next
    track on, or right now."""
    body = request.get_json(silent=True) or {}
    list_id = str(body.get("id") or "").strip() or None
    if list_id and not any(entry["id"] == list_id for entry in music_lists.load(_lists_path())):
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_lists")
    result = control("set_active_list", id=list_id, start=bool(body.get("start")))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/lists/from_genres", methods=["POST"])
def api_list_from_genres():
    """{genres: [...], start}: the list for these genres, the one that already
    matches or a fresh one - what the library's genre filter offers."""
    body = request.get_json(silent=True) or {}
    genres = [str(g).strip() for g in (body.get("genres") or []) if str(g).strip()]
    if not genres:
        return jsonify({"ok": False, "error": "list_genres_required"}), 400
    wanted = {g.casefold() for g in genres}
    entry = next((item for item in music_lists.load(_lists_path())
                  if item.get("kind") == "genre"
                  and {g.casefold() for g in (item.get("genres") or [])} == wanted), None)
    if entry is None:
        try:
            entry = music_lists.add(_lists_path(), {"name": music_lists.genre_list_name(genres),
                                                    "kind": "genre", "genres": genres})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        stats.record("list_added", label=entry["name"],
                     detail={"id": entry["id"], "kind": "genre"})
    notify_daemon("reload_lists")
    result = control("set_active_list", id=entry["id"], start=bool(body.get("start", True)))
    if not result.get("ok"):
        return jsonify(result), 400
    data = dict(result.get("data") or {})
    data["id"] = entry["id"]
    data["name"] = entry["name"]
    return jsonify({"ok": True, "data": data})


def _output_fallback_state():
    """Whether the planned output is missing, and which others are there."""
    c = cfg()
    planned = c.get("AUDIO_OUTPUT", "bluetooth")
    sinks = audio_output.list_sinks(env=_user_session_env())
    kinds = sorted({s["kind"] for s in sinks if s["kind"] in audio_output.KINDS})
    if planned == "bluetooth":
        mac = c.get("SPEAKER_MAC", "")
        link = _status_probe(("speaker", mac), lambda: _speaker_link(mac))
        # A controller that did not answer is not proof that the speaker is gone.
        missing = not (mac and link["connected"])
    else:
        missing = planned not in kinds
    status = control("get_status")
    override = (status.get("data") or {}).get("output_override") if status.get("ok") else None
    return {"planned": planned, "missing": missing, "override": override,
            "outputs": [k for k in kinds if k != planned]}


@app.route("/api/audio/fallback", methods=["GET", "POST"])
def api_audio_fallback():
    """Another output for as long as the planned one is missing."""
    state = _output_fallback_state()
    if request.method == "GET":
        return jsonify({"ok": True, "data": state})
    kind = (request.get_json(silent=True) or {}).get("output")
    if kind is not None and kind not in state["outputs"]:
        return jsonify({"ok": False, "error": "bad_output"}), 400
    if kind is not None and not state["missing"]:
        return jsonify({"ok": False, "error": "output_not_missing"}), 400
    result = control("set_output_override", output=kind)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/usb_music", methods=["POST"])
def api_usb_music():
    """Uses a plugged USB key for the library, gives it back, or forgets it; `switch: "now"`
    also changes the song playing."""
    body = request.get_json(silent=True) or {}
    if body.get("forget"):
        answer = control("usb_music", forget=True)
    else:
        wanted = str(body.get("key") or body.get("device") or "").strip()
        options = {"key": wanted} if wanted else {}
        if str(body.get("switch") or "").strip().lower() == "now":
            options["switch"] = "now"
        answer = control("usb_music", **options)
    if not answer.get("ok"):
        return jsonify({"ok": False, "error": answer.get("error", "daemon_unreachable"),
                        "detail": answer.get("detail")}), 409
    return jsonify({"ok": True, "data": answer.get("data")})


def _seen_on_the_network(box, mac, ip=None):
    """A device is "seen" from either side: associated to the access point, or
    talking to the interface. Only the second one is a request, and only the
    first one is visible to `iw`."""
    if not mac:
        return
    try:
        box.ensure_device_for_mac(mac, ip)
    except Exception:  # noqa: BLE001
        log.exception("Could not note the device seen on the network")


def _now_clients(box, stations):
    """Who is on the Rukebox now: the access point's stations, plus every
    device that talked to the interface a moment ago - a device on the owner's
    own network joins nothing of ours, so a request is all there is to see.
    Banned devices are nobody's business here: they have a page of their own."""
    banned = box.banned_devices()
    banned_ids = {member for b in banned for member in b["members"]}
    banned_macs = {mac for b in banned for mac in b["macs"]}
    me = _this_device(box)
    clients, on_ap = [], set()
    for mac, info in sorted((stations or {}).items(), key=lambda kv: -(kv[1].get("connected_sec") or 0)):
        on_ap.add(mac)
        ip = _ip_for_mac(mac)
        _seen_on_the_network(box, mac, ip)
        if mac in banned_macs:
            continue
        device = box.device_by_mac(mac)
        if device and device["id"] in banned_ids:
            continue
        entry = {"mac": mac, "ip": ip, "on_ap": True, **info}
        # The device's own "Finish connecting" tap, not whether the portal still holds it.
        entry["portal_released"] = _portal_tap_remembered(entry["ip"] or "", mac)
        if device:
            entry.update(box.device_summary(device))
            entry["me"] = device["id"] == me["id"]
        clients.append(entry)
    for device_id, age in sorted(_recently_seen().items(), key=lambda kv: kv[1]):
        if device_id in banned_ids:
            continue
        device = box.device_by_id(device_id)
        if not device or (device.get("mac") or "").lower() in on_ap:
            continue
        entry = {"mac": device.get("mac"), "ip": device.get("last_ip"), "on_ap": False,
                 "seen_sec": int(age)}
        entry["portal_released"] = _portal_tap_remembered(entry["ip"] or "", entry["mac"])
        entry.update(box.device_summary(device))
        entry["me"] = device_id == me["id"]
        clients.append(entry)
    return clients


@app.route("/api/wifi/clients")
def api_wifi_clients():
    """Who is on the Rukebox right now."""
    box = _suggestion_box()
    stations = _stations()
    return jsonify({"ok": True, "data": {
        "readable": stations is not None,
        "clients": _now_clients(box, stations),
    }})


def _ip_for_mac(mac):
    try:
        with open("/proc/net/arp") as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[3].lower() == mac:
                    return parts[0]
    except OSError:
        pass
    return None


@app.route("/api/wifi/clients/disconnect", methods=["POST"])
def api_wifi_client_disconnect():
    mac = str((request.get_json(silent=True) or {}).get("mac") or "").lower()
    if not _MAC_ARG_RE.match(mac):
        return jsonify({"ok": False, "error": "bad_mac"}), 400
    if not _kick_mac(mac):
        return jsonify({"ok": False, "error": "disconnect_failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/devices/ban", methods=["POST"])
def api_device_ban():
    """{device_id | mac, minutes (null: for good)}."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    device = None
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if body.get("lift"):
        box.set_ban(device["id"], None)
        stats.record("device_unbanned", label=device.get("name") or device["id"])
        _bans_changed()
        return jsonify({"ok": True})
    if device["person"] == _this_device(box)["person"]:
        return jsonify({"ok": False, "error": "cannot_ban_self"}), 400
    minutes = body.get("minutes")
    try:
        until = -1 if minutes in (None, "", 0) else time.time() + max(1, int(minutes)) * 60
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_value"}), 400
    box.set_ban(device["id"], until)
    stats.record("device_banned", label=device.get("name") or device["id"],
                 detail={"minutes": minutes})
    _bans_changed()
    return jsonify({"ok": True})


@app.route("/api/devices/portal", methods=["POST"])
def api_device_portal():
    """{device_id | mac, mode: auto | always | never}: this device's captive
    portal, whatever the general rule."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    mode = body.get("mode")
    if mode not in ("auto", "always", "never"):
        return jsonify({"ok": False, "error": "invalid_value"}), 400
    box.set_portal(device["id"], None if mode == "auto" else mode)
    return jsonify({"ok": True})


@app.route("/api/devices/forget", methods=["POST"])
def api_device_forget():
    """{device_id | mac}: the Rukebox forgets this device altogether - or
    {unnamed: true}: every device that never took a name. Forgotten means
    greeted as a new device if it comes back, ban included."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("unnamed"):
        # The sweeping device is spared: its own row is unnamed too.
        count = box.forget_unnamed(keep=_this_device(box)["id"])
        if count:
            stats.record("device_forgotten", label="unnamed", detail={"devices": count})
        return jsonify({"ok": True, "data": {"forgotten": count}})
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        device = box.device_by_mac(str(body["mac"]).lower())
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    box.forget_device(device["id"])
    stats.record("device_forgotten", label=device.get("name") or device["id"],
                 detail={"mac": device.get("mac")})
    return jsonify({"ok": True, "data": {"forgotten": 1}})


@app.route("/api/devices/seen")
def api_devices_seen():
    """The devices the Rukebox saw this week and which are not on it now: the
    other half of Connected devices, where a device that left can still be
    named, spared the credits, or sent back to the portal."""
    box = _suggestion_box()
    here = {c.get("device_id") for c in _now_clients(box, _stations())}
    since = time.time() - PREVIOUS_DAYS * 86400
    devices = [d for d in box.seen_devices(since, PREVIOUS_MAX)
               if d["device_id"] not in here and d.get("banned_until") is None]
    return jsonify({"ok": True, "data": {"devices": devices}})


@app.route("/api/devices/banned")
def api_devices_banned():
    """The banned devices, which are in neither of the other two lists."""
    return jsonify({"ok": True, "data": {"banned": _suggestion_box().banned_devices()}})


@app.route("/api/devices/portal_release", methods=["POST"])
def api_device_portal_release():
    """{device_id | mac}: forgets this device's tap on "Finish connecting", so
    the portal holds it again at its next connection."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    mac = device.get("mac") or ""
    forgotten = _portal_forget(mac, _ip_for_mac(mac))
    stats.record("portal_reset", label=device.get("name") or device["id"],
                 detail={"mac": mac, "forgotten": forgotten})
    return jsonify({"ok": True, "data": {"forgotten": forgotten}})


@app.route("/api/devices/free_credits", methods=["POST"])
def api_device_free_credits():
    """{device_id | mac, on: bool}: spares this guest device the credits."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    on = bool(body.get("on"))
    box.set_free_credits(device["id"], on)
    stats.record("device_free_credits", label=device.get("name") or device["id"], detail={"on": on})
    return jsonify({"ok": True})


LINK_CODE_SECONDS = 300
LINK_CODE_TRIES = 5
LINK_LOCK_SECONDS = 300
_link_codes = {}
_link_tries = {}
_link_lock = threading.Lock()


def _device_in_body(box, body):
    if body.get("device_id"):
        return box.device_by_id(str(body["device_id"]))
    mac = str(body.get("mac") or "").lower()
    if _MAC_ARG_RE.match(mac):
        return box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    return None


def _devices_linked(box, device_id, to_id, by):
    """Links, and gives the joined person one credit counter instead of two."""
    was = box.person_id(device_id)
    person = box.link(device_id, to_id)
    with _quota_lock:
        _quota_load(time.time())
        if _quota.pop(was, None) is not None:
            _quota_save()
    named = box.device_by_id(person) or {}
    stats.record("devices_linked", label=named.get("name") or person, detail={"by": by})
    return person


@app.route("/api/devices/link_code", methods=["POST"])
def api_device_link_code():
    """A one-time code this device shows, for another device of the same
    person to type: that one then takes this one's name, votes and credits."""
    box = _suggestion_box()
    device = _this_device(box)
    now = time.time()
    with _link_lock:
        for code in [c for c, e in _link_codes.items() if e["until"] < now or e["device"] == device["id"]]:
            del _link_codes[code]
        code = "%06d" % secrets.randbelow(10 ** 6)
        while code in _link_codes:
            code = "%06d" % secrets.randbelow(10 ** 6)
        _link_codes[code] = {"device": device["id"], "until": now + LINK_CODE_SECONDS}
    return jsonify({"ok": True, "data": {"code": code, "expires_in": LINK_CODE_SECONDS}})


@app.route("/api/devices/link_join", methods=["POST"])
def api_device_link_join():
    """{code}: links this device to the one showing that code."""
    box = _suggestion_box()
    device = _this_device(box)
    code = re.sub(r"\D", "", str((request.get_json(silent=True) or {}).get("code") or ""))
    now = time.time()
    with _link_lock:
        tries = _link_tries.get(device["id"])
        if tries and tries["until"] > now:
            return _too_many_attempts(int(tries["until"] - now) + 1)
        entry = _link_codes.get(code)
        if entry and entry["until"] < now:
            del _link_codes[code]
            entry = None
        if entry is None:
            tries = _link_tries.setdefault(device["id"], {"fails": 0, "until": 0})
            tries["fails"] += 1
            if tries["fails"] >= LINK_CODE_TRIES:
                tries.update(fails=0, until=now + LINK_LOCK_SECONDS)
            return jsonify({"ok": False, "error": "link_code_bad"}), 400
        if entry["device"] == device["id"]:
            return jsonify({"ok": False, "error": "link_same_device"}), 400
        if box.name_locked(device["id"]) and not _is_owner():
            return jsonify({"ok": False, "error": "name_locked"}), 400
        del _link_codes[code]
        _link_tries.pop(device["id"], None)
    try:
        person = _devices_linked(box, device["id"], entry["device"], "code")
    except suggestions.SuggestionError as e:
        return jsonify({"ok": False, "error": e.code}), 400
    named = box.device_by_id(person) or {}
    return jsonify({"ok": True, "data": {"name": named.get("name")}})


@app.route("/api/devices/link", methods=["POST"])
def api_device_link():
    """{device_id | mac, to}: the owner makes this device one person with the
    device `to`, whose name, votes, credits and ban it takes."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    device = _device_in_body(box, body)
    target = box.device_by_id(str(body.get("to") or ""))
    if not device or not target:
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        _devices_linked(box, device["id"], target["id"], "owner")
    except suggestions.SuggestionError as e:
        return jsonify({"ok": False, "error": e.code}), 400
    _bans_changed()
    return jsonify({"ok": True})


@app.route("/api/devices/unlink", methods=["POST"])
def api_device_unlink():
    """{device_id | mac}: this device leaves its person, with nothing."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    device = _device_in_body(box, body)
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        _unlink_device(box, device, "owner")
    except suggestions.SuggestionError as e:
        return jsonify({"ok": False, "error": e.code}), 400
    _bans_changed()
    return jsonify({"ok": True})


def _unlink_device(box, device, by):
    """Unlinks, and leaves both sides the credits the person had: leaving
    must never be a way to a full counter."""
    others = [one["device_id"] for one in box.linked_devices(device["id"])]
    was = box.person_id(device["id"])
    box.unlink(device["id"])
    with _quota_lock:
        _quota_load(time.time())
        entry = _quota.get(was)
        if entry is not None:
            for person in {device["id"], box.person_id(others[0])}:
                _quota[person] = dict(entry, history={k: list(v) for k, v in entry["history"].items()})
            _quota_save()
    stats.record("device_unlinked", label=device.get("name") or device["id"], detail={"by": by})


@app.route("/api/devices/link_leave", methods=["POST"])
def api_device_link_leave():
    """This device leaves its person by itself, with nothing but the credits
    it shared."""
    box = _suggestion_box()
    device = _this_device(box)
    if box.name_locked(device["id"]) and not _is_owner():
        return jsonify({"ok": False, "error": "name_locked"}), 400
    try:
        _unlink_device(box, device, "device")
    except suggestions.SuggestionError as e:
        return jsonify({"ok": False, "error": e.code}), 400
    return jsonify({"ok": True})


@app.route("/api/devices/name", methods=["POST"])
def api_device_name():
    """{device_id | mac, name}: the owner names this device. The device's own
    rename is the other door, `/api/suggestions/name`, with its interval and
    its lock - this one is the owner's, so neither applies."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    was = device.get("name")
    try:
        name = box.set_name(device, body.get("name"), interval_sec=0, by_owner=True)
    except suggestions.SuggestionError as e:
        return jsonify({"ok": False, "error": e.code}), 400
    stats.record("device_renamed", label=name, detail={"from": was})
    return jsonify({"ok": True, "data": {"name": name}})


@app.route("/api/devices/name_locked", methods=["POST"])
def api_device_name_locked():
    """{device_id | mac, on: bool}: pins this device's name, so the device may
    no longer change it itself."""
    body = request.get_json(silent=True) or {}
    box = _suggestion_box()
    if body.get("device_id"):
        device = box.device_by_id(str(body["device_id"]))
    elif _MAC_ARG_RE.match(str(body.get("mac") or "").lower()):
        mac = str(body["mac"]).lower()
        device = box.ensure_device_for_mac(mac, _ip_for_mac(mac))
    else:
        device = None
    if not device:
        return jsonify({"ok": False, "error": "not_found"}), 404
    on = bool(body.get("on"))
    box.set_name_locked(device["id"], on)
    stats.record("device_name_locked", label=device.get("name") or device["id"], detail={"on": on})
    return jsonify({"ok": True})


# Written by scripts/update.sh; a flag left behind by a killed updater is ignored and removed.
UPDATE_FLAG_MAX_AGE_SEC = 900


def update_in_progress(c):
    """True while an update is running, from the flag update.sh writes."""
    path = os.path.join(c.get("STATE_DIR") or "/var/lib/rukebox", "updating")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    if age > UPDATE_FLAG_MAX_AGE_SEC:
        try:
            os.unlink(path)
        except OSError:
            pass
        return False
    return True


@app.route("/api/status/wait")
def api_status_wait():
    """Answers as soon as something the page shows changes (mode, file, pause)."""
    try:
        since = int(request.args.get("since", "-1"))
    except ValueError:
        since = -1
    box = {}
    socket_path = cfg()["CONTROL_SOCKET"]
    asking = threading.Thread(target=lambda: box.update(result=send_control_command(
        socket_path, "wait_change", timeout=30, since=since)), daemon=True)
    asking.start()
    while asking.is_alive() and not _going_down_event.is_set():
        asking.join(0.25)
    if _going_down:
        return jsonify({"ok": True, "data": {"version": since, "going_down": _going_down}})
    result = box.get("result") or {}
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "daemon_unreachable"}), 503
    return jsonify(result)


@app.route("/api/status")
def api_status():
    result = control("get_status")
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 503

    data = result["data"]
    powering_off = data.pop("powering_off", False)
    data["going_down"] = _going_down or (powering_off if isinstance(powering_off, str)
                                         else ("poweroff" if powering_off else None))
    data["updating"] = update_in_progress(cfg())
    data["epoch"] = time.time()
    track_path = data.pop("current_track_path", None)
    upcoming = data.pop("upcoming_track_path", None)
    _warm_track_media(upcoming)
    if upcoming:
        tags = track_media.cached_tags(upcoming) or {}
        data["next_track"] = {
            "title": tags.get("title"),
            "artist": tags.get("artist"),
            "name": os.path.splitext(os.path.basename(upcoming))[0],
        }
    else:
        data["next_track"] = None
    data["track_key"] = track_media.track_key(track_path)
    data["guest_pages_off"] = _guest_pages_off() if _quota_applies() else []
    data["dedications"] = bool(cfg().get("DEDICATIONS_ENABLED"))
    data["dedications_voice"] = bool(cfg().get("DEDICATIONS_ENABLED") and cfg().get("DEDICATIONS_VOICE"))
    data["skip_vote"] = _skip_vote_state(data["track_key"], _repeat_person()) \
        if data.get("mode") == "music" else None
    info = track_media.tags(track_path) if track_path else {}
    data["track_title"] = info.get("title")
    data["track_artist"] = info.get("artist")
    data["speaker_mac"] = cfg().get("SPEAKER_MAC", "")
    mac = data["speaker_mac"]
    link = _status_probe(("speaker", mac), lambda: _speaker_link(mac))
    _tell_daemon_if_speaker_changed(mac, link)
    # A controller that does not answer is not a speaker that is off.
    data["speaker_connected"] = None if link["unknown"] else link["connected"]
    data["speaker_paired"] = None if link["unknown"] else bool(link["connected"] or link["paired_here"])
    data["speaker_controller"] = link["controller"] if link["connected"] else None
    data["speaker_battery"] = link.get("battery") if link["connected"] else None
    data["speaker_battery_low"] = int(cfg().get("SPEAKER_BATTERY_LOW", 0) or 0)
    data["speaker_controller_kind"] = _controller_kind(link["controller"])
    data["speaker_expected"] = link["expected"]
    data["speaker_expected_kind"] = _controller_kind(link["expected"])
    data["audio_output"] = _audio_output_state()
    data["timezone"] = _timezone_name()
    data["ssh_active"] = _status_probe("ssh_active", lambda: _service_is_active("ssh"))
    data["ssh_enabled"] = _status_probe("ssh_enabled", lambda: _service_is_enabled("ssh"))
    data["flic_active"] = _status_probe("flic_active", lambda: _service_is_active("flic-bridge"))
    data["quota"] = _quota_status()
    data["guest_locked"] = _guest_locked() if _quota_applies() else []
    # What this machine can do, so the page hides the cards that lead nowhere
    # rather than offering buttons that answer unsupported_here.
    data["capabilities"] = capabilities()
    # The network output, when there is one: the player's "Listen here" button
    # is the reason it is here rather than only on /api/stream.
    data["stream"] = _stream_status()
    return jsonify({"ok": True, "data": data})


STATUS_PROBE_TTL = 5
_status_probes = {}
_status_probes_lock = threading.Lock()


def _status_probe(key, compute):
    now = time.monotonic()
    with _status_probes_lock:
        hit = _status_probes.get(key)
        if hit and now - hit[0] < STATUS_PROBE_TTL:
            return hit[1]
    value = compute()
    with _status_probes_lock:
        _status_probes[key] = (now, value)
    return value


@app.after_request
def _forget_status_probes(response):
    if request.method == "POST":
        with _status_probes_lock:
            _status_probes.clear()
    return response


@app.route("/api/action/<name>", methods=["POST"])
def api_action(name):
    if name not in ("single_click", "double_click", "long_press", "start_music",
                    "next_track", "previous_track", "toggle_pause", "skip_sound", "standby"):
        return jsonify({"ok": False, "error": "unknown_action"}), 404
    result = control(name)
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


GAME_KEPT_SEC = 600
_game = None
_game_lock = threading.Lock()


def _game_player():
    """(person, name) of whoever asks."""
    try:
        device = _this_device(_suggestion_box())
        return device["person"], device.get("name") or None
    except Exception:  # noqa: BLE001
        return request.remote_addr, None


def _game_run(game):
    """Plays the rounds one after the other, then gives the radio back."""
    try:
        while not game.stopped:
            question = game.next_question()
            if question is None:
                break
            result = control("game_clip", path=question["path"], start=question["start"],
                             seconds=game.clip_sec)
            if not result.get("ok"):
                game.finish(result.get("error") or "daemon_unreachable")
                return
            question["started"] = time.monotonic()
            deadline = question["started"] + game.clip_sec
            while time.monotonic() < deadline and not game.stopped and not game.everyone_answered():
                time.sleep(0.25)
            game.close_round()
            reveal_end = time.monotonic() + blind_test.REVEAL_SEC
            while time.monotonic() < reveal_end and not game.stopped:
                time.sleep(0.25)
        game.finish()
        best = game.view(None)["scores"][:1]
        stats.record("game_over", label=best[0]["name"] if best else "-",
                     detail={"rounds": game.round, "players": len(game.scores)})
    except Exception:  # noqa: BLE001
        log.exception("Blind test failed")
        game.finish("game_failed")
    finally:
        control("game_end")


@app.route("/api/game")
def api_game():
    person, name = _game_player()
    game = _game
    options = {"owner": _is_owner(), "round_choices": list(blind_test.ROUNDS),
               "second_choices": list(blind_test.CLIP_SECONDS)}
    if game is None or (game.state == "over" and time.monotonic() - game.ended_at > GAME_KEPT_SEC):
        return jsonify({"ok": True, "data": dict(options, state="none")})
    game.seen(person, name)
    return jsonify({"ok": True, "data": dict(game.view(person), **options)})


@app.route("/api/game/start", methods=["POST"])
def api_game_start():
    global _game
    body = request.get_json(silent=True) or {}
    try:
        rounds, seconds = int(body.get("rounds", 10)), int(body.get("seconds", 20))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad_request"}), 400
    if rounds not in blind_test.ROUNDS or seconds not in blind_test.CLIP_SECONDS:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    with _game_lock:
        if _game is not None and _game.state != "over":
            return jsonify({"ok": False, "error": "game_running"}), 409
        tracks = blind_test.Game.playable(_get_library().quiz_tracks(), seconds)
        game = blind_test.Game(tracks, rounds, seconds)
        if not game.enough():
            return jsonify({"ok": False, "error": "game_not_enough_tracks"}), 409
        _game = game
    stats.record("game_started", label="%d x %ds" % (rounds, seconds), detail={"rounds": rounds})
    threading.Thread(target=_game_run, args=(game,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/game/answer", methods=["POST"])
def api_game_answer():
    game = _game
    if game is None:
        return jsonify({"ok": False, "error": "game_not_asking"}), 409
    person, name = _game_player()
    choice = (request.get_json(silent=True) or {}).get("choice")
    error = game.answer(person, name, choice if isinstance(choice, int) else -1)
    if error:
        return jsonify({"ok": False, "error": error}), 409
    return jsonify({"ok": True, "data": game.view(person)})


@app.route("/api/game/stop", methods=["POST"])
def api_game_stop():
    game = _game
    if game is not None and game.state != "over":
        game.stop()
    return jsonify({"ok": True})


SKIP_VOTE_MIN = 2
_skip_votes = {"key": None, "persons": set()}
_skip_votes_lock = threading.Lock()


def _skip_vote_state(key, person):
    """{votes, needed, mine} for the song `key`; None when there is no vote to hold:
    switched off, nothing playing, or fewer than two people with the page open."""
    c = cfg()
    if not c.get("SKIP_VOTE_ENABLED", True) or not key:
        return None
    try:
        person_of = _suggestion_box().person_id
    except Exception:  # noqa: BLE001 - without the devices' base, a device is a person
        person_of = str
    present = {person_of(device_id) for device_id in _recently_seen()}
    present.add(person)
    if len(present) < SKIP_VOTE_MIN:
        return None
    try:
        share = max(1, min(100, int(c.get("SKIP_VOTE_SHARE", 50) or 50)))
    except (TypeError, ValueError):
        share = 50
    needed = min(len(present), max(SKIP_VOTE_MIN, len(present) * share // 100 + 1))
    with _skip_votes_lock:
        if _skip_votes["key"] != key:
            _skip_votes.update(key=key, persons=set())
        return {"votes": len(_skip_votes["persons"]), "needed": needed,
                "mine": person in _skip_votes["persons"]}


@app.route("/api/vote/skip", methods=["POST"])
def api_vote_skip():
    """One vote per person and per song; enough of them, and the song changes."""
    status = control("get_status")
    data = (status.get("data") or {}) if status.get("ok") else {}
    if data.get("mode") != "music":
        return jsonify({"ok": False, "error": "not_playing_music"}), 409
    key = track_media.track_key(data.get("current_track_path"))
    person = _repeat_person()
    state = _skip_vote_state(key, person)
    if state is None:
        return jsonify({"ok": False, "error": "vote_unavailable"}), 409
    with _skip_votes_lock:
        _skip_votes["persons"].add(person)
        votes = len(_skip_votes["persons"])
        reached = votes >= state["needed"]
        if reached:
            _skip_votes.update(key=None, persons=set())
    if reached:
        result = control("next_track", source="vote")
        if not result.get("ok"):
            return jsonify(result), 409
        stats.record("skip_voted", label="%d / %d" % (votes, state["needed"]),
                     detail={"votes": votes, "needed": state["needed"]})
    return jsonify({"ok": True, "data": {"votes": votes, "needed": state["needed"], "mine": True,
                                         "skipped": reached}})


@app.route("/api/action/poweroff", methods=["POST"])
def api_action_poweroff():
    """Switches the Pi off, whatever the long press is set to do."""
    result = control("poweroff")
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/mute", methods=["POST"])
def api_mute():
    """{on: true | false | "toggle"}: mpv's mute, the volume untouched."""
    on = (request.get_json(silent=True) or {}).get("on", "toggle")
    result = control("set_mute", on=on if on == "toggle" else bool(on))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/today")
def api_today():
    """Today in a few figures, guests included."""
    data = stats.today_summary(limit=3)
    if data is None:
        return jsonify({"ok": True, "data": {"enabled": False}})
    lib = _get_library()
    for entry in data["top"]:
        item = lib.item_for_basename(entry["name"])
        entry["title"] = (item or {}).get("title") or os.path.splitext(entry["name"])[0]
        entry["artist"] = (item or {}).get("artist")
        entry.pop("name")
    data["enabled"] = True
    return jsonify({"ok": True, "data": data})


@app.route("/api/action/timed_pause", methods=["POST"])
def api_action_timed_pause():
    """"Pause for N minutes" (N from PAUSE_DURATIONS, checked by the daemon),
    then the music starts again by itself."""
    body = request.get_json(silent=True) or {}
    result = control("timed_pause", minutes=body.get("minutes"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/action/sleep_timer", methods=["POST"])
def api_action_sleep_timer():
    """{"on": false} cancels the sleep timer."""
    body = request.get_json(silent=True) or {}
    result = control("sleep_timer", on=bool(body.get("on", True)), minutes=body.get("minutes"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/loop", methods=["POST"])
def api_loop():
    """{"mode": "off" | "track" | "album" | "cycle"}."""
    body = request.get_json(silent=True) or {}
    result = control("set_loop", mode=body.get("mode"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/action/test_click", methods=["POST"])
def api_action_test_click():
    """The ▶ beside a click action in the Buttons card: runs that action with
    the values the page shows."""
    body = request.get_json(silent=True) or {}
    result = control("test_click", kind=body.get("kind"), action=body.get("action"),
                     sound=body.get("sound"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/volume", methods=["POST"])
def api_volume():
    body = request.get_json(silent=True) or {}
    if "value" not in body:
        return jsonify({"ok": False, "error": "missing_value"}), 400
    result = control("set_volume", value=body["value"])
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/rescan_music", methods=["POST"])
def api_rescan_music():
    result = control("rescan_music")
    _library_wake.set()
    return jsonify(result), (200 if result.get("ok") else 400)


def _announcements_path():
    return cfg()["ANNOUNCEMENTS_FILE"]


@app.route("/api/announcements")
def api_list_announcements():
    items = announcements.load(_announcements_path())
    for item in items:
        item["file_count"] = announcements.count_files(item.get("folder"))
    return jsonify({"ok": True, "data": items})


def _default_announcements_parent():
    """Where an announcement created without a folder gets one: beside the
    built-in announcement folders."""
    base = os.path.dirname(str(cfg().get("MEME_DIR", "")).rstrip("/")) or "/home/pi/audio"
    return os.path.join(base, "announcements")


@app.route("/api/announcements", methods=["POST"])
def api_create_announcement():
    body = request.get_json(silent=True) or {}
    try:
        item = announcements.add(_announcements_path(), body,
                                 default_parent=_default_announcements_parent())
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    notify_daemon("reload_announcements")
    stats.record(
        "announcement_type_added", label=item["name"],
        detail={"id": item["id"], "folder": item["folder"]},
    )
    return jsonify({"ok": True, "data": item})


@app.route("/api/announcements/<item_id>", methods=["POST"])
def api_update_announcement(item_id):
    body = request.get_json(silent=True) or {}
    try:
        item = announcements.update(_announcements_path(), item_id, body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_announcements")
    stats.record("announcement_type_updated", label=item["name"], detail={"id": item_id, "changes": sorted(body)})
    return jsonify({"ok": True, "data": item})


@app.route("/api/announcements/<item_id>", methods=["DELETE"])
def api_delete_announcement(item_id):
    try:
        announcements.delete(_announcements_path(), item_id)
    except KeyError:
        return jsonify({"ok": False, "error": "not_found"}), 404
    notify_daemon("reload_announcements")
    stats.record("announcement_type_removed", label=item_id)
    return jsonify({"ok": True})


@app.route("/api/announcement_volumes")
def api_announcement_volumes():
    """The volume each announcement source and System sound plays at, if it
    has one of its own."""
    return jsonify({"ok": True, "data": announcements.volumes(_announcements_path())})


@app.route("/api/announcement_volumes/<path:key>", methods=["POST"])
def api_set_announcement_volume(key):
    body = request.get_json(silent=True) or {}
    try:
        entry = announcements.set_volume(_announcements_path(), key, body)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    notify_daemon("reload_announcements")
    stats.record("announcement_volume_set", label=key, detail=entry)
    return jsonify({"ok": True, "data": entry})


@app.route("/api/action/announce", methods=["POST"])
def api_action_announce():
    """Plays one announcement folder on demand, chosen in the interface."""
    body = request.get_json(silent=True) or {}
    source = body.get("source")
    if not source:
        return jsonify({"ok": False, "error": "no_source"}), 400
    try:
        result = control("play_announcement", source=source)
    except Exception:  # noqa: BLE001
        log.exception("Could not reach the daemon to play an announcement")
        result = {"ok": False, "error": "daemon_unreachable"}
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/announcements/<item_id>/play", methods=["POST"])
def api_play_announcement(item_id):
    try:
        result = control("play_announcement", id=item_id)
    except Exception:  # noqa: BLE001
        log.exception("Could not reach the daemon to play an announcement")
        result = {"ok": False, "error": "daemon_unreachable"}
    return jsonify(result), (200 if result.get("ok") else 400)


def _resolve_track_order_folder(source_id):
    custom_items = announcements.load(_announcements_path())
    return announcements.resolve_source_folder(cfg(), custom_items, source_id)


def _list_audio_basenames(folder):
    if not folder or not os.path.isdir(folder):
        return []
    return [
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in announcements.AUDIO_EXTENSIONS
        and os.path.isfile(os.path.join(folder, f))
    ]


@app.route("/api/track_order/<path:source_id>")
def api_get_track_order(source_id):
    folder = _resolve_track_order_folder(source_id)
    if folder is None:
        return jsonify({"ok": False, "error": "unknown_source"}), 404
    basenames = _list_audio_basenames(folder)
    custom_order = track_order.get(cfg()["TRACK_ORDER_FILE"], source_id)
    ordered = playlist.build_ordered(basenames, custom_order)
    return jsonify({"ok": True, "data": {
        "folder": folder,
        "order": ordered,
        "customized": bool(custom_order),
    }})


@app.route("/api/track_order/<path:source_id>", methods=["POST"])
def api_set_track_order(source_id):
    folder = _resolve_track_order_folder(source_id)
    if folder is None:
        return jsonify({"ok": False, "error": "unknown_source"}), 404
    body = request.get_json(silent=True) or {}
    order = body.get("order")
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "missing_order"}), 400
    try:
        track_order.save_order(cfg()["TRACK_ORDER_FILE"], source_id, order)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    stats.record("track_order_changed", label=source_id, detail={"count": len(order)})
    notify_daemon("reset_click_bag", source_id=source_id)
    return jsonify({"ok": True})


def _announce_files_folder(source_id):
    """(folder, error_code): the source's folder if files may be written to it,
    created if it does not exist yet."""
    folder = _resolve_track_order_folder(source_id)
    if folder is None:
        return None, "unknown_source"
    parent = os.path.realpath(os.path.dirname(folder.rstrip("/")) or "/")
    if not _inside_roots(parent) and not _inside_roots(os.path.realpath(folder)):
        return None, "folder_not_allowed"
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None, "write_failed"
    if not _inside_roots(os.path.realpath(folder)):
        return None, "folder_not_allowed"
    return folder, None


def _announce_file_name(raw):
    """A bare, visible audio file name, or None."""
    name = os.path.basename(str(raw or "").replace("\\", "/")).strip()
    if not name or name.startswith(".") or "\x00" in name:
        return None
    if os.path.splitext(name)[1].lower() not in announcements.AUDIO_EXTENSIONS:
        return None
    return name


def _store_upload(upload, dest, mtime_field):
    """Writes an uploaded file beside `dest` and renames it into place."""
    temporary = dest + ".rukebox-part"
    try:
        upload.save(temporary)
        os.replace(temporary, dest)
    except OSError as exc:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        log.warning("Upload failed for %s: %s", dest, exc)
        return "write_failed"
    try:
        mtime = float(mtime_field or "0")
        if mtime > 0:
            os.utime(dest, (mtime, mtime))
    except (TypeError, ValueError, OSError):
        pass
    return None


SYSTEM_SOUND_MAX_BYTES = 20 * 1024 * 1024


def _system_sound_custom_dir(key, values=None):
    """Where a replacement sound is uploaded."""
    values = cfg() if values is None else values
    current = str(values.get(key) or "").strip() or DEFAULTS[key]
    return os.path.join(os.path.dirname(current), "custom")


def _is_system_sound_file(key, filename):
    """A custom file for `key`: <key>__<original name>.<ext>."""
    return filename.startswith(key.lower() + "__")


def _system_sound_info(key, values):
    current = values.get(key) or ""
    default = DEFAULTS[key]
    name = os.path.splitext(os.path.basename(current))[0] if current else None
    if name and _is_system_sound_file(key, name):
        name = name[len(key) + 2:]
    return {
        "key": key,
        "custom": bool(current) and current != default,
        "off": not current,
        "name": name,
        "exists": bool(current) and os.path.exists(current),
    }


@app.route("/api/system_sounds")
def api_system_sounds():
    values = cfg()
    return jsonify({"ok": True, "data": [_system_sound_info(k, values) for k in config_schema.SYSTEM_SOUNDS]})


@app.route("/api/system_sounds/<key>", methods=["POST"])
def api_system_sound_upload(key):
    if key not in config_schema.SYSTEM_SOUNDS:
        return jsonify({"ok": False, "error": "unknown_sound"}), 404
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"ok": False, "error": "no_file"}), 400
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in announcements.AUDIO_EXTENSIONS:
        return jsonify({"ok": False, "error": "not_audio", "detail": str(upload.filename)[:120]}), 400
    if (request.content_length or 0) > SYSTEM_SOUND_MAX_BYTES:
        return jsonify({"ok": False, "error": "file_too_big", "detail": upload.filename}), 400
    folder = _system_sound_custom_dir(key)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return jsonify({"ok": False, "error": "write_failed"}), 500
    stem = re.sub(r"[^\w .-]+", "_", os.path.splitext(os.path.basename(upload.filename or ""))[0]).strip(" .")[:60] or "sound"
    filename = "%s__%s%s" % (key.lower(), stem, ext)
    dest = os.path.join(folder, filename)
    error = _store_upload(upload, dest, None)
    if error:
        return jsonify({"ok": False, "error": error}), 500
    for other in os.listdir(folder):
        if other != filename and _is_system_sound_file(key, other):
            try:
                os.remove(os.path.join(folder, other))
            except OSError:
                pass
    update_config_file({key: dest})
    notify_daemon("reload_config")
    stats.record("system_sound_set", label=key)
    return jsonify({"ok": True, "data": _system_sound_info(key, cfg())})


@app.route("/api/system_sounds/<key>", methods=["DELETE"])
def api_system_sound_reset(key):
    """Back to the shipped sound."""
    if key not in config_schema.SYSTEM_SOUNDS:
        return jsonify({"ok": False, "error": "unknown_sound"}), 404
    folder = _system_sound_custom_dir(key)
    if os.path.isdir(folder):
        for other in os.listdir(folder):
            if _is_system_sound_file(key, other):
                try:
                    os.remove(os.path.join(folder, other))
                except OSError:
                    pass
    update_config_file({key: DEFAULTS[key]})
    notify_daemon("reload_config")
    stats.record("system_sound_reset", label=key)
    return jsonify({"ok": True, "data": _system_sound_info(key, cfg())})


@app.route("/api/system_sounds/<key>/off", methods=["POST"])
def api_system_sound_off(key):
    """No sound at all for this cue."""
    if key not in config_schema.SYSTEM_SOUNDS:
        return jsonify({"ok": False, "error": "unknown_sound"}), 404
    update_config_file({key: ""})
    notify_daemon("reload_config")
    stats.record("system_sound_off", label=key)
    return jsonify({"ok": True, "data": _system_sound_info(key, cfg())})


@app.route("/api/system_sounds/<key>/test", methods=["POST"])
def api_system_sound_test(key):
    if key not in config_schema.SYSTEM_SOUNDS:
        return jsonify({"ok": False, "error": "unknown_sound"}), 404
    result = control("test_system_sound", key=key)
    if result.get("ok"):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 409


@app.route("/api/speech/test", methods=["POST"])
def api_speech_test():
    kind = (request.get_json(silent=True) or {}).get("kind") or "time_date"
    result = control("speak", kind=kind)
    if result.get("ok"):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 409


@app.route("/api/announce_files/<path:source_id>", methods=["POST"])
def api_announce_file_upload(source_id):
    folder, error = _announce_files_folder(source_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"ok": False, "error": "no_file"}), 400
    asked = request.form.get("name") or upload.filename or ""
    name = _announce_file_name(asked)
    if name is None:
        return jsonify({"ok": False, "error": "not_audio", "detail": str(asked)[:120]}), 400
    size = request.content_length or 0
    if size > MUSIC_UPLOAD_MAX_BYTES:
        return jsonify({"ok": False, "error": "file_too_big", "detail": name})
    free = shutil.disk_usage(folder).free
    if size and size > free:
        return jsonify({"ok": False, "error": "no_space", "detail": str(free)})
    dest = os.path.join(folder, name)
    replaced = os.path.exists(dest)
    error = _store_upload(upload, dest, request.form.get("mtime"))
    if error:
        return jsonify({"ok": False, "error": error, "detail": name})
    stats.record("announce_file_added", label="%s/%s" % (source_id, name),
                 detail={"size": os.path.getsize(dest), "replaced": replaced})
    return jsonify({"ok": True, "data": {"name": name, "replaced": replaced}})


@app.route("/api/announce_files/<path:source_id>", methods=["GET"])
def api_announce_file_get(source_id):
    """One sound of an announcement, for the browser to play it itself ("Listen
    here")."""
    folder, error = _announce_files_folder(source_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    name = _announce_file_name(request.args.get("name"))
    path = os.path.join(folder, name) if name else None
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return send_from_directory(folder, name, conditional=True, max_age=0)


@app.route("/api/announce_files/<path:source_id>", methods=["DELETE"])
def api_announce_file_delete(source_id):
    folder, error = _announce_files_folder(source_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    name = _announce_file_name(request.args.get("name"))
    path = os.path.join(folder, name) if name else None
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        os.remove(path)
    except OSError:
        return jsonify({"ok": False, "error": "write_failed", "detail": name})
    stats.record("announce_file_removed", label="%s/%s" % (source_id, name))
    return jsonify({"ok": True})


@app.route("/api/track_order/<path:source_id>", methods=["DELETE"])
def api_clear_track_order(source_id):
    folder = _resolve_track_order_folder(source_id)
    if folder is None:
        return jsonify({"ok": False, "error": "unknown_source"}), 404
    track_order.clear(cfg()["TRACK_ORDER_FILE"], source_id)
    stats.record("track_order_reset", label=source_id)
    notify_daemon("reset_click_bag", source_id=source_id)
    return jsonify({"ok": True})


# Only /api/auth/set_password may write these (it checks the current one).
_SETTINGS_HIDDEN_KEYS = {"WEB_PASSWORD_HASH", "WEB_SESSION_SECRET"}


@app.route("/api/settings")
def api_get_settings():
    c = cfg()
    return jsonify({"ok": True, "data": {
        k: c[k] for k in DEFAULTS if k in c and k not in _SETTINGS_HIDDEN_KEYS
    }})


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"ok": False, "error": "no_data"}), 400
    if _SETTINGS_HIDDEN_KEYS & set(body):
        return jsonify({"ok": False, "error": "use_auth_endpoint"}), 400
    bad = (_check_github_repo(body) or _check_extra_hosts(body) or _check_bt_adapters(body)
           or _check_gpio_pins(body))
    if bad:
        return jsonify({"ok": False, "error": bad}), 400
    try:
        update_config_file(body)
    except ValueError:
        return jsonify({"ok": False, "error": "unknown_setting"}), 400
    stats.record("settings_changed", label=", ".join(sorted(body)), detail={"keys": sorted(body)})

    try:
        reload = control("reload_config")
    except Exception:  # noqa: BLE001
        reload = {"ok": False}
    if "ACT_LED" in body:
        if not system_actions.service_action("start", "rukebox-act-led"):
            log.warning("Could not apply the activity LED setting")
    if config_schema.GPIO_BUTTON_SETTINGS & set(body) and _service_is_active("rukebox-gpio-button"):
        for action in ("stop", "start"):
            if not system_actions.service_action(action, GPIO_BUTTON_SERVICE):
                log.warning("Could not %s %s", action, GPIO_BUTTON_SERVICE)
    if "FLIC_HCI_DEVICE" in body and _service_is_active("flicd"):
        if not system_actions.service_action("restart", "flicd"):
            log.warning("Could not restart flicd")
    if "SPEAKER_BT_ADAPTER" in body:
        if not system_actions.service_action("restart", "bt-connect"):
            log.warning("Could not restart bt-connect")
    audio_reloaded = False
    if "BT_AUDIO_CODECS" in body:
        # WirePlumber reads the drop-in when it starts: write it, then restart the user service.
        try:
            bt_codec.write(codecs=bt_codec.parse(body.get("BT_AUDIO_CODECS")))
            audio_reloaded = bt_codec.restart_wireplumber(env=_user_session_env())
        except Exception:  # noqa: BLE001
            log.exception("Could not apply the Bluetooth codec list")
        if not audio_reloaded:
            log.warning("The new codecs apply at the next start of WirePlumber")
    reboot_needed = False
    if "USB_PORT_MODE" in body:
        if not system_actions.service_action("restart", "rukebox-usb-gadget", timeout=30):
            log.warning("Could not apply the USB port mode")
        reboot_needed = True
    restart_needed = bool(config_schema.RESTART_REQUIRED & set(body))
    if config_schema.STREAM_SETTINGS & set(body):
        # The encoder is built on the next status read, from the file just written.
        forget_stream()
    else:
        _apply_stream_tuning()
    if config_schema.UPNP_SETTINGS & set(body):
        # Another name is another label over the same device: say goodbye under
        # the old one before announcing the new one.
        upnp.stop()
        _ensure_upnp_identity(cfg())
    if (config_schema.STREAM_SETTINGS | config_schema.UPNP_SETTINGS) & set(body):
        _upnp_follow_stream()
    if any(key.startswith("WLED_") for key in body):
        try:
            control("wled_refresh")
        except Exception:  # noqa: BLE001
            pass
    return jsonify({"ok": True, "data": {
        "applied_live": bool(reload.get("ok")),
        "restart_needed": restart_needed,
        "reboot_needed": reboot_needed,
        "audio_reloaded": audio_reloaded,
    }})



class _ForwardedHeaders:
    """Trusts a reverse proxy's X-Forwarded-* headers, only while the settings say one is in front."""

    def __init__(self, wsgi_app):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.plain = wsgi_app
        self.fixed = ProxyFix(wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    def __call__(self, environ, start_response):
        try:
            behind = bool(cfg().get("WEB_BEHIND_PROXY"))
        except Exception:  # noqa: BLE001 - an unreadable config is no proxy
            behind = False
        return (self.fixed if behind else self.plain)(environ, start_response)


app.wsgi_app = _ForwardedHeaders(app.wsgi_app)

def _wled_memory(c):
    """What the daemon learned of each WLED's timezone, keyed by address."""
    try:
        with open(os.path.join(c["STATE_DIR"], "state.json"), encoding="utf-8") as f:
            memory = json.load(f).get("wled_clock") or {}
    except (OSError, ValueError):
        return {}
    return memory if isinstance(memory, dict) else {}


@app.route("/api/wled")
def api_wled():
    """The light strips this radio drives: what each says about itself, its presets, and the clock."""
    c = cfg()
    hosts = wled.hosts(c)
    devices = _status_probe("wled:" + ",".join(hosts), lambda: [
        wled.describe(host, timeout=1.5) or {"host": host, "missing": True} for host in hosts])
    presets = []
    reachable = next((d for d in devices if not d.get("missing")), None)
    if reachable:
        try:
            presets = [{"id": i, "name": n} for i, n in wled.presets(reachable["host"], timeout=2)]
        except (OSError, ValueError):
            presets = []
    memory = _wled_memory(c)
    devices = [dict(d, clock_known=d["host"] in memory) for d in devices]
    lights = None
    try:
        status = control("get_status")
        if status.get("ok"):
            data = status.get("data") or {}
            lights = dict(data.get("lights") or {}, clock_source=data.get("clock_source"))
    except Exception:  # noqa: BLE001
        lights = None
    return jsonify({"ok": True, "data": {
        "enabled": bool(c.get("WLED_ENABLED")), "devices": devices, "presets": presets,
        "lights": lights}})


@app.route("/api/wled/discover", methods=["POST"])
def api_wled_discover():
    """Every WLED on the networks this radio is on."""
    return jsonify({"ok": True, "data": {"devices": wled.discover(extra=wled.hosts(cfg()))}})


@app.route("/api/wled/test", methods=["POST"])
def api_wled_test():
    """Shows a preset on every strip at once, to choose one."""
    body = request.get_json(silent=True) or {}
    try:
        state = wled.state_for(int(body.get("preset")))
    except (TypeError, ValueError):
        state = None
    if state is None:
        return jsonify({"ok": False, "error": "bad_preset"}), 400
    hosts = wled.hosts(cfg())
    if not hosts:
        return jsonify({"ok": False, "error": "wled_no_device"}), 400
    failed = []
    for host in hosts:
        try:
            wled.set_state(host, state)
        except (OSError, ValueError):
            failed.append(host)
    if len(failed) == len(hosts):
        return jsonify({"ok": False, "error": "wled_unreachable", "detail": ", ".join(failed)}), 502
    return jsonify({"ok": True, "data": {"failed": failed}})


@app.route("/api/wled/refresh", methods=["POST"])
def api_wled_refresh():
    """Sends the scene again and shares the time now, rather than at the next turn."""
    try:
        result = control("wled_refresh")
    except Exception:  # noqa: BLE001
        result = {"ok": False}
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "daemon_unreachable"}), 503
    return jsonify({"ok": True})


@app.route("/api/dj_announcements")
def api_dj_announcements():
    """What the prepared introductions folder holds, and how many songs it
    covers. Read when the settings are shown, never in the status: it walks the
    library."""
    c = cfg()
    tracks = get_music_list(_effective_music_dir(), c["MUSIC_CACHE_FILE"])
    folder = c.get("DJ_ANNOUNCE_DIR") or ""
    files, covered = dj_intro.scan(tracks, folder, _effective_music_dir())
    return jsonify({"ok": True, "data": {"dir": folder, "files": files,
                                         "covered": covered, "tracks": len(tracks)}})


@app.route("/api/covers")
def api_covers():
    """What the prepared covers folder holds, and how many songs it covers.
    Read when the settings are shown, never in the status: it walks the
    library."""
    c = cfg()
    tracks = get_music_list(_effective_music_dir(), c["MUSIC_CACHE_FILE"])
    folder = c.get("COVER_DIR") or ""
    files, covered = dj_intro.scan(tracks, folder, _effective_music_dir(),
                                   extensions=track_media.IMAGE_EXTENSIONS)
    return jsonify({"ok": True, "data": {"dir": folder, "files": files,
                                         "covered": covered, "tracks": len(tracks)}})


def _prepared_folder(kind, relative):
    """(root, folder, error): the folder a prepared card may write in, or the
    code its answer needs."""
    if not prepared_media.spec(kind):
        return None, None, "unknown_kind"
    top, folder = prepared_media.inside(kind, cfg(), relative)
    if not top:
        return None, None, "folder_unset"
    if folder is None:
        return None, None, "bad_path"
    return top, folder, None


@app.route("/api/prepared/<kind>")
def api_prepared_listing(kind):
    """What one prepared folder holds right now: its subfolders, and every file
    with whether that kind would use it."""
    data = prepared_media.listing(kind, cfg(), request.args.get("path") or "")
    if data is None:
        error = "unknown_kind" if not prepared_media.spec(kind) else "bad_path"
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "data": data})


@app.route("/api/prepared/<kind>/file", methods=["POST"])
def api_prepared_upload(kind):
    """One file per request, into the folder the client names."""
    top, folder, error = _prepared_folder(kind, request.form.get("path"))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"ok": False, "error": "no_file"}), 400
    asked = request.form.get("name") or upload.filename or ""
    name = prepared_media.file_name(kind, asked)
    if name is None:
        return jsonify({"ok": False, "error": "bad_file", "detail": str(asked)[:120]}), 400
    size = request.content_length or 0
    if size > MUSIC_UPLOAD_MAX_BYTES:
        return jsonify({"ok": False, "error": "file_too_big", "detail": name}), 400
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return jsonify({"ok": False, "error": "write_failed", "detail": name})
    free = shutil.disk_usage(top).free
    if size and size > free:
        return jsonify({"ok": False, "error": "no_space", "detail": str(free)}), 400
    dest = os.path.join(folder, name)
    replaced = os.path.exists(dest)
    error = _store_upload(upload, dest, request.form.get("mtime"))
    if error:
        return jsonify({"ok": False, "error": error, "detail": name})
    stats.record("prepared_file_added", label="%s/%s" % (kind, name),
                 detail={"size": os.path.getsize(dest), "replaced": replaced})
    return jsonify({"ok": True, "data": {"name": name, "replaced": replaced}})


@app.route("/api/prepared/<kind>/file", methods=["GET"])
def api_prepared_file(kind):
    """One prepared file, for a thumbnail or to listen to it here."""
    relative = prepared_media.relative_path(request.args.get("path"))
    if not relative:
        return jsonify({"ok": False, "error": "bad_path"}), 400
    folder, name = relative.rsplit("/", 1) if "/" in relative else ("", relative)
    _top, path, error = _prepared_folder(kind, folder)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    if prepared_media.file_name(kind, name) is None:
        return jsonify({"ok": False, "error": "bad_file"}), 400
    if not os.path.isfile(os.path.join(path, name)):
        return jsonify({"ok": False, "error": "not_found"}), 404
    return send_from_directory(path, name, conditional=True, max_age=0)


@app.route("/api/prepared/<kind>/file", methods=["DELETE"])
def api_prepared_delete(kind):
    """Takes one file out, whatever it is: a file this kind does not use is
    exactly the one to remove."""
    relative = prepared_media.relative_path(request.args.get("path"))
    if not relative:
        return jsonify({"ok": False, "error": "bad_path"}), 400
    folder, name = relative.rsplit("/", 1) if "/" in relative else ("", relative)
    _top, path, error = _prepared_folder(kind, folder)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    if prepared_media.visible_name(name) is None:
        return jsonify({"ok": False, "error": "bad_path"}), 400
    target = os.path.join(path, name)
    if not os.path.isfile(target):
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        os.remove(target)
    except OSError:
        return jsonify({"ok": False, "error": "write_failed", "detail": name})
    return jsonify({"ok": True})


@app.route("/api/prepared/<kind>/folder", methods=["POST"])
def api_prepared_folder(kind):
    """Makes one subfolder, so an artist or an album can be prepared before
    anything is put in it."""
    body = request.get_json(silent=True) or {}
    _top, folder, error = _prepared_folder(kind, body.get("path"))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    name = prepared_media.folder_name(body.get("name"))
    if name is None:
        return jsonify({"ok": False, "error": "bad_name"}), 400
    try:
        os.makedirs(os.path.join(folder, name), exist_ok=True)
    except OSError:
        return jsonify({"ok": False, "error": "write_failed", "detail": name})
    # The client asks with the same forward-slash form it reads back.
    here = prepared_media.relative_path(body.get("path")) or ""
    return jsonify({"ok": True, "data": {"path": "/".join(p for p in (here, name) if p)}})


@app.route("/api/system/reboot", methods=["POST"])
def api_system_reboot():
    stats.record("system_reboot", label="interface")
    system_actions.reboot()
    return jsonify({"ok": True})


@app.route("/api/daemon/restart_after_song", methods=["POST"])
def api_daemon_restart_after_song():
    """Plans (on=true) or cancels (on=false) a restart of the service at the
    end of the song playing."""
    body = request.get_json(silent=True) or {}
    target = "reboot" if body.get("target") == "reboot" else "service"
    result = control("schedule_restart", on=bool(body.get("on", True)), target=target)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "daemon_unreachable")}), 503
    return jsonify(result)


@app.route("/api/daemon/restart", methods=["POST"])
def api_daemon_restart():
    stats.record("daemon_restart", label="web interface")
    try:
        system_actions.restart_daemon()
    except OSError as error:
        return jsonify({"ok": False, "error": "restart_failed", "detail": str(error)}), 500
    return jsonify({"ok": True})


@app.route("/api/time", methods=["POST"])
def api_set_time():
    body = request.get_json(silent=True) or {}
    value = body.get("datetime")
    utc_value = body.get("utc")
    if not value and not utc_value:
        return jsonify({"ok": False, "error": "missing_datetime"}), 400

    before = time.time()
    ok, detail = system_actions.set_clock(utc_value or value, utc=bool(utc_value))
    if not ok:
        return jsonify({"ok": False, "error": "time_set_failed", "detail": detail}), 400

    has_rtc = system_actions.has_rtc()
    written = system_actions.write_rtc() if has_rtc else False

    offset = time.time() - before
    try:
        notified = control("clock_set", clock_source="manual", offset=offset)
    except Exception:  # noqa: BLE001
        log.exception("Could not notify the daemon of the clock change")
        notified = {"ok": False}
    if not notified.get("ok"):
        stats.attach_current_session()
        stats.set_clock("manual", offset_sec=offset, trusted=True)
    stats.attach_current_session()
    stats.record("clock_manual_set",
                 label=(utc_value or value),
                 detail={"written_to_rtc": written, "utc": bool(utc_value)})

    return jsonify({"ok": True, "data": {
        "system_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "written_to_rtc": written,
        "has_rtc": has_rtc,
    }})


_TIMEZONE_CACHE_SECONDS = 30
_timezone_cache = {"at": 0.0, "name": "", "list": None}


def _timezone_name():
    """The machine's timezone, e.g."""
    if _timezone_cache["name"] and time.time() - _timezone_cache["at"] < _TIMEZONE_CACHE_SECONDS:
        return _timezone_cache["name"]
    name = system_actions.timezone_name()
    _timezone_cache["name"] = name
    _timezone_cache["at"] = time.time()
    return name


def _list_timezones():
    """Every zone the system knows, or [] when it cannot be asked."""
    cached = _timezone_cache["list"]
    if cached is not None:
        return cached
    zones = system_actions.list_timezones()
    _timezone_cache["list"] = zones
    return zones


@app.route("/api/time/timezone")
def api_get_timezone():
    return jsonify({"ok": True, "data": {"current": _timezone_name(), "zones": _list_timezones()}})


@app.route("/api/time/timezone", methods=["POST"])
def api_set_timezone():
    body = request.get_json(silent=True) or {}
    value = (body.get("timezone") or "").strip()
    if not value:
        return jsonify({"ok": False, "error": "missing_timezone"}), 400

    zones = _list_timezones()
    if zones and value not in zones:
        return jsonify({"ok": False, "error": "bad_timezone"}), 400

    ok, detail = system_actions.set_timezone(value)
    if not ok:
        return jsonify({"ok": False, "error": "timezone_failed", "detail": detail}), 400
    _timezone_cache["name"] = ""
    _timezone_cache["at"] = 0.0
    stats.record("timezone_set", label=value)
    return jsonify({"ok": True, "data": {"current": _timezone_name()}})


@app.route("/api/clock/test_bt", methods=["POST"])
def api_test_bt_clock():
    """Tests Bluetooth time recovery on a given device, without waiting for a
    real daemon restart."""
    body = request.get_json(silent=True) or {}
    mac = body.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing_mac"}), 400

    bt_clock_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_clock.py")
    result = subprocess.run(
        ["python3", bt_clock_script, mac, "15"],
        capture_output=True, text=True, timeout=25,
    )
    if result.returncode == 0:
        return jsonify({"ok": True, "time": result.stdout.strip()})
    return jsonify({"ok": False, "error": "no_time_service"})


def _bt_script(commands, timeout=15, agent=False):
    """Runs bluetoothctl with commands on stdin; returns its output."""
    command = ["bluetoothctl"]
    if agent:
        command += ["--agent", "NoInputNoOutput"]
    adapter = cfg().get("SPEAKER_BT_ADAPTER", "")
    script = (f"select {adapter}\n" if adapter else "") + "\n".join(commands) + "\n"
    try:
        return subprocess.run(command, input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return subprocess.CompletedProcess(command, 1, out, "timed out")


# flicd takes a controller for itself (HCI user channel), so the two must differ.
FLIC_SDK_DIR = "/opt/fliclib-linux-hci"
_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# BlueZ lists a device it has no name for under its own address, dashes and all.
_MAC_ANY_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def _bt_controllers():
    """Every Bluetooth controller the kernel knows, with the bus and any USB model."""
    try:
        out = subprocess.run(["hciconfig"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    controllers = []
    current = None
    for line in out.splitlines():
        m = re.match(r"^(hci\d+):\s+Type: \S+\s+Bus: (\S+)", line)
        if m:
            current = {"name": m.group(1), "bus": m.group(2).lower(), "address": None, "model": None}
            controllers.append(current)
            continue
        m = re.search(r"BD Address: ([0-9A-Fa-f:]{17})", line)
        if m and current is not None:
            current["address"] = m.group(1).upper()
    for c in controllers:
        if c["bus"] == "usb":
            device = os.path.realpath("/sys/class/bluetooth/%s/device" % c["name"])
            for folder in (os.path.dirname(device), device):
                product = (_read_first(os.path.join(folder, "product")) or "").strip()
                if product:
                    maker = (_read_first(os.path.join(folder, "manufacturer")) or "").strip()
                    c["model"] = (maker + " " + product).strip()
                    break
    return controllers


def _resolve_controller(value, controllers):
    """The controller a setting names (an address or an hciN name), or None."""
    value = str(value or "").strip()
    if not value:
        return None
    for c in controllers:
        if value.upper() == (c["address"] or "") or value == c["name"]:
            return c
    return None


def _flic_enabled():
    return _service_is_enabled("flicd")


def _flic_availability(controllers=None):
    """Whether a Flic button can be used here, and why not: flicd takes a
    controller for itself - unless the sound goes to a wired output. The rule
    itself lives in audio_diag, where the daemon reads it too: it is the daemon
    that puts the button on hold while that is so, and lets it go afterwards."""
    if controllers is None:
        controllers = _bt_controllers()
    return audio_diag.flic_availability(
        (cfg().get("AUDIO_OUTPUT") or "bluetooth") == "bluetooth", controllers)


@app.route("/api/bluetooth/controllers")
def api_bt_controllers():
    c = cfg()
    controllers = _bt_controllers()
    speaker = _resolve_controller(c.get("SPEAKER_BT_ADAPTER"), controllers)
    flic = _resolve_controller(c.get("FLIC_HCI_DEVICE") or "hci0", controllers)
    if speaker is None and controllers:
        # "Automatic": the first controller flicd does not hold.
        free = [x for x in controllers if not (_flic_enabled() and x is flic)]
        speaker = (free or controllers)[0]
    return jsonify({"ok": True, "data": {
        "controllers": controllers,
        "speaker": speaker["address"] if speaker else None,
        "speaker_setting": c.get("SPEAKER_BT_ADAPTER") or "",
        "flic": flic["address"] if flic else None,
    }})


def _check_bt_adapters(body):
    """The speaker and the Flic button on the same controller cannot work
    together while the Flic button is in use."""
    if not ({"SPEAKER_BT_ADAPTER", "FLIC_HCI_DEVICE"} & set(body)) or not _flic_enabled():
        return None
    c = cfg()
    if (c.get("AUDIO_OUTPUT") or "bluetooth") != "bluetooth":
        return None
    c.update({k: v for k, v in body.items() if k in ("SPEAKER_BT_ADAPTER", "FLIC_HCI_DEVICE")})
    controllers = _bt_controllers()
    flic = _resolve_controller(c.get("FLIC_HCI_DEVICE") or "hci0", controllers)
    speaker = _resolve_controller(c.get("SPEAKER_BT_ADAPTER"), controllers)
    if speaker is None and not c.get("SPEAKER_BT_ADAPTER"):
        others = [x for x in controllers if x is not flic]
        speaker = others[0] if others else flic
    if flic is not None and speaker is flic:
        return "bt_adapter_conflict"
    return None


def _fliclib():
    path = os.path.join(FLIC_SDK_DIR, "clientlib", "python")
    if path not in sys.path:
        sys.path.insert(0, path)
    import fliclib  # noqa: E402  (from the Flic SDK)
    return fliclib


def _flic_session(start, timeout):
    """Opens a client to flicd, runs start(client, finish) on it and waits for
    finish(result) or the timeout. Raises ConnectionError when unreachable."""
    fliclib = _fliclib()
    try:
        client = fliclib.FlicClient("localhost")
    except OSError:
        raise ConnectionError("flicd_not_running")
    box = {}
    done = threading.Event()

    def finish(result=None):
        box["result"] = result
        done.set()
        client.close()

    start(client, finish)
    worker = threading.Thread(target=client.handle_events, daemon=True)
    worker.start()
    done.wait(timeout)
    if not done.is_set():
        client.close()
    return box.get("result")


def _flic_buttons():
    """Addresses of the buttons flicd knows, or None when it is not running."""
    try:
        info = _flic_session(lambda client, finish: client.get_info(finish), 4)
    except (ConnectionError, ImportError):
        return None
    return list((info or {}).get("bd_addr_of_verified_buttons") or [])


def _flic_button_count():
    """How many Flic buttons are paired, or None when flicd cannot say."""
    if not _service_is_active("flicd"):
        return None
    buttons = _flic_buttons()
    return None if buttons is None else len(buttons)


@app.route("/api/flic/status")
def api_flic_status():
    sdk = os.path.isfile(os.path.join(FLIC_SDK_DIR, "clientlib", "python", "fliclib.py"))
    active = _service_is_active("flicd")
    usable, reason = _flic_availability()
    enabled = _flic_enabled()
    return jsonify({"ok": True, "data": {
        "sdk": sdk,
        "usable": usable,
        "unusable_reason": reason,
        "enabled": enabled,
        # Wanted, but the radio cannot let it run: the daemon restarts it when a controller frees.
        "held": bool(enabled and not usable),
        "active": active,
        "bridge": _service_is_active("flic-bridge"),
        "buttons": _flic_buttons() if sdk and active else None,
        "pairing": _flic_pair["state"],
    }})


@app.route("/api/flic/install", methods=["POST"])
def api_flic_install():
    """Downloads the Flic SDK (needs Internet)."""
    try:
        r = subprocess.run(["sudo", "-n", "/usr/local/sbin/rukebox-flic-sdk"],
                           capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired):
        return jsonify({"ok": False, "error": "flic_install_failed"}), 500
    if r.returncode != 0:
        return jsonify({"ok": False, "error": "flic_install_failed",
                        "detail": (r.stderr or r.stdout).strip()[-300:]}), 500
    stats.record("flic_sdk_installed")
    return jsonify({"ok": True})


@app.route("/api/flic/enable", methods=["POST"])
def api_flic_enable():
    """{on}: starts flicd and the bridge now and at every boot, or stops them."""
    on = bool((request.get_json(silent=True) or {}).get("on"))
    if on:
        if not os.path.isdir(FLIC_SDK_DIR):
            return jsonify({"ok": False, "error": "flic_sdk_missing"}), 400
        c = cfg()
        controllers = _bt_controllers()
        usable, reason = _flic_availability(controllers)
        if not usable:
            return jsonify({"ok": False, "error": "flic_" + reason}), 400
        flic = _resolve_controller(c.get("FLIC_HCI_DEVICE") or "hci0", controllers)
        speaker = _resolve_controller(c.get("SPEAKER_BT_ADAPTER"), controllers)
        if flic is None:
            return jsonify({"ok": False, "error": "flic_no_controller"}), 400
        if (c.get("AUDIO_OUTPUT") or "bluetooth") == "bluetooth" and speaker is flic:
            return jsonify({"ok": False, "error": "bt_adapter_conflict"}), 400
    enable_ok = system_actions.service_action("enable" if on else "disable", "flicd")
    run_ok = system_actions.service_action("start" if on else "stop", "flicd")
    bridge_ok = system_actions.service_action("enable" if on else "disable", "flic-bridge")
    bridge_run_ok = system_actions.service_action("start" if on else "stop", "flic-bridge")
    if not (enable_ok and run_ok and bridge_ok and bridge_run_ok):
        return jsonify({"ok": False, "error": "service_failed"}), 500
    stats.record("flic_enabled" if on else "flic_disabled")
    return jsonify({"ok": True})


_flic_pair = {"state": "idle", "result": None, "address": None, "name": None, "client": None, "wizard": None,
              "until": 0.0}
_flic_pair_lock = threading.Lock()
# A scan the wizard never reports on - the button stays in a pocket - must not
# leave the interface saying a pairing is running until the page is reloaded.
PAIR_SCAN_SEC = 120
_PAIR_RUNNING = ("searching", "private", "found", "connected")


def _pair_state():
    """The pairing state, an overrun counted as finished. The wizard thread can
    die with its connection to flicd, and nothing would ever report it."""
    with _flic_pair_lock:
        if _flic_pair["state"] in _PAIR_RUNNING and _flic_pair["until"] and time.time() > _flic_pair["until"]:
            _flic_pair.update(state="done", result="WizardFailedTimeout", client=None, wizard=None)
        return {k: _flic_pair[k] for k in ("state", "result", "address", "name")}


def _flic_pair_run():
    """Runs the SDK's scan wizard; whatever happens to it, the state ends on
    "done"."""
    try:
        _flic_pair_scan()
    except Exception:  # noqa: BLE001
        log.exception("Flic pairing failed")
    finally:
        with _flic_pair_lock:
            if _flic_pair["state"] != "done":
                _flic_pair.update(state="done", result="flicd_not_running", client=None, wizard=None)


def _flic_pair_scan():
    fliclib = _fliclib()
    try:
        client = fliclib.FlicClient("localhost")
    except OSError:
        with _flic_pair_lock:
            _flic_pair.update(state="done", result="flicd_not_running", client=None)
        return
    wizard = fliclib.ScanWizard()

    def found_private(w):
        with _flic_pair_lock:
            _flic_pair["state"] = "private"

    def found_public(w, bd_addr, name):
        with _flic_pair_lock:
            _flic_pair.update(state="found", address=bd_addr, name=name)

    def connected(w, bd_addr, name):
        with _flic_pair_lock:
            _flic_pair.update(state="connected", address=bd_addr, name=name)

    def completed(w, result, bd_addr, name):
        with _flic_pair_lock:
            _flic_pair.update(state="done", result=result.name, address=bd_addr, name=name, client=None)
        client.close()

    wizard.on_found_private_button = found_private
    wizard.on_found_public_button = found_public
    wizard.on_button_connected = connected
    wizard.on_completed = completed
    with _flic_pair_lock:
        _flic_pair.update(client=client, wizard=wizard)
    client.add_scan_wizard(wizard)
    client.handle_events()


@app.route("/api/flic/pair/start", methods=["POST"])
def api_flic_pair_start():
    if not _service_is_active("flicd"):
        return jsonify({"ok": False, "error": "flicd_not_running"}), 400
    if _pair_state()["state"] in _PAIR_RUNNING:
        return jsonify({"ok": False, "error": "flic_pairing_running"}), 409
    with _flic_pair_lock:
        _flic_pair.update(state="searching", result=None, address=None, name=None,
                          until=time.time() + PAIR_SCAN_SEC)
    threading.Thread(target=_flic_pair_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/flic/pair/status")
def api_flic_pair_status():
    return jsonify({"ok": True, "data": _pair_state()})


@app.route("/api/flic/pair/cancel", methods=["POST"])
def api_flic_pair_cancel():
    with _flic_pair_lock:
        client, wizard = _flic_pair["client"], _flic_pair["wizard"]
        _flic_pair.update(state="done", result="WizardCancelledByUser", client=None, wizard=None)
    if client is not None and wizard is not None:
        client.cancel_scan_wizard(wizard)
    return jsonify({"ok": True})


@app.route("/api/flic/buttons/delete", methods=["POST"])
def api_flic_button_delete():
    address = str((request.get_json(silent=True) or {}).get("address") or "")
    if not _MAC_RE.match(address):
        return jsonify({"ok": False, "error": "invalid_value"}), 400

    def start(client, finish):
        client.delete_button(address.lower())
        client.set_timer(300, finish)
    try:
        _flic_session(start, 3)
    except (ConnectionError, ImportError):
        return jsonify({"ok": False, "error": "flicd_not_running"}), 400
    stats.record("flic_button_removed", label=address)
    return jsonify({"ok": True})


_AUDIO_ICONS = {
    "audio-card", "audio-headset", "audio-headphones", "audio-speakers",
    "audio-input-microphone",
}
_OTHER_ICONS = {
    "phone", "computer", "printer", "scanner", "camera-photo", "camera-video",
    "video-display", "modem",
}


def _bt_kind(icon):
    if icon in _AUDIO_ICONS:
        return "audio"
    if icon.startswith("input-"):
        return "input"
    if icon in _OTHER_ICONS:
        return "other"
    return "unknown"


def _bt_device_info(mac):
    """What a device is and whether it is paired or connected."""
    info = {
        "paired": False, "trusted": False, "connected": False,
        "icon": "", "kind": "unknown", "available": False, "name": "",
    }
    for line in _bt_script([f"info {mac}"], timeout=10).stdout.splitlines():
        line = line.strip()
        if line.startswith("Device ") and "not available" not in line:
            info["available"] = True
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key in ("Paired", "Trusted", "Connected"):
            info[key.lower()] = value == "yes"
        elif key == "Icon":
            info["icon"] = value
        elif key in ("Name", "Alias") and value and not info["name"]:
            info["name"] = value
    info["kind"] = _bt_kind(info["icon"])
    return info


def _bt_wait_flag(mac, flag, timeout=4):
    """Polls the device's flags for a moment."""
    deadline = time.time() + timeout
    while True:
        info = _bt_device_info(mac)
        if info[flag] or time.time() > deadline:
            return info
        time.sleep(0.5)


def _bt_await(commands, verdicts, timeout=30, agent=False):
    """Runs bluetoothctl with its stdin left open and waits for the answer."""
    # A number in `commands` is a pause: bluetoothctl has no wait of its own.
    command = ["bluetoothctl"]
    if agent:
        command += ["--agent", "NoInputNoOutput"]
    adapter = cfg().get("SPEAKER_BT_ADAPTER", "")
    script = ([f"select {adapter}"] if adapter else []) + list(commands)
    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    collected = []

    def reader():
        for raw in proc.stdout:
            collected.append(raw)

    def feed():
        for item in script:
            if isinstance(item, (int, float)):
                time.sleep(float(item))
                continue
            try:
                proc.stdin.write(item + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                return

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=feed, daemon=True).start()

    verdict = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = _bt_clean_output("".join(collected))
        for marker, value in verdicts.items():
            if marker in text:
                verdict = value
                break
        if verdict is not None or proc.poll() is not None:
            break
        time.sleep(0.2)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    time.sleep(0.1)
    return _bt_clean_output("".join(collected)), verdict


def _bt_discover(mac, timeout=12):
    """`pair` and `connect` need BlueZ to already know the device: a MAC it has
    never seen is answered with a flat "not available"."""
    if _bt_device_info(mac)["available"]:
        return True
    adapter = cfg().get("SPEAKER_BT_ADAPTER", "")
    proc = subprocess.Popen(
        ["bluetoothctl"], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
    )
    proc.stdin.write((f"select {adapter}\n" if adapter else "") + "scan on\n")
    proc.stdin.flush()
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _bt_device_info(mac)["available"]:
                return True
            time.sleep(0.7)
        return False
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


CONNECT_SETTLE_SECONDS = 2.5
PAIR_SCAN_WAIT = 2.5


_speaker_seen = {}


def _tell_daemon_if_speaker_changed(mac, link):
    """The page often learns first that the speaker came or went: the daemon
    checks it at once rather than at its next turn."""
    if link["unknown"]:
        return
    before = _speaker_seen.get(mac)
    _speaker_seen[mac] = link["connected"]
    if before is not None and before != link["connected"]:
        threading.Thread(target=notify_daemon, args=("speaker_check",), daemon=True).start()


def _speaker_paired(mac):
    """False when no controller of this Pi is paired with the speaker, None when unknown."""
    link = _status_probe(("speaker", mac), lambda: _speaker_link(mac))
    if link["unknown"]:
        return None
    return bool(link["connected"] or link["paired_here"])


def _speaker_link(mac):
    """Where the speaker is: connected (on any controller), and which one
    carries it - see src/bt_link.py."""
    return bt_link.locate(mac, cfg().get("SPEAKER_BT_ADAPTER", ""))


def _controller_kind(address, controllers=None):
    """Whether a controller is the USB dongle or the built-in chip."""
    if not address:
        return ""
    controllers = _bt_controllers() if controllers is None else controllers
    found = next((c for c in controllers if c["address"] == address), None)
    if found is None:
        return ""
    return "usb" if found["bus"] == "usb" else "builtin"


def _service_is_active(name):
    return system_actions.service_is_active(name)


def _service_is_enabled(name):
    return system_actions.service_is_enabled(name)


def capabilities():
    """What this machine can do, read fresh: the interface hides what is not
    there and the routes below answer `unsupported_here` for it."""
    return platform_mod.caps()


_stream_lock = threading.Lock()
_stream = {"server": None}


def stream_server():
    """The encoder, started on first use: `pactl` is asked once per process,
    not on every status poll, and the stream settings need a restart (they are
    in config_schema.RESTART_REQUIRED)."""
    with _stream_lock:
        server = _stream["server"]
        if server is None:
            server = stream_mod.build(cfg())
            _stream["server"] = server
        if server is not None and server.source and not server.alive():
            server.start()
        return server


def forget_stream():
    """The tests' door, and what a settings import needs: the next status
    rebuilds the encoder."""
    with _stream_lock:
        server, _stream["server"] = _stream["server"], None
    if server is not None:
        server.stop()


def _stream_status():
    server = stream_server()
    url = ""
    if server is not None and server.source:
        url = request.host_url.rstrip("/") + "/stream." + server.suffix
    data = stream_mod.status(server, url=url)
    if data["enabled"] and not data["available"]:
        # Say WHICH cause it is: no tools, no sound server, no source at all.
        data["why"] = stream_mod.why_unavailable(env=stream_mod.audio_env())
    return data


BT_SCAN_SECONDS = 15
_bt_scan_lock = threading.Lock()
_bt_scan = {"proc": None, "started": 0.0, "devices": {}}


def _bt_radio_blocked():
    """True while the kernel has the Bluetooth radio blocked: BlueZ cannot undo
    that, only the switch can (scripts/bt_radio.sh)."""
    try:
        entries = os.listdir("/sys/class/rfkill")
    except OSError:
        return False
    for name in entries:
        base = os.path.join("/sys/class/rfkill", name)
        try:
            with open(os.path.join(base, "type"), encoding="utf-8") as f:
                if f.read().strip() != "bluetooth":
                    continue
            states = []
            for key in ("soft", "hard"):
                with open(os.path.join(base, key), encoding="utf-8") as f:
                    states.append(f.read().strip())
        except OSError:
            continue
        if "1" in states:
            return True
    return False


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SCAN_EVENT_RE = re.compile(r"\[(NEW|CHG|DEL)\]\s+Device\s+([0-9A-Fa-f:]{17})(?:\s+(.*))?")
_bt_info_cache = {}


def _bt_clean_output(text, limit=300):
    """bluetoothctl's output without colours, prompts and echoed commands."""
    messages = []
    for raw in text.splitlines():
        line = "".join(ch for ch in _ANSI_RE.sub("", raw) if ch >= " ")
        line = line.replace("[bluetoothctl]>", " ").strip()
        if not line or line.startswith(_BT_NOISE_PREFIXES):
            continue
        if "new_settings:" in line:
            continue
        if line.startswith(_BT_ECHOED_COMMANDS):
            continue
        if line not in messages:
            messages.append(line)
    return " · ".join(messages)[-limit:]


_BT_NOISE_PREFIXES = (
    "Agent registered", "Waiting to connect", "SetDiscoveryFilter",
    "Discovery started", "Discovery stopped", "[CHG]", "[NEW]", "[DEL]",
)
_BT_ECHOED_COMMANDS = (
    "pair ", "trust ", "connect ", "disconnect ", "agent ", "default-agent",
    "scan ", "select ",
)
_BT_PAIR_VERDICTS = {"Pairing successful": True, "Failed to pair": False}
_BT_CONNECT_VERDICTS = {
    "Connection successful": True, "Failed to connect": False, "In Progress": False,
}


def _bt_scan_running():
    proc = _bt_scan["proc"]
    return proc is not None and proc.poll() is None


def _bt_scan_finish():
    proc = _bt_scan["proc"]
    _bt_scan["proc"] = None
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _bt_scan_reader(proc, devices):
    """Accumulates what the running scan discovers, until it exits."""
    for raw in proc.stdout:
        line = _ANSI_RE.sub("", raw)
        line = "".join(ch for ch in line if ch >= " ").replace("[bluetoothctl]>", " ").strip()
        match = _SCAN_EVENT_RE.search(line)
        if not match:
            continue
        event, mac, rest = match.group(1), match.group(2).upper(), (match.group(3) or "").strip()
        if event == "DEL":
            devices.pop(mac, None)
            continue
        devices.setdefault(mac, rest if event == "NEW" else "")
        if rest.startswith(("Name:", "Alias:")):
            devices[mac] = rest.split(":", 1)[1].strip()


def _bt_scan_watchdog(proc):
    time.sleep(BT_SCAN_SECONDS + 2)
    with _bt_scan_lock:
        if _bt_scan["proc"] is proc:
            _bt_scan_finish()


@app.route("/api/bluetooth/scan/start", methods=["POST"])
def api_bt_scan_start():
    # An unblocked adapter can still be off; a blocked one cannot be woken here.
    if not _bt_radio_blocked():
        _bt_script(["power on"], timeout=20)
    with _bt_scan_lock:
        if not _bt_scan_running():
            adapter = cfg().get("SPEAKER_BT_ADAPTER", "")
            scan_script = (f"select {adapter}\n" if adapter else "") + "scan on\n"
            proc = subprocess.Popen(
                ["bluetoothctl"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            proc.stdin.write(scan_script)
            proc.stdin.flush()
            _bt_scan["proc"] = proc
            _bt_scan["started"] = time.time()
            _bt_scan["devices"] = {}
            _bt_info_cache.clear()
            threading.Thread(target=_bt_scan_reader, args=(proc, _bt_scan["devices"]), daemon=True).start()
            threading.Thread(target=_bt_scan_watchdog, args=(proc,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/bluetooth/scan/status")
def api_bt_scan_status():
    with _bt_scan_lock:
        if _bt_scan_running() and time.time() - _bt_scan["started"] > BT_SCAN_SECONDS:
            _bt_scan_finish()
        running = _bt_scan_running()
        found = dict(_bt_scan["devices"])

    devices = []
    for mac, name in sorted(found.items(), key=lambda item: item[1].lower()):
        info = _bt_info_cache.get(mac)
        if info is None:
            info = _bt_device_info(mac)
            _bt_info_cache[mac] = info
        label = (name or info["name"] or "").strip()
        named = bool(label) and not _MAC_ANY_RE.match(label)
        devices.append({
            "mac": mac,
            "name": label if named else mac,
            "named": named,
            **{k: v for k, v in info.items() if k != "name"},
        })
    return jsonify({"ok": True, "data": {"running": running, "devices": devices,
                                         "blocked": _bt_radio_blocked()}})


@app.route("/api/bluetooth/scan/stop", methods=["POST"])
def api_bt_scan_stop():
    with _bt_scan_lock:
        _bt_scan_finish()
    return jsonify({"ok": True})


@app.route("/api/bluetooth/pair", methods=["POST"])
def api_bt_pair():
    body = request.get_json(silent=True) or {}
    mac = body.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing_mac"}), 400

    already = _bt_device_info(mac)["paired"]
    if not _bt_discover(mac):
        stats.record("bluetooth_pair", label=mac, detail={"ok": False, "reason": "not_found"})
        return jsonify({"ok": False, "error": "bt_not_found"})

    # The scan must run in the same session: BlueZ drops a device discovery has only ever seen.
    text, verdict = _bt_await(["scan on", PAIR_SCAN_WAIT, f"pair {mac}"], _BT_PAIR_VERDICTS,
                              timeout=40, agent=True)
    _bt_info_cache.pop(mac, None)
    info = _bt_wait_flag(mac, "paired")
    if info["paired"]:
        _bt_script([f"trust {mac}"], timeout=10)
        stats.record("bluetooth_pair", label=mac, detail={"ok": True, "already": already})
        return jsonify({"ok": True, "data": {"already": already}})

    stats.record("bluetooth_pair", label=mac, detail={"ok": False, "verdict": verdict})
    return jsonify({
        "ok": False,
        "error": "bt_pair_lost" if verdict else "bt_pair_failed",
        "detail": text,
    })


@app.route("/api/bluetooth/connect", methods=["POST"])
def api_bt_connect():
    body = request.get_json(silent=True) or {}
    mac = body.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing_mac"}), 400

    if not _bt_discover(mac):
        return jsonify({"ok": False, "error": "bt_not_found"})

    if body.get("reconnect"):
        # Disconnect first: a plain connect does nothing on a link BlueZ believes is up.
        _bt_script([f"disconnect {mac}"], timeout=10)
        time.sleep(1.5)

    verdict = None
    text = ""
    already_trying = False
    _bt_info_cache.pop(mac, None)
    info = _bt_device_info(mac)
    accepted = info["connected"]
    for _attempt in range(2):
        if not info["connected"]:
            text, verdict = _bt_await([f"connect {mac}"], _BT_CONNECT_VERDICTS, timeout=8, agent=True)
            already_trying = "In Progress" in text
            accepted = accepted or verdict is True
            _bt_info_cache.pop(mac, None)
            info = _bt_wait_flag(
                mac, "connected", timeout=15 if already_trying else (8 if accepted else 4),
            )
            if not info["connected"]:
                break
        time.sleep(CONNECT_SETTLE_SECONDS)
        _bt_info_cache.pop(mac, None)
        info = _bt_device_info(mac)
        if info["connected"]:
            break
    ok = info["connected"]

    # The choice stands even when the link does not come up now: bt-connect keeps trying.
    if body.get("set_as_speaker"):
        update_config_file({"SPEAKER_MAC": mac})
        notify_daemon("reload_config")

    stats.record(
        "bluetooth_connect", label=mac,
        detail={"ok": ok, "set_as_speaker": bool(body.get("set_as_speaker")),
                "reconnect": bool(body.get("reconnect")), "verdict": verdict},
    )
    if not ok:
        if accepted:
            error = "bt_connect_lost"
        elif already_trying:
            error = "bt_connecting"
        else:
            error = "bt_connect_failed"
        return jsonify({
            "ok": False,
            "error": error,
            "detail": text,
        })
    return jsonify({"ok": True})


@app.route("/api/bluetooth/forget", methods=["POST"])
def api_bt_forget():
    """{mac}: the bond removed, so the device can be paired again - the only
    way out when one side forgot the other."""
    mac = str((request.get_json(silent=True) or {}).get("mac") or "")
    if not _MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "missing_mac"}), 400
    _bt_script([f"remove {mac}"], timeout=10)
    _bt_info_cache.pop(mac, None)
    if _bt_device_info(mac)["paired"]:
        return jsonify({"ok": False, "error": "bt_forget_failed"})
    stats.record("bluetooth_forget", label=mac, detail={"source": "web"})
    return jsonify({"ok": True})


def _remote_macs(c=None):
    """The remotes the settings name, upper case."""
    found = []
    for part in re.split(r"[\s,;]+", str((c or cfg()).get("BT_BUTTONS") or "")):
        mac = part.strip().upper()
        if _MAC_RE.match(mac) and mac not in found:
            found.append(mac)
    return found


@app.route("/api/bluetooth/remotes")
def api_bt_remotes():
    """The remotes used as a button, with their name and whether they are connected."""
    remotes = []
    for mac in _remote_macs():
        info = _bt_device_info(mac)
        remotes.append({"mac": mac, "name": info.get("name") or mac,
                        "paired": bool(info.get("paired")), "connected": bool(info.get("connected"))})
    return jsonify({"ok": True, "data": {"remotes": remotes}})


@app.route("/api/bluetooth/remote", methods=["POST"])
def api_bt_remote():
    """{mac, on}: a paired device becomes a remote, or stops being one."""
    body = request.get_json(silent=True) or {}
    mac = str(body.get("mac") or "").upper()
    if not _MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "missing_mac"}), 400
    macs = _remote_macs()
    if body.get("on", True):
        if not _bt_device_info(mac)["paired"]:
            return jsonify({"ok": False, "error": "bt_remote_not_paired"}), 400
        _bt_script([f"trust {mac}", f"connect {mac}"], timeout=10)
        _bt_info_cache.pop(mac, None)
        if mac not in macs:
            macs.append(mac)
    else:
        macs = [one for one in macs if one != mac]
    update_config_file({"BT_BUTTONS": ",".join(macs)})
    stats.record("bluetooth_remote", label=mac, detail={"on": bool(body.get("on", True))})
    return jsonify({"ok": True, "data": {"remotes": macs}})


@app.route("/api/bluetooth/disconnect", methods=["POST"])
def api_bt_disconnect():
    body = request.get_json(silent=True) or {}
    mac = body.get("mac")
    if not mac:
        return jsonify({"ok": False, "error": "missing_mac"}), 400
    _bt_script([f"disconnect {mac}"], timeout=10)
    _bt_info_cache.pop(mac, None)
    stats.record("bluetooth_disconnect", label=mac, detail={"source": "web"})
    return jsonify({"ok": True})


MUSIC_UPLOAD_MAX_BYTES = 512 * 1024 * 1024


def _music_root():
    return cfg().get("MUSIC_DIR", "")


def _music_path_parts(name):
    """Turns a browser-supplied relative path into safe path segments, or None
    if it cannot be trusted."""
    if not name or "\x00" in name:
        return None
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _music_destination(parts):
    """The absolute path a set of trusted segments may be written to, or None
    if it would land outside the music directory."""
    root = os.path.realpath(_music_root())
    dest = os.path.join(root, *parts)
    parent = os.path.realpath(os.path.dirname(dest))
    try:
        if os.path.commonpath([parent, root]) != root:
            return None
    except ValueError:
        return None
    return dest


@app.route("/api/music/manifest")
def api_music_manifest():
    """Everything already in the music directory, so that the browser can work
    out the difference without sending a byte of it."""
    root = _music_root()
    if not root or not os.path.isdir(root):
        return jsonify({"ok": True, "data": {
            "dir": root, "exists": False, "files": [], "free_bytes": 0,
        }})
    files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                info = os.stat(full)
            except OSError:
                continue
            files.append({
                "path": os.path.relpath(full, root),
                "size": info.st_size,
                "mtime": int(info.st_mtime),
            })
    return jsonify({"ok": True, "data": {
        "dir": root,
        "exists": True,
        "files": files,
        "free_bytes": shutil.disk_usage(root).free,
    }})


@app.route("/api/music/upload", methods=["POST"])
def api_music_upload():
    """One file per request, deliberately."""
    root = _music_root()
    if not root:
        return jsonify({"ok": False, "error": "music_dir_unset"}), 400
    if _usb_music_in_use():
        # The key is mounted read-only: say so rather than fail a write.
        return jsonify({"ok": False, "error": "usb_read_only", "detail": _effective_music_dir()}), 400
    if not os.path.isdir(root):
        return jsonify({"ok": False, "error": "music_dir_missing", "detail": root}), 400

    relative = request.form.get("path", "")
    parts = _music_path_parts(relative)
    dest = _music_destination(parts) if parts else None
    if dest is None:
        return jsonify({"ok": False, "error": "bad_path", "detail": relative[:120]})

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"ok": False, "error": "no_file"}), 400
    if os.path.isdir(dest):
        return jsonify({"ok": False, "error": "bad_path", "detail": relative[:120]})

    size = request.content_length or 0
    if size > MUSIC_UPLOAD_MAX_BYTES:
        return jsonify({"ok": False, "error": "file_too_big", "detail": relative[:120]})
    free = shutil.disk_usage(root).free
    if size and size > free:
        return jsonify({"ok": False, "error": "no_space", "detail": str(free)})

    existed = os.path.exists(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if _store_upload(upload, dest, request.form.get("mtime")):
        return jsonify({"ok": False, "error": "write_failed", "detail": relative[:120]})

    stored = os.path.getsize(dest)
    stats.record(
        "music_upload", label="/".join(parts),
        detail={"size": stored, "replaced": existed},
    )
    return jsonify({"ok": True, "data": {"path": "/".join(parts), "size": stored}})


AUDIO_STATE_CACHE_SECONDS = 10
_audio_state_lock = threading.Lock()
_audio_state = {"at": 0.0, "data": None}


def _user_runtime_dir():
    """Where the sound server's session lives, or None if there is none."""
    # XDG_RUNTIME_DIR first: a container's socket is not under /run/user.
    for name in ("PIPEWIRE_RUNTIME_DIR", "XDG_RUNTIME_DIR"):
        chosen = os.environ.get(name)
        if chosen and os.path.isdir(chosen):
            return chosen
    runtime = os.path.join("/run/user", str(os.getuid()))
    return runtime if os.path.isdir(runtime) else None


def _user_session_env():
    """The environment a *user* service needs."""
    runtime = _user_runtime_dir()
    if runtime is None:
        return None
    return {
        **os.environ,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + os.path.join(runtime, "bus"),
    }


def _audio_output_state():
    """What the Pi can play through right now."""
    with _audio_state_lock:
        if _audio_state["data"] and time.time() - _audio_state["at"] < AUDIO_STATE_CACHE_SECONDS:
            return _audio_state["data"]

    runtime = _user_runtime_dir()
    state = {"server": False, "sink": None}
    if runtime is None or not os.path.exists(os.path.join(runtime, "pipewire-0")):
        state = {"server": False, "sink": None}
    else:
        state = {"server": True, "sink": None}
        try:
            result = subprocess.run(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=5, env=_user_session_env(),
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "node.description" in line:
                        name = line.split("=", 1)[1].strip().strip('"').strip()
                        if name:
                            state["sink"] = name
                        break
        except (subprocess.TimeoutExpired, OSError):
            pass

    kind = cfg().get("AUDIO_OUTPUT", "bluetooth")
    state["output"] = kind
    state["missing"] = False
    if state["server"] and kind in audio_output.KINDS and kind != "bluetooth":
        sinks = audio_output.list_sinks(env=_user_session_env())
        sink = audio_output.find(kind, sinks)
        state["sink"] = sink["description"] if sink else None
        state["missing"] = sink is None

    with _audio_state_lock:
        _audio_state["at"] = time.time()
        _audio_state["data"] = state
    return state


@app.route("/api/audio/outputs")
def api_audio_outputs():
    """The outputs PipeWire can play to right now, by kind, for the "Audio
    output" card to say which choices are actually there."""
    sinks = audio_output.list_sinks(env=_user_session_env())
    return jsonify({"ok": True, "data": {
        "output": cfg().get("AUDIO_OUTPUT", "bluetooth"),
        "outputs": [{"kind": s["kind"], "description": s["description"]}
                    for s in sinks if s["kind"] in audio_output.KINDS],
    }})


@app.route("/api/audio/test", methods=["POST"])
def api_audio_test():
    """Plays the short "clock OK" chime through the output being chosen (saved
    or not), from this process."""
    body = request.get_json(silent=True) or {}
    kind = body.get("output")
    if kind not in audio_output.KINDS:
        return jsonify({"ok": False, "error": "bad_output"}), 400
    env = _user_session_env()
    sinks = audio_output.list_sinks(env=env)
    if kind == "bluetooth":
        sink = audio_output.find("bluetooth", sinks)
        device = "pipewire/" + sink["name"] if sink else "auto"
    elif kind == "docker":
        # Nothing to hear unless something is listening: the chime would go
        # into the virtual sink and stop there.
        state = _stream_status()
        if not state["available"]:
            return jsonify({"ok": False, "error": "stream_unavailable"}), 404
        return jsonify({"ok": True, "data": {"listeners": state["listeners"]}})
    else:
        device, found = audio_output.mpv_device(kind, sinks)
        if not found:
            return jsonify({"ok": False, "error": "audio_output_missing"}), 404
    sound = cfg().get("CLOCK_OK_SOUND") or ""
    if not os.path.exists(sound):
        return jsonify({"ok": False, "error": "no_test_sound"}), 404
    try:
        subprocess.Popen(["mpv", "--no-terminal", "--really-quiet", "--no-video",
                          "--audio-device=" + device, sound],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return jsonify({"ok": False, "error": "audio_test_failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/audio/restart", methods=["POST"])
def api_audio_restart():
    """Restarts the user's audio services."""
    env = _user_session_env()
    if env is None:
        return jsonify({"ok": False, "error": "no_user_session"}), 400
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart",
             "wireplumber.service", "pipewire.service", "pipewire-pulse.service"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "audio_restart_failed", "detail": "timeout"})
    except OSError as exc:
        return jsonify({"ok": False, "error": "audio_restart_failed", "detail": str(exc)})

    if result.returncode != 0:
        return jsonify({
            "ok": False, "error": "audio_restart_failed",
            "detail": (result.stderr or result.stdout or "").strip()[-200:],
        })

    stats.record("audio_restart", detail={"source": "web"})
    with _audio_state_lock:
        _audio_state["at"] = 0.0
        _audio_state["data"] = None
    time.sleep(2)
    return jsonify({"ok": True, "data": _audio_output_state()})


def _same_network(address, reference):
    """True when both IPv4 addresses sit in the same /24 (what a home network
    is, in practice)."""
    left = str(address or "").split("/")[0].rsplit(".", 1)
    right = str(reference or "").split("/")[0].rsplit(".", 1)
    return len(left) == 2 and len(right) == 2 and left[0] == right[0]


@app.route("/api/wifi/status")
def api_wifi_status():
    conn_name = cfg().get("HOME_WIFI_CONN_NAME", "")
    if not conn_name:
        return jsonify({"ok": True, "data": {"configured": False}})
    data = dict(_status_probe("wifi_status:" + conn_name, lambda: _read_home_wifi(conn_name)))
    # Cutting the network this page is reached through closes it.
    data["client_here"] = bool(data["ip_address"]) and _same_network(request.remote_addr, data["ip_address"])
    return jsonify({"ok": True, "data": data})


def _read_home_wifi(conn_name):
    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
        capture_output=True, text=True,
    )
    active = any(line.split(":")[0] == conn_name for line in result.stdout.splitlines())

    ip_address = None
    if active:
        ip_result = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "device", "show", "wlan0"],
            capture_output=True, text=True,
        )
        ip_address = ip_result.stdout.strip().splitlines()[0] if ip_result.stdout.strip() else None

    auto = subprocess.run(
        ["nmcli", "-g", "connection.autoconnect", "connection", "show", conn_name],
        capture_output=True, text=True,
    ).stdout.strip()
    return {"configured": True, "conn_name": conn_name, "active": active, "ip_address": ip_address,
            "autoconnect": auto == "yes"}


@app.route("/api/wifi/autoconnect", methods=["POST"])
def api_wifi_autoconnect():
    """{on}: whether the personal Wi-Fi comes back by itself at every boot."""
    conn_name = cfg().get("HOME_WIFI_CONN_NAME", "")
    if not conn_name:
        return jsonify({"ok": False, "error": "no_home_wifi"}), 400
    on = bool((request.get_json(silent=True) or {}).get("on"))
    result = subprocess.run(
        ["sudo", "-n", "nmcli", "connection", "modify", conn_name, "connection.autoconnect", "yes" if on else "no"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        return jsonify({"ok": False, "error": "wifi_setting_failed",
                        "detail": (result.stderr or result.stdout).strip()[-300:]}), 500
    stats.record("home_wifi_autoconnect", label="on" if on else "off")
    return jsonify({"ok": True})


@app.route("/api/wifi/toggle", methods=["POST"])
def api_wifi_toggle():
    conn_name = cfg().get("HOME_WIFI_CONN_NAME", "")
    if not conn_name:
        return jsonify({"ok": False, "error": "no_home_wifi"}), 400

    body = request.get_json(silent=True) or {}
    action = "up" if body.get("enabled") else "down"
    result = subprocess.run(
        ["sudo", "nmcli", "connection", action, conn_name],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return jsonify({"ok": False, "error": "wifi_toggle_failed",
                        "detail": (result.stderr or result.stdout).strip()[-300:]}), 500
    # Read by scripts/home-wifi-connect.sh, which leaves it alone when switched off here on purpose.
    update_config_file({"HOME_WIFI_ENABLED": action == "up"})
    stats.record("home_wifi_toggle", label=action, detail={"conn_name": conn_name})
    return jsonify({"ok": True})


AP_CONNECTION_NAME = "rukebox-ap"
_ap_save_lock = threading.Lock()


def _ap_connection_exists():
    """Whether the admin access point's NetworkManager profile exists, active
    or not. False where nmcli is not installed at all (a container has no
    NetworkManager), rather than an exception in a caller's face."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show"],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    return AP_CONNECTION_NAME in result.stdout.splitlines()


SYSTEM_SERVICES = (
    ("rukebox-daemon", True),
    ("rukebox-web", True),
    ("bt-connect", True),
    ("home-wifi-connect", True),
    ("rukebox-speaker-buttons", True),
    ("rukebox-bt-buttons", True),
    ("rukebox-card-reader", True),
    ("rukebox-gpio-button", True),
    ("flicd", True),
    ("flic-bridge", True),
    ("rukebox-config", False),
    ("create-uap0", False),
    ("rukebox-usb-gadget", False),
    ("rukebox-gpio-reset", False),
    ("rukebox-act-led", False),
)
_SERVICE_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
                  "ActiveEnterTimestampMonotonic", "Result", "Type")


def _services_state():
    """One `systemctl show` for every unit: [{name, state, sub, enabled, since."""
    if not system_actions.local_service_units():
        # No systemd here: a container's services are its supervisor's children.
        return []
    units = [name + ".service" for name, _ in SYSTEM_SERVICES]
    out = system_actions.service_show(units, props=_SERVICE_PROPS)
    if not out:
        return []
    blocks, current = [], {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    restartable = dict(SYSTEM_SERVICES)
    now = time.monotonic()
    services = []
    for block in blocks:
        name = block.get("Id", "").removesuffix(".service")
        if name not in restartable or block.get("LoadState") != "loaded":
            continue
        since = None
        try:
            entered = int(block.get("ActiveEnterTimestampMonotonic") or 0)
            if entered and block.get("ActiveState") == "active":
                since = max(0, round(now - entered / 1e6))
        except ValueError:
            pass
        enabled = block.get("UnitFileState") in ("enabled", "static", "enabled-runtime", "alias")
        state = block.get("ActiveState", "unknown")
        services.append({
            "name": name,
            "state": state,
            "sub": block.get("SubState"),
            "oneshot": block.get("Type") == "oneshot",
            "enabled": enabled,
            "since": since,
            "result": block.get("Result"),
            "restartable": restartable[name] and (enabled or state == "active"),
        })
    return services


@app.route("/api/system/services")
def api_system_services():
    return jsonify({"ok": True, "data": _services_state()})


@app.route("/api/system/services/<name>/restart", methods=["POST"])
def api_system_service_restart(name):
    allowed = dict(SYSTEM_SERVICES)
    if not allowed.get(name):
        return jsonify({"ok": False, "error": "unknown_service"}), 404
    service = next((s for s in _services_state() if s["name"] == name), None)
    if not service or not service["restartable"]:
        return jsonify({"ok": False, "error": "service_not_in_use"}), 400
    if name == "rukebox-web":
        # The answer has to leave before the server it comes from is replaced.
        threading.Timer(0.8, system_actions.restart_web_server).start()
        return jsonify({"ok": True, "data": {"reload": True}})
    if not system_actions.service_action("restart", name, timeout=30):
        log.warning("Restarting %s failed", name)
        return jsonify({"ok": False, "error": "service_restart_failed"}), 500
    return jsonify({"ok": True})


_system_info_cache = {"at": 0.0, "data": None}
SYSTEM_INFO_CACHE_SECONDS = 10


def _read_first(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _throttled():
    """`vcgencmd get_throttled`: under-voltage and throttling, now and since
    boot."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        value = int(out.strip().split("=", 1)[1], 16)
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        return None
    return {
        "undervoltage_now": bool(value & 0x1),
        "throttled_now": bool(value & 0x4),
        "undervoltage_since_boot": bool(value & 0x10000),
        "throttled_since_boot": bool(value & 0x40000),
    }


def _disk(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": usage.total, "free": usage.free}


def _mount_of(path):
    """(device, mount point, fs type, options) of the mount holding `path`."""
    best = None
    real = os.path.realpath(path)
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                mnt = parts[1].replace("\\040", " ")
                if real == mnt or real.startswith(mnt.rstrip("/") + "/"):
                    if best is None or len(mnt) > len(best[1]):
                        best = (parts[0], mnt, parts[2], parts[3].split(","))
    except OSError:
        return None
    return best


def _block_info(device):
    """What the device holding a file system is: kind (sd / usb_flash /
    usb_disk / disk), model and size."""
    name = os.path.basename(os.path.realpath(device)) if device.startswith("/dev/") else ""
    if not name or not os.path.exists("/sys/class/block/" + name):
        return {"kind": None}
    disk = name
    part_dir = os.path.realpath("/sys/class/block/" + name)
    if os.path.exists(os.path.join(part_dir, "partition")):
        disk = os.path.basename(os.path.dirname(part_dir))
    base = "/sys/block/" + disk
    real = os.path.realpath(base)
    _read = lambda p: (_read_first(p) or "").strip()  # noqa: E731
    rotational = _read(base + "/queue/rotational") == "1"
    if disk.startswith("mmcblk"):
        kind = "sd" if (_read(base + "/device/type") or "SD") == "SD" else "emmc"
    elif "/usb" in real:
        kind = "usb_disk" if rotational else "usb_flash"
    else:
        kind = "disk"
    model = _read(base + "/device/model") or _read(base + "/device/name") or None
    try:
        size = int(_read(base + "/size") or 0) * 512
    except ValueError:
        size = 0
    return {"kind": kind, "disk": disk, "part": name, "model": model, "size": size or None}


def _io_errors(disk):
    """Kernel I/O errors about this disk since boot."""
    def compute():
        try:
            out = subprocess.run(["journalctl", "-k", "-b", "-p", "err", "--no-pager", "-q", "-o", "cat"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        return sum(1 for line in out.splitlines() if disk in line and "error" in line.lower())
    return _cached_probe("io_errors_" + disk, 300, compute) if disk else None


def _storage_health(path, role):
    """A file system as the health card and "To finish" see it: the device
    kind, the free space, read-only."""
    mount = _mount_of(path)
    usage = _disk(path)
    if not mount or not usage:
        return None
    device, mnt, fstype, options = mount
    info = _block_info(device)
    fs_errors = None
    if info.get("part") and fstype == "ext4":
        try:
            fs_errors = int((_read_first("/sys/fs/ext4/%s/errors_count" % info["part"]) or "0").strip() or 0)
        except ValueError:
            fs_errors = None
    io_errors = _io_errors(info.get("disk"))
    read_only = "ro" in options
    # A `:ro` share and the USB key are read-only on purpose; only a device the
    # kernel remounted read-only is a failure.
    on_purpose = info.get("part") is None or mnt == usb_storage.mount_point()
    read_only_on_purpose = read_only and on_purpose
    status = "ok"
    # A share of the device: a fixed 500 MB floor would call a small key full for
    # ever.
    if usage["free"] < usage["total"] * 0.05 or io_errors:
        status = "warn"
    if (read_only and not read_only_on_purpose) or fs_errors:
        status = "error"
    return {
        "role": role, "mount": mnt, "fs": fstype, "kind": info.get("kind"), "model": info.get("model"),
        "size": info.get("size"), "total": usage["total"], "free": usage["free"],
        "read_only": read_only, "read_only_on_purpose": read_only_on_purpose,
        "fs_errors": fs_errors, "io_errors": io_errors, "status": status,
    }


def _storages():
    """The system's file system, and the music's when it is elsewhere."""
    out = []
    system = _storage_health("/", "system")
    if system:
        out.append(system)
    music_dir = _effective_music_dir() or ""
    if music_dir and os.path.isdir(music_dir):
        music = _storage_health(music_dir, "music")
        if music and (not system or music["mount"] != system["mount"]):
            out.append(music)
        elif system:
            system["role"] = "system_music"
    return out


def _usb_devices():
    """Names of the USB devices plugged in (root hubs left out), from lsusb."""
    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = []
    for line in out.splitlines():
        m = re.match(r"Bus \d+ Device \d+: ID ([0-9a-f]{4}):[0-9a-f]{4}\s*(.*)$", line.strip())
        if m and m.group(1) != "1d6b":
            names.append(m.group(2).strip() or "USB device")
    return names


@app.route("/api/system/info")
def api_system_info():
    """Uptime, temperature, memory, storage, power supply, addresses."""
    if _system_info_cache["data"] and time.time() - _system_info_cache["at"] < SYSTEM_INFO_CACHE_SECONDS:
        return jsonify({"ok": True, "data": _system_info_cache["data"]})
    data = {}
    uptime = _read_first("/proc/uptime")
    data["uptime"] = round(float(uptime.split()[0])) if uptime else None
    temp = _read_first("/sys/class/thermal/thermal_zone0/temp")
    try:
        data["temperature"] = round(int(temp) / 1000, 1) if temp else None
    except ValueError:
        data["temperature"] = None
    meminfo = {}
    for line in (_read_first("/proc/meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        try:
            meminfo[key] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    data["memory"] = ({"total": meminfo["MemTotal"], "available": meminfo.get("MemAvailable", 0)}
                      if "MemTotal" in meminfo else None)
    load = _read_first("/proc/loadavg")
    data["load"] = [float(x) for x in load.split()[:3]] if load else None
    data["storage"] = _storages()
    # Where the Clock page is not offered (a container cannot set its clock),
    # this is the one place the zone it runs on is readable.
    data["timezone"] = _timezone_name()
    data["model"] = (_read_first("/proc/device-tree/model") or "").strip("\x00 \n") or None
    data["usb_port_mode"] = cfg().get("USB_PORT_MODE", "gadget")
    data["usb_devices"] = _usb_devices()
    data["kernel"] = os.uname().release if hasattr(os, "uname") else None
    data["power"] = _throttled()
    addresses = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[1] != "lo":
                addresses.append({"interface": parts[1], "address": parts[3].split("/")[0]})
    except (OSError, subprocess.TimeoutExpired):
        pass
    data["addresses"] = addresses
    _system_info_cache.update(at=time.time(), data=data)
    return jsonify({"ok": True, "data": data})


def _ap_is_open():
    """True when the admin access point has no password (cached a minute)."""
    def compute():
        if not _ap_connection_exists():
            return False
        key_mgmt = subprocess.run(
            ["nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", AP_CONNECTION_NAME],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return not key_mgmt
    try:
        return _cached_probe("ap_open", 60, compute)
    except (OSError, subprocess.TimeoutExpired):
        return False


_slow_probes = {}


def _cached_probe(key, ttl, compute):
    hit = _slow_probes.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = compute()
    _slow_probes[key] = (time.monotonic(), value)
    return value


SETUP_ITEMS = ("speaker", "speaker_pair", "flic_pair", "clock", "timezone", "music", "password",
               "ap_open", "storage")


@app.route("/api/diag/audio", methods=["POST"])
def api_diag_audio():
    """What the audio path is made of right now - the link, the sink, the codec
    and the bitrate actually measured - for a cut-out or a doubt about the
    sound. Measured over a few seconds, so the page disables the button."""
    try:
        text = audio_diag.report(cfg=cfg(), env=_user_session_env())
    except Exception:  # noqa: BLE001
        log.exception("Could not build the audio diagnostic")
        return jsonify({"ok": False, "error": "diag_failed"}), 500
    log.info("Audio diagnostic sent to %s", _client_ip())
    return jsonify({"ok": True, "data": {"report": text}})


@app.route("/api/diag/network", methods=["POST"])
def api_diag_network():
    """The personal Wi-Fi's link and every drop since the Pi started, set
    against the Bluetooth radio's own attempts. A few seconds of pings."""
    try:
        text = net_diag.report(cfg=cfg())
    except Exception:  # noqa: BLE001
        log.exception("Could not build the network diagnostic")
        return jsonify({"ok": False, "error": "diag_failed"}), 500
    log.info("Network diagnostic sent to %s", _client_ip())
    return jsonify({"ok": True, "data": {"report": text}})


@app.route("/api/likes")
def api_likes():
    """The liked tracks, most recently liked first, with their dates."""
    items = likes.load(cfg()["LIKES_FILE"])
    return jsonify({"ok": True, "data": {
        "tracks": [{"key": item["key"], "title": item.get("title") or "",
                    "artist": item.get("artist") or "", "liked_at": item.get("liked_at")}
                   for item in items],
        "keys": [item["key"] for item in items],
    }})


@app.route("/api/cards")
def api_cards():
    """The RFID cards, by name."""
    found = cards.load(cfg()["CARDS_FILE"])
    return jsonify({"ok": True, "data": sorted(found.values(), key=lambda c: c["name"].lower())})


@app.route("/api/cards", methods=["POST"])
def api_cards_save():
    try:
        card = cards.save(cfg()["CARDS_FILE"], request.get_json(silent=True) or {})
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    stats.record("card_saved", label=card["name"], detail={"id": card["id"], "action": card["action"]})
    return jsonify({"ok": True, "data": card})


@app.route("/api/cards/<card_id>", methods=["DELETE"])
def api_cards_delete(card_id):
    try:
        cards.delete(cfg()["CARDS_FILE"], card_id)
    except KeyError:
        return jsonify({"ok": False, "error": "card_unknown"}), 404
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})


@app.route("/api/cards/<card_id>/play", methods=["POST"])
def api_cards_play(card_id):
    """Does what the card would do, without the card."""
    result = control("card", id=card_id, source="web")
    return jsonify(result), (200 if result.get("ok") else 409)


@app.route("/api/memories")
def api_memories():
    """Songs liked on this day in an earlier year, or a month ago."""
    items = likes.load(cfg()["LIKES_FILE"])
    return jsonify({"ok": True, "data": likes.memories(items, datetime.now().date())})


@app.route("/api/likes/toggle", methods=["POST"])
def api_likes_toggle():
    """{key, title, artist}: likes the track, or takes the like back."""
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "like_key_required"}), 400
    known = _library_item(key)
    title = str(body.get("title") or "").strip() or str(known.get("title") or "").strip()
    artist = str(body.get("artist") or "").strip() or str(known.get("artist") or "").strip()
    try:
        liked = likes.toggle(cfg()["LIKES_FILE"], key, title, artist)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    stats.record("track_liked" if liked else "track_unliked", label=title or key,
                 detail={"key": key})
    return jsonify({"ok": True, "data": {"liked": liked,
                                         "count": len(likes.load(cfg()["LIKES_FILE"]))}})


@app.route("/api/library/duplicates")
def api_library_duplicates():
    """The songs the library holds more than once, from library.db alone: the
    scan already read every file, so the check costs no disk access at all."""
    c = cfg()
    path = c.get("LIBRARY_DB_FILE") or ""
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "library_missing"}), 404
    hidden = hidden_tracks.keys(c.get("HIDDEN_FILE") or "")
    found = duplicates.groups(path, hidden)
    return jsonify({"ok": True, "data": {"groups": found,
                                         "summary": duplicates.summary(found)}})


@app.route("/api/library/hide", methods=["POST"])
def api_library_hide():
    """{key, hidden}: keeps one copy out of what the radio chooses by itself.
    Nothing is deleted - the file is still there to be played by hand."""
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "hidden_key_required"}), 400
    hidden = bool(body.get("hidden", True))
    known = _library_item(key)
    c = cfg()
    file_path = c.get("HIDDEN_FILE") or ""
    try:
        hidden_tracks.set_hidden(
            file_path, key, hidden,
            track_path=str(body.get("path") or known.get("path") or ""),
            title=str(body.get("title") or known.get("title") or ""),
            artist=str(body.get("artist") or known.get("artist") or ""),
            origin="duplicate")
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    # The queue must not keep a copy that was just hidden.
    _after_exclusion_change()
    stats.record("track_hidden" if hidden else "track_shown",
                 label=str(known.get("title") or key), detail={"key": key})
    return jsonify({"ok": True, "data": {
        "hidden": hidden, "count": len(hidden_tracks.keys(file_path))}})


def _excluded_entries():
    """The excluded tracks as the file holds them, most recent first."""
    try:
        return hidden_tracks.load(cfg().get("HIDDEN_FILE") or "")
    except Exception:  # noqa: BLE001 - a broken file must not close the page
        log.exception("Could not read the excluded tracks")
        return []


def _excluded_keys():
    """The library keys the radio does not pick by itself."""
    try:
        return hidden_tracks.keys(cfg().get("HIDDEN_FILE") or "")
    except Exception:  # noqa: BLE001
        log.exception("Could not read the excluded tracks")
        return set()


def _after_exclusion_change():
    """The daemon's running pass must not keep what just changed."""
    control("reload_hidden")


def _excluded_rows(keys):
    """The library rows for these keys, in the order asked for; a key the
    catalogue does not know names no track."""
    lib = _get_library()
    rows = []
    for key in keys:
        path = lib.path_for_key(key)
        item = lib.item_for_path(path) if path else None
        if item:
            item["path"] = path
            rows.append(item)
    return rows


EXCLUDED_PAGE = 30
EXCLUDED_PAGE_MAX = 500


@app.route("/api/excluded")
def api_excluded():
    """What the radio never picks by itself, and how each one got there. A page
    at a time, like the library: an exclusion list can be long, and the page
    asks for more rather than being handed all of it."""
    entries = _excluded_entries()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(EXCLUDED_PAGE_MAX, int(request.args.get("limit", EXCLUDED_PAGE))))
    except (TypeError, ValueError):
        limit = EXCLUDED_PAGE
    page = entries[offset:offset + limit]
    rows = {item["path"]: item for item in
            _get_library().items_for_paths([entry.get("path") for entry in page])}
    items = []
    for entry in page:
        row = rows.get(entry.get("path")) or {}
        items.append({
            "key": entry["key"],
            "path": entry.get("path") or "",
            "title": row.get("title") or entry.get("title") or "",
            "artist": row.get("artist") or entry.get("artist") or "",
            "album": row.get("album") or "",
            "excluded_at": entry.get("hidden_at"),
            "origin": entry.get("origin") or "manual",
            "missing": not row,
        })
    return jsonify({"ok": True, "data": {"items": items, "count": len(entries),
                                         "offset": offset, "limit": limit}})


@app.route("/api/excluded", methods=["POST"])
def api_excluded_add():
    """{keys: [...]}: the radio stops choosing these on its own. Nothing is
    deleted, and they stay in the library and in every list."""
    body = request.get_json(silent=True) or {}
    keys = [str(key).strip() for key in (body.get("keys") or []) if str(key).strip()]
    rows = _excluded_rows(keys)
    if not rows:
        return jsonify({"ok": False, "error": "excluded_key_required"}), 400
    count = hidden_tracks.set_many(cfg().get("HIDDEN_FILE") or "", rows, True, "manual")
    _after_exclusion_change()
    stats.record("track_hidden", label=rows[0].get("title") if count == 1 else None,
                 detail={"keys": count, "source": "page"})
    return jsonify({"ok": True, "data": {"count": count, "total": len(_excluded_keys())}})


@app.route("/api/excluded/filter", methods=["POST"])
def api_excluded_add_filter():
    """{q, artist, album, genre}: every track the filter finds, in one write -
    what excluding a whole artist or album needs, and not only the rows the
    page happens to show."""
    body = request.get_json(silent=True) or {}
    words = str(body.get("q") or "").strip()
    artist = str(body.get("artist") or "").strip() or None
    album = str(body.get("album") or "").strip() or None
    genre = str(body.get("genre") or "").strip() or None
    if not (words or artist or album or genre):
        return jsonify({"ok": False, "error": "excluded_filter_required"}), 400
    rows = _get_library().all_items(words, artist, album, genre)
    count = hidden_tracks.set_many(cfg().get("HIDDEN_FILE") or "", rows, True, "filter")
    _after_exclusion_change()
    stats.record("track_hidden", detail={"keys": count, "source": "filter"})
    return jsonify({"ok": True, "data": {"count": count, "total": len(_excluded_keys())}})


@app.route("/api/excluded/restore", methods=["POST"])
def api_excluded_restore():
    """{keys: [...]} or {all: true}: the radio picks them again."""
    body = request.get_json(silent=True) or {}
    file_path = cfg().get("HIDDEN_FILE") or ""
    if body.get("all"):
        count = len(hidden_tracks.keys(file_path))
        hidden_tracks.clear(file_path)
    else:
        keys = [str(key).strip() for key in (body.get("keys") or []) if str(key).strip()]
        if not keys:
            return jsonify({"ok": False, "error": "excluded_key_required"}), 400
        count = hidden_tracks.set_many(file_path, [{"key": key} for key in keys], False)
    _after_exclusion_change()
    stats.record("track_shown", detail={"keys": count, "source": "page"})
    return jsonify({"ok": True, "data": {"count": count, "total": len(_excluded_keys())}})


def _library_item(key):
    """The catalogue row for a track key, {} when the library does not know it
    (a like can name a track that has since been removed)."""
    try:
        database = _get_library()
        path = database.path_for_key(key)
        return (database.item_for_path(path) or {}) if path else {}
    except Exception:  # noqa: BLE001
        return {}


@app.route("/api/setup/pending")
def api_setup_pending():
    c = cfg()
    pending = []
    if (c.get("AUDIO_OUTPUT") or "bluetooth") == "bluetooth":
        mac = c.get("SPEAKER_MAC")
        if not mac:
            pending.append("speaker")
        elif _speaker_paired(mac) is False:
            # A restored configuration still names a speaker this Pi was never paired with.
            pending.append("speaker_pair")
    if config_bundle.read_repair(c).get("flic_buttons"):
        if _flic_button_count():
            config_bundle.clear_repair(c)
        else:
            pending.append("flic_pair")
    has_rtc = os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc")
    bt_mac = str(c.get("BT_CLOCK_MAC") or "")
    bt_clock = bool(c.get("BT_CLOCK_ENABLED")) and re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", bt_mac)
    wled_clock = (wled.enabled(c) and bool(c.get("WLED_CLOCK"))
                  and any(host in _wled_memory(c) for host in wled.hosts(c)))
    if platform_mod.has("set_clock") and not has_rtc and not bt_clock and not wled_clock:
        # A container cannot set its clock and does not need to: it is the
        # host's, and the host keeps it. Nothing to finish there.
        pending.append("clock")
    if _timezone_name() in ("", "n/a", "Factory"):
        pending.append("timezone")
    status = control("get_status")
    if status.get("ok") and (status.get("data") or {}).get("track_count") == 0:
        pending.append("music")
    if not c.get("WEB_PASSWORD_HASH"):
        pending.append("password")
    if _ap_is_open():
        pending.append("ap_open")
    if any(s["status"] != "ok" for s in _storages()):
        pending.append("storage")
    hidden = [x.strip() for x in str(c.get("SETUP_HIDDEN") or "").split(",") if x.strip() in SETUP_ITEMS]
    return jsonify({"ok": True, "data": {
        "items": [x for x in pending if x not in hidden],
        "hidden": hidden,
    }})


@app.route("/api/wifi/ap/share")
def api_wifi_ap_share():
    """What the "Share access" card draws as QR codes: the access point."""
    if not _ap_connection_exists():
        return jsonify({"ok": True, "data": {"configured": False}})
    ssid = subprocess.run(
        ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", AP_CONNECTION_NAME],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    key_mgmt = subprocess.run(
        ["nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", AP_CONNECTION_NAME],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    password = ""
    if key_mgmt:
        password = subprocess.run(
            ["sudo", "-n", "nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show",
             AP_CONNECTION_NAME],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    ip = captive_portal.interface_ipv4(cfg().get("AP_INTERFACE", "uap0")) or "10.42.0.1"
    port = int(cfg().get("WEB_PORT") or 80)
    suffix = "" if port == 80 else ":%d" % port
    return jsonify({"ok": True, "data": {
        "configured": True, "ssid": ssid, "open": not key_mgmt, "password": password,
        "url": "http://%s%s/" % (ip, suffix),
        "local_url": "http://%s.local%s/" % (socket.gethostname(), suffix),
    }})


@app.route("/api/wifi/ap")
def api_wifi_ap_status():
    """Current admin access point SSID and whether it's password- protected."""
    return jsonify({"ok": True, "data": _status_probe("wifi_ap", _read_access_point)})


def _read_access_point():
    if not _ap_connection_exists():
        return {"configured": False}

    ssid = subprocess.run(
        ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", AP_CONNECTION_NAME],
        capture_output=True, text=True,
    ).stdout.strip()
    key_mgmt = subprocess.run(
        ["nmcli", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", AP_CONNECTION_NAME],
        capture_output=True, text=True,
    ).stdout.strip()
    active_result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
        capture_output=True, text=True,
    )
    active = any(line.rpartition(":")[0] == AP_CONNECTION_NAME for line in active_result.stdout.splitlines())
    return {"configured": True, "ssid": ssid, "password_set": bool(key_mgmt), "active": active}


@app.route("/api/wifi/ap", methods=["POST"])
def api_wifi_ap_save():
    """Changes the admin access point's SSID and/or password, applied
    immediately."""
    iface = cfg().get("AP_INTERFACE", "uap0")
    body = request.get_json(silent=True) or {}
    ssid = (body.get("ssid") or "").strip()
    open_network = bool(body.get("open"))
    password = "" if open_network else (body.get("password") or "")

    if not ssid:
        return jsonify({"ok": False, "error": "ssid_required"}), 400

    if not _ap_save_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "ap_save_in_progress"}), 409
    try:
        return _do_ap_save(iface, ssid, open_network, password)
    finally:
        _ap_save_lock.release()


def _do_ap_save(iface, ssid, open_network, password):
    try:
        existing = _ap_connection_exists()

        if not open_network and not password:
            if not existing:
                return jsonify({"ok": False, "error": "password_required"}), 400
            secret_result = subprocess.run(
                ["sudo", "nmcli", "-s", "-g", "802-11-wireless-security.psk",
                 "connection", "show", AP_CONNECTION_NAME],
                capture_output=True, text=True, timeout=15,
            )
            password = secret_result.stdout.strip()
            if not password:
                return jsonify({"ok": False, "error": "password_required"}), 400

        if password and len(password) < 8:
            return jsonify({"ok": False, "error": "password_too_short"}), 400

        if existing:
            subprocess.run(
                ["sudo", "nmcli", "connection", "delete", AP_CONNECTION_NAME],
                capture_output=True, text=True, timeout=15,
            )

        add_argv = [
            "sudo", "nmcli", "connection", "add", "type", "wifi",
            "ifname", iface, "con-name", AP_CONNECTION_NAME,
            "autoconnect", "yes", "ssid", ssid,
            "--",
            "802-11-wireless.mode", "ap",
            "ipv4.method", "shared",
            "ipv6.method", "ignore",
            "802-11-wireless.powersave", "2",
        ]
        result = subprocess.run(add_argv, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            log.warning("Access point profile could not be created: %s", error)
            return jsonify({"ok": False, "error": "ap_create_failed", "detail": error[-300:]}), 500

        if password:
            sec_result = subprocess.run(
                ["sudo", "nmcli", "connection", "modify", AP_CONNECTION_NAME,
                 "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
                capture_output=True, text=True, timeout=15,
            )
            if sec_result.returncode != 0:
                error = sec_result.stderr.strip() or sec_result.stdout.strip() or "nmcli failed (no output)"
                log.warning("Access point password could not be set: %s", error)
                return jsonify({"ok": False, "error": error}), 500

        up_result = subprocess.run(
            ["sudo", "nmcli", "connection", "up", AP_CONNECTION_NAME, "ifname", iface],
            capture_output=True, text=True, timeout=60,
        )
        if up_result.returncode != 0:
            error = up_result.stderr.strip() or up_result.stdout.strip() or "nmcli failed (no output)"
            log.warning("Access point could not be activated: %s", error)
            return jsonify({"ok": False, "error": error}), 500
    except subprocess.TimeoutExpired as e:
        log.warning("Access point save timed out: %s", e)
        return jsonify({"ok": False, "error": "nmcli_timeout"}), 500
    except OSError as e:
        log.warning("Access point save failed to run nmcli: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    stats.record("ap_config_changed", label="secured" if password else "open")
    return jsonify({"ok": True, "password_set": bool(password)})


@app.route("/api/ssh", methods=["POST"])
def api_ssh_toggle():
    body = request.get_json(silent=True) or {}
    enable = bool(body.get("enabled"))
    unit = "ssh.service"
    enable_ok = system_actions.service_action("enable" if enable else "disable", unit)
    run_ok = system_actions.service_action("start" if enable else "stop", unit)
    if not (enable_ok and run_ok):
        return jsonify({"ok": False, "error": "ssh_toggle_failed"}), 500
    stats.record("ssh_toggle", label="enabled" if enable else "disabled")
    return jsonify({"ok": True, "enabled": enable})


@app.route("/api/journal/summary")
def api_stats_summary():
    data = stats.summary()
    lib = _get_library()
    for kind in ("music", "error"):
        for row in (data.get("top") or {}).get(kind) or []:
            row["in_library"] = bool(row.get("name")) and lib.item_for_basename(row["name"]) is not None
    return jsonify({"ok": True, "data": data})


@app.route("/api/journal/daily")
def api_stats_daily():
    try:
        days = int(request.args.get("days", 14))
    except ValueError:
        days = 14
    return jsonify({"ok": True, "data": stats.daily_series(days)})


RECAP_PERIODS = ("month", "last_month", "year", "last_year", "all")


def recap_range(period, today):
    """(first day, last day) of a recap period."""
    first_of_month = today.replace(day=1)
    if period == "last_month":
        last = first_of_month - timedelta(days=1)
        return last.replace(day=1), last
    if period == "year":
        return today.replace(month=1, day=1), today
    if period == "last_year":
        return today.replace(year=today.year - 1, month=1, day=1), today.replace(year=today.year - 1, month=12, day=31)
    if period == "all":
        return today.replace(year=2000, month=1, day=1), today
    return first_of_month, today


@app.route("/api/journal/recap")
def api_journal_recap():
    """The period in figures, the songs named after their tags and grouped by artist."""
    period = request.args.get("period", "month")
    if period not in RECAP_PERIODS:
        period = "month"
    first, last = recap_range(period, datetime.now().date())
    data = stats.recap(first, last)
    if data is None:
        return jsonify({"ok": True, "data": {"enabled": False}})
    lib = _get_library()
    artists = {}
    named = []
    for track in data.pop("tracks"):
        item = lib.item_for_basename(track["name"]) or {}
        title = item.get("title") or os.path.splitext(track["name"])[0]
        artist = item.get("artist") or ""
        named.append({"title": title, "artist": artist, "count": track["count"],
                      "name": track["name"], "in_library": bool(item)})
        if artist:
            entry = artists.setdefault(library.fold(artist), {"artist": artist, "count": 0, "seconds": 0.0})
            entry["count"] += track["count"]
            entry["seconds"] += track["seconds"] or 0
    limit = data.pop("top_limit")
    data["top_tracks"] = named[:limit]
    data["top_artists"] = sorted(artists.values(), key=lambda a: (-a["count"], a["artist"]))[:limit]
    data["artists"] = len(artists)
    if data.get("morning"):
        item = lib.item_for_basename(data["morning"]["name"]) or {}
        data["morning"].update(title=item.get("title") or os.path.splitext(data["morning"]["name"])[0],
                               artist=item.get("artist") or "", in_library=bool(item))
    start = datetime.combine(first, datetime.min.time()).timestamp()
    end = datetime.combine(last + timedelta(days=1), datetime.min.time()).timestamp()
    data["likes"] = sum(1 for item in likes.load(cfg()["LIKES_FILE"])
                        if isinstance(item.get("liked_at"), (int, float)) and start <= item["liked_at"] < end)
    data["period"] = period
    return jsonify({"ok": True, "data": data})


@app.route("/api/journal/entries")
def api_stats_events():
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    before_id = request.args.get("before_id")
    try:
        before_id = int(before_id) if before_id else None
    except ValueError:
        before_id = None
    event_type = request.args.get("type") or None
    # A search runs in the database, so it finds what "Load more" has not
    # fetched yet - and `total` is then the number of matches, not the log's.
    query = (request.args.get("q") or "").strip()[:120] or None
    # Event types the page recognised in the search text: it shows translated
    # names, so it is the only side that knows "interface" means web_session.
    labels = [name.strip()[:60] for name in (request.args.get("labels") or "").split(",")
              if name.strip()][:40] or None
    return jsonify({
        "ok": True,
        "data": {
            "events": _name_visitors(stats.events(
                limit=limit,
                event_type=event_type,
                before_id=before_id,
                query=query,
                labels=labels,
            )),
            "types": stats.event_types(),
            "total": stats.count_rows("events", event_type, query, labels),
        },
    })


@app.route("/api/journal/sessions")
def api_stats_sessions():
    """Older sessions for the "Load more" of the startups list."""
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    try:
        before_id = int(request.args["before_id"]) if request.args.get("before_id") else None
    except ValueError:
        before_id = None
    return jsonify({"ok": True, "data": {"sessions": stats.sessions_page(limit, before_id)}})


CONFIG_IMPORT_MAX_BYTES = 2 * 1024 * 1024


AUDIO_EXTENSIONS = (".mp3", ".opus", ".ogg", ".oga", ".wav", ".m4a", ".aac",
                    ".flac", ".wma", ".mp4", ".webm")


def _allowed_roots():
    """Every folder this installation keeps sounds in, created or not, outside which nothing is
    browsed or written."""
    roots = []
    values = cfg()
    for setting in config_schema.SETTINGS:
        if setting.section != "folders" or not setting.env.endswith("_DIR"):
            continue
        folder = str(values.get(setting.env) or "").strip()
        if folder:
            roots.append(folder)
    try:
        for item in announcements.read_items(values.get("ANNOUNCEMENTS_FILE")) or []:
            folder = str(item.get("folder") or "").strip()
            if folder:
                roots.append(folder)
    except Exception:  # noqa: BLE001 - a broken list must not take browsing down
        log.debug("Could not read the announcement folders", exc_info=True)
    for key in config_schema.SYSTEM_SOUNDS:
        # A sound that is off has no original to replace, and its folder would
        # only exist at the template's path - the Pi's own, on a container.
        if not str(values.get(key) or "").strip():
            continue
        roots.append(_system_sound_custom_dir(key, values))
    return list(dict.fromkeys(roots))


def _browse_roots():
    """The roots that actually exist, in order: every folder this installation
    keeps its sounds in - a container keeps them elsewhere, and each
    announcement type and system sound has its own."""
    return [root for root in _allowed_roots() if os.path.isdir(root)]


def _inside_roots(path):
    """Whether a path is one the interface may read or write: a configured
    folder, or something inside one."""
    for root in _allowed_roots():
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return True
    return False


def _browse_audio_count(path):
    """How many audio files sit DIRECTLY in this folder."""
    try:
        names = os.listdir(path)
    except OSError:
        return 0
    return sum(1 for name in names
               if name.lower().endswith(AUDIO_EXTENSIONS)
               and os.path.isfile(os.path.join(path, name)))


@app.route("/api/browse")
def api_browse():
    roots = _browse_roots()
    if not roots:
        return jsonify({"ok": False, "error": "bad_path"}), 400

    raw = (request.args.get("path") or "").strip() or roots[0]
    path = os.path.realpath(raw)
    if not _inside_roots(path):
        return jsonify({"ok": False, "error": "bad_path"}), 400
    if os.path.basename(path).startswith("."):
        return jsonify({"ok": False, "error": "bad_path"}), 400
    if not os.path.isdir(path):
        return jsonify({"ok": False, "error": "bad_path"}), 400

    try:
        entries = sorted(os.listdir(path), key=str.lower)
    except OSError:
        return jsonify({"ok": False, "error": "bad_path"}), 400

    dirs = []
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        if not os.path.isdir(full):
            continue
        dirs.append({
            "name": name,
            "path": full,
            "audio_files": _browse_audio_count(full),
        })

    parent = os.path.dirname(path)
    return jsonify({"ok": True, "data": {
        "path": path,
        "parent": parent if _inside_roots(parent) and parent != path else None,
        "roots": roots,
        "dirs": dirs,
    }})


@app.route("/api/config/export")
def api_config_export():
    """The whole hand-made configuration as one downloadable file."""
    c = cfg()
    bundle = config_bundle.export_bundle(c, read_version_file(c["UPDATE_VERSION_FILE"]),
                                         flic_buttons=_flic_button_count())
    payload = json.dumps(bundle, ensure_ascii=False, indent=2)
    filename = "rukebox-config-%s.json" % time.strftime("%Y%m%d-%H%M%S")
    stats.record("config_exported", detail={"settings": len(bundle["settings"])})
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@app.route("/api/config/import", methods=["POST"])
def api_config_import():
    """Applies a bundle exported from this."""
    if not request.content_length or request.content_length > CONFIG_IMPORT_MAX_BYTES:
        return jsonify({"ok": False, "error": "no_file"}), 400

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "not_a_bundle"}), 400

    try:
        summary = config_bundle.import_bundle(body, cfg())
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    notify_daemon("reload_announcements")
    _reload_after_import(summary)
    notify_daemon("reload_config")
    stats.record("config_imported", label=str(summary["settings"]), detail=summary)
    return jsonify({"ok": True, "data": summary})


def _reload_after_import(summary):
    """The daemon reads these stores on its own clock: tell it they changed."""
    if "schedules" in summary:
        notify_daemon("reload_schedules")
    if "music_lists" in summary:
        notify_daemon("reload_lists")
    if "hidden" in summary:
        notify_daemon("reload_hidden")
    forget_stream()


BACKUP_FORMAT = "rukebox-backup"
BACKUP_VERSION = 1
BACKUP_PARTS = ("stats", "suggestions", "sounds")
BACKUP_MAX_BYTES = 4 * 1024 ** 3
BACKUP_PENDING_SEC = 600
_backup_pending = {}
_backup_lock = threading.Lock()


def _backup_dir():
    path = os.path.join(cfg()["STATE_DIR"], "backup-tmp")
    os.makedirs(path, exist_ok=True)
    return path


def _sound_sources():
    """{source_id: folder} of every announcement list."""
    c = cfg()
    items = announcements.load(_announcements_path())
    ids = list(announcements.BUILTIN_SOURCES) + ["custom:" + it["id"] for it in items]
    out = {}
    for source_id in ids:
        folder = announcements.resolve_source_folder(c, items, source_id)
        if folder and os.path.isdir(folder):
            out[source_id] = folder
    return out


def _sound_files(folder):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".")
            and os.path.splitext(n)[1].lower() in announcements.AUDIO_EXTENSIONS
            and os.path.isfile(os.path.join(folder, n))]


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


@app.route("/api/backup/sizes")
def api_backup_sizes():
    """How big each optional part would be, for the checkboxes."""
    c = cfg()
    sounds = sum(_file_size(os.path.join(folder, n))
                 for folder in _sound_sources().values() for n in _sound_files(folder))
    return jsonify({"ok": True, "data": {
        "stats": _file_size(c["STATS_DB_FILE"]) if c.get("STATS_ENABLED") else 0,
        "suggestions": _file_size(c["SUGGESTIONS_DB_FILE"]),
        "sounds": sounds,
    }})


def _sqlite_snapshot(db_path, zf, arcname):
    """A consistent copy of a live SQLite file into the zip."""
    if not os.path.exists(db_path):
        return False
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=_backup_dir())
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        zf.write(tmp, arcname, compress_type=zipfile.ZIP_DEFLATED)
        return True
    finally:
        os.remove(tmp)


@app.route("/api/backup")
def api_backup():
    """?parts=stats,suggestions,sounds."""
    c = cfg()
    wanted = [p for p in (request.args.get("parts") or "").split(",") if p in BACKUP_PARTS]
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=_backup_dir())
    os.close(fd)
    parts = ["config"]
    manifest = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "created": time.time(), "sounds": {}}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            bundle = config_bundle.export_bundle(c, read_version_file(c["UPDATE_VERSION_FILE"]),
                                                 flic_buttons=_flic_button_count())
            zf.writestr("config.json", json.dumps(bundle, ensure_ascii=False, indent=2))
            if "stats" in wanted and c.get("STATS_ENABLED") and _sqlite_snapshot(c["STATS_DB_FILE"], zf, "stats.db"):
                parts.append("stats")
            if "suggestions" in wanted and _sqlite_snapshot(c["SUGGESTIONS_DB_FILE"], zf, "suggestions.db"):
                parts.append("suggestions")
            if "sounds" in wanted:
                for source_id, folder in _sound_sources().items():
                    names = _sound_files(folder)
                    for n in names:
                        zf.write(os.path.join(folder, n), "sounds/%s/%s" % (source_id.replace(":", "_"), n),
                                 compress_type=zipfile.ZIP_STORED)
                    if names:
                        manifest["sounds"][source_id] = names
                parts.append("sounds")
            manifest["parts"] = parts
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    except Exception:  # noqa: BLE001
        log.exception("Backup failed")
        os.remove(tmp)
        return jsonify({"ok": False, "error": "backup_failed"}), 500
    stats.record("backup_exported", label=",".join(parts), detail={"bytes": _file_size(tmp)})

    def stream():
        try:
            with open(tmp, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    filename = "rukebox-backup-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    return Response(stream(), mimetype="application/zip", headers={
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "Content-Length": str(_file_size(tmp)),
    })


def _read_backup(path):
    """(manifest, zipfile) of a backup, or raises ValueError(code)."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise ValueError("not_a_backup")
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError):
        zf.close()
        raise ValueError("not_a_backup")
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        zf.close()
        raise ValueError("not_a_backup")
    if int(manifest.get("version") or 0) > BACKUP_VERSION:
        zf.close()
        raise ValueError("newer_backup")
    if sum(i.file_size for i in zf.infolist()) > BACKUP_MAX_BYTES:
        zf.close()
        raise ValueError("backup_too_big")
    return manifest, zf


def _forget_old_backups():
    now = time.time()
    for token, entry in list(_backup_pending.items()):
        if now - entry["at"] > BACKUP_PENDING_SEC:
            _backup_pending.pop(token, None)
            try:
                os.remove(entry["path"])
            except OSError:
                pass


@app.route("/api/backup/inspect", methods=["POST"])
def api_backup_inspect():
    """Step 1 of a restore: the file is kept aside and described."""
    upload = request.files.get("file")
    if not upload:
        return jsonify({"ok": False, "error": "no_file"}), 400
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=_backup_dir())
    os.close(fd)
    upload.save(tmp)
    try:
        manifest, zf = _read_backup(tmp)
        zf.close()
    except ValueError as e:
        os.remove(tmp)
        return jsonify({"ok": False, "error": str(e)}), 400
    token = secrets.token_hex(16)
    with _backup_lock:
        _forget_old_backups()
        _backup_pending[token] = {"path": tmp, "at": time.time()}
    parts = [p for p in manifest.get("parts") or [] if p in ("config",) + BACKUP_PARTS]
    return jsonify({"ok": True, "data": {
        "token": token, "parts": parts, "created": manifest.get("created"),
        "sounds": sum(len(v) for v in (manifest.get("sounds") or {}).values()),
    }})


def _restore_sqlite(zf, member, db_path, needed_table):
    """Copies a database of the backup INTO the live one."""
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=_backup_dir())
    os.close(fd)
    try:
        with zf.open(member) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        src_db = sqlite3.connect(tmp)
        try:
            tables = {r[0] for r in src_db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if needed_table not in tables:
                raise ValueError("not_a_backup")
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            live = sqlite3.connect(db_path)
            try:
                src_db.backup(live)
            finally:
                live.close()
        finally:
            src_db.close()
    except sqlite3.DatabaseError:
        raise ValueError("not_a_backup")
    finally:
        os.remove(tmp)


@app.route("/api/backup/restore", methods=["POST"])
def api_backup_restore():
    """Step 2: applies what the kept file holds."""
    token = str((request.get_json(silent=True) or {}).get("token") or "")
    with _backup_lock:
        entry = _backup_pending.pop(token, None)
    if not entry:
        return jsonify({"ok": False, "error": "backup_expired"}), 400
    c = cfg()
    done = {}
    try:
        manifest, zf = _read_backup(entry["path"])
        with zf:
            names = set(zf.namelist())
            if "config.json" in names:
                bundle = json.loads(zf.read("config.json").decode("utf-8"))
                done["config"] = config_bundle.import_bundle(bundle, c)
                notify_daemon("reload_announcements")
                _reload_after_import(done["config"])
                notify_daemon("reload_config")
                c = cfg()
            if "suggestions.db" in names:
                _restore_sqlite(zf, "suggestions.db", c["SUGGESTIONS_DB_FILE"], "devices")
                _bans_cache["at"] = NEVER
                done["suggestions"] = True
            if "stats.db" in names and c.get("STATS_ENABLED"):
                _restore_sqlite(zf, "stats.db", c["STATS_DB_FILE"], "events")
                done["stats"] = True
            restored = skipped = 0
            sources = _sound_sources()
            for source_id, files in (manifest.get("sounds") or {}).items():
                folder = sources.get(source_id) or _resolve_track_order_folder(source_id)
                if not folder or not _inside_roots(os.path.realpath(folder)):
                    skipped += len(files)
                    continue
                os.makedirs(folder, exist_ok=True)
                for n in files:
                    n = os.path.basename(str(n))
                    member = "sounds/%s/%s" % (source_id.replace(":", "_"), n)
                    if n.startswith(".") or os.path.splitext(n)[1].lower() not in announcements.AUDIO_EXTENSIONS \
                            or member not in names:
                        skipped += 1
                        continue
                    dest = os.path.join(folder, n)
                    with zf.open(member) as src, open(dest + ".rukebox-part", "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(dest + ".rukebox-part", dest)
                    restored += 1
            if manifest.get("sounds"):
                done["sounds"] = {"restored": restored, "skipped": skipped}
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except (OSError, zipfile.BadZipFile) as e:
        log.warning("Restore failed: %s", e)
        return jsonify({"ok": False, "error": "restore_failed"}), 500
    finally:
        try:
            os.remove(entry["path"])
        except OSError:
            pass
    stats.record("backup_restored", label=",".join(sorted(done)))
    if done.get("stats"):
        system_actions.restart_daemon()
    return jsonify({"ok": True, "data": {
        "parts": sorted(done),
        "settings_changed": (done.get("config") or {}).get("settings_changed", 0),
        "sounds": (done.get("sounds") or {}).get("restored", 0),
        "daemon_restarted": bool(done.get("stats")),
    }})


@app.route("/api/journal/export")
def api_stats_export():
    """Download everything as JSON."""
    payload = json.dumps(stats.export(), ensure_ascii=False, indent=2)
    filename = "rukebox-stats-%s.json" % time.strftime("%Y%m%d-%H%M%S")
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@app.route("/api/journal/reset_counters", methods=["POST"])
def api_stats_reset_counters():
    """A long press on a KPI tile."""
    body = request.get_json(silent=True) or {}
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
        return jsonify({"ok": False, "error": "bad_request"}), 400
    done = stats.reset_counters(keys)
    if done is None:
        return jsonify({"ok": False, "error": "reset_failed"}), 500
    if not done:
        return jsonify({"ok": False, "error": "unknown_counter"}), 400
    return jsonify({"ok": True, "data": {"reset": done}})


@app.route("/api/journal/delete", methods=["POST"])
def api_stats_delete():
    """Removes selected rows from one of the statistics lists."""
    body = request.get_json(silent=True) or {}
    if body.get("all"):
        scope = body.get("scope")
        if scope not in ("sessions", "played", "errors", "events"):
            return jsonify({"ok": False, "error": "invalid_target"}), 400
        removed = stats.delete_all(scope, body.get("type") or None)
        return jsonify({"ok": True, "data": {"removed": removed}})
    target = body.get("target")
    keys = body.get("keys")
    if target not in ("events", "sessions", "items"):
        return jsonify({"ok": False, "error": "invalid_target"}), 400
    if not isinstance(keys, list) or not keys:
        return jsonify({"ok": False, "error": "no_keys"}), 400
    if len(keys) > 500:
        return jsonify({"ok": False, "error": "too_many"}), 400
    removed = stats.delete_rows(target, keys)
    return jsonify({"ok": True, "data": {"removed": removed}})


@app.route("/api/journal/reset", methods=["POST"])
def api_stats_reset():
    """Reset requested from the access point."""
    body = request.get_json(silent=True) or {}
    scope = body.get("scope", "all")
    if scope not in ("all", "events", "counters"):
        return jsonify({"ok": False, "error": "invalid_scope"}), 400

    try:
        result = control("reset_stats", scope=scope)
    except Exception:  # noqa: BLE001
        log.exception("Could not reach the daemon for the statistics reset")
        result = {"ok": False}
    if result.get("ok"):
        return jsonify({"ok": True, "via": "daemon"})

    if stats.reset(scope):
        return jsonify({"ok": True, "via": "direct"})
    return jsonify({"ok": False, "error": "reset_failed"}), 500


UPDATE_WRAPPER = "/usr/local/sbin/rukebox-update"
UPDATE_LOG = "/var/lib/rukebox/update.log"

OS_UPDATES_CACHE_SECONDS = 300
_os_updates_cache = {"at": 0.0, "count": None, "internet": None}


def _has_internet():
    """True if something answered on the network just now."""
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=3):
            return True
    except OSError:
        return False


def _os_update_state():
    """(pending_count_or_None, has_internet), cached for a few minutes."""
    if time.time() - _os_updates_cache["at"] < OS_UPDATES_CACHE_SECONDS:
        return _os_updates_cache["count"], _os_updates_cache["internet"]

    count = None
    try:
        result = subprocess.run(["apt", "list", "--upgradable"],
                                capture_output=True, text=True, timeout=20)
        lines = [line for line in result.stdout.splitlines()
                 if "/" in line and not line.startswith("Listing")]
        count = len(lines)
    except (OSError, subprocess.SubprocessError):
        count = None
    internet = _has_internet()
    _os_updates_cache.update({"at": time.time(), "count": count, "internet": internet})
    return count, internet


def _updater_alive():
    """True while a rukebox-update process is running."""
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % name, "rb") as f:
                if b"rukebox-update" in f.read():
                    return True
        except OSError:
            continue
    return False


@app.route("/api/update/status")
def api_update_status():
    c = cfg()
    version = read_version_file(c["UPDATE_VERSION_FILE"])

    log_tail = ""
    running = False
    interrupted = False
    try:
        if os.path.exists(UPDATE_LOG):
            with open(UPDATE_LOG, "r", encoding="utf-8", errors="replace") as f:
                log_tail = "".join(f.readlines()[-40:])
            # A killed update leaves no end marker, so the process is the other half of the answer.
            done = "__RUKEBOX_UPDATE_DONE__" in log_tail
            running = not done and _updater_alive()
            interrupted = not done and not running and bool(log_tail.strip())
    except OSError:
        pass

    os_pending, internet = _os_update_state()

    return jsonify({"ok": True, "data": {
        "version": version,
        "platform": capabilities()["platform"],
        "self_update": system_actions.can_self_update(),
        "docker_image": c.get("UPDATE_DOCKER_IMAGE") or "",
        "interrupted": interrupted,
        "github_repo": c.get("UPDATE_GITHUB_REPO") or "",
        "web_updates_allowed": bool(c["UPDATE_ALLOW_WEB"]),
        "wrapper_installed": os.path.exists(UPDATE_WRAPPER),
        "log_tail": log_tail.replace("__RUKEBOX_UPDATE_DONE__", "").strip(),
        "running": running,
        "os_pending": os_pending,
        "has_internet": internet,
    }})


RELEASE_CACHE_SEC = 600
_release_cache = {"at": 0.0, "repo": None, "result": None}
_GITHUB_REPO_RE = re.compile(r"^(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+$)[A-Za-z0-9_.-]+$")
# A release declares its tree hash as a "tree-hash: <hash>" line or a shields.io badge.
_TREE_HASH_RE = re.compile(r"(?:tree-hash:\s*|badge/[^\s)\"']*?)([0-9a-f]{8,64})", re.IGNORECASE)


def _declared_tree_hash(notes):
    """The tree hash a release declares in its notes, if it declares one."""
    match = _TREE_HASH_RE.search(notes or "")
    return match.group(1).lower() if match else None


def _latest_release(repo, force=False):
    """(data, error): the latest published release of `repo` on GitHub, or an
    error code."""
    now = time.time()
    if (not force and _release_cache["repo"] == repo and _release_cache["result"]
            and now - _release_cache["at"] < RELEASE_CACHE_SEC):
        return _release_cache["result"]
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % repo,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Rukebox"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        result = ({
            "tag": info.get("tag_name"),
            "name": info.get("name") or info.get("tag_name"),
            "published_at": info.get("published_at"),
            "notes": (info.get("body") or "")[:3000],
            "url": info.get("html_url"),
        }, None)
    except urllib.error.HTTPError as e:
        result = (None, "release_none" if e.code == 404 else "github_error")
    except (urllib.error.URLError, OSError, ValueError):
        result = (None, "no_internet")
    if result[1] != "no_internet":
        _release_cache.update({"at": now, "repo": repo, "result": result})
    return result


def _check_gpio_pins(updates):
    """Error code for a pin a button may not be wired to, or for one pin given
    to both the button and the password reset, or None. The picker greys those
    out, but the field beside it takes any number - and the reset configures
    its pin as an input at every boot, which on an I2C pin loses the clock."""
    keys = ("GPIO_BUTTON_PIN", "GPIO_RESET_PIN")
    if not any(key in updates for key in keys):
        return None
    if any(key in updates and not gpio_pins.is_selectable(updates[key]) for key in keys):
        return "gpio_pin_reserved"
    current = cfg()
    try:
        button, reset = (int(updates.get(key, current.get(key))) for key in keys)
    except (TypeError, ValueError):
        return None
    return "gpio_pin_taken" if button == reset else None


def _check_extra_hosts(updates):
    """Error code for a WEB_EXTRA_HOSTS that holds something other than host names, or None."""
    value = updates.get("WEB_EXTRA_HOSTS")
    if value is None:
        return None
    names = str(value).lower().replace(",", " ").split()
    if any(not _HOST_NAME_RE.match(name.split(":", 1)[0]) for name in names):
        return "bad_host_name"
    updates["WEB_EXTRA_HOSTS"] = ", ".join(dict.fromkeys(names))
    return None


@app.route("/api/security/hosts", methods=["DELETE"])
def api_security_hosts_forget():
    """{host}: a refused name taken off the list, without allowing it."""
    name = str((request.get_json(silent=True) or {}).get("host") or "").strip().lower()
    if _refused_hosts.pop(name, None) is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True})


@app.route("/api/security/hosts")
def api_security_hosts():
    """The names the interface may be opened under, and the ones it refused lately."""
    c = cfg()
    allowed = str(c.get("WEB_EXTRA_HOSTS") or "").lower().replace(",", " ").split()
    now = time.time()
    refused = [{"host": h, "ago": round(now - at)} for h, at in sorted(_refused_hosts.items(), key=lambda i: -i[1])
               if h not in allowed and now - at <= REFUSED_HOSTS_KEEP_SEC]
    return jsonify({"ok": True, "data": {"allowed": allowed, "refused": refused,
                                         "behind_proxy": bool(c.get("WEB_BEHIND_PROXY")),
                                         "this_host": (request.host or "").split(":", 1)[0].lower()}})


def _check_github_repo(updates):
    """Error code for a malformed UPDATE_GITHUB_REPO in a settings save, or
    None."""
    repo = updates.get("UPDATE_GITHUB_REPO")
    if repo is None:
        return None
    repo = str(repo).strip()
    return None if repo == "" or _GITHUB_REPO_RE.match(repo) else "bad_github_repo"


@app.route("/api/update/release", methods=["GET", "POST"])
def api_update_release():
    """The latest GitHub release, and whether it is newer than what is
    installed."""
    c = cfg()
    repo = (c.get("UPDATE_GITHUB_REPO") or "").strip()
    if not repo:
        return jsonify({"ok": False, "error": "no_github_repo"}), 400
    if not _GITHUB_REPO_RE.match(repo):
        return jsonify({"ok": False, "error": "bad_github_repo"}), 400
    if request.method == "GET":
        data, error = _latest_release(repo, force=request.args.get("force") == "1")
        if error:
            return jsonify({"ok": False, "error": error, "detail": repo}), 200
        version = read_version_file(c["UPDATE_VERSION_FILE"])
        installed = version.get("release")
        declared = _declared_tree_hash(data.get("notes"))
        tree = (version.get("tree_hash") or "").lower()
        if data["tag"] and data["tag"] != installed and declared and tree.startswith(declared):
            installed = data["tag"]
            try:
                set_release(c["UPDATE_VERSION_FILE"], installed)
            except OSError:
                pass
        return jsonify({"ok": True, "data": dict(data, repo=repo, installed=installed,
                                                 newer=is_newer(data["tag"], installed),
                                                 allowed=bool(c["UPDATE_ALLOW_WEB"]))})
    if not c["UPDATE_ALLOW_WEB"]:
        return jsonify({"ok": False, "error": "web_updates_disabled"}), 403
    if not os.path.exists(UPDATE_WRAPPER):
        return jsonify({"ok": False, "error": "updater_missing", "detail": UPDATE_WRAPPER}), 500
    stats.attach_current_session()
    stats.record("update_started", label="release", detail={"repo": repo})
    return _start_update("--from-release")


def _start_update(argument):
    """Runs the root-owned updater with exactly `argument`."""
    try:
        log_file = open(UPDATE_LOG, "w", encoding="utf-8")
    except OSError as e:
        return jsonify({"ok": False, "error": "cannot_write_log",
                        "detail": "%s: %s" % (UPDATE_LOG, e)}), 500
    command = (
        "sudo -n %s %s >> %s 2>&1; "
        "echo __RUKEBOX_UPDATE_DONE__ >> %s"
        % (UPDATE_WRAPPER, argument, UPDATE_LOG, UPDATE_LOG)
    )
    try:
        subprocess.Popen(["/bin/sh", "-c", command], stdout=log_file,
                         stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        log_file.close()
        return jsonify({"ok": False, "error": "update_start_failed"}), 500
    log_file.close()
    return jsonify({"ok": True, "started": True})


GPIO_DETECT_TIMEOUT_SEC = 20
GPIO_BUTTON_SERVICE = "rukebox-gpio-button.service"

_gpio_detect_lock = threading.Lock()


def _pinctrl(args, timeout=5):
    """Runs pinctrl, returning stdout or None."""
    try:
        result = subprocess.run(
            ["pinctrl"] + [str(a) for a in args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("pinctrl %s failed: %s", args, e)
        return None
    if result.returncode != 0:
        log.warning("pinctrl %s failed: %s", args, result.stderr.strip())
        return None
    return result.stdout


def _read_levels(pins):
    """{bcm: "hi"|"lo"} for the given pins, from one pinctrl call."""
    out = _pinctrl(["get", "%d-%d" % (min(pins), max(pins))], timeout=10)
    if out is None:
        return {}
    levels = {}
    for line in out.splitlines():
        head, _, rest = line.partition(":")
        if "|" not in rest:
            continue
        try:
            bcm = int(head.strip())
        except ValueError:
            continue
        if bcm not in pins:
            continue
        level = rest.split("|", 1)[1].strip().split()[0]
        if level in ("hi", "lo"):
            levels[bcm] = level
    return levels


@app.route("/api/gpio/pinout")
def api_gpio_pinout():
    """The header layout, plus which pins this installation has already spoken
    for, so the picker can grey them out rather than letting the button and
    the password reset be assigned the same pin."""
    c = cfg()
    return jsonify({"ok": True, "data": {
        "pins": gpio_pins.pinout(),
        "assigned": {
            "GPIO_BUTTON_PIN": c.get("GPIO_BUTTON_PIN"),
            "GPIO_RESET_PIN": c.get("GPIO_RESET_PIN"),
        },
        "supported": gpio_reset.is_raspberry_pi() and gpio_reset.pinctrl_available(),
    }})


@app.route("/api/gpio/detect", methods=["POST"])
def api_gpio_detect():
    """Waits for a button to be pressed and reports which pin it is on."""
    if not (gpio_reset.is_raspberry_pi() and gpio_reset.pinctrl_available()):
        return jsonify({"ok": False, "error": "not_supported"}), 400

    if not _gpio_detect_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "detect_in_progress"}), 409

    was_active = False
    try:
        pins = gpio_pins.selectable_bcm()
        was_active = _service_is_active(GPIO_BUTTON_SERVICE)
        if was_active:
            system_actions.service_action("stop", GPIO_BUTTON_SERVICE)

        if _pinctrl(["set", "%d-%d" % (min(pins), max(pins)), "ip", "pu"], timeout=10) is None:
            return jsonify({"ok": False, "error": "pinctrl_failed"}), 500
        time.sleep(0.2)

        baseline = _read_levels(pins)
        watching = [p for p in pins if baseline.get(p) == "hi"]
        if not watching:
            return jsonify({"ok": False, "error": "no_pins_available"}), 400

        deadline = time.time() + GPIO_DETECT_TIMEOUT_SEC
        while time.time() < deadline:
            levels = _read_levels(watching)
            pressed = [p for p in watching if levels.get(p) == "lo"]
            if pressed:
                found = min(pressed)
                log.info("GPIO detection: pin %s pressed", found)
                return jsonify({"ok": True, "data": {
                    "bcm": found,
                    "physical": gpio_pins.physical_for_bcm(found),
                }})
            time.sleep(0.1)

        return jsonify({"ok": False, "error": "timeout"}), 408
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("GPIO detection failed: %s", e)
        return jsonify({"ok": False, "error": "detect_failed"}), 500
    finally:
        if was_active and not system_actions.service_action("start", GPIO_BUTTON_SERVICE):
            log.warning("Could not restart %s after detection", GPIO_BUTTON_SERVICE)
        _gpio_detect_lock.release()


_going_down = None
_going_down_event = threading.Event()


def _system_going_down():
    """"poweroff" / "reboot" when systemd has that job queued."""
    return system_actions.going_down()


def _on_sigterm(signum, frame):
    global _going_down
    kind = _system_going_down()
    if kind:
        log.info("The Pi is going down (%s): telling the open pages", kind)
        _going_down = kind
        _going_down_event.set()
        time.sleep(1.5)
    os._exit(0)


def main():
    c = load_config()
    signal.signal(signal.SIGTERM, _on_sigterm)
    if c.get("CAPTIVE_PORTAL_ENABLED") and int(c["WEB_PORT"]) != captive_portal.PORTAL_PORT:
        captive_portal.start(c.get("AP_INTERFACE", "uap0"), c["WEB_PORT"])
    _upnp_follow_stream()
    threading.Thread(target=_upnp_watch, name="upnp-watch", daemon=True).start()
    try:
        _get_library()
    except Exception:  # noqa: BLE001
        log.exception("Library catalogue: could not open")
    app.run(host="0.0.0.0", port=c["WEB_PORT"], threaded=True)


if __name__ == "__main__":
    main()

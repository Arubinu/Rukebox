"""Folders filled from another machine: the connections and the syncs that use them.

A connection says where and how to reach a machine; a sync says which of its
folders fills which folder of the radio, when, and on what conditions. Secrets
are never here: they are sealed apart (secret_box), by connection id.
"""

import ipaddress
import os
import re

import config_document
import paths
import secret_box

BLOCK = "syncs"
SECRETS_FILE = "sync-secrets.json"
NAME_MAX = 40
PATH_MAX = 512
CONNECTIONS_MAX = 20
JOBS_MAX = 50

TYPES = ("sftp", "smb", "webdav", "ftp")
AUTHS = ("password", "key", "none")
TLS = ("verify", "self_signed", "none")
VENDORS = ("nextcloud", "owncloud", "other")
INTERNET = ("any", "needed", "forbidden")
WIFI = ("any", "home", "ssid")
PLAYBACK = ("idle", "any")
# copy adds and updates; mirror also removes what the other machine no longer has.
MODES = ("copy", "mirror")
DEFAULT_PORTS = {"sftp": 22, "smb": 445, "webdav": 443, "ftp": 21}

AUDIO = ("mp3", "flac", "ogg", "oga", "opus", "m4a", "aac", "wav")
# What each kind of folder needs: never everything the other machine holds.
KINDS = {
    "music": AUDIO + ("jpg", "jpeg", "png", "webp", "lrc", "srt", "vtt", "txt"),
    "announcement": AUDIO,
    "memes": AUDIO,
    "dj_announcements": AUDIO,
    "system_sounds": AUDIO,
}

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[A-Za-z0-9._@\\-]{1,64}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SSID_MAX = 32


def _slugify(name, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or fallback


def _name(data, code):
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise ValueError(code + "_name_required")
    if len(name) > NAME_MAX:
        raise ValueError(code + "_name_too_long")
    return name


def _choice(value, allowed, default):
    value = str(value or default).strip().lower()
    if value not in allowed:
        raise ValueError("sync_bad_choice")
    return value


def clean_host(value):
    """A host name or an address, never something a program could read as an option."""
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if not _HOST_RE.match(text) or ".." in text:
        raise ValueError("sync_bad_host")
    return text.lower()


def clean_remote_path(value):
    """A folder of the other machine: relative segments only, never `..`, never an option."""
    text = str(value or "").strip().replace("\\", "/")
    if len(text) > PATH_MAX or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("sync_bad_path")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts) or (parts and parts[0].startswith("-")):
        raise ValueError("sync_bad_path")
    return "/".join(parts)


def clean_extensions(value, kind):
    """The file types to fetch, lower case and without the dot; the kind's own list by default."""
    if value in (None, "", []):
        return list(KINDS[kind])
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError("sync_bad_extensions")
    out = []
    for item in items:
        ext = str(item).strip().lower().lstrip(".")
        if not ext:
            continue
        if not _EXT_RE.match(ext):
            raise ValueError("sync_bad_extensions")
        if ext not in out:
            out.append(ext)
    if not out or len(out) > 40:
        raise ValueError("sync_bad_extensions")
    return out


def validate_connection(data):
    """Raises ValueError carrying an error CODE, like the other stores."""
    kind = _choice(data.get("type"), TYPES, "sftp")
    clean = {"name": _name(data, "connection"), "type": kind, "host": clean_host(data.get("host"))}
    port = data.get("port") or DEFAULT_PORTS[kind]
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("sync_bad_port") from None
    if not 1 <= port <= 65535:
        raise ValueError("sync_bad_port")
    clean["port"] = port
    user = str(data.get("user") or "").strip()
    if user and (not _USER_RE.match(user) or user.startswith("-")):
        raise ValueError("sync_bad_user")
    clean["user"] = user
    clean["path"] = clean_remote_path(data.get("path"))
    auth = _choice(data.get("auth"), AUTHS, "password")
    if auth == "key" and kind != "sftp":
        raise ValueError("sync_key_sftp_only")
    clean["auth"] = auth
    tls = _choice(data.get("tls"), TLS, "verify")
    if kind in ("sftp", "smb"):
        tls = "verify"
    clean["tls"] = tls
    if kind == "webdav":
        clean["vendor"] = _choice(data.get("vendor"), VENDORS, "other")
    # The server's own key, pinned once the owner accepted it: never typed in.
    host_key = str(data.get("host_key") or "").strip()
    # One line per key type the server offers, nothing else.
    if host_key and (len(host_key) > 8192 or len(host_key.splitlines()) > 8
                     or any(ord(c) < 32 and c != "\n" for c in host_key)):
        raise ValueError("sync_bad_host_key")
    if kind == "sftp":
        clean["host_key"] = host_key
    return clean


def validate_job(data, connections):
    clean = {"name": _name(data, "sync")}
    connection = str(data.get("connection") or "").strip()
    if connection not in {item["id"] for item in connections}:
        raise ValueError("sync_connection_unknown")
    clean["connection"] = connection
    clean["remote"] = clean_remote_path(data.get("remote"))
    kind = _choice(data.get("kind"), tuple(KINDS), "music")
    clean["kind"] = kind
    target = str(data.get("target") or "").strip()
    if not target or len(target) > PATH_MAX or any(ord(c) < 32 for c in target) or not os.path.isabs(target):
        raise ValueError("sync_bad_target")
    if any(part == ".." for part in target.replace("\\", "/").split("/")):
        raise ValueError("sync_bad_target")
    clean["target"] = target.rstrip("/\\") or target
    clean["extensions"] = clean_extensions(data.get("extensions"), kind)
    try:
        every = int(data.get("every_hours") if data.get("every_hours") not in (None, "") else 24)
        max_size = int(data.get("max_size_mb") if data.get("max_size_mb") not in (None, "") else 500)
    except (TypeError, ValueError):
        raise ValueError("sync_bad_number") from None
    if not 0 <= every <= 168 or not 1 <= max_size <= 20000:
        raise ValueError("sync_bad_number")
    clean["every_hours"] = every
    clean["max_size_mb"] = max_size
    for key in ("window_start", "window_end"):
        value = str(data.get(key) or "").strip()
        if value and not _TIME_RE.match(value):
            raise ValueError("sync_bad_time")
        clean[key] = value
    clean["internet"] = _choice(data.get("internet"), INTERNET, "any")
    clean["wifi"] = _choice(data.get("wifi"), WIFI, "any")
    ssid = str(data.get("ssid") or "").strip()
    if len(ssid.encode("utf-8")) > _SSID_MAX or any(ord(c) < 32 for c in ssid):
        raise ValueError("sync_bad_ssid")
    if clean["wifi"] == "ssid" and not ssid:
        raise ValueError("sync_bad_ssid")
    clean["ssid"] = ssid
    clean["playback"] = _choice(data.get("playback"), PLAYBACK, "idle")
    clean["mode"] = _choice(data.get("mode"), MODES, "copy")
    for key, default in (("enabled", True), ("at_boot", True), ("recent_list", True)):
        value = data.get(key, default)
        clean[key] = value in (True, "true", "1", 1, "on")
    return clean


def load(path=None):
    """{"connections": [...], "jobs": [...]}, each with its id."""
    data = config_document.read(BLOCK, path) or {}
    out = {}
    for key in ("connections", "jobs"):
        items = data.get(key)
        out[key] = [item for item in items if isinstance(item, dict) and item.get("id")] \
            if isinstance(items, list) else []
    return out


def _strict(path):
    """A document that cannot be read is never replaced by an empty one."""
    if config_document.read(BLOCK, path) is None:
        raise ValueError("file_unreadable")
    return load(path)


def _write(path, data):
    config_document.write(BLOCK, data, path)


def _new_id(name, taken, fallback):
    new_id = base = _slugify(name, fallback)
    suffix = 2
    while new_id in taken:
        new_id = "%s-%d" % (base, suffix)
        suffix += 1
    return new_id


def connection(connection_id, path=None):
    found = next((item for item in load(path)["connections"] if item["id"] == connection_id), None)
    if found is None:
        raise KeyError(connection_id)
    return found


def job(job_id, path=None):
    found = next((item for item in load(path)["jobs"] if item["id"] == job_id), None)
    if found is None:
        raise KeyError(job_id)
    return found


def save_connection(data, connection_id=None, path=None):
    """Creates a connection, or changes the one named; the pinned key goes when the host does."""
    with config_document.lock(path):
        doc = _strict(path)
        items = doc["connections"]
        if connection_id is None:
            if len(items) >= CONNECTIONS_MAX:
                raise ValueError("sync_too_many")
            clean = validate_connection(data)
            clean["id"] = _new_id(clean["name"], {item["id"] for item in items}, "connection")
            items.append(clean)
        else:
            index = next((i for i, item in enumerate(items) if item["id"] == connection_id), None)
            if index is None:
                raise KeyError(connection_id)
            before = items[index]
            merged = dict(before, **{k: v for k, v in data.items() if k != "host_key"})
            if (merged.get("host"), merged.get("port"), merged.get("type")) != \
                    (before.get("host"), before.get("port"), before.get("type")):
                merged["host_key"] = ""
            clean = validate_connection(merged)
            clean["id"] = connection_id
            items[index] = clean
        _write(path, doc)
    return clean


def pin_host_key(connection_id, host_key, path=None):
    """Keeps the server key the owner accepted; a later different key stops the syncs."""
    with config_document.lock(path):
        doc = _strict(path)
        for item in doc["connections"]:
            if item["id"] == connection_id:
                item["host_key"] = host_key
                clean = validate_connection(item)
                clean["id"] = connection_id
                item.clear()
                item.update(clean)
                _write(path, doc)
                return clean
    raise KeyError(connection_id)


def delete_connection(connection_id, path=None):
    """Removes a connection no sync uses any more."""
    with config_document.lock(path):
        doc = _strict(path)
        if any(item["connection"] == connection_id for item in doc["jobs"]):
            raise ValueError("connection_in_use")
        remaining = [item for item in doc["connections"] if item["id"] != connection_id]
        if len(remaining) == len(doc["connections"]):
            raise KeyError(connection_id)
        doc["connections"] = remaining
        _write(path, doc)


def _overlaps(one, other):
    one, other = os.path.normpath(one), os.path.normpath(other)
    return one == other or one.startswith(other.rstrip(os.sep) + os.sep) \
        or other.startswith(one.rstrip(os.sep) + os.sep)


def _check_mirror(clean, others):
    """A mirror removes files: never in a folder another sync fills, nor around one."""
    for other in others:
        if (clean["mode"] == "mirror" or other.get("mode") == "mirror") \
                and _overlaps(clean["target"], other["target"]):
            raise ValueError("sync_mirror_shared")


def save_job(data, job_id=None, path=None):
    with config_document.lock(path):
        doc = _strict(path)
        items = doc["jobs"]
        if job_id is None:
            if len(items) >= JOBS_MAX:
                raise ValueError("sync_too_many")
            clean = validate_job(data, doc["connections"])
            _check_mirror(clean, items)
            clean["id"] = _new_id(clean["name"], {item["id"] for item in items}, "sync")
            items.append(clean)
        else:
            index = next((i for i, item in enumerate(items) if item["id"] == job_id), None)
            if index is None:
                raise KeyError(job_id)
            clean = validate_job(dict(items[index], **data), doc["connections"])
            _check_mirror(clean, [item for item in items if item["id"] != job_id])
            clean["id"] = job_id
            items[index] = clean
        _write(path, doc)
    return clean


def delete_job(job_id, path=None):
    with config_document.lock(path):
        doc = _strict(path)
        remaining = [item for item in doc["jobs"] if item["id"] != job_id]
        if len(remaining) == len(doc["jobs"]):
            raise KeyError(job_id)
        doc["jobs"] = remaining
        _write(path, doc)


def save_all(data, path=None):
    """Replaces every connection and sync (a configuration import)."""
    with config_document.lock(path):
        _write(path, {"connections": list(data.get("connections") or []),
                      "jobs": list(data.get("jobs") or [])})


def export_block(state_dir=None):
    """The connections, the syncs and the sealed passwords, for the owner's own backup."""
    data = load()
    data["secrets"] = secrets(state_dir)._read()
    return data


def import_block(data, state_dir=None, target_ok=None):
    """Takes a backup's syncs back, each entry checked again; sealed passwords are kept as they
    are and show up as lost when this machine did not seal them. The count taken."""
    if not isinstance(data, dict):
        return 0
    connections, jobs = [], []
    for item in data.get("connections") or []:
        try:
            clean = validate_connection(item)
        except (ValueError, AttributeError):
            continue
        clean["id"] = _slugify(item.get("id"), "connection")
        if clean["id"] not in {c["id"] for c in connections}:
            connections.append(clean)
    for item in data.get("jobs") or []:
        try:
            clean = validate_job(item, connections)
        except (ValueError, AttributeError):
            continue
        if target_ok is not None and not target_ok(clean["target"]):
            continue
        try:
            _check_mirror(clean, jobs)
        except ValueError:
            clean["mode"] = "copy"
        clean["id"] = _slugify(item.get("id"), "sync")
        if clean["id"] not in {j["id"] for j in jobs}:
            jobs.append(clean)
    save_all({"connections": connections, "jobs": jobs})
    tokens = data.get("secrets")
    if isinstance(tokens, dict):
        store = secrets(state_dir)
        kept = store._read()
        kept.update({str(k): str(v) for k, v in tokens.items() if k in {c["id"] for c in connections}})
        store._save(kept)
    return len(connections) + len(jobs)


def secrets(state_dir=None):
    """The sealed passwords of the connections, kept beside the configuration."""
    return secret_box.Store(paths.config(SECRETS_FILE), state_dir)


def public_connection(item, store=None):
    """A connection as the page may see it: whether a secret is kept, never the secret."""
    store = store or secrets()
    out = dict(item)
    out["secret_set"] = store.has(item["id"])
    out["secret_lost"] = item["id"] in store.lost() if out["secret_set"] else False
    return out

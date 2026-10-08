"""The tracks the radio must not choose by itself."""

import os
import threading
import time

import json_file

MAX = 5000

# Where an entry came from: the duplicate check, a whole filter from the
# excluded page, or one track picked by hand there.
ORIGINS = ("duplicate", "filter", "manual")

# keys() and paths() sit on every library search and on the daemon's track
# change: the file is read again only when it moved.
_cache = {}
_cache_lock = threading.Lock()


def _stamp(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _once(path, kind, read):
    """`read(path)`, or the answer it gave for this very file: keys and paths
    are two different answers about the same file, so both are named."""
    stamp = _stamp(path)
    key = (path, kind)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] == stamp:
            return entry[1]
    value = read(path)
    with _cache_lock:
        _cache[key] = (stamp, value)
    return value


def load(path):
    """Every excluded track, most recently excluded first."""
    data = json_file.read(path)
    items = (data or {}).get("tracks")
    if not isinstance(items, list):
        return []
    kept = [item for item in items if isinstance(item, dict) and item.get("key")]
    kept.sort(key=lambda item: item.get("hidden_at") or 0, reverse=True)
    return kept


def keys(path):
    """The excluded library keys, for the mark on a track."""
    return _once(path, "keys", lambda p: {item["key"] for item in load(p)})


def paths(path):
    """The excluded file paths: what the daemon compares without a stat()."""
    return _once(path, "paths", lambda p: {item.get("path") for item in load(p) if item.get("path")})


def _entry(key, track_path, title, artist, origin, hidden_at=None):
    return {
        "key": key,
        "path": str(track_path or "").strip(),
        "title": str(title or "").strip(),
        "artist": str(artist or "").strip(),
        "origin": origin if origin in ORIGINS else "manual",
        "hidden_at": float(hidden_at or 0) or time.time(),
    }


def _trimmed(items):
    """The oldest ones fall off the end at MAX, the newest are what is kept."""
    items.sort(key=lambda item: item.get("hidden_at") or 0)
    return items[-MAX:]


def set_hidden(file_path, key, hidden, track_path="", title="", artist="", origin="manual"):
    """Excludes a track, or gives it back to the radio; returns the new state."""
    key = str(key or "").strip()
    if not key:
        raise ValueError("hidden_key_required")
    with json_file.lock(file_path):
        items = [item for item in load(file_path) if item.get("key") != key]
        if hidden:
            items.append(_entry(key, track_path, title, artist, origin))
            items = _trimmed(items)
        json_file.write(file_path, {"tracks": items})
    return bool(hidden)


def set_many(file_path, tracks, hidden=True, origin="manual"):
    """Excludes (or gives back) several tracks in one write: what excluding a
    whole filter needs, rather than one file rewrite per track."""
    entries = {}
    for track in tracks or []:
        key = str((track or {}).get("key") or "").strip()
        if key:
            entries[key] = track or {}
    if not entries:
        return 0
    with json_file.lock(file_path):
        items = [item for item in load(file_path) if item.get("key") not in entries]
        if hidden:
            items.extend(_entry(key, track.get("path"), track.get("title"),
                                track.get("artist"), origin)
                         for key, track in entries.items())
            items = _trimmed(items)
        json_file.write(file_path, {"tracks": items})
    return len(entries)


def clear(file_path):
    """Gives every excluded track back to the radio."""
    with json_file.lock(file_path):
        json_file.write(file_path, {"tracks": []})


def save_all(path, items):
    """Replaces the whole list (a configuration import)."""
    with json_file.lock(path):
        json_file.write(path, {"tracks": items[-MAX:]})

"""The tracks the radio must not choose by itself.

What a duplicate check keeps aside: the file stays where it is and can still be
played by hand, it is only out of what the radio picks on its own. Plain JSON
like likes.json, remembered by the library key so it survives a rescan, and
carrying the path as well so the daemon can filter without a stat() per track.
"""

import time

import json_file

MAX = 5000


def load(path):
    """Every hidden track, most recently hidden first."""
    data = json_file.read(path)
    items = (data or {}).get("tracks")
    if not isinstance(items, list):
        return []
    kept = [item for item in items if isinstance(item, dict) and item.get("key")]
    kept.sort(key=lambda item: item.get("hidden_at") or 0, reverse=True)
    return kept


def keys(path):
    """The hidden library keys, for the star on a track."""
    return {item["key"] for item in load(path)}


def paths(path):
    """The hidden file paths: what the daemon compares without a stat()."""
    return {item.get("path") for item in load(path) if item.get("path")}


def set_hidden(file_path, key, hidden, track_path="", title="", artist=""):
    """Hides a track, or gives it back to the radio; returns the new state."""
    key = str(key or "").strip()
    if not key:
        raise ValueError("hidden_key_required")
    with json_file.lock(file_path):
        items = load(file_path)
        keeping = [item for item in items if item.get("key") != key]
        if hidden:
            keeping.append({
                "key": key,
                "path": str(track_path or "").strip(),
                "title": str(title or "").strip(),
                "artist": str(artist or "").strip(),
                "hidden_at": time.time(),
            })
            keeping.sort(key=lambda item: item.get("hidden_at") or 0)
            keeping = keeping[-MAX:]
        json_file.write(file_path, {"tracks": keeping})
    return bool(hidden)


def clear(file_path):
    """Gives every hidden track back to the radio."""
    with json_file.lock(file_path):
        json_file.write(file_path, {"tracks": []})


def save_all(path, items):
    """Replaces the whole list (a configuration import)."""
    with json_file.lock(path):
        json_file.write(path, {"tracks": items[-MAX:]})

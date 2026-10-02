"""The liked tracks: a heart on the song playing, and the list it builds.

Plain JSON like music_lists.json - a growable list with its own dates, not a
scalar setting. The web server is the only writer; the file survives a rescan
because a track is remembered by its library key, not by its file name."""

import logging
import time

import json_file

log = logging.getLogger("likes")

MAX = 2000


def load(path):
    """Every liked track, most recently liked first."""
    data = json_file.read(path)
    items = (data or {}).get("tracks")
    if not isinstance(items, list):
        return []
    kept = [item for item in items if isinstance(item, dict) and item.get("key")]
    kept.sort(key=lambda item: item.get("liked_at") or 0, reverse=True)
    return kept


def keys(path):
    """The liked track keys, for the heart on the player."""
    return {item["key"] for item in load(path)}


def toggle(path, key, title="", artist=""):
    """Likes a track, or takes the like back; returns the new state."""
    key = str(key or "").strip()
    if not key:
        raise ValueError("like_key_required")
    with json_file.lock(path):
        items = load(path)
        keeping = [item for item in items if item.get("key") != key]
        liked = len(keeping) == len(items)
        if liked:
            keeping.append({
                "key": key,
                "title": str(title or "").strip(),
                "artist": str(artist or "").strip(),
                "liked_at": time.time(),
            })
            keeping.sort(key=lambda item: item.get("liked_at") or 0)
            keeping = keeping[-MAX:]
        json_file.write(path, {"tracks": keeping})
    return liked

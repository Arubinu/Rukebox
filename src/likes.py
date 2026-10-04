"""The liked tracks: a heart on the song playing, and the list it builds.

Plain JSON like music_lists.json - a growable list with its own dates, not a
scalar setting. The web server is the only writer; the file survives a rescan
because a track is remembered by its library key, not by its file name."""

import calendar
import datetime
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


def save_all(path, items):
    """Replaces the whole list (a configuration import)."""
    with json_file.lock(path):
        json_file.write(path, {"tracks": items[-MAX:]})


MEMORY_SLACK_DAYS = 2


def _shifted(day, years=0, months=0):
    """`day` moved back by whole years or months, the day clamped to the month."""
    month = day.month - months
    year = day.year - years + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def memories(items, today, limit=3):
    """The songs liked on this day in an earlier year, else a month ago (a
    couple of days either way): [{key, title, artist, years | months}]."""
    found = []
    for item in items:
        at = item.get("liked_at")
        if not isinstance(at, (int, float)):
            continue
        liked = datetime.date.fromtimestamp(at)
        years = today.year - liked.year
        if years >= 1 and abs((_shifted(today, years=years) - liked).days) <= MEMORY_SLACK_DAYS:
            found.append(dict(item, years=years))
        elif abs((_shifted(today, months=1) - liked).days) <= MEMORY_SLACK_DAYS:
            found.append(dict(item, months=1))
    found.sort(key=lambda m: (-m.get("years", 0), -(m.get("liked_at") or 0)))
    return [{k: m.get(k) for k in ("key", "title", "artist", "years", "months") if m.get(k) is not None}
            for m in found[:limit]]

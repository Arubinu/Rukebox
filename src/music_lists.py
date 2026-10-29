"""The music lists: what the radio plays instead of the whole library."""

import logging
import os
import re
import time
from datetime import date, timedelta

import config_document

log = logging.getLogger("music_lists")

BLOCK = "music_lists"

KINDS = ("manual", "genre")
NAME_MAX = 40
GENRES_MAX = 12
SEASONS_MAX = 12
# Any leap year: February 29th is a day a season may name.
_LEAP_YEAR = 2024


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "list"


def load(path=None):
    """Every list, oldest first."""
    data = config_document.read(BLOCK, path)
    if not data:
        return []
    items = data.get("lists")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _write(path, items):
    config_document.write(BLOCK, {"lists": items}, path)


def save_all(items, path=None):
    """Replaces every list (a configuration import)."""
    with config_document.lock(path):
        _write(path, items)


def _genres(raw):
    if isinstance(raw, str):
        raw = re.split(r"[;,]", raw)
    if not isinstance(raw, (list, tuple)):
        return []
    seen, out = set(), []
    for genre in raw:
        genre = str(genre).strip()
        if genre and genre.casefold() not in seen:
            seen.add(genre.casefold())
            out.append(genre)
    return out[:GENRES_MAX]


def _month_day(raw):
    """"MM-DD" as a valid (month, day), or ValueError."""
    match = re.match(r"^(\d{2})-(\d{2})$", str(raw or "").strip())
    if not match:
        raise ValueError("list_bad_season")
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(_LEAP_YEAR, month, day)
    except ValueError:
        raise ValueError("list_bad_season")
    return "%02d-%02d" % (month, day)


def _seasons(raw):
    """The periods of the year a list plays in: [{"from": "MM-DD", "to": "MM-DD"}], [] for all year."""
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > SEASONS_MAX:
        raise ValueError("list_bad_season")
    out = []
    for period in raw:
        if not isinstance(period, dict):
            raise ValueError("list_bad_season")
        start = _month_day(period.get("from"))
        end = _month_day(period.get("to") or start)
        if {"from": start, "to": end} not in out:
            out.append({"from": start, "to": end})
    return out


def _in_period(period, day):
    key = "%02d-%02d" % (day.month, day.day)
    start, end = period["from"], period["to"]
    # A period that crosses the new year runs from its start to December, then from January.
    return start <= key <= end if start <= end else key >= start or key <= end


def seasonal(entry):
    """Whether the list keeps to its seasons: switched on, with at least one period."""
    return bool((entry or {}).get("seasonal") and (entry or {}).get("seasons"))


def in_season(entry, day):
    """Whether `day` falls in one of the list's seasons; a list that is not seasonal plays all year."""
    return not seasonal(entry) or any(_in_period(period, day) for period in entry["seasons"])


def season_state(entry, day):
    """None for a list of all year; else {"on": bool, "change": "MM-DD"}: the last day of the
    season under way, or the first day of the next one."""
    if not seasonal(entry):
        return None
    on = in_season(entry, day)
    for ahead in range(1, 367):
        later = day + timedelta(days=ahead)
        if in_season(entry, later) != on:
            last = later - timedelta(days=1) if on else later
            return {"on": on, "change": "%02d-%02d" % (last.month, last.day)}
    return {"on": on, "change": None}


def validate(data):
    """Raises ValueError carrying an error CODE (list_name_required, ...),
    translated by the web interface like every other API error."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("list_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("list_name_too_long")

    kind = str(data.get("kind", "manual")).strip() or "manual"
    if kind not in KINDS:
        raise ValueError("list_kind_unknown")

    genres = _genres(data.get("genres"))
    if kind == "genre" and not genres:
        raise ValueError("list_genres_required")

    tracks = data.get("tracks") or []
    if not isinstance(tracks, (list, tuple)):
        tracks = []

    return {
        "name": name,
        "kind": kind,
        "genres": genres if kind == "genre" else [],
        "tracks": [str(t) for t in tracks if str(t).strip()] if kind == "manual" else [],
        # The dates are kept while the switch is off, ready for the next time.
        "seasonal": bool(data.get("seasonal")),
        "seasons": _seasons(data.get("seasons")),
    }


def add(data, path=None, owner=None):
    """Creates a list; its id is a slug of the name, so it stays readable in
    state.json. `owner` is the member account it belongs to, None for the radio's."""
    clean = validate(data)
    if owner:
        clean["owner"] = str(owner)
    with config_document.lock(path):
        items = load(path)
        taken = {item["id"] for item in items}
        new_id = base_id = _slugify(clean["name"])
        suffix = 2
        while new_id in taken:
            new_id = "%s-%d" % (base_id, suffix)
            suffix += 1
        clean["id"] = new_id
        clean["created_at"] = time.time()
        items.append(clean)
        _write(path, items)
    return clean


def update(list_id, data, path=None):
    """Partial update: fields left out of `data` keep their current value."""
    with config_document.lock(path):
        items = load(path)
        for i, item in enumerate(items):
            if item["id"] == list_id:
                merged = dict(item)
                merged.update(data)
                clean = validate(merged)
                clean["id"] = list_id
                clean["created_at"] = item.get("created_at", time.time())
                # Whose it is never changes with an edit.
                if item.get("owner"):
                    clean["owner"] = item["owner"]
                items[i] = clean
                _write(path, items)
                return clean
    raise KeyError(list_id)


def delete(list_id, path=None):
    with config_document.lock(path):
        items = load(path)
        remaining = [item for item in items if item["id"] != list_id]
        if len(remaining) == len(items):
            raise KeyError(list_id)
        _write(path, remaining)


def get(list_id, path=None):
    for item in load(path):
        if item["id"] == list_id:
            return item
    raise KeyError(list_id)


def _mutate(path, list_id, change):
    """Reads, changes one entry, writes the whole file back."""
    with config_document.lock(path):
        items = load(path)
        for i, item in enumerate(items):
            if item["id"] == list_id:
                items[i] = change(dict(item))
                _write(path, items)
                return items[i]
    raise KeyError(list_id)


def add_track(list_id, track, path=None):
    """Adds a track at the end of a manual list; already there is not an
    error, it just stays where it is."""
    track = str(track or "")
    if not track:
        raise ValueError("list_track_required")

    def change(entry):
        if entry.get("kind") != "manual":
            raise ValueError("list_not_manual")
        tracks = list(entry.get("tracks") or [])
        if track not in tracks:
            tracks.append(track)
        entry["tracks"] = tracks
        return entry

    return _mutate(path, list_id, change)


def remove_track(list_id, track, path=None):
    track = str(track or "")

    def change(entry):
        if entry.get("kind") != "manual":
            raise ValueError("list_not_manual")
        return {**entry, "tracks": [t for t in (entry.get("tracks") or []) if t != track]}

    return _mutate(path, list_id, change)


def resolved(entry, all_tracks, genre_paths):
    """The tracks of `entry` as they stand now: for a manual list the ones
    still in the library, in the list's own order; for a genre list whatever
    the library has for those genres, in library order."""
    if not entry:
        return []
    if entry.get("kind") == "genre":
        return list(genre_paths(entry.get("genres") or []))
    available = set(all_tracks or [])
    return [t for t in (entry.get("tracks") or []) if t in available]


def custom_order(entry):
    """The basenames of a manual list, in its own order - what
    playlist.build_ordered() needs to keep a hand-made list as it was made
    instead of sorting it by file name."""
    if not entry or entry.get("kind") != "manual":
        return None
    return [os.path.basename(t) for t in (entry.get("tracks") or [])]


def genre_list_name(genres):
    """The name a list made from a genre filter gets."""
    genres = [str(g).strip() for g in genres if str(g).strip()]
    return ", ".join(genres)[:NAME_MAX] or "Genre"

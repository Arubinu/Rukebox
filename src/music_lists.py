"""The music lists: what the radio plays instead of the whole library.

A list is either "manual" (tracks picked one by one from the web interface,
in the order they were added) or "genre" (kept up to date from the genres
read in the library's tags). Plain JSON, like announcements.json: a growable
list with its own form, not a scalar setting. The daemon reads it to build
its queue, the web server writes it."""

import logging
import os
import re
import time

import json_file

log = logging.getLogger("music_lists")

KINDS = ("manual", "genre")
NAME_MAX = 40
GENRES_MAX = 12


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "list"


def load(path):
    """Every list, oldest first."""
    data = json_file.read(path)
    if not data:
        return []
    items = data.get("lists")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _write(path, items):
    json_file.write(path, {"lists": items})


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
    }


def add(path, data):
    """Creates a list; its id is a slug of the name, so it stays readable in
    state.json."""
    clean = validate(data)
    with json_file.lock(path):
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


def update(path, list_id, data):
    """Partial update: fields left out of `data` keep their current value."""
    with json_file.lock(path):
        items = load(path)
        for i, item in enumerate(items):
            if item["id"] == list_id:
                merged = dict(item)
                merged.update(data)
                clean = validate(merged)
                clean["id"] = list_id
                clean["created_at"] = item.get("created_at", time.time())
                items[i] = clean
                _write(path, items)
                return clean
    raise KeyError(list_id)


def delete(path, list_id):
    with json_file.lock(path):
        items = load(path)
        remaining = [item for item in items if item["id"] != list_id]
        if len(remaining) == len(items):
            raise KeyError(list_id)
        _write(path, remaining)


def get(path, list_id):
    for item in load(path):
        if item["id"] == list_id:
            return item
    raise KeyError(list_id)


def _mutate(path, list_id, change):
    """Reads, changes one entry, writes the whole file back."""
    with json_file.lock(path):
        items = load(path)
        for i, item in enumerate(items):
            if item["id"] == list_id:
                items[i] = change(dict(item))
                _write(path, items)
                return items[i]
    raise KeyError(list_id)


def add_track(path, list_id, track):
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


def remove_track(path, list_id, track):
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

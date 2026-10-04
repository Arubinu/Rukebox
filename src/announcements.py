"""User-defined announcement types (JSON, edited from the web interface)."""

import logging
import os
import re
import time

import json_file

log = logging.getLogger("announcements")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus"}


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "announcement"


def _is_absolute_path(folder):
    # Not os.path.isabs(): its answer for "/x" differs between Python builds on Windows.
    return folder.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", folder))


def _read_doc(path, strict=False):
    """The whole file: {"items": [...], "volumes": {...}}.

    `strict` raises instead of answering "empty" for a file that exists but
    cannot be read: a writer must never replace a document it did not
    understand with an empty one."""
    data = json_file.read(path)
    if data is None:
        if strict:
            raise ValueError("file_unreadable")
        return {"items": [], "volumes": {}}
    items = data.get("items")
    data["items"] = (
        [item for item in items if isinstance(item, dict) and item.get("id")]
        if isinstance(items, list) else []
    )
    if not isinstance(data.get("volumes"), dict):
        data["volumes"] = {}
    return data


def load(path):
    """Every custom announcement, oldest first."""
    return _read_doc(path)["items"]


def read_items(path):
    """The announcements, or None when the file cannot be read at all."""
    try:
        return _read_doc(path, strict=True)["items"]
    except ValueError:
        return None


def _mutate(path, change):
    """Read-modify-write, one writer at a time (src/json_file.py)."""
    with json_file.lock(path):
        doc = _read_doc(path, strict=True)
        out = change(doc)
        if out is not None:
            json_file.write(path, out)
        return out


def save_all(path, items):
    """Replaces the whole list, validating nothing.

    Unlike the others this one may write over a file it cannot read: replacing
    everything is exactly what it is asked to do (a config restore brings its
    own announcements, and must be able to repair a broken file)."""
    with json_file.lock(path):
        doc = json_file.read(path)
        if not isinstance(doc, dict):
            doc = {}
        doc["items"] = items
        if not isinstance(doc.get("volumes"), dict):
            doc["volumes"] = {}
        json_file.write(path, doc)


VOLUME_MIN = 0
VOLUME_MAX = 100


def clean_volume(data):
    """One volume entry ({"on": bool, "volume": 0..100}) or a ValueError."""
    try:
        volume = int(round(float((data or {}).get("volume", VOLUME_MAX))))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("announcement_bad_volume")
    if not (VOLUME_MIN <= volume <= VOLUME_MAX):
        raise ValueError("announcement_bad_volume")
    return {"on": bool((data or {}).get("on", False)), "volume": volume}


def volumes(path):
    """The per-source volume: {"<source id>": {"on": bool, "volume": 0..100}}.

    A source id is "meme"/"cutoff", "custom:<id>", or the name of a System
    sound setting (KEEPALIVE_SOUND, ...)."""
    out = {}
    for key, entry in _read_doc(path)["volumes"].items():
        if not isinstance(entry, dict):
            continue
        try:
            out[key] = clean_volume(entry)
        except ValueError:
            continue
    return out


def set_volume(path, key, data):
    """Stores one source's volume."""
    key = str(key or "").strip()
    if not key or len(key) > 80:
        raise ValueError("announcement_bad_volume")
    entry = clean_volume(data)

    def change(doc):
        doc["volumes"][key] = entry
        return doc

    _mutate(path, change)
    return entry


def source_volume(entries, key):
    """The volume a source plays at, or None when it follows the music."""
    entry = (entries or {}).get(key) or {}
    return entry.get("volume") if entry.get("on") else None


TRIGGERS = ("time", "manual", "after_music", "after_boot")

CHANCES = ("1/1", "3/4", "2/3", "1/2", "1/3", "1/4", "1/5", "1/7", "1/10")

SPEECH = ("none", "time", "time_date")

AFTER_ACTIONS = ("none", "pause", "mute", "loop_track", "loop_album", "loop_off",
                 "volume_up", "volume_down", "sleep", "standby", "poweroff")


def validate(data):
    """Raises ValueError carrying an error CODE (announcement_name_required,
    ...), translated by the web interface like every other API error."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("announcement_name_required")
    if len(name) > 80:
        raise ValueError("announcement_name_too_long")

    folder = str(data.get("folder", "")).strip().rstrip("/\\")
    if not folder:
        raise ValueError("announcement_folder_required")
    if not _is_absolute_path(folder):
        raise ValueError("announcement_folder_not_absolute")

    try:
        hour = int(data.get("hour", 0))
        minute = int(data.get("minute", 0))
    except (TypeError, ValueError):
        raise ValueError("announcement_bad_time")
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError("announcement_bad_time")

    trigger = str(data.get("trigger", "time"))
    if trigger not in TRIGGERS:
        raise ValueError("announcement_bad_trigger")
    try:
        delay_min = int(data.get("delay_min", 30))
        repeat_times = int(data.get("repeat_times", 1))
    except (TypeError, ValueError):
        raise ValueError("announcement_bad_delay")
    if not (1 <= delay_min <= 1440) or not (0 <= repeat_times <= 999):
        raise ValueError("announcement_bad_delay")

    auto_chance = str(data.get("auto_chance", "1/1"))
    manual_chance = str(data.get("manual_chance", "1/1"))
    if auto_chance not in CHANCES or manual_chance not in CHANCES:
        raise ValueError("announcement_bad_chance")

    spoken = str(data.get("speech") or "none")
    if spoken not in SPEECH:
        raise ValueError("announcement_bad_speech")

    after_action = str(data.get("after_action") or "none")
    if after_action not in AFTER_ACTIONS:
        raise ValueError("announcement_bad_action")

    return {
        "name": name,
        "folder": folder,
        "hour": hour,
        "minute": minute,
        "auto_chance": auto_chance,
        "manual_chance": manual_chance,
        "trigger": trigger,
        "delay_min": delay_min,
        "repeat_times": repeat_times,
        "after_action": after_action,
        "speech": spoken,
        "enabled": bool(data.get("enabled", True)),
    }


def add(path, data, default_parent=None):
    """Creates a new announcement type."""
    data = dict(data)
    wants_default = not str(data.get("folder", "")).strip() and default_parent
    if wants_default:
        data["folder"] = default_parent
    clean = validate(data)

    def change(doc):
        items = doc["items"]
        existing_ids = {item["id"] for item in items}
        base_id = _slugify(clean["name"])
        new_id = base_id
        suffix = 2
        while new_id in existing_ids:
            new_id = "%s-%d" % (base_id, suffix)
            suffix += 1
        if wants_default:
            clean["folder"] = os.path.join(default_parent, new_id)
            try:
                os.makedirs(clean["folder"], exist_ok=True)
            except OSError as exc:
                log.warning("Could not create %s: %s", clean["folder"], exc)

        clean["id"] = new_id
        clean["created_at"] = time.time()
        doc["items"] = items + [clean]
        return doc

    _mutate(path, change)
    return clean


def update(path, item_id, data):
    """Partial update: fields left out of `data` keep their current value."""
    found = []

    def change(doc):
        items = doc["items"]
        for i, item in enumerate(items):
            if item["id"] == item_id:
                merged = dict(item)
                merged.update(data)
                clean = validate(merged)
                clean["id"] = item_id
                clean["created_at"] = item.get("created_at", time.time())
                items[i] = clean
                doc["items"] = items
                found.append(clean)
                return doc
        return None

    _mutate(path, change)
    if not found:
        raise KeyError(item_id)
    return found[0]


def delete(path, item_id):
    found = []

    def change(doc):
        items = doc["items"]
        remaining = [item for item in items if item["id"] != item_id]
        if len(remaining) == len(items):
            return None
        doc["items"] = remaining
        doc["volumes"].pop("custom:%s" % item_id, None)
        found.append(True)
        return doc

    _mutate(path, change)
    if not found:
        raise KeyError(item_id)


def get(path, item_id):
    for item in load(path):
        if item["id"] == item_id:
            return item
    raise KeyError(item_id)


def count_files(folder):
    """Audio file count in a folder, for the web UI's "N file(s)" hint."""
    if not folder or not os.path.isdir(folder):
        return 0
    try:
        return sum(
            1 for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        )
    except OSError:
        return 0


BUILTIN_SOURCES = ("meme", "cutoff")


def resolve_source_folder(cfg, custom_items, source_id):
    """Folder path for a source id: one of BUILTIN_SOURCES, or "custom:<id>"
    for a custom announcement type."""
    builtin = {
        "meme": cfg.get("MEME_DIR"),
        "cutoff": cfg.get("CUTOFF_ANNOUNCE_DIR"),
    }
    if source_id in builtin:
        return builtin[source_id]
    if source_id.startswith("custom:"):
        item_id = source_id[len("custom:"):]
        item = next((i for i in custom_items if i["id"] == item_id), None)
        return item["folder"] if item else None
    return None


DEFAULT_AUDIO_ROOT = "/home/pi/audio"


def _default_items(morning_folder, morning_hour, morning_minute, doubleclick_folder):
    now = time.time()
    return [
        {"id": "morning", "name": "Morning", "folder": morning_folder,
         "hour": morning_hour, "minute": morning_minute,
         "trigger": "time", "delay_min": 30, "repeat_times": 1,
         "enabled": True, "created_at": now},
        {"id": "doubleclick", "name": "Double click", "folder": doubleclick_folder,
         "hour": 12, "minute": 0,
         "trigger": "manual", "delay_min": 30, "repeat_times": 1,
         "enabled": True, "created_at": now},
    ]


def seed_defaults(path, audio_root=DEFAULT_AUDIO_ROOT):
    """Creates the list with the two announcement types a new radio is offered,
    when the file does not exist at all."""
    if not path or os.path.exists(path):
        return False
    save_all(path, _default_items(
        os.path.join(audio_root, "morning_announcements"), 5, 58,
        os.path.join(audio_root, "doubleclick_announcements")))
    return True

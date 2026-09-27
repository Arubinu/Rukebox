"""User-defined announcement types (JSON, edited from the web interface)."""

import json
import logging
import os
import re
import time

log = logging.getLogger("announcements")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus"}


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "announcement"


def _is_absolute_path(folder):
    # Not os.path.isabs(): its answer for "/x" differs between Python builds
    # on Windows, where this also runs in tests.
    return folder.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", folder))


def load(path):
    """Every custom announcement, oldest first."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s, treating as empty", path)
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _save(path, items):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def save_all(path, items):
    """Replaces the whole list, validating nothing."""
    _save(path, items)


TRIGGERS = ("time", "manual", "after_music", "after_boot")

CHANCES = ("1/1", "3/4", "2/3", "1/2", "1/3", "1/4", "1/5", "1/7", "1/10")


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
        "enabled": bool(data.get("enabled", True)),
    }


def add(path, data, default_parent=None):
    """Creates a new announcement type."""
    items = load(path)
    data = dict(data)
    wants_default = not str(data.get("folder", "")).strip() and default_parent
    if wants_default:
        data["folder"] = default_parent
    clean = validate(data)
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
    items.append(clean)
    _save(path, items)
    return clean


def update(path, item_id, data):
    """Partial update: fields left out of `data` keep their current value."""
    items = load(path)
    for i, item in enumerate(items):
        if item["id"] == item_id:
            merged = dict(item)
            merged.update(data)
            clean = validate(merged)
            clean["id"] = item_id
            clean["created_at"] = item.get("created_at", time.time())
            items[i] = clean
            _save(path, items)
            return clean
    raise KeyError(item_id)


def delete(path, item_id):
    items = load(path)
    remaining = [item for item in items if item["id"] != item_id]
    if len(remaining) == len(items):
        raise KeyError(item_id)
    _save(path, remaining)


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
    _save(path, _default_items(
        os.path.join(audio_root, "morning_announcements"), 5, 58,
        os.path.join(audio_root, "doubleclick_announcements")))
    return True

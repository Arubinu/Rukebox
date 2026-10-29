"""The birthdays of the house: a name and a day of the year, wished on that day."""

import re

import config_document
import music_lists

BLOCK = "birthdays"
NAME_MAX = 40
BIRTHDAYS_MAX = 100
# When the radio wishes them: as soon as music plays that day, or only when a button asks.
WISH = ("start", "button")


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "birthday"


def _read(path):
    data = config_document.read(BLOCK, path)
    return data if isinstance(data, dict) else {}


def load(path=None):
    """Every birthday, in the order of the year."""
    items = _read(path).get("people")
    if not isinstance(items, list):
        return []
    kept = [item for item in items if isinstance(item, dict) and item.get("id") and item.get("date")]
    return sorted(kept, key=lambda item: (item["date"], item.get("name", "").casefold()))


def wish(path=None):
    value = _read(path).get("wish")
    return value if value in WISH else "start"


def _strict(path):
    """A document that cannot be read is never replaced by an empty one."""
    if config_document.read(BLOCK, path) is None:
        raise ValueError("file_unreadable")
    return load(path)


def _write(path, items, when):
    config_document.write(BLOCK, {"people": items, "wish": when}, path)


def validate(data):
    """Raises ValueError carrying an error CODE, like the other stores."""
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise ValueError("birthday_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("birthday_name_too_long")
    try:
        day = music_lists.month_day(data.get("date"))
    except ValueError:
        raise ValueError("birthday_bad_date")
    return {"name": name, "date": day}


def add(data, path=None):
    clean = validate(data)
    with config_document.lock(path):
        items = _strict(path)
        if len(items) >= BIRTHDAYS_MAX:
            raise ValueError("birthday_too_many")
        taken = {item["id"] for item in items}
        new_id = base = _slugify(clean["name"])
        suffix = 2
        while new_id in taken:
            new_id = "%s-%d" % (base, suffix)
            suffix += 1
        clean["id"] = new_id
        items.append(clean)
        _write(path, items, wish(path))
    return clean


def delete(birthday_id, path=None):
    with config_document.lock(path):
        items = _strict(path)
        kept = [item for item in items if item["id"] != birthday_id]
        if len(kept) == len(items):
            raise KeyError(birthday_id)
        _write(path, kept, wish(path))


def set_wish(value, path=None):
    if value not in WISH:
        raise ValueError("birthday_bad_wish")
    with config_document.lock(path):
        _write(path, _strict(path), value)


def save_all(items, when, path=None):
    """Replaces every birthday (a configuration import)."""
    with config_document.lock(path):
        _write(path, items, when if when in WISH else "start")


def names_on(day, path=None):
    """The names whose birthday is `day`; February 29th is wished on the 28th in other years."""
    key = "%02d-%02d" % (day.month, day.day)
    leap_day = key == "02-28" and not (day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0))
    return [item["name"] for item in load(path) if item["date"] == key or (leap_day and item["date"] == "02-29")]

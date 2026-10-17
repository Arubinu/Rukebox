"""Web radios: a name and a stream address, played in place of the radio's own music."""

import re

import config_document

BLOCK = "stations"
NAME_MAX = 40
URL_MAX = 2048
STATIONS_MAX = 50


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "station"


def load(path=None):
    data = config_document.read(BLOCK, path)
    items = (data or {}).get("stations")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id") and item.get("url")]


def get(station_id, path=None):
    found = next((item for item in load(path) if item["id"] == station_id), None)
    if found is None:
        raise KeyError(station_id)
    return found


def _strict(path):
    """A document that cannot be read is never replaced by an empty one."""
    if config_document.read(BLOCK, path) is None:
        raise ValueError("file_unreadable")
    return load(path)


def _write(path, items):
    config_document.write(BLOCK, {"stations": items}, path)


def valid_url(value):
    """The address when it is a web stream, else None: never a file of the radio."""
    text = str(value or "").strip()
    if not text.lower().startswith(("http://", "https://")) or len(text) > URL_MAX \
            or any(c.isspace() for c in text):
        return None
    return text


def validate(data):
    """Raises ValueError carrying an error CODE, like the other stores."""
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise ValueError("station_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("station_name_too_long")
    url = valid_url(data.get("url"))
    if url is None:
        raise ValueError("station_bad_url")
    return {"name": name, "url": url}


def add(data, path=None):
    clean = validate(data)
    with config_document.lock(path):
        items = _strict(path)
        if len(items) >= STATIONS_MAX:
            raise ValueError("station_too_many")
        taken = {item["id"] for item in items}
        new_id = base = _slugify(clean["name"])
        suffix = 2
        while new_id in taken:
            new_id = "%s-%d" % (base, suffix)
            suffix += 1
        clean["id"] = new_id
        items.append(clean)
        _write(path, items)
    return clean


def update(station_id, data, path=None):
    with config_document.lock(path):
        items = _strict(path)
        for i, item in enumerate(items):
            if item["id"] == station_id:
                clean = validate(dict(item, **data))
                clean["id"] = station_id
                items[i] = clean
                _write(path, items)
                return clean
    raise KeyError(station_id)


def delete(station_id, path=None):
    with config_document.lock(path):
        items = _strict(path)
        remaining = [item for item in items if item["id"] != station_id]
        if len(remaining) == len(items):
            raise KeyError(station_id)
        _write(path, remaining)


def save_all(items, path=None):
    """Replaces every station (a configuration import)."""
    with config_document.lock(path):
        _write(path, items)

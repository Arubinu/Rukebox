"""Saved play order of announcement folders."""

import json
import logging
import os

log = logging.getLogger("track_order")


def load(path):
    """{source_id: [basename, ...]} for every source that has a saved order."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s, treating as empty", path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value for key, value in data.items()
        if isinstance(key, str) and isinstance(value, list)
        and all(isinstance(name, str) for name in value)
    }


def get(path, source_id):
    """The saved order for one source, or None if it has never been customized."""
    return load(path).get(source_id)


def _save(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def save_all(path, data):
    """Replaces the whole file."""
    _save(path, data)


def save_order(path, source_id, order):
    """Saves an explicit order (a list of basenames) for one source."""
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise ValueError("bad_order")
    data = load(path)
    data[source_id] = list(order)
    _save(path, data)


def clear(path, source_id):
    """Removes a saved order, reverting that source to natural sort."""
    data = load(path)
    if source_id in data:
        del data[source_id]
        _save(path, data)

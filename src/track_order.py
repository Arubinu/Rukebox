"""Saved play order of announcement folders."""

import logging

import json_file

log = logging.getLogger("track_order")


def load(path):
    """{source_id: [basename, ...]} for every source that has a saved order."""
    data = json_file.read(path)
    if not data:
        return {}
    return {
        key: value for key, value in data.items()
        if isinstance(key, str) and isinstance(value, list)
        and all(isinstance(name, str) for name in value)
    }


def get(path, source_id):
    """The saved order for one source, or None if it has never been customized."""
    return load(path).get(source_id)


def save_all(path, data):
    """Replaces the whole file."""
    with json_file.lock(path):
        json_file.write(path, data)


def save_order(path, source_id, order):
    """Saves an explicit order (a list of basenames) for one source."""
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise ValueError("bad_order")
    with json_file.lock(path):
        data = load(path)
        data[source_id] = list(order)
        json_file.write(path, data)


def clear(path, source_id):
    """Removes a saved order, reverting that source to natural sort."""
    with json_file.lock(path):
        data = load(path)
        if source_id in data:
            del data[source_id]
            json_file.write(path, data)

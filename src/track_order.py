"""Saved play order of announcement folders."""

import logging

import config_document

log = logging.getLogger("track_order")

BLOCK = "track_order"


def load(path=None):
    """{source_id: [basename, ...]} for every source that has a saved order."""
    data = config_document.read(BLOCK, path)
    if not data:
        return {}
    return {
        key: value for key, value in data.items()
        if isinstance(key, str) and isinstance(value, list)
        and all(isinstance(name, str) for name in value)
    }


def get(source_id, path=None):
    """The saved order for one source, or None if it has never been customized."""
    return load(path).get(source_id)


def save_all(data, path=None):
    """Replaces the whole block."""
    with config_document.lock(path):
        config_document.write(BLOCK, data, path)


def save_order(source_id, order, path=None):
    """Saves an explicit order (a list of basenames) for one source."""
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise ValueError("bad_order")
    with config_document.lock(path):
        data = load(path)
        data[source_id] = list(order)
        config_document.write(BLOCK, data, path)


def clear(source_id, path=None):
    """Removes a saved order, reverting that source to natural sort."""
    with config_document.lock(path):
        data = load(path)
        if source_id in data:
            del data[source_id]
            config_document.write(BLOCK, data, path)

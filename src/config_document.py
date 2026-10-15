"""The lists a page writes, kept in rukebox.yaml beside the settings.

A store (announcements, music_lists, schedules, cards, track_order) is one
block of the configuration document: a top-level key whose value is the JSON
document that store used to keep in its own file. Writing a block replaces
those lines only, so the comments, the order and the settings of the rest of
the file are untouched.
"""

import logging
import os
import threading

import config_file
import json_file
import paths

log = logging.getLogger("config_document")

# Every block: the name it had as a setting, the key it had in `paths:`, and
# the file it lived in before this version.
BLOCKS = {
    "announcements": ("ANNOUNCEMENTS_FILE", "announcements_file", "announcements.json"),
    "music_lists": ("MUSIC_LISTS_FILE", "music_lists_file", "music_lists.json"),
    "schedules": ("SCHEDULES_FILE", "schedules_file", "schedules.json"),
    "cards": ("CARDS_FILE", "cards_file", "cards.json"),
    "track_order": ("TRACK_ORDER_FILE", "track_order_file", "track_order.json"),
}

# What a block added to the end of a document is introduced by.
COMMENTS = {
    "announcements": "# Custom announcement types, their folders and their volumes.",
    "music_lists": "# The lists built in the interface, and the genres they hold.",
    "schedules": "# The schedules: when the radio starts, stops, and with what settings.",
    "cards": "# The RFID cards and what each one starts.",
    "track_order": "# The play order saved for an announcement folder, when one was chosen.",
}

# What a new document shows of each block, so that the shape is there to read.
EMPTY = {
    "announcements": {"items": [], "volumes": {}},
    "music_lists": {"lists": []},
    "schedules": {"schedules": []},
    "cards": {"cards": []},
    "track_order": {},
}

DESCRIPTIONS = {
    "announcements": "The announcement types made in the interface",
    "music_lists": "The music lists, and the genres they play",
    "schedules": "When the radio starts and stops by itself",
    "cards": "The cards, and what each one starts",
    "track_order": "The play order chosen for an announcement folder",
}

_cache = {}
_locks = {}
_guard = threading.Lock()


def _key(path):
    return os.path.abspath(str(path))


def document_path(path=None):
    """The configuration document a store was pointed at.

    A store may still be handed the file it used to keep - a test does, and a
    caller of one's own: the document is the rukebox.yaml beside it.
    """
    if not path:
        return config_file.find_yaml_file()
    text = str(path)
    if text.endswith(".json") or text.endswith(".yml"):
        return os.path.join(os.path.dirname(text) or ".", "rukebox.yaml")
    return text


def lock(path=None):
    """The one lock of the document: the settings and the blocks are one file."""
    key = _key(document_path(path))
    with _guard:
        held = _locks.get(key)
        if held is None:
            held = _locks[key] = threading.RLock()
        return held


def stamp(path=None):
    """The date and size of the document, what a reader watches to know it
    changed."""
    try:
        info = os.stat(document_path(path))
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def legacy_path(name, path=None):
    """The file this block lived in before it was a block: beside the document,
    unless the environment or an older `paths:` key names another place."""
    env_name, key, filename = BLOCKS[name]
    forced = os.environ.get("RUKEBOX_" + env_name)
    if forced:
        return forced
    document = document_path(path)
    data = _load(document) if os.path.exists(document) else {}
    section = (data or {}).get("paths")
    if isinstance(section, dict) and section.get(key):
        return str(section[key])
    beside = os.path.join(os.path.dirname(document) or ".", filename)
    return beside if os.path.dirname(document) else paths.config(filename)


def _load(path):
    """The whole document, or None when it is there but cannot be read."""
    yaml = config_file._yaml_module()
    if yaml is None:
        return None
    # The C parser reads the same YAML some twenty times faster: 20 ms instead
    # of 0.4 s on a Pi Zero, and this reads the whole file.
    loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f, Loader=loader)  # noqa: S506 - a safe loader either way
    except Exception:  # noqa: BLE001
        log.exception("%s is not valid YAML: the lists keep the values they had", path)
        return None
    return data if isinstance(data, dict) else {}


def _parsed(path):
    """The document, read again only when the file changed."""
    key = _key(path)
    mark = None
    try:
        info = os.stat(path)
        mark = (info.st_mtime_ns, info.st_size)
    except OSError:
        pass
    with _guard:
        cached = _cache.get(key)
        if cached is not None and cached[0] == mark:
            return cached[1]
    data = _load(path) if mark is not None else None
    with _guard:
        _cache[key] = (mark, data)
    return data


def forget(path=None):
    """Drops what was cached for one document, after a write."""
    with _guard:
        _cache.pop(_key(document_path(path)), None)


def read(name, path=None):
    """One block: its document, {} when the document has no such block, None
    when the document itself cannot be read."""
    if name not in BLOCKS:
        raise ValueError("unknown block: %s" % name)
    if config_file._yaml_module() is None:
        # No YAML reader here: the file the block lived in is still written, so
        # that the lists keep working on such a machine.
        return json_file.read(legacy_path(name, path))
    document = document_path(path)
    with lock(document):
        if not os.path.exists(document):
            return {}
        data = _parsed(document)
        if data is None:
            return None
        block = data.get(name)
        return block if isinstance(block, dict) else {}


def render(name, doc):
    """The lines one block takes: its key, then the document indented under it."""
    if not doc:
        return ["%s: {}\n" % name]
    out = ["%s:\n" % name]
    yaml = config_file._yaml_module()
    if yaml is None:
        return out
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=1000, indent=2)
    for line in body.splitlines():
        out.append("  %s\n" % line if line.strip() else "\n")
    return out


def render_template():
    """The five blocks, empty, with the comment that introduces them: what a
    new document shows of the lists the interface fills."""
    out = []
    for name in BLOCKS:
        out.append("\n# ---------------------------------------------------------------\n")
        out.append("# %s\n" % DESCRIPTIONS[name])
        out.append("# ---------------------------------------------------------------\n")
        out.extend(render(name, EMPTY[name]))
        out.append("\n")
    return "".join(out)


def write(name, doc, path=None):
    """Replaces one block, leaving every other line of the document alone."""
    if name not in BLOCKS:
        raise ValueError("unknown block: %s" % name)
    if config_file._yaml_module() is None:
        json_file.write(legacy_path(name, path), doc)
        return
    document = document_path(path)
    with lock(document):
        if os.path.exists(document):
            with open(document, "r", encoding="utf-8", newline="") as f:
                lines = f.readlines()
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
        else:
            lines = [config_file.HEADER]
        block = render(name, doc)
        bounds = config_file._section_bounds(lines)
        if name in bounds:
            start, end = bounds[name]
            lines[start:end] = block
        else:
            lines.append("\n")
            lines.append(COMMENTS[name] + "\n")
            lines.extend(block)
        config_file._atomic_write(document, "".join(lines))
    forget(document)


def import_legacy(path=None):
    """Moves the lists kept in their own files into the document, once.

    Each file is written into its block, read back to be sure, and only then
    removed: an unreadable file is left alone, and so is one with nothing to
    take. Answers the blocks that moved.
    """
    if config_file._yaml_module() is None:
        log.warning("PyYAML is not installed: the lists stay in their own files")
        return []
    document = document_path(path)
    moved = []
    for name in BLOCKS:
        data = _parsed(document) if os.path.exists(document) else {}
        block = (data or {}).get(name)
        # A block the document already fills is not taken over; one that is
        # there but empty is a shape, not data, so the file still moves in.
        if isinstance(block, dict) and any(block.values()):
            continue
        legacy = legacy_path(name, document)
        if not legacy or not os.path.exists(legacy):
            continue
        doc = json_file.read(legacy)
        if not isinstance(doc, dict) or not doc:
            continue
        write(name, doc, document)
        if read(name, document) != doc:
            log.error("Could not move %s into %s: the file is kept", legacy, document)
            continue
        try:
            os.remove(legacy)
        except OSError:
            log.warning("Could not remove %s, it is no longer read", legacy)
        moved.append(name)
        log.info("The %s list moved into %s, %s removed", name, document, legacy)
    return moved

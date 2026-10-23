"""The lists a page writes, kept in rukebox.yaml beside the settings.

A store (announcements, music_lists, schedules, cards, track_order) is one
block of the configuration document: a top-level key whose value is the JSON
document that store used to keep in its own file. Writing a block replaces
those lines only, so the comments, the order and the settings of the rest of
the file are untouched.
"""

import copy
import logging
import os
import shutil
import threading
import time

import config_file
import json_file
import paths

log = logging.getLogger("config_document")

# Where the configuration folder is copied to, once, before the lists move out
# of their own files: a version before the blocks reads that folder as it was.
BACKUP_DIR = "backup-before-v2"
README_NAME = "WHAT-IS-THIS.txt"
README = """\
The configuration as it was before the lists (announcements, music lists,
schedules, cards and the play order saved per announcement folder) moved into
rukebox.yaml. Taken on %(date)s, with every file of %(folder)s.

Putting it back: stop the radio, then copy this folder over the configuration
folder, which restores the settings and the five list files too:

%(steps)s

A single list can also be put back on its own, without touching the rest: copy
one file (announcements.json, cards.json...) into %(folder)s, and a version
before this one reads it again. Once you are sure you will not go back, this
folder can be deleted.
"""
README_STEPS = """\
    sudo systemctl stop rukebox-daemon rukebox-web
    sudo cp -a . %(folder)s/
    sudo systemctl start rukebox-daemon rukebox-web"""
# A container has no systemd, and its folder is a volume the host reaches by its own path.
README_STEPS_CONTAINER = """\
    docker compose stop
    on the host, copy the files of this folder into the folder mounted as
    %(folder)s, leaving this folder out
    name the image of the version to go back to in the compose file, then
    docker compose up -d"""

# Every block: the name it had as a setting, the key it had in `paths:`, and
# the file it lived in before this version.
BLOCKS = {
    "announcements": ("ANNOUNCEMENTS_FILE", "announcements_file", "announcements.json"),
    "music_lists": ("MUSIC_LISTS_FILE", "music_lists_file", "music_lists.json"),
    "schedules": ("SCHEDULES_FILE", "schedules_file", "schedules.json"),
    "cards": ("CARDS_FILE", "cards_file", "cards.json"),
    "track_order": ("TRACK_ORDER_FILE", "track_order_file", "track_order.json"),
    # The accounts and the volume curve were never files of their own:
    # nothing to take in, but the document holds them like the other lists.
    "people": (None, "people", "people.json"),
    "volume_curve": (None, "volume_curve", "volume_curve.json"),
    "stations": (None, "stations", "stations.json"),
    "syncs": (None, "syncs", "syncs.json"),
}

# What a block added to the end of a document is introduced by.
COMMENTS = {
    "announcements": "# Custom announcement types, their folders and their volumes.",
    "music_lists": "# The lists built in the interface, and the genres they hold.",
    "schedules": "# The schedules: when the radio starts, stops, and with what settings.",
    "cards": "# The RFID cards and what each one starts.",
    "track_order": "# The play order saved for an announcement folder, when one was chosen.",
    "people": "# The people who may log in: name, role, and the password they chose.",
    "volume_curve": "# The daily volume curve: points on the clock, level by level.",
    "stations": "# The web radios: a name and the address of the stream.",
    "syncs": "# Folders filled from another machine: the connections, and the syncs that use them.",
}

# What a new document shows of each block, so that the shape is there to read.
EMPTY = {
    "announcements": {"items": [], "volumes": {}},
    "music_lists": {"lists": []},
    "schedules": {"schedules": []},
    "cards": {"cards": []},
    "track_order": {},
    "people": {"items": []},
    "volume_curve": {"points": []},
    "stations": {"stations": []},
    "syncs": {"connections": [], "jobs": []},
}

DESCRIPTIONS = {
    "announcements": "The announcement types made in the interface",
    "music_lists": "The music lists, and the genres they play",
    "schedules": "When the radio starts and stops by itself",
    "cards": "The cards, and what each one starts",
    "track_order": "The play order chosen for an announcement folder",
    "people": "Who may log in, and as what",
    "volume_curve": "The daily volume curve",
    "stations": "The web radios, by name and stream address",
    "syncs": "Folders filled from another machine, and when",
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
    forced = os.environ.get("RUKEBOX_" + env_name) if env_name else None
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
        # A copy: the parsed document is shared by every reader until the file changes.
        return copy.deepcopy(block) if isinstance(block, dict) else {}


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
        if BLOCKS[name][0] is None:
            log.warning("%s needs PyYAML: nothing was written", name)
            return
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


def _readme(folder):
    container = os.environ.get("RUKEBOX_PLATFORM") == "docker"
    steps = (README_STEPS_CONTAINER if container else README_STEPS) % {"folder": folder}
    return README % {"date": time.strftime("%Y-%m-%d"), "folder": folder, "steps": steps}


def _keep_the_folder(document):
    """Copies every file of the configuration folder into BACKUP_DIR, once.

    Taken before a list is moved in, so that a version that read the files can
    be put back by copying the folder over: it is the whole configuration as it
    was, the document and its generated environment file included. A folder
    that is already there is left as it is - the first copy is the good one.
    """
    source = os.path.dirname(document) or "."
    target = os.path.join(source, BACKUP_DIR)
    if os.path.isdir(target):
        return target
    # Listed before the folder is made: it must not be copied into itself.
    # A README left behind by putting an earlier copy back is not part of the configuration.
    entries = [entry for entry in sorted(os.listdir(source))
               if entry not in (BACKUP_DIR, README_NAME) and not entry.endswith(".tmp")]
    os.makedirs(target, mode=0o700)
    for entry in entries:
        origin = os.path.join(source, entry)
        if os.path.isdir(origin):
            shutil.copytree(origin, os.path.join(target, entry))
            continue
        shutil.copy2(origin, os.path.join(target, entry))
        config_file.secure_file(os.path.join(target, entry), source)
    with open(os.path.join(target, README_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(_readme(os.path.abspath(source)))
    config_file.secure_file(os.path.join(target, README_NAME), source)
    log.info("The configuration folder was copied to %s", target)
    return target


def import_legacy(path=None):
    """Moves the lists kept in their own files into the document, once.

    The whole configuration folder is copied first, then each file is written
    into its block, read back to be sure, and only then removed: an unreadable
    file is left alone, and so is one with nothing to take. Answers the blocks
    that moved.
    """
    if config_file._yaml_module() is None:
        log.warning("PyYAML is not installed: the lists stay in their own files")
        return []
    document = document_path(path)
    waiting = []
    for name in BLOCKS:
        if BLOCKS[name][0] is None:
            continue          # never a file of its own: nothing to take in
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
        if isinstance(doc, dict) and doc:
            waiting.append((name, legacy, doc))
    if not waiting:
        return []
    try:
        _keep_the_folder(document)
    except OSError:
        # Nothing is moved and nothing is removed: without the copy there is no
        # way back, and the files are still read by a version before this one.
        log.exception("Could not copy the configuration folder: the lists stay where they are")
        return []

    moved = []
    for name, legacy, doc in waiting:
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

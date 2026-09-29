"""JSON files that several threads write: one at a time, and never half-written.

Measured on the Pi: with a temporary name shared by every writer, two saves
filling it at once publish a file that is two JSON documents glued together
(`Extra data: line 33 column 1`), and the reader then sees nothing at all -
which is how an announcement came to answer "that announcement no longer
exists" while it was still there. One lock per file, and a temporary name of
its own for every write, is the whole answer."""

import json
import logging
import os
import threading

log = logging.getLogger("json_file")

_locks = {}
_locks_guard = threading.Lock()


def lock(path):
    """The lock for one file: the web server answers requests in threads."""
    key = os.path.abspath(str(path))
    with _locks_guard:
        held = _locks.get(key)
        if held is None:
            held = _locks[key] = threading.RLock()
        return held


def read(path):
    """The document, or None when the file exists but cannot be read."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s", path)
        return None
    return data if isinstance(data, dict) else None


def write(path, doc):
    """Writes the document whole: its own temporary file, then a rename."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

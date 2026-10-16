"""M3U playlists: a list written out for another player, and one read back in.

An exported list names each song by its path inside the music folder, with the
#EXTINF line other players show. An imported one is matched against the
library line by line: the path as written, then the path inside the music
folder, then the file's name alone, then the "Artist - Title" of its #EXTINF.
"""

import os
import re

MAX_LINES = 20000
_EXTINF = re.compile(r"^#EXTINF:\s*(-?\d+)?[^,]*,(.*)$", re.IGNORECASE)


def export(items, roots):
    """The M3U text of these catalogue rows ({path, title, artist, duration})."""
    lines = ["#EXTM3U"]
    for item in items:
        path = str(item.get("path") or "")
        if not path:
            continue
        label = " - ".join(part for part in (item.get("artist"), item.get("title")) if part)
        seconds = int(round(float(item.get("duration") or 0))) or -1
        lines.append("#EXTINF:%d,%s" % (seconds, label or os.path.splitext(os.path.basename(path))[0]))
        lines.append(relative(path, roots))
    return "\n".join(lines) + "\n"


def relative(path, roots):
    """The path as another machine holding the same music folder reads it."""
    for root in roots:
        top = str(root or "").replace("\\", "/").rstrip("/")
        if top and path.replace("\\", "/").startswith(top + "/"):
            return path.replace("\\", "/")[len(top) + 1:]
    return path


def decode(raw):
    """The text of an uploaded file: UTF-8 (.m3u8, and most .m3u today), else Latin-1."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse(text):
    """[(location, label)] in the file's order; the label is its #EXTINF text."""
    entries, label = [], ""
    for line in text.splitlines()[:MAX_LINES]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            found = _EXTINF.match(line)
            if found:
                label = found.group(2).strip()
            continue
        entries.append((line, label))
        label = ""
    return entries


def match(entries, lib, roots):
    """(paths found in the library, entries not found), each path once."""
    found, missing, seen = [], [], set()
    for location, label in entries:
        path = _find(location, label, lib, roots)
        if path is None:
            missing.append(label or location)
        elif path not in seen:
            seen.add(path)
            found.append(path)
    return found, missing


def _find(location, label, lib, roots):
    """The library path a line names, or None."""
    if location.lower().startswith(("http://", "https://")):
        return None
    if location.lower().startswith("file://"):
        location = location[7:]
    location = location.replace("\\", "/")
    candidates = [location] + [os.path.join(root, location.lstrip("/")) for root in roots if root]
    for candidate in candidates:
        for spelled in (candidate, os.path.normpath(candidate)):
            if lib.item_for_path(spelled):
                return spelled
    item = lib.item_for_basename(location.rsplit("/", 1)[-1])
    if item is None and label and " - " in label:
        item = lib.match(label)
    return lib.path_for_key(item["key"]) if item else None

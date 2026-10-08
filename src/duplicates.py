"""Tracks the library holds more than once."""

import os
import re
import sqlite3

import library
import track_media

BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
FEAT = re.compile(r"\b(?:feat|featuring|ft|avec)\b.*$", re.IGNORECASE)
LEADING_TRACK = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")
# Re-tagged copies of one file can differ by the last frame, so a second of tolerance.
SAME_FILE_SEC = 1.0
# Jingles and samples are too short to be worth reporting.
MIN_SIZE = 200 * 1024
MAX_GROUPS = 400


def song_key(title, artist):
    """The identity two copies of one song share."""
    title = FEAT.sub(" ", BRACKETS.sub(" ", str(title or "")))
    title = LEADING_TRACK.sub("", title)
    artist = BRACKETS.sub(" ", str(artist or ""))
    return "%s|%s" % (library.fold(artist), library.fold(title))


def kbps(size, duration):
    """The bitrate the file's own bytes imply, rounded."""
    try:
        return int(round(size * 8 / duration / 1000.0))
    except (TypeError, ZeroDivisionError):
        return 0


def rows(db_path):
    """Every catalogued track, with the tags the scan already read."""
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT path, size, duration, title, artist, album FROM tracks")]
    finally:
        connection.close()


def _display(row):
    """A title and an artist to show, from the tags or from the file name."""
    name = os.path.splitext(os.path.basename(row["path"]))[0]
    title = (row["title"] or "").strip() or name
    return title, (row["artist"] or "").strip()


def _entry(row, hidden):
    key = track_media.track_key(row["path"])
    return {
        "key": key,
        "path": row["path"],
        "name": os.path.basename(row["path"]),
        "album": (row["album"] or "").strip(),
        "size": int(row["size"] or 0),
        "duration": round(float(row["duration"] or 0), 1),
        "kbps": kbps(row["size"], row["duration"]),
        "hidden": bool(key and key in hidden),
        "same_file": False,
    }


def groups(db_path, hidden=()):
    """Every song the library holds more than once, the copies of one file
    first and then the most room to win, with what each copy weighs."""
    hidden = set(hidden or ())
    by_song = {}
    for row in rows(db_path):
        if (row["size"] or 0) < MIN_SIZE:
            continue
        entry = _entry(row, hidden)
        title, artist = _display(row)
        key = song_key(row["title"], row["artist"])
        found = by_song.setdefault(key, {
            "key": key, "title": title, "artist": artist, "tracks": [],
        })
        if not found["artist"] and artist:
            found["artist"] = artist
        found["tracks"].append(entry)

    out = []
    for group in by_song.values():
        if len(group["tracks"]) < 2:
            continue
        group["tracks"].sort(key=lambda one: (-one["size"], one["path"]))
        for one in group["tracks"]:
            one["same_file"] = any(
                other is not one
                and other["size"] == one["size"]
                and abs(other["duration"] - one["duration"]) <= SAME_FILE_SEC
                for other in group["tracks"])
        group["same_file"] = any(one["same_file"] for one in group["tracks"])
        group["reclaimable"] = sum(one["size"] for one in group["tracks"]) - max(
            one["size"] for one in group["tracks"])
        out.append(group)

    out.sort(key=lambda group: (not group["same_file"], -group["reclaimable"],
                                -len(group["tracks"])))
    return out[:MAX_GROUPS]


def summary(found):
    """(groups, tracks, bytes held twice) for a line above the list."""
    return {
        "groups": len(found),
        "tracks": sum(len(group["tracks"]) for group in found),
        "reclaimable": sum(group["reclaimable"] for group in found),
        "same_file": sum(1 for group in found if group["same_file"]),
    }


def report(db_path, hidden=()):
    """The check as plain text, for a terminal over SSH."""
    found = groups(db_path, hidden)
    totals = summary(found)
    lines = ["%d group(s) of duplicates, %d track(s), %.1f MB held twice" % (
        totals["groups"], totals["tracks"], totals["reclaimable"] / 1048576.0)]
    for group in found:
        lines.append("")
        lines.append("%s - %s%s" % (
            group["artist"] or "?", group["title"],
            "   [same file, twice]" if group["same_file"] else ""))
        for one in group["tracks"]:
            lines.append("   %s %6.1f MB  %5.0fs  %4d kb/s  %s%s" % (
                "=" if one["same_file"] else " ",
                one["size"] / 1048576.0, one["duration"], one["kbps"],
                "(hidden) " if one["hidden"] else "", one["path"]))
    return "\n".join(lines)


def main(argv=None):
    """Prints the duplicate report for a database path, or for this Pi's."""
    import sys

    argv = sys.argv[1:] if argv is None else list(argv)
    path = argv[0] if argv else ""
    if path:
        hidden = ()
    else:
        import config_and_scan
        cfg = config_and_scan.load_config()
        path = cfg["LIBRARY_DB_FILE"]
        hidden = _hidden_keys(cfg)
    if not os.path.exists(path):
        print("no library database at %s" % path)
        return 1
    # A C-locale terminal cannot print every track name: replace rather than raise.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    print(report(path, hidden))
    return 0


def _hidden_keys(cfg):
    try:
        import hidden_tracks
        return hidden_tracks.keys(cfg.get("HIDDEN_FILE") or "")
    except Exception:  # noqa: BLE001 - a check must never fail on this
        return ()


if __name__ == "__main__":
    raise SystemExit(main())

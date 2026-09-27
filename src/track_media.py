#!/usr/bin/env python3
"""Cover art, tags and lyrics of a track (ffprobe / ffmpeg, cached)."""

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from collections import OrderedDict

log = logging.getLogger("track_media")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
FOLDER_IMAGE_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
LYRICS_EXTENSIONS = (".lrc", ".srt", ".vtt", ".txt")

COVER_MAX_BYTES = 350 * 1024
COVER_MAX_SIDE = 640
COVER_CACHE_BYTES = 6 * 1024 * 1024
LYRICS_MAX_BYTES = 512 * 1024
TOOL_TIMEOUT_SEC = 15

_lock = threading.Lock()
_cover_cache = OrderedDict()
_cover_cache_bytes = 0
_lyrics_cache = OrderedDict()
_LYRICS_CACHE_ENTRIES = 32


def _file_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_size, int(st.st_mtime))


def _sidecar(path, extensions):
    """The file beside `path` with the same name and one of these extensions,
    case-insensitively."""
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    wanted = {(stem + ext).lower() for ext in extensions}
    by_lower = {name.lower(): name for name in names}
    for ext in extensions:
        name = by_lower.get((stem + ext).lower())
        if name and name.lower() in wanted and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    return None


def _image_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _run(cmd, stdin_data=None):
    try:
        proc = subprocess.run(
            cmd, input=stdin_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("%s failed: %s", cmd[0], exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _shrink(data):
    """Re-encodes an oversized picture to a JPEG of COVER_MAX_SIDE at most."""
    out = _run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0",
        "-vf", "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease"
        % (COVER_MAX_SIDE, COVER_MAX_SIDE),
        "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
    ], stdin_data=data)
    if out and _image_mime(out) and len(out) < len(data):
        return out
    return data


def _embedded_picture(path):
    """The first attached picture of an audio file, as encoded bytes."""
    out = _run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", path,
        "-map", "0:v:0", "-frames:v", "1", "-c:v", "copy", "-f", "image2pipe", "pipe:1",
    ])
    if out and _image_mime(out):
        return out
    if out is None:
        return None
    return _run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", path,
        "-map", "0:v:0", "-frames:v", "1", "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1",
    ]) or None


def _folder_picture(path):
    folder = os.path.dirname(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    by_lower = {name.lower(): name for name in names}
    for stem in FOLDER_IMAGE_NAMES:
        for ext in IMAGE_EXTENSIONS:
            name = by_lower.get(stem + ext)
            if name:
                return os.path.join(folder, name)
    return None


def _read_file(path, limit):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return None


def _find_cover(path):
    sidecar = _sidecar(path, IMAGE_EXTENSIONS)
    data = _read_file(sidecar, 32 * 1024 * 1024) if sidecar else None
    if not data:
        data = _embedded_picture(path)
    if not data:
        folder_image = _folder_picture(path)
        data = _read_file(folder_image, 32 * 1024 * 1024) if folder_image else None
    if not data or not _image_mime(data):
        return None
    if len(data) > COVER_MAX_BYTES:
        data = _shrink(data)
    return data, _image_mime(data)


def cover(path):
    """(bytes, mime) for the track's cover, or None."""
    global _cover_cache_bytes
    key = _file_key(path) if path else None
    if key is None:
        return None
    with _lock:
        if key in _cover_cache:
            _cover_cache.move_to_end(key)
            return _cover_cache[key]
    try:
        result = _find_cover(path)
    except Exception:
        log.exception("Cover lookup failed for %s", path)
        result = None
    with _lock:
        _cover_cache[key] = result
        _cover_cache_bytes += len(result[0]) if result else 0
        while _cover_cache_bytes > COVER_CACHE_BYTES and len(_cover_cache) > 1:
            _old_key, old = _cover_cache.popitem(last=False)
            _cover_cache_bytes -= len(old[0]) if old else 0
    return result


_LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_WORD_TIME = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_LRC_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE | re.MULTILINE)
_SUB_TIME = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
)
_TAG_MARKUP = re.compile(r"<[^>]+>")


def _decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _fraction(text):
    if not text:
        return 0.0
    return int(text) / (10 ** len(text))


def parse_lrc(text):
    """Timed lines of an LRC text, sorted, or [] if it carries no timing."""
    offset = 0.0
    m = _LRC_OFFSET.search(text)
    if m:
        offset = int(m.group(1)) / 1000.0
    lines = []
    for raw in text.splitlines():
        stamps = []
        rest = raw.strip()
        while True:
            m = _LRC_TIME.match(rest)
            if not m:
                break
            stamps.append(int(m.group(1)) * 60 + int(m.group(2)) + _fraction(m.group(3)))
            rest = rest[m.end():]
        if not stamps:
            continue
        words = _LRC_WORD_TIME.sub("", rest).strip()
        for stamp in stamps:
            lines.append({"t": round(max(0.0, stamp - offset), 2), "text": words})
    lines.sort(key=lambda line: line["t"])
    return lines


def parse_subtitles(text):
    """Timed lines of an SRT or WebVTT file."""
    lines = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for block in blocks:
        rows = block.strip().split("\n")
        for i, row in enumerate(rows):
            m = _SUB_TIME.search(row)
            if not m:
                continue
            start = (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60
                     + int(m.group(3)) + _fraction(m.group(4)))
            body = " ".join(_TAG_MARKUP.sub("", r).strip() for r in rows[i + 1:] if r.strip())
            lines.append({"t": round(start, 2), "text": body})
            break
    lines.sort(key=lambda line: line["t"])
    return lines


def _from_text(text, kind):
    text = text.strip()
    if not text:
        return None
    if kind in (".srt", ".vtt"):
        timed = parse_subtitles(text)
    else:
        timed = parse_lrc(text)
    if timed:
        return {"synced": True, "lines": timed}
    if kind in (".srt", ".vtt"):
        return None
    return {"synced": False, "text": text}


_tags_cache = OrderedDict()
_TAGS_CACHE_ENTRIES = 64


def _probe_tags(path):
    """Every tag set of the file (container, then each stream), as read by
    ffprobe."""
    key = _file_key(path) if path else None
    if key is None:
        return []
    with _lock:
        if key in _tags_cache:
            _tags_cache.move_to_end(key)
            return _tags_cache[key]
    sets = []
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags",
        "-of", "json", path,
    ])
    if out is None:
        return []
    if out:
        try:
            info = json.loads(out.decode("utf-8", errors="replace"))
            sets = [info.get("format", {}).get("tags", {}) or {}]
            sets += [s.get("tags", {}) or {} for s in info.get("streams", [])]
        except ValueError:
            sets = []
    with _lock:
        _tags_cache[key] = sets
        while len(_tags_cache) > _TAGS_CACHE_ENTRIES:
            _tags_cache.popitem(last=False)
    return sets


def _first_tag(sets, *names):
    wanted = [n.lower() for n in names]
    for want in wanted:
        for tags in sets:
            for name, value in tags.items():
                if name.lower() == want and isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def tags(path):
    """{"title", "artist", "album"} from the file's own tags, each None when
    absent."""
    try:
        sets = _probe_tags(path)
    except Exception:
        log.exception("Tag lookup failed for %s", path)
        sets = []
    return {
        "title": _first_tag(sets, "title"),
        "artist": _first_tag(sets, "artist", "album_artist", "album artist"),
        "album": _first_tag(sets, "album"),
    }


def cached_tags(path):
    """tags(path) when the file was already read, else None."""
    key = _file_key(path) if path else None
    if key is None:
        return None
    with _lock:
        if key not in _tags_cache:
            return None
    return tags(path)


def _embedded_lyrics(path):
    for tags_set in _probe_tags(path):
        for name, value in tags_set.items():
            low = name.lower()
            if (low.startswith("lyrics") or low in ("unsyncedlyrics", "unsynced lyrics")) \
                    and isinstance(value, str) and value.strip():
                return value
    return None


def _find_lyrics(path):
    sidecar = _sidecar(path, LYRICS_EXTENSIONS)
    if sidecar:
        data = _read_file(sidecar, LYRICS_MAX_BYTES)
        if data:
            result = _from_text(_decode(data), os.path.splitext(sidecar)[1].lower())
            if result:
                result["source"] = os.path.splitext(sidecar)[1].lower().lstrip(".")
                return result
    embedded = _embedded_lyrics(path)
    if embedded:
        result = _from_text(embedded, "tag")
        if result:
            result["source"] = "tag"
            return result
    return None


def lyrics(path):
    """{"synced": True, "lines": [{"t", "text"}...]} or {"synced": False,
    "text": ...}, with a "source", or None."""
    key = _file_key(path) if path else None
    if key is None:
        return None
    with _lock:
        if key in _lyrics_cache:
            _lyrics_cache.move_to_end(key)
            return _lyrics_cache[key]
    try:
        result = _find_lyrics(path)
    except Exception:
        log.exception("Lyrics lookup failed for %s", path)
        result = None
    with _lock:
        _lyrics_cache[key] = result
        while len(_lyrics_cache) > _LYRICS_CACHE_ENTRIES:
            _lyrics_cache.popitem(last=False)
    return result


def track_key(path):
    """A short opaque id for "this file, as it is now"."""
    key = _file_key(path) if path else None
    if key is None:
        return None
    return hashlib.sha1(("%s|%d|%d" % key).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def is_companion_file(name):
    """A lyrics file or a picture."""
    ext = os.path.splitext(name)[1].lower()
    return ext in LYRICS_EXTENSIONS or ext in IMAGE_EXTENSIONS


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        c = cover(arg)
        lyr = lyrics(arg)
        print(arg)
        print("  key   :", track_key(arg))
        print("  cover :", ("%s, %d bytes" % (c[1], len(c[0]))) if c else None)
        if lyr and lyr["synced"]:
            print("  lyrics: synced, %d lines (%s), first: %r" % (
                len(lyr["lines"]), lyr["source"], lyr["lines"][:2]))
        elif lyr:
            print("  lyrics: plain (%s), %d chars" % (lyr["source"], len(lyr["text"])))
        else:
            print("  lyrics: None")

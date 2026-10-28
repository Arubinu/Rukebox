"""A music file's own tags and pictures, changed in place through ffmpeg without re-encoding."""
# Ogg/Opus pictures are a METADATA_BLOCK_PICTURE tag; a folder's picture is cover.jpg, looked up first.

import base64
import json
import logging
import os
import re
import shutil
import struct
import subprocess

import track_media

log = logging.getLogger("tag_editor")

# The fields the page edits, and the name ffmpeg writes each one under.
FIELDS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "album_artist",
    "year": "date",
    "genre": "genre",
    "track": "track",
}
# What an album-wide change may carry: what the songs of one album share.
ALBUM_FIELDS = ("artist", "album", "album_artist", "year", "genre")
VALUE_MAX = 200
OGG_EXTENSIONS = (".ogg", ".oga", ".opus")
WRITABLE_EXTENSIONS = (".mp3", ".flac", ".m4a", ".mp4") + OGG_EXTENSIONS
FOLDER_PICTURE = "cover"
PICTURE_SIDE = 1000
WRITE_TIMEOUT_SEC = 180
PROBE_TIMEOUT_SEC = 30
# A rewritten file whose length moved more than this is not put in place.
DURATION_SLACK_SEC = 1.0
TMP_MARK = ".rukebox-tmp"


class TagError(Exception):
    """A change that could not be made; the message is the error code."""


def writable(path):
    return os.path.splitext(str(path))[1].lower() in WRITABLE_EXTENSIONS


def _probe(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration:format_tags:stream=codec_type:stream_tags:stream_disposition=attached_pic",
           "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_SEC).stdout
        return json.loads(out.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _first(tagsets, *names):
    wanted = {name.lower() for name in names}
    for tags in tagsets:
        for key, value in tags.items():
            if key.lower() in wanted and str(value).strip():
                return str(value).strip()
    return ""


def read(path):
    """The file's tags as the page edits them, its length, and whether it holds a picture; None if unreadable."""
    info = _probe(path)
    if info is None:
        return None
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return None
    tagsets = [fmt.get("tags") or {}] + [s.get("tags") or {} for s in audio]
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except ValueError:
        duration = None
    picture = any(s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic")
                  for s in streams)
    return {
        "title": _first(tagsets, "title"),
        "artist": _first(tagsets, "artist"),
        "album": _first(tagsets, "album"),
        "album_artist": _first(tagsets, "album_artist", "album artist", "albumartist"),
        "year": _first(tagsets, "date", "year", "originaldate"),
        "genre": _first(tagsets, "genre"),
        "track": _first(tagsets, "track", "tracknumber"),
        "duration": duration,
        "picture": picture,
        "writable": writable(path),
    }


def clean_changes(changes, allowed=FIELDS):
    """The changes worth writing, or TagError("bad_tag")."""
    out = {}
    for key, value in (changes or {}).items():
        if key not in allowed:
            continue
        text = "" if value is None else str(value).strip()
        if len(text) > VALUE_MAX or "\x00" in text or "\n" in text:
            raise TagError("bad_tag")
        if key == "year" and text and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", text):
            raise TagError("bad_year")
        if key == "track" and text and not re.fullmatch(r"\d{1,3}(/\d{1,3})?", text):
            raise TagError("bad_track")
        out[key] = text
    return out


def picture_jpeg(data):
    """Any picture the page accepts, as a JPEG of PICTURE_SIDE at most."""
    if not data or not track_media._image_mime(data):
        raise TagError("bad_picture")
    out = None
    try:
        proc = subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0",
            "-vf", "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease"
            % (PICTURE_SIDE, PICTURE_SIDE),
            "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
        ], input=data, capture_output=True, timeout=PROBE_TIMEOUT_SEC)
        out = proc.stdout if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if not out or track_media._image_mime(out) != "image/jpeg":
        raise TagError("bad_picture")
    return out


def picture_block(data, mime="image/jpeg"):
    """A FLAC picture block (front cover) in base64: Ogg's picture tag."""
    kind = mime.encode("ascii")
    block = (struct.pack(">II", 3, len(kind)) + kind + struct.pack(">I", 0)
             + struct.pack(">IIIII", 0, 0, 0, 0, len(data)) + data)
    return base64.b64encode(block).decode("ascii")


KEEP = object()


def _command(path, tmp, changes, picture, picture_file):
    ogg = os.path.splitext(path)[1].lower() in OGG_EXTENSIONS
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", path]
    if picture_file and not ogg:
        cmd += ["-i", picture_file]
    cmd += ["-map", "0:a", "-map_metadata", "0"]
    if ogg:
        # Ogg's tags live on the audio stream, and so must every change.
        cmd += ["-map_metadata:s:a", "0:s:a"]
    elif picture_file:
        cmd += ["-map", "1", "-disposition:v:0", "attached_pic"]
    elif picture is KEEP:
        cmd += ["-map", "0:v?"]
    cmd += ["-c", "copy"]
    if path.lower().endswith(".mp3"):
        cmd += ["-id3v2_version", "3"]
    target = "-metadata:s:a:0" if ogg else "-metadata"
    for key, value in changes.items():
        cmd += [target, "%s=%s" % (FIELDS[key], value)]
    if ogg and picture is not None:
        mime = track_media._image_mime(picture) or "image/jpeg"
        cmd += [target, "METADATA_BLOCK_PICTURE=%s" % picture_block(picture, mime)]
    return cmd + [tmp]


def write(path, changes, picture=KEEP):
    """Writes these tag changes and the picture (KEEP, None to remove, or JPEG bytes); read() of the result."""
    if not writable(path):
        raise TagError("format_not_writable")
    if not os.access(os.path.dirname(path) or ".", os.W_OK) or not os.access(path, os.W_OK):
        raise TagError("read_only")
    changes = clean_changes(changes)
    before = read(path)
    if before is None:
        raise TagError("unreadable")
    ogg = os.path.splitext(path)[1].lower() in OGG_EXTENSIONS
    if ogg and picture is KEEP:
        # Ogg's own picture is dropped by a copy that keeps only the audio: carried over by hand.
        picture = track_media._embedded_picture(path) if before["picture"] else None
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    tmp = os.path.join(folder, "." + stem + TMP_MARK + ext)
    picture_file = None
    if isinstance(picture, (bytes, bytearray)) and not ogg:
        picture_file = os.path.join(folder, "." + stem + TMP_MARK + ".jpg")
        with open(picture_file, "wb") as f:
            f.write(picture)
    try:
        cmd = _command(path, tmp, changes, picture, picture_file)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=WRITE_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("Could not rewrite %s: %s", path, exc)
            raise TagError("write_failed")
        if proc.returncode != 0:
            log.warning("ffmpeg refused to rewrite %s: %s", path,
                        proc.stderr.decode("utf-8", "replace")[-300:])
            raise TagError("write_failed")
        after = read(tmp)
        if after is None or (before["duration"] and after["duration"] is not None
                             and abs(after["duration"] - before["duration"]) > DURATION_SLACK_SEC):
            raise TagError("write_failed")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        for leftover in (tmp, picture_file):
            if leftover and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    return read(path)


def folder_picture(folder):
    """The picture the cover lookup finds in that folder, or None."""
    return track_media._picture_in(folder, track_media._picture_names(folder))


def set_folder_picture(folder, data):
    """Writes the folder's cover.jpg; another cover.* there would compete with it."""
    if not os.path.isdir(folder) or not os.access(folder, os.W_OK):
        raise TagError("read_only")
    names = track_media._listing(folder)
    for ext in track_media.IMAGE_EXTENSIONS:
        old = names.get(FOLDER_PICTURE + ext)
        if old:
            os.remove(os.path.join(folder, old))
    target = os.path.join(folder, FOLDER_PICTURE + ".jpg")
    tmp = os.path.join(folder, "." + FOLDER_PICTURE + TMP_MARK + ".jpg")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)
    return target


def remove_folder_picture(folder):
    """Removes the picture the lookup finds there; the next one, if any, shows."""
    found = folder_picture(folder)
    if not found:
        return None
    if not os.access(folder, os.W_OK):
        raise TagError("read_only")
    os.remove(found)
    return found


def levels(path, music_dir):
    """{"album": folder, "artist": folder or None}: where this track's album and artist pictures go."""
    # A song straight in an artist's folder: that folder is the album's, and the artist has none.
    root = os.path.abspath(str(music_dir or ""))
    album = os.path.abspath(os.path.dirname(path))
    artist = os.path.dirname(album)
    inside = root and album != root and (artist == root or artist.startswith(root + os.sep))
    return {"album": album, "artist": artist if inside and artist != root else None}

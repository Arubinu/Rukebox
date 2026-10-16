"""The separate covers folder, emptied into the music folder once.

Covers used to be prepared in a folder mirroring the music one (`folders.covers`).
They now live beside the music, where every other player looks for them too.
Each picture is copied to the same place in the music folder: a picture named
after a song lands beside the song, and `_any` - which covered an album, an
artist or everything - becomes that folder's `cover`. A name the music folder
already holds is left alone. The old folder is then renamed, which keeps it
whole as the way back and stops it being read.
"""

import logging
import os
import shutil

import prepared_media
import track_media

log = logging.getLogger("covers_move")

ANY = "_any"
KEPT_SUFFIX = ".before-v2"


def _target_name(name):
    stem, ext = os.path.splitext(name)
    return ("cover" + ext.lower()) if stem.lower() == ANY else name


def _kept_name(folder):
    candidate = folder.rstrip(os.sep) + KEPT_SUFFIX
    number = 2
    while os.path.exists(candidate):
        candidate = "%s%s-%d" % (folder.rstrip(os.sep), KEPT_SUFFIX, number)
        number += 1
    return candidate


def waiting(cover_dir):
    """Whether the old folder still holds pictures it could not hand over."""
    source = str(cover_dir or "").strip()
    if not source or not os.path.isdir(source):
        return False
    for _folder, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not prepared_media.system_name(d)]
        if any(os.path.splitext(f)[1].lower() in track_media.IMAGE_EXTENSIONS
               and not prepared_media.system_name(f) for f in files):
            return True
    return False


def move(cover_dir, music_dir):
    """Copies every picture of `cover_dir` into `music_dir`, then renames the
    old folder. None when there is nothing to do, else a summary."""
    source = os.path.abspath(str(cover_dir or "").strip()) if cover_dir else ""
    target = os.path.abspath(str(music_dir or "").strip()) if music_dir else ""
    if not source or not os.path.isdir(source) or not target or source == target:
        return None
    if not os.path.isdir(target) or not os.access(target, os.W_OK):
        log.warning("The music folder %s cannot be written: the covers stay in %s", target, source)
        return None
    summary = {"copied": 0, "kept": 0, "no_folder": 0, "kept_in": None}
    for folder, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not prepared_media.system_name(d)]
        relative = os.path.relpath(folder, source)
        destination = target if relative == "." else os.path.join(target, relative)
        pictures = [f for f in files if not prepared_media.system_name(f)
                    and os.path.splitext(f)[1].lower() in track_media.IMAGE_EXTENSIONS]
        if not pictures:
            continue
        if not os.path.isdir(destination):
            # It mirrored a folder the music no longer has.
            summary["no_folder"] += len(pictures)
            continue
        present = track_media._listing(destination)
        for name in sorted(pictures):
            wanted = _target_name(name)
            if wanted.lower() in present:
                summary["kept"] += 1
                continue
            try:
                shutil.copy2(os.path.join(folder, name), os.path.join(destination, wanted))
            except OSError:
                log.exception("Could not copy %s", os.path.join(folder, name))
                summary["kept"] += 1
                continue
            present[wanted.lower()] = wanted
            summary["copied"] += 1
    kept_in = _kept_name(source)
    try:
        os.rename(source, kept_in)
        summary["kept_in"] = kept_in
    except OSError:
        log.exception("Could not rename %s: it is read again at the next start", source)
    log.info("Covers moved into %s: %s", target, summary)
    return summary

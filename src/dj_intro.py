"""The DJ's introduction, read from a prepared file when there is one."""

import os

# What a prepared introduction may be: the extension is not what decides, the
# name is. Always looked at in this order, so the same file wins every time.
EXTENSIONS = (".wav", ".opus", ".mp3", ".ogg", ".m4a", ".flac")
ANY = "_any"
MODES = ("spoken", "files", "files_first")


def _listing(directory, listings=None):
    """The names in a folder, remembered for one pass over the library."""
    if listings is None:
        try:
            return os.listdir(directory)
        except OSError:
            return []
    if directory not in listings:
        try:
            listings[directory] = os.listdir(directory)
        except OSError:
            listings[directory] = []
    return listings[directory]


def _child(directory, name, listings=None):
    """The real name of `name` under `directory`, whatever its case, or None."""
    lowered = name.lower()
    for entry in _listing(directory, listings):
        if entry.lower() == lowered:
            return entry
    return None


def named(directory, stem, listings=None, extensions=None):
    """The prepared file of that name in that folder, whatever its case."""
    wanted = stem.strip().lower()
    found = {}
    for name in _listing(directory, listings):
        base, extension = os.path.splitext(name)
        found.setdefault((base.strip().lower(), extension.lower()), name)
    for extension in (EXTENSIONS if extensions is None else extensions):
        name = found.get((wanted, extension))
        if name:
            return os.path.join(directory, name)
    return None


def levels(track, folder, music_dir, listings=None):
    """(the song's own folder in there, the folders above it), most precise
    first. The song's folder is None when the tree is not reproduced at all."""
    try:
        relative = os.path.relpath(track, music_dir)
    except ValueError:  # different drives, on a machine that has them
        return None, [folder]
    if relative.startswith("..") or os.path.isabs(relative):
        # Not under the music folder: only the folder's own `_any` can speak
        # for it, which is where a single jingle lives.
        return None, [folder]
    parts = [p for p in relative.split(os.sep) if p not in ("", ".")]
    if len(parts) < 2:
        # The song sits in the music folder itself: its file is in this one.
        return folder, [folder]
    above = [folder]
    directory = folder
    for name in parts[:-1]:
        child = _child(directory, name, listings)
        if child is None:
            return None, above[::-1]
        directory = os.path.join(directory, child)
        above.append(directory)
    return directory, above[::-1]


def candidates(track, folder, music_dir, listings=None, extensions=None):
    """Every prepared file that could speak for `track`, most precise first, as a generator so
    a caller can skip one it cannot read."""
    folder = str(folder or "").strip()
    music_dir = str(music_dir or "").strip()
    if not folder or not music_dir or not track:
        return
    own, above = levels(track, folder, music_dir, listings)
    if own:
        found = named(own, os.path.splitext(os.path.basename(track))[0], listings, extensions)
        if found:
            yield found
    for directory in above:
        found = named(directory, ANY, listings, extensions)
        if found:
            yield found


def find(track, folder, music_dir, listings=None, extensions=None):
    """The prepared file for `track`, or None."""
    return next(candidates(track, folder, music_dir, listings, extensions), None)


def scan(tracks, folder, music_dir, extensions=None):
    """(how many prepared files there are, how many of these songs have one)."""
    wanted = EXTENSIONS if extensions is None else extensions
    folder = str(folder or "").strip()
    files = 0
    if folder and os.path.isdir(folder):
        for _root, _dirs, names in os.walk(folder):
            files += sum(1 for name in names
                         if not name.startswith(".")
                         and os.path.splitext(name)[1].lower() in wanted)
    listings = {}
    covered = 0
    for track in tracks:
        if find(track, folder, music_dir, listings, extensions):
            covered += 1
    return files, covered

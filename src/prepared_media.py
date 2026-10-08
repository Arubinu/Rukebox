"""The folders you prepare yourself - cover pictures and prepared
introductions - which mirror the music folder: what the interface browses,
uploads into and deletes from.

Nothing here is about HTTP: the routes in web_server.py hand it a kind, the
configuration and a relative path, and it answers what may be touched. Every
path is checked against the root it belongs to, resolved through symlinks, so
a `..` or a link pointing outside is refused rather than followed."""

import os

import dj_intro
import track_media

# One entry per card. `setting` names the configured folder, `extensions` what
# may be put there - the pictures the cover lookup reads, and the formats the
# prepared introductions are looked for in (the two must agree, or a file the
# interface accepts would never be used).
KINDS = {
    "covers": {"setting": "COVER_DIR", "extensions": track_media.IMAGE_EXTENSIONS},
    "intros": {"setting": "DJ_ANNOUNCE_DIR", "extensions": dj_intro.EXTENSIONS},
}


def spec(kind):
    """That kind's entry, or None for a name the interface does not offer."""
    return KINDS.get(str(kind or "").strip().lower())


def root(kind, cfg):
    """The folder of that kind, or None when the setting is empty."""
    entry = spec(kind)
    if not entry:
        return None
    return str(cfg.get(entry["setting"]) or "").strip() or None


def relative_path(raw):
    """A relative path inside a root: forward slashes, no absolute path, no
    `..`, no hidden part. None when the text is not one."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/") or "\x00" in text:
        return None
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith("."):
            return None
        parts.append(part)
    return "/".join(parts)


def parent_of(relative):
    """The folder above that one. A top-level folder's parent is the root,
    which is the empty path; the root itself has none."""
    text = str(relative or "")
    return text.rsplit("/", 1)[0] if "/" in text else ""


def inside(kind, cfg, relative):
    """(root, folder): the folder `relative` names inside that root, or
    (root, None) when it is not one this kind may touch."""
    top = root(kind, cfg)
    if not top:
        return None, None
    relative = relative_path(relative)
    if relative is None:
        return top, None
    top = os.path.realpath(top)
    path = os.path.realpath(os.path.join(top, relative)) if relative else top
    if path != top and not path.startswith(top.rstrip(os.sep) + os.sep):
        return top, None
    return top, path


def visible_name(raw):
    """One bare, visible name (a file or a folder), or None. A name carrying a
    separator is refused, never shortened: the caller already knows the folder
    it is talking about, so a path here means the client is confused."""
    name = str(raw or "").strip()
    if not name or name in (".", "..") or name.startswith(".") or "\x00" in name:
        return None
    if "/" in name or "\\" in name:
        return None
    return name


def file_name(kind, raw):
    """A name this kind may be given: the file part of what a browser sends,
    visible, and of a format it uses."""
    entry = spec(kind)
    name = visible_name(os.path.basename(str(raw or "").replace("\\", "/")))
    if not entry or not name:
        return None
    if os.path.splitext(name)[1].lower() not in entry["extensions"]:
        return None
    return name


def folder_name(raw):
    """A name a subfolder may be created under: one part, never hidden."""
    return visible_name(raw)


def listing(kind, cfg, relative=""):
    """What a folder of that kind holds, or None when the path is not one of
    its own: its subfolders, and every file, each saying whether that kind
    would use it.

    A root that does not exist yet answers an empty listing rather than an
    error: it is what a first upload creates."""
    entry = spec(kind)
    if not entry:
        return None
    top, folder = inside(kind, cfg, relative)
    if not top or folder is None:
        return None
    if not os.path.isdir(folder):
        if folder == top:
            return {"kind": kind, "root": top, "path": "", "parent": None,
                    "missing": True, "dirs": [], "files": [],
                    "extensions": list(entry["extensions"])}
        return None
    directories, files = [], []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return None
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(folder, name)
        if os.path.isdir(full):
            directories.append({"name": name})
            continue
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        files.append({
            "name": name,
            "size": size,
            "used": os.path.splitext(name)[1].lower() in entry["extensions"],
        })
    return {"kind": kind, "root": top, "path": relative_path(relative) or "",
            "parent": parent_of(relative_path(relative) or "") if relative_path(relative) else None,
            "missing": False, "dirs": directories, "files": files,
            "extensions": list(entry["extensions"])}

"""Identifies the installed version: a hash of the tree, plus git metadata."""

import hashlib
import json
import os
import subprocess
import time

VERSIONED_DIRS = ("src", "scripts", "web", "systemd", "config", "bootstrap", "assets")

IGNORED_DIR_NAMES = {"__pycache__", ".git", ".idea", ".vscode", "node_modules", "graphify-out"}
IGNORED_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", ".log", ".orig")
IGNORED_FILE_NAMES = {".DS_Store", "Thumbs.db"}


def _iter_versioned_files(root):
    for name in VERSIONED_DIRS:
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIR_NAMES)
            for filename in sorted(filenames):
                if filename in IGNORED_FILE_NAMES:
                    continue
                if filename.endswith(IGNORED_SUFFIXES):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                yield rel, full


def compute_tree_hash(root):
    """Digest of the installable source tree."""
    digest = hashlib.sha256()
    count = 0
    for rel, full in _iter_versioned_files(root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        try:
            with open(full, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    file_digest.update(chunk)
        except OSError:
            file_digest.update(b"<unreadable>")
        digest.update(file_digest.digest())
        count += 1
    return digest.hexdigest(), count


def collect_git_info(root):
    """Git metadata if this tree came from a repository."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return {}

    def _git(*args):
        try:
            result = subprocess.run(
                ["git", "-C", root] + list(args),
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {}
    info = {"commit": commit, "short": commit[:12]}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        info["branch"] = branch
    described = _git("describe", "--tags", "--always", "--dirty")
    if described:
        info["describe"] = described
    status = _git("status", "--porcelain")
    if status is not None:
        info["dirty"] = bool(status)
    return info


def describe(root):
    tree_hash, file_count = compute_tree_hash(root)
    return {
        "tree_hash": tree_hash,
        "tree_hash_short": tree_hash[:12],
        "file_count": file_count,
        "git": collect_git_info(root),
    }


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def set_release(version_file, release):
    """Names the installed tree after the release GitHub recognised it as."""
    info = read_version_file(version_file)
    if not info or not release or info.get("release") == release:
        return False
    info["release"] = release
    _write_json(version_file, info)
    return True


def write_version_file(version_file, root, source, extra=None):
    """Records what was just installed."""
    info = describe(root)
    info["installed_at"] = time.time()
    info["source"] = source
    if extra:
        info.update(extra)

    previous = read_version_file(version_file)
    if previous.get("tree_hash"):
        info["previous_tree_hash"] = previous["tree_hash"]
        info["previous_installed_at"] = previous.get("installed_at")

    _write_json(version_file, info)
    return info


def read_version_file(version_file):
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _main(argv):
    """Usage:
    version.py show  <root>
    version.py write <version_file> <root> <source> [release_tag]
    """
    if len(argv) >= 3 and argv[1] == "show":
        print(json.dumps(describe(argv[2]), ensure_ascii=False, indent=2))
        return 0
    if len(argv) >= 5 and argv[1] == "write":
        extra = {"release": argv[5]} if len(argv) >= 6 and argv[5] else None
        print(json.dumps(
            write_version_file(argv[2], argv[3], argv[4], extra), ensure_ascii=False, indent=2,
        ))
        return 0
    print(_main.__doc__)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))

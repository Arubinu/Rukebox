"""Makes the project's modules importable from the tests.

The tests import `src/` directly (no package). RUKEBOX_SRC points them at
another copy - the installed one on a Pi (/opt/rukebox/src), where Flask
is there for the web tests. Imported first by every test module.

It also moves the project's four roots into a throwaway directory, before
config_schema builds its defaults from them. On an installed Pi those roots
name the real configuration and the real speaker, and a test would then move
its volume."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.environ.get("RUKEBOX_SRC") or os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

import paths  # noqa: E402

_SANDBOX = tempfile.mkdtemp(prefix="rukebox-tests-")
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)

# Rebinding the module's roots rather than the environment: the environment is
# also what load_config() reads as an override, and a test that passes its own
# cfg dictionary must keep the last word.
for _name in ("CONFIG_DIR", "STATE_DIR", "MUSIC_DIR"):
    _path = os.path.join(_SANDBOX, _name.lower())
    setattr(paths, "DEFAULT_" + _name, _path)
    os.makedirs(_path, exist_ok=True)

import config_file  # noqa: E402

config_file._refresh_paths(force=True)


def repo_file(*parts):
    """A file of the repository (web/, config/, bootstrap/...). When the
    tests run against an installed copy, its parent directory is used."""
    for base in (ROOT, os.path.dirname(SRC)):
        path = os.path.join(base, *parts)
        if os.path.exists(path):
            return path
    return os.path.join(ROOT, *parts)


def read(*parts):
    with open(repo_file(*parts), encoding="utf-8", newline="") as f:
        return f.read()

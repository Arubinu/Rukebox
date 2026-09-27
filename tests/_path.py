"""Makes the project's modules importable from the tests.

The tests import `src/` directly (no package). RUKEBOX_SRC points them at
another copy - the installed one on a Pi (/opt/rukebox/src), where Flask
is there for the web tests. Imported first by every test module."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.environ.get("RUKEBOX_SRC") or os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


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

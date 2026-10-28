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

# The suite runs as the Pi, which is what the product is: a route or a card
# that only exists there has to be exercised. tests/test_platform.py and the
# container cases in tests/test_web.py force the other platforms on purpose.
os.environ.setdefault("RUKEBOX_PLATFORM", "pi")

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


def document(directory=None):
    """Points the configuration document at a test's own directory, where the daemon reads it too."""
    if directory is None:
        os.environ.pop("RUKEBOX_YAML_FILE", None)
    else:
        os.environ["RUKEBOX_YAML_FILE"] = os.path.join(directory, "rukebox.yaml")
    config_file._refresh_paths(force=True)
    return config_file.find_yaml_file()


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


def app_script():
    """The page's script as the web server serves it, put together from web/js."""
    import web_bundle
    return web_bundle.app_script(repo_file("web"))


# A fade waits for real between its steps: the suite would spend half a minute on them.
import rukebox_daemon  # noqa: E402

rukebox_daemon.RadioDaemon._step_wait = lambda self, seconds: None

# A hash keeps its own count, so a short one still checks; the real count costs a tenth of a second each.
import web_auth  # noqa: E402

web_auth.ITERATIONS = 1000

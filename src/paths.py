"""Where Rukebox keeps its files, one root per kind of content."""
# Read at every call: the tests move a root while the process runs.

import os
import posixpath

# /etc/rukebox on a Pi; sourced from the systemd units as an EnvironmentFile.
DEFAULT_CONFIG_DIR = "/etc/rukebox"
DEFAULT_STATE_DIR = "/var/lib/rukebox"
# The audio tree: music/, memes/, cutoff_announcements/ and system/ under it.
DEFAULT_MUSIC_DIR = "/home/pi/audio"
DEFAULT_INSTALL_DIR = "/opt/rukebox"


def _root(name, default):
    return os.environ.get("RUKEBOX_" + name) or default


def config_dir():
    return _root("CONFIG_DIR", DEFAULT_CONFIG_DIR)


def state_dir():
    return _root("STATE_DIR", DEFAULT_STATE_DIR)


def music_dir():
    return _root("MUSIC_DIR", DEFAULT_MUSIC_DIR)


def install_dir():
    return _root("INSTALL_DIR", DEFAULT_INSTALL_DIR)


def resolve(env, fallback):
    """The value of one config key: RUKEBOX_<KEY> wins, then the fallback."""
    return os.environ.get("RUKEBOX_" + env) or fallback


def under(root, *parts):
    """Joins onto a root, so a default path reads as the root plus its name."""
    # posixpath: these paths reach a Linux YAML even when rendered on Windows.
    return posixpath.join(root, *parts)


def config(*parts):
    return under(config_dir(), *parts)


def state(*parts):
    return under(state_dir(), *parts)


def music(*parts):
    return under(music_dir(), *parts)


def env_file():
    """The generated flat file, beside the YAML it is written from."""
    return os.environ.get("RUKEBOX_ENV_FILE") or config("rukebox.env")


def yaml_candidates():
    """Where the YAML file is looked for, in order."""
    forced = os.environ.get("RUKEBOX_YAML_FILE")
    if forced:
        return [forced]
    return [config("rukebox.yaml"), config("rukebox.yml")]

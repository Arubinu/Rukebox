"""Configuration loading and the cached scan of the music folder."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_schema import DEFAULTS, SETTINGS, coerce  # noqa: E402
import config_file  # noqa: E402

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".wav", ".opus"}

CONFIG_FILE_CANDIDATES = config_file.YAML_FILE_CANDIDATES


def load_config(env_overrides=True):
    """Priority: environment variables already present."""
    raw = dict(DEFAULTS)
    raw.update(config_file.read_values())

    if env_overrides:
        for key in raw:
            if key in os.environ:
                raw[key] = os.environ[key]

    return {setting.env: coerce(setting, raw[setting.env]) for setting in SETTINGS}


def update_config_file(updates: dict, path=None):
    """Saves settings changed from the web interface into rukebox.yaml."""
    return config_file.write_values(updates, path)


def scan_audio_dir(root_dir):
    """Recursive scan (subfolders included) of audio files."""
    results = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for name in filenames:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                results.append(os.path.join(dirpath, name))
    return sorted(results)


def get_music_list(music_dir, cache_file, max_cache_age_sec=3600):
    """Returns the list of tracks, using a JSON cache to avoid rescanning the
    SD card on every restart."""
    dir_mtime = os.path.getmtime(music_dir) if os.path.exists(music_dir) else 0

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cache_fresh = (
                cache.get("music_dir") == music_dir
                and cache.get("dir_mtime") == dir_mtime
                and (time.time() - cache.get("scanned_at", 0)) < max_cache_age_sec
            )
            if cache_fresh:
                return cache["tracks"]
        except (json.JSONDecodeError, KeyError):
            pass

    tracks = scan_audio_dir(music_dir)
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "music_dir": music_dir,
                "dir_mtime": dir_mtime,
                "scanned_at": time.time(),
                "tracks": tracks,
            },
            f,
        )
    return tracks


def force_rescan(cache_file):
    """Call after adding new tracks, to force an immediate rescan on the next
    access instead of waiting for the cache to expire."""
    if os.path.exists(cache_file):
        os.remove(cache_file)

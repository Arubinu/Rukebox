"""A person's own recording of their name, said by the blind test in place of the synthetic voice."""

import os
import re
import shutil
import subprocess
import wave

MAX_SEC = 6.0
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
# Silence trimmed at both ends (the filter only trims the start, hence the reversals), then levelled.
_CHAIN = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
          "loudnorm=I=-16:TP=-1.5,aresample=44100")


def folder(state_dir):
    return os.path.join(state_dir, "name-voices")


def path(state_dir, person):
    """Where `person`'s recording lives, or None for an id that cannot name a file."""
    safe = _UNSAFE.sub("", str(person or ""))[:64]
    return os.path.join(folder(state_dir), safe + ".wav") if safe else None


def existing(state_dir, person):
    found = path(state_dir, person)
    return found if found and os.path.isfile(found) else None


def seconds(wav_path):
    try:
        with wave.open(wav_path, "rb") as f:
            return f.getnframes() / float(f.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


def prepare(source, target):
    """`source` (any audio ffmpeg reads) as a trimmed, levelled mono WAV at `target`: its
    length in seconds, or None when it could not be made."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp.wav"
    try:
        done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", source, "-af", _CHAIN,
                               "-ac", "1", tmp], capture_output=True, timeout=60)
        length = seconds(tmp) if done.returncode == 0 else None
        if not length:
            return None
        os.replace(tmp, target)
        return length
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def remove(state_dir, person):
    found = existing(state_dir, person)
    if found:
        os.remove(found)
    return bool(found)


def carry(state_dir, was, now, keep=False):
    """`was`'s recording goes to `now` when `now` has none: linking devices keeps it; with
    `keep`, both have it (unlinking)."""
    src = existing(state_dir, was)
    dst = path(state_dir, now)
    if not src or not dst or was == now or os.path.isfile(dst):
        return False
    if keep:
        shutil.copyfile(src, dst)
    else:
        os.replace(src, dst)
    return True

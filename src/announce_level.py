"""Announcement files brought to the music's loudness: measured once, remembered per file version."""

import json
import logging
import os
import re
import subprocess
import threading

log = logging.getLogger("radio")

_INTEGRATED_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", re.MULTILINE)
MAX_BOOST_DB = 12.0
HEADROOM_DB = -1.0
MEASURE_TIMEOUT_SEC = 60
SOUND_EXTENSIONS = (".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma")


def measure(path):
    """(integrated loudness in LUFS, sample peak in dBFS or None), or None for silence or a failure."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner", "-i", path, "-map", "0:a:0",
           "-af", "ebur128=peak=sample", "-f", "null", "-"]
    if os.name == "posix":
        cmd = ["nice", "-n", "19"] + cmd
    try:
        err = subprocess.run(cmd, capture_output=True, timeout=MEASURE_TIMEOUT_SEC).stderr
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = err.decode("utf-8", errors="replace")
    loud = _INTEGRATED_RE.findall(text)
    if not loud or float(loud[-1]) <= -70:
        return None
    peaks = _PEAK_RE.findall(text)
    peak = float(peaks[-1]) if peaks and peaks[-1] != "-inf" else None
    return float(loud[-1]), peak


def gain_for(level, target):
    """The dB a file needs to reach `target` LUFS, never pushing its peak past the headroom."""
    if not level:
        return 0.0
    loudness, peak = level
    gain = min(MAX_BOOST_DB, target - loudness)
    if peak is not None:
        gain = min(gain, HEADROOM_DB - peak)
    return round(gain, 1)


class Levels:
    """The measures, kept in a JSON file as {path: [size, mtime, lufs, peak]}: the web
    server's library pass writes it, the radio reads it (and fills in a file it lacks)."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._pending = set()
        self._data = {}
        self._loaded = None
        self._refresh()

    def _refresh(self):
        """Re-reads the file when the other process wrote it."""
        stamp = self._stamp(self._path)
        if stamp == self._loaded:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        with self._lock:
            self._data = data if isinstance(data, dict) else {}
            self._loaded = stamp

    @staticmethod
    def _stamp(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return [st.st_size, st.st_mtime_ns]

    @staticmethod
    def _version(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return [st.st_size, int(st.st_mtime)]

    def _entry(self, path):
        version = self._version(path)
        with self._lock:
            entry = self._data.get(path)
        return entry if version and entry and entry[:2] == version else None

    def known(self, path):
        """The (lufs, peak) measured for this version of the file, or None."""
        self._refresh()
        entry = self._entry(path)
        return (entry[2], entry[3]) if entry and entry[2] is not None else None

    def measure(self, path):
        """known(), measuring the file first when it changed or was never measured."""
        self._refresh()
        if self._version(path) and not self._entry(path):
            level = measure(path)
            version = self._version(path)
            if version:
                # A failure is written down too, or it would be measured again at every play.
                self._write({path: version + (list(level) if level else [None, None])})
        return self.known(path)

    def measure_later(self, path):
        """measure() in the background, once at a time per file."""
        with self._lock:
            if path in self._pending:
                return
            self._pending.add(path)

        def run():
            try:
                self.measure(path)
            finally:
                with self._lock:
                    self._pending.discard(path)

        threading.Thread(target=run, daemon=True).start()

    def warm(self, folders, keep_going=lambda: True):
        """Measures every sound in `folders` not measured yet, and forgets files gone from them."""
        seen = set()
        for folder in folders:
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                if not keep_going():
                    return
                if name.lower().endswith(SOUND_EXTENSIONS):
                    path = os.path.join(folder, name)
                    seen.add(path)
                    self.measure(path)
        roots = tuple(os.path.join(f, "") for f in folders)
        with self._lock:
            gone = [p for p in self._data if p.startswith(roots) and p not in seen]
        if gone:
            self._write(dict.fromkeys(gone))

    def _write(self, changes):
        """Applies `changes` (None removes a file) to what is on disk now, then saves."""
        self._loaded = None
        self._refresh()
        with self._lock:
            for path, entry in changes.items():
                if entry is None:
                    self._data.pop(path, None)
                else:
                    self._data[path] = entry
            data = json.dumps(self._data)
        tmp = "%s.%d.tmp" % (self._path, os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("Could not save the announcement levels: %s", exc)

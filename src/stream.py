"""The network output: what the radio plays, encoded and served over HTTP.

This is the only way to hear a Rukebox in a container - there is no sound card
to give it - and it is the fallback of any server without one. The radio keeps
playing into a virtual sink of its own; this module encodes that sink's monitor
with ffmpeg and serves the result on the web port, so the same page that
controls the radio can listen to it, and so can VLC.

One encoder only, however many listeners: ffmpeg is started on the first
listener and kept running afterwards, its output going nowhere when nobody is
connected. That is what keeps a second listener from waiting for one, and it
costs a process that a Pi Zero can afford because the feature is off by
default there."""

import logging
import os
import queue
import subprocess
import threading
import time

log = logging.getLogger("stream")

# What the container image installs, best first: Opus is what a browser plays
# without a plugin and what the bandwidth of a Pi Zero can carry.
ENCODERS = {
    "opus": {
        "content_type": "audio/ogg",
        "suffix": "opus",
        "args": ["-c:a", "libopus", "-b:a", "128k", "-f", "ogg"],
    },
    "mp3": {
        "content_type": "audio/mpeg",
        "suffix": "mp3",
        "args": ["-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"],
    },
    "aac": {
        "content_type": "audio/aac",
        "suffix": "aac",
        "args": ["-c:a", "aac", "-b:a", "192k", "-f", "adts"],
    },
    "vorbis": {
        "content_type": "audio/ogg",
        "suffix": "ogg",
        "args": ["-c:a", "libvorbis", "-b:a", "192k", "-f", "ogg"],
    },
}

PREFERENCE = ("opus", "mp3", "aac", "vorbis")

# A listener that falls this far behind is following the past: the oldest
# chunks go, which is what keeps the stream live rather than a slow playback.
QUEUE_CHUNKS = 32
CHUNK_BYTES = 4096
# How long a listener waits before checking the encoder is still there.
CHUNK_WAIT_SEC = 5.0
# An ffmpeg that dies at once - no PipeWire, no such sink - must not be
# restarted for ever: after this many tries in a row the stream gives up and
# says why.
MAX_RESTARTS = 5
RESTART_DELAY_SEC = 1.0


def probe_source(env=None, timeout=6):
    """The monitor to encode, or "" when this machine has no sound server.

    `pactl` first - it is the compatibility layer of PipeWire, and it has been
    the one that answers everywhere this was tried, including inside the
    container where `pw-dump` cannot reach the daemon at all. `pw-dump` second,
    for a machine running bare PipeWire with no Pulse layer at all.

    The default output's own monitor first, then any monitor there is."""
    from_pactl = _probe_with_pactl(env, timeout)
    if from_pactl is not None:
        return from_pactl
    return _probe_with_pw_dump(env, timeout)


def _probe_with_pactl(env, timeout):
    """("" when there is no monitor) or None when pactl is not usable."""
    try:
        done = subprocess.run(["pactl", "list", "short", "sources"],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    monitors = []
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].endswith(".monitor"):
            monitors.append(parts[1])
    if not monitors:
        return ""
    try:
        default = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                                 text=True, timeout=timeout, env=env)
        name = (default.stdout or "").strip().splitlines()
        if name:
            wanted = name[0].strip() + ".monitor"
            if wanted in monitors:
                return wanted
    except (OSError, subprocess.SubprocessError):
        pass
    return monitors[0]


def _probe_with_pw_dump(env, timeout):
    import json

    try:
        done = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=timeout, env=env)
        dump = json.loads(done.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    sinks, monitors = [], []
    for obj in dump if isinstance(dump, list) else []:
        props = ((obj or {}).get("info") or {}).get("props") or {}
        name = props.get("node.name") or ""
        if not name:
            continue
        if props.get("media.class") == "Audio/Sink":
            sinks.append(name)
        elif props.get("media.class") == "Audio/Source" and name.endswith(".monitor"):
            monitors.append(name)
    for sink in sinks:
        if sink + ".monitor" in monitors:
            return sink + ".monitor"
    return monitors[0] if monitors else ""


def encoders_available(ffmpeg="ffmpeg", timeout=10):
    """Which of ENCODERS this ffmpeg can actually encode, best first."""
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    text = done.stdout or ""
    names = {"opus": "libopus", "mp3": "libmp3lame", "aac": "aac",
             "vorbis": "libvorbis"}
    return [key for key in PREFERENCE if names[key] in text]


class StreamServer:
    """Encodes one audio source and hands the bytes to every listener.

    `source` is an empty string when nothing could be found: the stream then
    stays "unavailable" and the interface says so rather than offering a
    button that plays silence."""

    def __init__(self, source, encoder, ffmpeg="ffmpeg", env=None,
                 queue_chunks=QUEUE_CHUNKS, on_stop=None):
        self.source = source
        self.encoder = encoder
        self.ffmpeg = ffmpeg
        self.env = env
        self.queue_chunks = queue_chunks
        self._on_stop = on_stop
        self._lock = threading.Lock()
        self._listeners = []
        self._process = None
        self._thread = None
        self._stopping = False
        self._restarts = 0
        self._gave_up = False
        self.last_error = ""

    @property
    def content_type(self):
        return ENCODERS[self.encoder]["content_type"]

    @property
    def suffix(self):
        return ENCODERS[self.encoder]["suffix"]

    def command(self):
        return [self.ffmpeg, "-hide_banner", "-loglevel", "warning",
                "-f", "pulse", "-i", self.source, "-vn"] + \
            ENCODERS[self.encoder]["args"] + ["pipe:1"]

    def listener_count(self):
        with self._lock:
            return len(self._listeners)

    def start(self):
        """Starts the encoder now, so the first listener does not wait for one."""
        if not self.source or self._gave_up:
            return False
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            try:
                self._process = subprocess.Popen(
                    self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, env=self.env)
            except OSError as error:
                log.exception("Could not start ffmpeg for the network stream")
                self.last_error = str(error)
                self._process = None
                return False
            self._thread = threading.Thread(target=self._pump, name="stream-encode",
                                            daemon=True)
            self._thread.start()
        log.info("Network stream started: %s -> %s", self.source, self.encoder)
        return True

    def stop(self):
        self._stopping = True
        with self._lock:
            process, self._process = self._process, None
            listeners, self._listeners = self._listeners, []
        for box in listeners:
            box.put_nowait(None)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if self._on_stop:
            try:
                self._on_stop()
            except Exception:  # noqa: BLE001
                log.exception("The stream's stop hook failed")

    def alive(self):
        with self._lock:
            return bool(self._process is not None and self._process.poll() is None)

    def encoder_stderr(self):
        process = self._process
        if process is None or process.stderr is None:
            return ""
        try:
            return (process.stderr.read() or b"").decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def _pump(self):
        """Reads ffmpeg's output and gives it to every listener."""
        process = self._process
        if process is None or process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            self._broadcast(chunk)
        self._broadcast(None)
        with self._lock:
            self._process = None
        self.last_error = self.encoder_stderr()
        if not self._stopping:
            log.warning("The network stream encoder stopped: %s",
                        self.last_error or "no message")
            self._on_stopped_unexpectedly()

    def _on_stopped_unexpectedly(self):
        """A speaker or a sink that comes and goes must not end the stream -
        but an encoder that cannot start at all must not be restarted for ever."""
        if self._stopping:
            return
        if self._restarts >= MAX_RESTARTS:
            log.error("The network stream gave up after %s attempts: %s",
                      self._restarts, self.last_error or "no message")
            self._gave_up = True
            self._broadcast(None)
            return
        self._restarts += 1
        log.info("Restarting the network stream (attempt %s)", self._restarts)
        time.sleep(RESTART_DELAY_SEC)
        self.start()

    def _broadcast(self, chunk):
        with self._lock:
            listeners = list(self._listeners)
        for box in listeners:
            try:
                box.put_nowait(chunk)
            except queue.Full:
                try:
                    box.get_nowait()
                    box.put_nowait(chunk)
                except (queue.Empty, queue.Full):
                    pass

    def listen(self):
        """One listener: a queue of bytes, ending with None when the encoder
        goes away - which is what tells a client to reconnect."""
        box = queue.Queue(maxsize=self.queue_chunks)
        with self._lock:
            self._listeners.append(box)
        if not self.alive():
            self.start()
        return box

    def forget(self, box):
        with self._lock:
            if box in self._listeners:
                self._listeners.remove(box)

    def chunks(self, wait=CHUNK_WAIT_SEC):
        """The bytes for one listener, as a generator an HTTP response writes.

        The wait is bounded: an encoder that dies without closing its pipe
        must end the response rather than leave a thread parked on an empty
        queue for ever."""
        box = self.listen()
        try:
            while True:
                try:
                    chunk = box.get(timeout=wait)
                except queue.Empty:
                    if not self.alive():
                        return
                    continue
                if chunk is None:
                    return
                yield chunk
        finally:
            self.forget(box)


def build(cfg, probe=True):
    """The stream this configuration asks for, or None when it is off.

    Returns a StreamServer whether or not the machine can encode: `source` is
    empty when it cannot, and the interface reports that instead of offering
    silence."""
    if not cfg.get("STREAM_ENABLED"):
        return None
    source = cfg.get("STREAM_SOURCE") or ""
    if not source and probe:
        source = probe_source(env=audio_env())
    wanted = (cfg.get("STREAM_ENCODER") or "").strip().lower()
    available = encoders_available()
    if wanted in available:
        chosen = wanted
    elif available:
        chosen = available[0]
    elif wanted in ENCODERS:
        chosen = wanted
    else:
        chosen = "opus"
    return StreamServer(source, chosen, env=audio_env())


def audio_env():
    """The environment mpv gets: the same PipeWire socket, wherever it is."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % uid()
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("PIPEWIRE_RUNTIME_DIR", runtime)
    return env


def uid():
    """This user's id, or 0 where there is no such thing (a development
    machine): the caller only uses it to build a path nothing looks at."""
    return getattr(os, "getuid", lambda: 0)()


def status(server, url=""):
    """What /api/stream answers: enough for the page to offer or refuse."""
    if server is None:
        return {"enabled": False, "available": False, "url": "", "encoder": "",
                "content_type": "", "listeners": 0, "source": ""}
    return {
        "enabled": True,
        "available": bool(server.source),
        "url": url if server.source else "",
        "encoder": server.encoder if server.source else "",
        "content_type": server.content_type if server.source else "",
        "listeners": server.listener_count(),
        "source": server.source,
    }

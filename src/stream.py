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

import audio_output

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
# The headers of an Ogg stream, kept for a listener that arrives late, and the
# point past which they are given up on as "not a header after all".
HEADER_MAX_BYTES = 16384
# How long a listener waits before checking the encoder is still there.
CHUNK_WAIT_SEC = 5.0
# An ffmpeg that dies at once - no PipeWire, no such sink - must not be
# restarted for ever: after this many tries in a row the stream gives up and
# says why.
MAX_RESTARTS = 5
RESTART_DELAY_SEC = 1.0


def probe_source(env=None, timeout=6, kind=None):
    """The monitor to encode, or "" when this machine has no sound server.

    `pactl` first - it is the compatibility layer of PipeWire, and it has been
    the one that answers everywhere this was tried, including inside the
    container where `pw-dump` cannot reach the daemon at all. `pw-dump` second,
    for a machine running bare PipeWire with no Pulse layer at all (a Pi
    without pipewire-pulse, where pw-dump works and pactl is not installed).

    The output the radio plays to first: for a wired output mpv is pointed at
    that sink by name, so the default sink's monitor would carry silence. Then
    the default output's own monitor, then any monitor there is."""
    chosen = _chosen_monitor(kind, env, timeout)
    if chosen:
        return chosen
    from_pactl = _probe_with_pactl(env, timeout)
    if from_pactl is not None:
        return from_pactl
    return _probe_with_pw_dump(env, timeout)


def _chosen_monitor(kind, env, timeout):
    """The monitor of the sink this output kind names, or "".

    Bluetooth is left out on purpose: mpv follows the default sink there, so
    the default monitor is the right one and this would only guess."""
    if not kind or kind == "bluetooth":
        return ""
    monitors = _monitors_from_pactl(env, timeout)
    if not monitors:
        return ""
    for name in _sinks_from_pactl(env, timeout):
        if audio_output.classify({"node.name": name}) == kind:
            wanted = name + ".monitor"
            if wanted in monitors:
                return wanted
    return ""


def why_unavailable(env=None):
    """A code saying why there is no monitor, for the interface to show.

    "Nothing plays over the network" has several very different causes, and
    they are not diagnosable from a browser: the stream can be off, the sound
    server can be absent, or it can have no source to encode (a container
    without its virtual sink). Each gets its own code, so the page can say
    which one it is instead of leaving a silent button."""
    if not _has_program("pw-dump") and not _has_program("pactl"):
        return "no_tools"
    if not _has_program("ffmpeg"):
        return "no_ffmpeg"
    sources = _all_sources(env)
    if sources is None:
        return "no_sound_server"
    if not sources:
        return "no_source"
    return "unknown"


def _has_program(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory and os.path.exists(os.path.join(directory, name)):
            return True
    return False


def _all_sources(env=None):
    """What can be encoded, or None when the sound server cannot be asked.

    A monitor source when PipeWire advertises one, and the default sink's name
    plus `.monitor` otherwise: PipeWire lets a capture client attach that way
    even when the monitor node is not listed, which is what makes this work on
    a machine where nothing has listed the sources yet (a Pi whose pactl was
    just installed, before anything has asked)."""
    from_pactl = _monitors_from_pactl(env, 6)
    if from_pactl is not None:
        return from_pactl or _sinks_from_pactl(env, 6)
    dump = _pw_dump(env, 6)
    if dump is None:
        return None
    monitors, sinks = _nodes_from_dump(dump)
    return monitors or sinks


def _sinks_from_pactl(env, timeout=6):
    try:
        done = subprocess.run(["pactl", "list", "short", "sinks"],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return [line.split()[1] for line in (done.stdout or "").splitlines()
            if len(line.split()) >= 2]


def _probe_with_pactl(env, timeout):
    """("" when there is no monitor) or None when pactl is not usable."""
    monitors = _monitors_from_pactl(env, timeout)
    if monitors is None:
        return None
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


def _monitors_from_pactl(env, timeout=6):
    """["sink.monitor", ...] or None when pactl cannot be asked."""
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
    return monitors


def _probe_with_pw_dump(env, timeout):
    dump = _pw_dump(env, timeout)
    if dump is None:
        return ""
    monitors, sinks = _nodes_from_dump(dump)
    for sink in sinks:
        if sink + ".monitor" in monitors:
            return sink + ".monitor"
    if monitors:
        return monitors[0]
    # Nothing advertises a monitor: PipeWire still lets a capture client
    # attach to the sink's own monitor by name, which is what the stream needs.
    return (sinks[0] + ".monitor") if sinks else ""


def _monitors_from_pw_dump(env, timeout=6):
    """["sink.monitor", ...] or None when pw-dump cannot be asked."""
    dump = _pw_dump(env, timeout)
    if dump is None:
        return None
    return _nodes_from_dump(dump)[0]


def _nodes_from_dump(dump):
    """(monitors, sinks): one pass in one dump, never two `pw-dump` calls."""
    monitors, sinks = [], []
    for props in _node_props(dump):
        name = props["node.name"]
        if props.get("media.class") == "Audio/Sink":
            sinks.append(name)
        elif props.get("media.class") == "Audio/Source" and name.endswith(".monitor"):
            monitors.append(name)
    return monitors, sinks


def _pw_dump(env, timeout=6):
    import json

    try:
        done = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=timeout, env=env)
        return json.loads(done.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _node_props(dump):
    for obj in dump if isinstance(dump, list) else []:
        props = ((obj or {}).get("info") or {}).get("props") or {}
        if props.get("node.name"):
            yield props


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


def _ogg_header_length(data):
    """How many bytes of an Ogg stream are its headers.

    None while they are not all there yet, and 0 for a stream that is not Ogg
    at all (MP3, AAC): those have nothing a late listener needs first. The
    headers are the leading pages whose granule position is 0, which is the
    identification and comment pages of every Ogg codec ffmpeg writes here."""
    if len(data) < 4:
        return None
    if not data.startswith(b"OggS"):
        return 0
    offset = 0
    while True:
        if offset >= len(data):
            return None
        if data[offset:offset + 4] != b"OggS":
            return 0
        if len(data) < offset + 27:
            return None
        segments = data[offset + 26]
        head = 27 + segments
        if len(data) < offset + head:
            return None
        total = head + sum(data[offset + 27:offset + head])
        if len(data) < offset + total:
            return None
        if int.from_bytes(data[offset + 6:offset + 14], "little") != 0:
            return offset
        offset += total


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
        self.header = b""
        self._unparsed = b""
        self._header_read = False

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
            # A new encoder writes new headers: the ones kept for late
            # listeners belong to the process before.
            self.header = b""
            self._unparsed = b""
            self._header_read = False
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
        if chunk:
            self._remember_header(chunk)
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

    def _remember_header(self, chunk):
        """Keeps the stream's own beginning for whoever arrives later.

        An Ogg stream without its headers cannot be decoded at all, so a
        listener that joins a minute in would hear nothing (that is what VLC
        reports as "couldn't find any ogg logical stream"); the headers are
        replayed to it before the live bytes."""
        if self._header_read:
            return
        self._unparsed += chunk
        length = _ogg_header_length(self._unparsed)
        if length is None and len(self._unparsed) < HEADER_MAX_BYTES:
            return
        self.header = self._unparsed if length is None else self._unparsed[:length]
        self._header_read = True
        self._unparsed = b""

    def listen(self):
        """One listener: a queue of bytes, ending with None when the encoder
        goes away - which is what tells a client to reconnect."""
        box = queue.Queue(maxsize=self.queue_chunks)
        with self._lock:
            header = self.header
            self._listeners.append(box)
        if header:
            box.put_nowait(header)
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
        source = probe_source(env=audio_env(), kind=cfg.get("AUDIO_OUTPUT"))
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
    """What /api/stream answers: enough for the page to offer or refuse.

    `why` is the code saying what is missing, and it is only filled when
    something IS missing: an interface that offers a button leading nowhere
    has to say which of the causes it is."""
    if server is None:
        return {"enabled": False, "available": False, "url": "", "encoder": "",
                "content_type": "", "listeners": 0, "source": "",
                "why": "off"}
    available = bool(server.source)
    return {
        "enabled": True,
        "available": available,
        "url": url if available else "",
        "encoder": server.encoder if available else "",
        "content_type": server.content_type if available else "",
        "listeners": server.listener_count(),
        "source": server.source,
        "why": "" if available else "no_source",
    }

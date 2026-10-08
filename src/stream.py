"""The network output: what the radio plays, encoded and served over HTTP."""

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

# Generous: an Ogg page cannot be cut in half, so a listener this far behind is
# let go and reconnects.
QUEUE_CHUNKS = 256
CHUNK_BYTES = 4096
# The headers of an Ogg stream, kept for a listener that arrives late, and the
# point past which they are given up on as "not a header after all".
HEADER_MAX_BYTES = 16384
# How long a listener waits before checking the encoder is still there.
CHUNK_WAIT_SEC = 5.0
# An encoder that dies at once (no PipeWire, no sink) is not restarted for ever.
MAX_RESTARTS = 5
RESTART_DELAY_SEC = 1.0
# Older than this, an encoder hands the first listener a timeline that starts
# late, and the player waits (see restart()).
FRESH_START_SEC = 2.0
# How long the listeners kept over a replacement wait for its first bytes
# before they are let go on without them.
AWAIT_MAX_SEC = 5.0
# The stream's own volume, in percent of what the radio is playing: 100 leaves
# it alone, and anything above boosts it, which is why there is a ceiling.
DEFAULT_VOLUME = 100
MAX_VOLUME = 200
# What the stream calls itself when nothing named it: a player that reads the
# stream's own metadata has to show something.
DEFAULT_TITLE = "Rukebox"


def probe_source(env=None, timeout=6, kind=None):
    """The monitor to encode, or "" when this machine has no sound server."""
    # pactl first: inside a container pw-dump cannot reach the daemon.
    chosen = _chosen_monitor(kind, env, timeout)
    if chosen:
        return chosen
    from_pactl = _probe_with_pactl(env, timeout)
    if from_pactl is not None:
        return from_pactl
    return _probe_with_pw_dump(env, timeout)


def _chosen_monitor(kind, env, timeout):
    """The monitor of the sink this output kind names, or ""."""
    # Not Bluetooth: mpv follows the default sink there.
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
    """A code saying why there is no monitor, for the interface to show."""
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
    """What can be encoded, or None when the sound server cannot be asked."""
    # `<sink>.monitor` works even when no monitor node is listed.
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
    """How many bytes of an Ogg stream are its headers."""
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


def _stderr_of(process):
    """What an ffmpeg of ours said, or "" when it said nothing."""
    if process is None or process.stderr is None:
        return ""
    try:
        return (process.stderr.read() or b"").decode("utf-8", "replace").strip()
    except OSError:
        return ""


class StreamServer:
    """Encodes one audio source and hands the bytes to every listener."""

    def __init__(self, source, encoder, ffmpeg="ffmpeg", env=None,
                 queue_chunks=QUEUE_CHUNKS, on_stop=None,
                 volume=DEFAULT_VOLUME, title=DEFAULT_TITLE):
        self.source = source
        self.encoder = encoder
        self.ffmpeg = ffmpeg
        self.env = env
        self.queue_chunks = queue_chunks
        self._on_stop = on_stop
        self.volume = clamp_volume(volume)
        self.title = str(title or "").strip() or DEFAULT_TITLE
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
        self._unaligned = {}
        self._awaiting = set()
        self._awaiting_gen = None
        self._awaiting_since = None
        self._generation = 0
        self._started_at = 0.0
        self._last_chunk_at = None

    @property
    def content_type(self):
        return ENCODERS[self.encoder]["content_type"]

    @property
    def suffix(self):
        return ENCODERS[self.encoder]["suffix"]

    def command(self):
        tuning = []
        if self.volume != DEFAULT_VOLUME:
            tuning = ["-af", "volume=%.3f" % (self.volume / 100.0)]
        return [self.ffmpeg, "-hide_banner", "-loglevel", "warning",
                "-f", "pulse", "-i", self.source, "-vn"] + tuning + \
            ["-metadata", "title=" + self.title] + \
            ENCODERS[self.encoder]["args"] + ["pipe:1"]

    def retune(self, volume=None, title=None):
        """The stream's own volume and title, from now on: the listeners are
        kept over it, they are listening right now."""
        changed = False
        if volume is not None:
            volume = clamp_volume(volume)
            if volume != self.volume:
                self.volume = volume
                changed = True
        if title is not None:
            title = str(title or "").strip() or DEFAULT_TITLE
            if title != self.title:
                self.title = title
                changed = True
        if changed and self.alive():
            self.restart()
        return changed

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
            self._generation += 1
            # Whoever was kept over a replacement is now waiting for THIS
            # encoder's own beginning (see restart()).
            self._awaiting_gen = self._generation
            self._started_at = time.monotonic()
            self._last_chunk_at = None
            try:
                self._process = process = subprocess.Popen(
                    self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, env=self.env)
            except OSError as error:
                log.exception("Could not start ffmpeg for the network stream")
                self.last_error = str(error)
                self._process = None
                return False
            self._thread = threading.Thread(target=self._pump,
                                            args=(process, self._generation),
                                            name="stream-encode", daemon=True)
            self._thread.start()
        log.info("Network stream started: %s -> %s", self.source, self.encoder)
        return True

    def restart(self):
        """Replaces the encoder without dropping the listeners."""
        # A late listener would get granule-0 headers then the encoder's uptime, and its player waits.
        with self._lock:
            process, self._process = self._process, None
            # Whatever the process before still has buffered is stale from here
            # on, and must not reach the listeners nor the header capture.
            self.header = b""
            self._unparsed = b""
            self._header_read = False
            # The listeners are kept, and hold until the new encoder writes its
            # first bytes: those bytes ARE the new stream's beginning.
            self._awaiting = set(self._listeners)
            self._awaiting_since = time.monotonic()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        return self.start()

    def worth_restarting(self):
        """True when a listener would be handed a stream that does not begin at zero, and
        starting the encoder again is what fixes that."""
        # Never on a quiet radio: a new encoder writes nothing until there is sound.
        with self._lock:
            started = self._started_at
            last = self._last_chunk_at
        if not started or (time.monotonic() - started) <= FRESH_START_SEC:
            return False
        return bool(last) and (time.monotonic() - last) < FRESH_START_SEC

    def stalled_for(self):
        """Seconds since the encoder last produced anything."""
        # A capture attached to a silent sink can stay silent for ever.
        with self._lock:
            started = self._started_at
            last = self._last_chunk_at
        since = last or started
        if not since:
            return 0.0
        return time.monotonic() - since

    def stop(self):
        self._stopping = True
        with self._lock:
            process, self._process = self._process, None
            self._generation += 1
            listeners, self._listeners = self._listeners, []
            self._awaiting, self._awaiting_gen = set(), None
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
        return _stderr_of(self._process)

    def _pump(self, process, generation):
        """Reads ffmpeg's output and gives it to every listener."""
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            with self._lock:
                if self._process is not process:
                    # A replaced encoder's leftovers would be taken for the new
                    # stream's headers.
                    break
            self._broadcast(chunk, generation)
        with self._lock:
            mine = self._process is process
            if mine:
                self._process = None
        if not mine:
            # This encoder was replaced on purpose (restart()): the stream goes
            # on, and the listeners are not this process's to end.
            return
        self._broadcast(None)
        self.last_error = _stderr_of(process)
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

    def _broadcast(self, chunk, generation=None):
        with self._lock:
            generation = self._generation if generation is None else generation
            if generation == self._awaiting_gen:
                # This IS the new stream's first byte: nothing to replay.
                self._awaiting, self._awaiting_gen = set(), None
            # Read before the hand-off above would matter: a listener given the
            # new stream by this very chunk keeps it whole.
            holding = set(self._awaiting)
        if chunk:
            with self._lock:
                self._last_chunk_at = time.monotonic()
            self._remember_header(chunk)
        with self._lock:
            listeners = list(self._listeners)
        for box in listeners:
            if box in holding:
                continue
            data = chunk
            if chunk and box in self._unaligned:
                data = self._align(box, chunk)
                if data is None:
                    continue
            try:
                box.put_nowait(data)
            except queue.Full:
                # Dropping bytes would cut an Ogg page (CRC mismatch): the listener
                # is let go and reconnects, headers included.
                try:
                    while True:
                        box.get_nowait()
                except queue.Empty:
                    pass
                try:
                    box.put_nowait(None)
                except queue.Full:
                    pass

    def _remember_header(self, chunk):
        """Keeps the stream's own beginning for whoever arrives later."""
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
            first = not self._listeners
            header = self.header
            self._listeners.append(box)
        if first and self.worth_restarting():
            self.restart()
            return box
        if header:
            # The header pages are replayed, so the live stream has to be picked
            # up at the next page: see _align().
            box.put_nowait(header)
            with self._lock:
                self._unaligned[box] = b""
        if not self.alive():
            self.start()
        return box

    def forget(self, box):
        with self._lock:
            if box in self._listeners:
                self._listeners.remove(box)
            self._unaligned.pop(box, None)
            self._awaiting.discard(box)

    def _align(self, box, chunk):
        """The bytes from the next page start, or None while there is none."""
        # Half a page is a CRC mismatch, and the player decodes nothing.
        carry = self._unaligned.get(box, b"") + chunk
        index = carry.find(b"OggS")
        if index < 0:
            self._unaligned[box] = carry[-3:]
            return None
        self._unaligned.pop(box, None)
        return carry[index:]

    def chunks(self, wait=CHUNK_WAIT_SEC):
        """The bytes for one listener, as a generator an HTTP response writes."""
        box = self.listen()
        try:
            while True:
                try:
                    chunk = box.get(timeout=wait)
                except queue.Empty:
                    if not self.alive() or self.awaiting_too_long():
                        return
                    continue
                if chunk is None:
                    return
                yield chunk
        finally:
            self.forget(box)

    def awaiting_too_long(self):
        """True when the listeners kept over a replacement have been held long
        enough: an encoder that stays silent through it is one whose bytes are
        never coming, and a player waiting on a held queue would wait for ever."""
        with self._lock:
            if not self._awaiting or not self._awaiting_since:
                return False
            waited = time.monotonic() - self._awaiting_since
            if waited < AWAIT_MAX_SEC:
                return False
            # They carry on with whatever the stream has: no worse than the
            # encoder that went quiet under them.
            self._awaiting, self._awaiting_gen = set(), None
        log.warning("A replaced encoder wrote nothing for %.0fs: letting the "
                    "listeners go on without its beginning", waited)
        return True


def build(cfg, probe=True):
    """The stream this configuration asks for, or None when it is off."""
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
    return StreamServer(source, chosen, env=audio_env(),
                        volume=stream_volume(cfg), title=stream_title(cfg))


def stream_volume(cfg):
    """The stream's own volume, in percent, from a configuration."""
    return clamp_volume(cfg.get("STREAM_VOLUME"))


def clamp_volume(volume):
    """A volume in percent that the encoder can be given, 0 to MAX_VOLUME."""
    try:
        percent = int(round(float(volume)))
    except (TypeError, ValueError):
        return DEFAULT_VOLUME
    return max(0, min(MAX_VOLUME, percent))


def stream_title(cfg):
    """What the stream calls itself: the radio's own name on the network."""
    return str(cfg.get("UPNP_NAME") or "").strip() or DEFAULT_TITLE


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

"""WLED light strips: scenes that follow the radio, its beat, and the clock."""

import cmath
import json
import logging
import math
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger("wled")

TIMEOUT = 2.0
SCENES = ("start", "play", "pause", "idle", "announce", "cutoff", "game", "button", "alert")
FLASHES = ("button", "alert", "cutoff")
OFF = -1
FLASH_SEC = 3.0
# WLED rounds every timezone it knows to a quarter of an hour.
TZ_STEP = 900
EARLIEST_YEAR = 2025

SYNC_GROUP = "239.0.0.1"
SYNC_PORT = 11988
SYNC_HEADER = b"00002\x00"
SYNC_PACKET = struct.Struct("<6s2xffBx16s2xff")
RATE = 16000
WINDOW = 256
FPS = 25
# The edges of WLED's own sixteen channels, in Hz.
BAND_EDGES = (43, 86, 129, 216, 301, 430, 560, 818, 1077, 1421, 1895, 2412, 3015, 3704,
              4479, 7106, 8000)

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")
_TIME_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2}),\s*(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?", re.I)


def hosts(cfg):
    """The addresses WLED_HOSTS names, the malformed ones left out."""
    raw = str(cfg.get("WLED_HOSTS") or "")
    seen = []
    for part in re.split(r"[\s,;]+", raw):
        if part and _HOST_RE.match(part) and part not in seen:
            seen.append(part)
    return seen


def enabled(cfg):
    return bool(cfg.get("WLED_ENABLED")) and bool(hosts(cfg))


def scene_preset(cfg, scene):
    """The preset a scene asks for: 0 keeps the light as it is, -1 switches it off."""
    try:
        return int(cfg.get("WLED_SCENE_" + scene.upper()) or 0)
    except (TypeError, ValueError):
        return 0


def _request(host, path, body=None, timeout=TIMEOUT):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://%s%s" % (host, path), data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8", "replace") or "{}")


def info(host, timeout=TIMEOUT):
    return _request(host, "/json/info", timeout=timeout)


def set_state(host, state, timeout=TIMEOUT):
    return _request(host, "/json/state", state, timeout=timeout)


def presets(host, timeout=TIMEOUT):
    """[(id, name), ...] of the presets the device holds, by id."""
    raw = _request(host, "/presets.json", timeout=timeout)
    found = []
    for key, value in (raw or {}).items():
        if not str(key).isdigit() or int(key) <= 0 or not isinstance(value, dict) or not value:
            continue
        found.append((int(key), str(value.get("n") or "#%s" % key)))
    return sorted(found)


def state_for(preset):
    """The JSON a preset becomes: off, a preset, or nothing at all."""
    if preset == OFF:
        return {"on": False}
    if preset > 0:
        return {"on": True, "ps": preset}
    return None


def describe(host, timeout=TIMEOUT):
    """What a device says about itself, or None when it is not a WLED."""
    try:
        data = info(host, timeout=timeout)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(data, dict) or str(data.get("brand") or "WLED") != "WLED" or "ver" not in data:
        return None
    return {"host": host, "name": data.get("name") or "WLED", "ver": data.get("ver"),
            "leds": (data.get("leds") or {}).get("count"), "mac": data.get("mac") or "",
            "time": data.get("time"), "uptime": data.get("uptime")}


def _avahi_hosts(timeout=4):
    if not shutil.which("avahi-browse"):
        return []
    try:
        out = subprocess.run(["avahi-browse", "-rtpk", "_wled._tcp"], capture_output=True,
                             text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        out = getattr(e, "stdout", "") or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
    found = []
    for line in out.splitlines():
        parts = line.split(";")
        if len(parts) > 7 and parts[0] == "=" and parts[2] == "IPv4":
            found.append(parts[7])
    return found


def _neighbours():
    found = []
    try:
        with open("/proc/net/arp", encoding="utf-8") as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[2] != "0x0" and parts[3] != "00:00:00:00:00:00":
                    found.append(parts[0])
    except OSError:
        pass
    return found


def discover(extra=(), timeout=1.5):
    """Every WLED this machine can see: by its own announcement, then by asking each neighbour."""
    candidates = []
    for host in list(extra) + _avahi_hosts() + _neighbours():
        if host and host not in candidates:
            candidates.append(host)
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(24, len(candidates))) as pool:
        answers = pool.map(lambda h: describe(h, timeout=timeout), candidates)
    found, macs = [], set()
    for item in answers:
        if item and (not item["mac"] or item["mac"] not in macs):
            macs.add(item["mac"])
            found.append(item)
    return found


def parse_time(text):
    """WLED's own clock ("2026-10-9, 07:03:05", local to WLED) as a naive datetime."""
    m = _TIME_RE.search(str(text or ""))
    if not m:
        return None
    year, month, day, hour, minute, second = (int(x) for x in m.groups()[:6])
    ampm = (m.group(7) or "").upper()
    if ampm == "PM" and hour < 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def wall_seconds(local):
    """A naive datetime read as if it were UTC, in epoch seconds."""
    return local.replace(tzinfo=timezone.utc).timestamp()


def learn_offset(local, utc_epoch):
    """WLED's distance from UTC, rounded to the quarter hour its timezones use."""
    return int(round((wall_seconds(local) - utc_epoch) / TZ_STEP)) * TZ_STEP


def trusted_time(local, offset, not_before=0.0):
    """WLED's clock in UTC seconds, or None when it cannot be right."""
    if local is None or local.year < EARLIEST_YEAR:
        return None
    utc = wall_seconds(local) - offset
    return utc if utc >= not_before - 120 else None


def push_time(host, timeout=TIMEOUT):
    """Hands WLED the time, then learns its timezone from what it shows back."""
    sent = time.time()
    set_state(host, {"time": int(sent)}, timeout=timeout)
    local = parse_time(info(host, timeout=timeout).get("time"))
    if local is None:
        return None
    return learn_offset(local, time.time())


def read_time(host, offset, not_before=0.0, timeout=TIMEOUT):
    """WLED's clock in UTC seconds, corrected for half the round trip, or None."""
    asked = time.monotonic()
    local = parse_time(info(host, timeout=timeout).get("time"))
    half = (time.monotonic() - asked) / 2
    utc = trusted_time(local, offset, not_before)
    return None if utc is None else utc + half


class Lights:
    """Sends each device the preset of the scene the radio is in, and flashes on events."""

    def __init__(self, cfg, scene, turn=None):
        self._cfg = cfg
        self._scene = scene
        self._turn = turn
        self._current = None
        self._flash_until = 0.0
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._failed = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="wled", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def refresh(self):
        """Applies the scene again, as after a settings change."""
        with self._lock:
            self._current = None
        self._wake.set()

    def flash(self, kind):
        """A short scene that then gives way to the one the radio is in."""
        cfg = self._cfg()
        if not enabled(cfg) or scene_preset(cfg, kind) == 0:
            return
        with self._lock:
            self._flash_until = time.monotonic() + FLASH_SEC
            self._current = None
        self.send(scene_preset(cfg, kind))
        self._wake.set()

    def switch_off(self):
        """Synchronous, for the moment just before the machine stops."""
        cfg = self._cfg()
        if enabled(cfg) and cfg.get("WLED_OFF_AT_POWEROFF", True):
            self.send(OFF, wait=True)

    def send(self, preset, wait=False):
        state = state_for(preset)
        if state is None:
            return
        for host in hosts(self._cfg()):
            if wait:
                self._send(host, state)
            else:
                threading.Thread(target=self._send, args=(host, state), daemon=True).start()

    def _send(self, host, state):
        try:
            set_state(host, state)
            self._failed.pop(host, None)
        except (OSError, ValueError, urllib.error.URLError) as e:
            now = time.monotonic()
            if now - self._failed.get(host, -3600) >= 3600:
                log.warning("WLED %s did not answer: %s", host, e)
                self._failed[host] = now

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            try:
                self._tick()
                if self._turn is not None:
                    self._turn(self._cfg())
            except Exception:  # noqa: BLE001 - a light must never stop the radio
                log.exception("WLED turn failed")

    def _tick(self):
        cfg = self._cfg()
        if not enabled(cfg):
            self._current = None
            return
        with self._lock:
            if time.monotonic() < self._flash_until:
                return
            scene = self._scene()
            if scene == self._current:
                return
            self._current = scene
        self.send(scene_preset(cfg, scene) if scene else 0)


def _fft(values):
    """Radix-2 FFT of a list of complex numbers whose length is a power of two."""
    n = len(values)
    out = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    size = 2
    while size <= n:
        step = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1
            for k in range(start, start + half):
                t = w * out[k + half]
                out[k + half] = out[k] - t
                out[k] += t
                w *= step
        size *= 2
    return out


_HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / (WINDOW - 1)) for i in range(WINDOW)]
_BIN_HZ = RATE / WINDOW

def _band(lo, hi):
    """The bins of one channel: never empty, even where the low channels share a bin."""
    first = max(1, round(lo / _BIN_HZ))
    return first, max(first + 1, round(hi / _BIN_HZ))


_BANDS = [_band(lo, hi) for lo, hi in zip(BAND_EDGES, BAND_EDGES[1:])]


class Analyser:
    """Turns windows of samples into WLED audio sync packets, with its own gain control."""

    def __init__(self):
        self.peak_level = 1e-3
        self.peak_spectrum = 1e-3
        self.smooth = 0.0
        self.bass_avg = 0.0
        self.last_beat = 0.0

    def packet(self, samples, now=None):
        now = time.monotonic() if now is None else now
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0
        spectrum = _fft([s * h for s, h in zip(samples, _HANN)])
        mags = [abs(c) for c in spectrum[:WINDOW // 2]]
        bands = [sum(mags[lo:hi]) / (hi - lo) for lo, hi in _BANDS]
        self.peak_level = max(rms, self.peak_level * 0.998)
        level = 0.0 if rms < 0.002 else min(1.0, rms / self.peak_level)
        self.smooth = self.smooth * 0.7 + level * 0.3
        # One gain for every channel, or each would fill up and the spectrum lose its shape.
        self.peak_spectrum = max(max(bands), self.peak_spectrum * 0.995)
        fft = bytearray(16)
        for i, value in enumerate(bands):
            share = 0.0 if rms < 0.002 else value / self.peak_spectrum
            fft[i] = min(254, int(254 * math.sqrt(max(0.0, share))))
        bass = sum(bands[:3]) / 3
        beat = bass > self.bass_avg * 1.6 and level > 0.15 and now - self.last_beat > 0.15
        self.bass_avg = self.bass_avg * 0.9 + bass * 0.1
        if beat:
            self.last_beat = now
        top = max(range(1, len(mags)), key=lambda i: mags[i]) if len(mags) > 1 else 0
        return SYNC_PACKET.pack(SYNC_HEADER, level * 255.0, self.smooth * 255.0, 1 if beat else 0,
                                bytes(fft), mags[top] * 8.0, float(min(11025.0, top * _BIN_HZ)))


def _local_address(host):
    """The address of this machine on the way to `host`, or "" when there is none."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host.split(":")[0], SYNC_PORT))
            return probe.getsockname()[0]
    except OSError:
        return ""


class AudioSync:
    """Captures what the radio plays and multicasts its beat to WLED's audio reactive effects."""

    def __init__(self, source, env, targets, delay_ms=0, ffmpeg="ffmpeg"):
        self.source = source
        self.env = env
        self.targets = list(targets)
        self.delay = max(0.0, min(2.0, delay_ms / 1000.0))
        self.ffmpeg = ffmpeg
        self.process = None
        self._stop = threading.Event()
        self._thread = None

    def command(self):
        return [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "pulse", "-i", self.source, "-ac", "1", "-ar", str(RATE),
                "-f", "s16le", "-"]

    def start(self):
        self._thread = threading.Thread(target=self._run, name="wled-audio", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _sockets(self):
        sockets = []
        addresses = {_local_address(h) for h in self.targets} - {""} or {""}
        for address in addresses:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            if address:
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                except OSError:
                    pass
            sockets.append(sock)
        return sockets

    def _run(self):
        try:
            self.process = subprocess.Popen(self.command(), stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, env=self.env)
        except OSError as e:
            log.warning("The WLED beat could not start ffmpeg: %s", e)
            return
        sockets = self._sockets()
        analyser = Analyser()
        hop = RATE // FPS
        window = deque([0.0] * WINDOW, maxlen=WINDOW)
        pending = deque()
        try:
            while not self._stop.is_set():
                raw = self.process.stdout.read(hop * 2)
                if not raw or len(raw) < 2:
                    break
                window.extend(v / 32768.0 for v in struct.unpack("<%dh" % (len(raw) // 2), raw[:len(raw) // 2 * 2]))
                now = time.monotonic()
                pending.append((now + self.delay, analyser.packet(list(window), now)))
                while pending and pending[0][0] <= now:
                    data = pending.popleft()[1]
                    for sock in sockets:
                        try:
                            sock.sendto(data, (SYNC_GROUP, SYNC_PORT))
                        except OSError:
                            pass
        finally:
            for sock in sockets:
                sock.close()
            if self.process.poll() is None:
                self.process.terminate()

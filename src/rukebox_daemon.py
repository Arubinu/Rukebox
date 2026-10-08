#!/usr/bin/env python3
"""The radio daemon: playback state machine, scheduler and control socket."""

import json
import logging
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import announcements  # noqa: E402
import audio_diag  # noqa: E402
import audio_output  # noqa: E402
import bt_link  # noqa: E402
import cards  # noqa: E402
import dj_intro  # noqa: E402
from config_and_scan import DEFAULTS, get_music_list, load_config  # noqa: E402
from config_schema import RESTART_REQUIRED, SYSTEM_SOUNDS  # noqa: E402
import hidden_tracks  # noqa: E402
import library  # noqa: E402
from mpv_controller import MPVController, audio_chain, audio_env, compression_filter  # noqa: E402
import music_lists  # noqa: E402
import playlist  # noqa: E402
import platform as platform_mod  # noqa: E402
import schedules  # noqa: E402
import speech  # noqa: E402
from state import RadioState  # noqa: E402
from stats import StatsRecorder  # noqa: E402
import system_actions  # noqa: E402
import track_media  # noqa: E402
import track_order  # noqa: E402
import stream  # noqa: E402
import usb_storage  # noqa: E402
import wled  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("radio")

# "No list remembered" - None already means "the whole library".
_NO_LIST = object()
NEVER = float("-inf")


def _file_stamp(path):
    """A file's date AND its size, which is what the three caches below compare."""
    # A coarse filesystem clock leaves the date unchanged for two writes in one tick.
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def announcement_target(msg):
    """(source, item_id, chooser) for a play_announcement command."""
    # `source` also says where a command came from ("web"...): only a real announcement source counts.
    source = msg.get("source")
    item_id = msg.get("id")
    if source and not (source in announcements.BUILTIN_SOURCES
                       or source.startswith("custom:")):
        source = None
    chooser = bool(source)
    if source and source.startswith("custom:"):
        item_id = source[len("custom:"):]
        source = None
    return source, item_id, chooser


def _crossfade_in_curve(share):
    """mpv's volume is cubic: this makes the incoming song's loudness rise as a
    sine, the mirror of the outgoing one's quarter-sine fade (constant power)."""
    return math.sin(math.pi / 2 * share) ** (1.0 / 3.0)


class TailPlayer:
    """The end of a song played by a second, disposable mpv for a crossfade:
    started paused, told to play once it is ready."""

    READY_WAIT_SEC = 20

    def __init__(self, command, socket_path):
        self.command = command
        self.socket_path = socket_path
        self.proc = None
        self.sock = None
        self.ready = False
        self.started = False
        self.handing = False
        self.log = None

    def start(self):
        try:
            if os.path.exists(self.socket_path):
                os.remove(self.socket_path)
            self.log = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(self.command, stdout=subprocess.DEVNULL,
                                         stderr=self.log, env=audio_env())
        except OSError:
            log.exception("Could not prepare the crossfade")
            return False
        threading.Thread(target=self._wait_ready, daemon=True).start()
        return True

    def _ask(self, command):
        self.sock.sendall((json.dumps({"command": command}) + "\n").encode())

    def _wait_ready(self):
        """Ready once the file is loaded at its start position."""
        end = time.monotonic() + self.READY_WAIT_SEC
        while time.monotonic() < end and self.proc and self.proc.poll() is None:
            try:
                if self.sock is None:
                    sock = socket.socket(socket.AF_UNIX)
                    sock.settimeout(1.0)
                    sock.connect(self.socket_path)
                    self.sock = sock
                self._ask(["get_property", "time-pos"])
                for line in self.sock.recv(65536).split(b"\n"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in msg and isinstance(msg.get("data"), (int, float)):
                        self.ready = True
                        return
            except OSError:
                pass
            time.sleep(0.2)

    def _time(self):
        """Its own position, read from its answers (events in between skipped)."""
        self._ask(["get_property", "time-pos"])
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            for line in self.sock.recv(65536).split(b"\n"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "event" not in msg and isinstance(msg.get("data"), (int, float)):
                    return float(msg["data"])
        return None

    def cue(self, at):
        """Moves, still paused, to `at` seconds; True once it is there."""
        try:
            self._ask(["seek", float(at), "absolute+exact"])
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                now = self._time()
                if now is not None and abs(now - at) < 0.06:
                    return True
                time.sleep(0.03)
        except (OSError, AttributeError, ValueError):
            pass
        return False

    def play(self, volume):
        """True once the player was told to go on; False leaves the song to play out."""
        try:
            self._ask(["set_property", "volume", max(0.0, min(100.0, float(volume)))])
            self._ask(["set_property", "pause", False])
            self.started = True
            return True
        except (OSError, AttributeError) as e:
            alive = self.proc is not None and self.proc.poll() is None
            log.warning("The crossfade player did not answer (%r, process %s): %s", e,
                        "running" if alive else "gone: %s" % (self.proc.poll() if self.proc else None),
                        self.output())
            return False

    def output(self):
        try:
            self.log.seek(0)
            return self.log.read()[-600:].decode("utf-8", "replace").strip() or "-"
        except (OSError, AttributeError, ValueError):
            return "-"

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        for f in (self.sock, self.log):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass


class RadioDaemon:
    # Two watch turns, so a track change or a PipeWire hiccup is not a dead link.
    SINK_MISSING_CHECKS = 2
    # A press on the speaker is only visible as a new level; 2s makes it feel answered.
    SINK_POLL_SEC = 2.0
    SINK_RESYNC_TURNS = 2
    QUICK_WINDOW_SEC = 10.0
    SINK_NAME_EVERY = 3
    CUTOFF_CATCH_UP_SEC = 300
    CUTOFF_PENDING_MAX_SEC = 1800

    def __init__(self, cfg):
        self._state_cond = threading.Condition()
        self._state_version = 0
        self._mode = "idle"
        # Re-entrant: the control socket already holds it around every command, and
        # a command that takes it again would otherwise wait on itself.
        self._command_lock = threading.RLock()
        self._speaker_watch_lock = threading.Lock()
        self._waiting_for_tracks = False
        self.cfg = cfg
        self.state = RadioState(os.path.join(cfg["STATE_DIR"], "state.json"))
        self.mpv = MPVController(cfg["MPV_SOCKET"])
        self.mode = "music"
        self._announce_queue = []
        self._current_volume = cfg["BASE_VOLUME"]
        self._shown_volume = cfg["BASE_VOLUME"]
        self._user_volume = None
        self._position = 0.0
        self._duration = 0.0
        self._paused = False
        self._current_track = None
        self._last_music_track = None
        self._stop_event = threading.Event()

        self.stats = StatsRecorder(
            cfg["STATS_DB_FILE"],
            retention_days=cfg["STATS_RETENTION_DAYS"],
            max_events=cfg["STATS_MAX_EVENTS"],
            enabled=cfg["STATS_ENABLED"],
        )

        self._play_kind = None
        self._play_path = None
        self._play_since = None

        self._consecutive_play_errors = 0
        self._error_backoff_timer = None

        self._speaker_was_connected = None
        self._sink_missing_checks = 0
        # Never 0: the uptime clock starts at boot, and 0 would read as "just done" for minutes.
        self._speaker_move_at = NEVER
        self._speaker_move_failed = False
        self._restart_pending = False
        self._restart_target = "service"
        # A Piper model costs seconds to load: a sentence known in advance is
        # rendered in the background.
        self._speech_warm = None
        self._speech_warming = None
        self._speech_busy = 0
        self._audio_device = None
        self._audio_output_checked = NEVER
        self._audio_output_missing = None
        self._powering_off = False
        self._music_started_mono = None
        self._delay_progress = {}
        self._last_sound = None
        self._paused_for_speaker = False
        self._speaker_lost_at = None
        self._speaker_ever_connected = False
        self._battery_step = None
        self._battery_warned = False
        self._game_return = None
        self._last_card = None
        self._quick_until = 0.0
        self._duck_factor = 1.0
        self._duck_proc = None
        self._tail_at = None
        self._tail_done = False
        self._xfade_checked = False
        self._xfade_proc = None
        self._tail_player_cls = TailPlayer
        self._xfade_in = 0.0
        self._dj_count = 0
        self._time_source = None
        self._clock_lock = threading.Lock()
        self._lights = wled.Lights(lambda: self.cfg, self._light_scene, self._light_turn)
        self._light_start_until = 0.0
        self._light_audio = None
        self._light_audio_key = None
        self._light_audio_retry = 0.0
        self._wled_clock_next = time.monotonic() + 20
        self._bt_start_done = False
        self._started_monotonic = time.monotonic()
        self._ap_known_clients = set()
        self._ap_iw_needs_sudo = None
        self._ap_warned = False
        self._session_marked_used = False
        self._last_volume_event = NEVER
        self._volume_glide_gen = 0
        self._volume_glide_target = None
        self._sink_level = None
        self._sink_name = None
        self._sink_resync = 0
        self._sink_turn = 0
        self._sink_warned = False
        self._after_action = None
        self._speaker_warned = None
        self._last_tick = None
        self._flic_warned = False

        self._custom_announcements = announcements.load(cfg["ANNOUNCEMENTS_FILE"])
        self._announce_volumes = announcements.volumes(cfg["ANNOUNCEMENTS_FILE"])
        self._announcements_stamp = None
        self._sound_volume = None
        self._music_lists = music_lists.load(cfg["MUSIC_LISTS_FILE"])
        self._lists_stamp = None
        self._schedule_items = []
        self._schedules_stamp = False
        self._schedule_active = None
        self._schedule_signature = None
        self._schedule_overrides = {}
        self._schedule_fired = {}
        self._resume_armed = False
        self._schedule_list_before = _NO_LIST
        self._list_library = None
        self._genre_resolved = None

        self._resume_mode = "music"

        self._history = [p for p in reversed(self.state.recent_paths()) if os.path.exists(p)][-50:]
        self._forced_next = None
        self._next_is_user = False
        self._resume_track = None
        self._pending_seek = None
        self._loop_mode = "off"
        self._muted = False
        self._timer_gen = {}
        self._timer_due = {}
        self._track_count = None
        self._output_override = None
        # The USB key the library reads from, while one is in use: None means
        # the configured music folder, which is also where a pulled key lands.
        self._usb_music = None
        self._usb_devices = []
        self._usb_error = None
        self._usb_tracks = 0
        self._usb_bytes = 0
        self._usb_left_behind = None

    def _bluetoothctl(self, *args, timeout=15):
        """Runs bluetoothctl, explicitly selecting the configured interface."""
        adapter = self.cfg.get("SPEAKER_BT_ADAPTER", "")
        commands = []
        if adapter:
            commands.append(f"select {adapter}")
        commands.append(" ".join(args))
        script = "\n".join(commands) + "\n"
        return subprocess.run(
            ["bluetoothctl"], input=script, capture_output=True, text=True, timeout=timeout,
        )

    AUDIO_OUTPUT_RECHECK_SEC = 30
    GENRE_CACHE_SEC = 5.0
    SPEAKER_MOVE_RETRY_SEC = 300
    RESUME_REWIND_SEC = 3.0

    def _wired_output(self):
        """True when the sound goes to a wired output of the Pi, not the
        Bluetooth speaker."""
        return self._output_kind() in ("jack", "usb", "hdmi")

    def _output_kind(self):
        return self._output_override or self.cfg.get("AUDIO_OUTPUT", "bluetooth")

    def _apply_audio_output(self, force=False):
        """Points mpv at the chosen output, and keeps the sound there."""
        now = time.monotonic()
        if not force and now - self._audio_output_checked < self.AUDIO_OUTPUT_RECHECK_SEC:
            return
        self._audio_output_checked = now
        if self._output_override:
            planned = self.cfg.get("AUDIO_OUTPUT", "bluetooth")
            # A departing speaker's sink can linger a moment: only its return counts.
            back = planned != "bluetooth" or self._speaker_lost_at is None
            if back and audio_output.find(planned, audio_output.list_sinks(env=audio_env())):
                log.info("Audio output: '%s' is back, leaving '%s'", planned, self._output_override)
                self._output_override = None
                self._bump_state()
        kind = self._output_kind()
        # Read even for an unknown kind: that is how a container's virtual sink is
        # found.
        sinks = audio_output.list_sinks(env=audio_env())
        if kind == "bluetooth":
            device, found = audio_output.mpv_device(kind, [])
        elif kind in audio_output.KINDS:
            device, found = audio_output.mpv_device(kind, sinks)
        else:
            device, found = audio_output.any_device(sinks)
        if not found and self._audio_output_missing != kind:
            log.warning("Audio output '%s' not found, using the default output", kind)
        self._audio_output_missing = None if found else kind
        if device != self._audio_device:
            log.info("Audio output: %s (%s)", kind, device)
            try:
                self.mpv.set_audio_device(device)
                self._audio_device = device
            except Exception:  # noqa: BLE001
                log.exception("Could not switch the audio output")
        self._hold_the_output(kind, device, sinks)

    def _hold_the_output(self, kind, device, sinks):
        """A chosen output keeps the sound, even when another device appears."""
        # WirePlumber moves streams to a newly connected default device.
        if device.startswith("pipewire/"):
            name = device[len("pipewire/"):]
        elif kind == "bluetooth":
            sink = audio_output.find("bluetooth", sinks)
            name = sink["name"] if sink else None
        else:
            name = None
        if not name:
            return
        env = audio_env()
        audio_diag.set_default_sink(name, env=env)
        if audio_diag.move_streams_to(name, env=env):
            log.info("Audio output: the sound had left %s and was put back on it", name)

    def _wait_for_speaker_connected(self, timeout_sec):
        """Polls bluetoothctl until the speaker is connected, so the clock
        confirmation sounds can be played at the right moment."""
        if self._wired_output():
            return True
        mac = self.cfg.get("SPEAKER_MAC", "")
        if not mac or mac == "XX:XX:XX:XX:XX:XX":
            return False
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._speaker_link(mac)["connected"]:
                return True
            time.sleep(1)
        return False

    def _speaker_link(self, mac=None):
        """Where the speaker is: connected (on any controller), and which one
        carries it - see src/bt_link.py."""
        mac = mac if mac is not None else self.cfg.get("SPEAKER_MAC", "")
        return bt_link.locate(mac, self.cfg.get("SPEAKER_BT_ADAPTER", ""))

    def _move_speaker_to_its_controller(self, state):
        """The speaker is connected on a controller the settings did not name:
        ask the right one to take it, because that is which radio carries the
        sound. Only worth trying when that controller already knows it."""
        expected = state.get("expected")
        if not expected or not state.get("paired_here"):
            return
        now = time.monotonic()
        if now - self._speaker_move_at < self.SPEAKER_MOVE_RETRY_SEC:
            return
        self._speaker_move_at = now
        if bt_link.connect_here(state["mac"], expected):
            log.info("Speaker moved from %s to %s, the sound follows it",
                     state["controller"], expected)
            self._speaker_move_failed = False
            return
        if not self._speaker_move_failed:
            self._speaker_move_failed = True
            log.warning("The speaker is connected over %s while %s is the controller set for "
                        "it, and it cannot be moved (it has to be paired there once): the "
                        "sound goes out of the first one", state["controller"], expected)

    def _play_cue_sound(self, path, key=None):
        if not path or not os.path.exists(path):
            log.warning("Confirmation sound not found: %s", path)
            return
        volume = self._source_volume(key) if key else None
        command = [
            "mpv", "--no-terminal", "--really-quiet",
            "--audio-device=" + (self._audio_device or "auto"),
        ]
        if volume is not None:
            command.append("--volume=%.1f" % volume)
        command.append(path)
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=audio_env(),
            )
        except FileNotFoundError:
            log.exception("Could not launch mpv for the confirmation sound")

    def _signal_clock_outcome(self, success: bool):
        """Plays the matching confirmation sound, only once, as soon as the
        speaker is available."""
        if self._cue_played.is_set():
            return
        self._cue_played.set()

        def _worker():
            connected = self._wait_for_speaker_connected(self.cfg["SPEAKER_READY_TIMEOUT_SEC"])
            if not connected:
                log.warning(
                    "Speaker not connected after %ss, clock confirmation sound skipped",
                    self.cfg["SPEAKER_READY_TIMEOUT_SEC"],
                )
                return
            sound = self.cfg["CLOCK_OK_SOUND"] if success else self.cfg["CLOCK_FALLBACK_SOUND"]
            log.info("Playing clock confirmation sound (%s)", "success" if success else "fallback")
            self._play_cue_sound(sound, "CLOCK_OK_SOUND" if success else "CLOCK_FALLBACK_SOUND")

        threading.Thread(target=_worker, daemon=True).start()

    def _init_clock_sync(self):
        """Determines clock reliability WITHOUT blocking startup."""
        self._clock_ready = threading.Event()
        self._cue_played = threading.Event()

        if not system_actions.can_set_clock():
            # A container's clock is its host's, kept by the host and not ours
            # to write: no RTC to read, nothing to recover, nothing to doubt.
            log.info("The clock is this machine's own, kept by the host")
            self._declare_clock("host")
            self._clock_ready.set()
            return

        if os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"):
            log.info("Hardware RTC module detected, clock considered reliable")
            self._declare_clock("rtc", offset_sec=0.0)
            self._clock_ready.set()
            return

        log.warning("No hardware RTC detected, the clock is not guaranteed to be reliable")

        if self._bt_clock_wanted() or self._wled_clock_targets():
            threading.Thread(target=self._recover_clock, daemon=True).start()
        else:
            log.warning("Bluetooth time recovery disabled or BT_CLOCK_MAC not configured")
            self._declare_clock("none", trusted=False)
            self._signal_clock_outcome(False)

        def grace_timeout():
            self._clock_ready.wait(self.cfg["CLOCK_SYNC_GRACE_SEC"])
            if not self._clock_ready.is_set():
                log.warning(
                    "No reliable time source confirmed after %ss, the "
                    "scheduler starts anyway with the current system time "
                    "(risk of incorrect time)",
                    self.cfg["CLOCK_SYNC_GRACE_SEC"],
                )
                self._declare_clock("none", trusted=False)
                self._signal_clock_outcome(False)
                self._clock_ready.set()

        threading.Thread(target=grace_timeout, daemon=True).start()

    def _declare_clock(self, source, offset_sec=0.0, trusted=True):
        self._time_source = source if trusted else None
        self.stats.set_clock(source, offset_sec=offset_sec, trusted=trusted)

    def _bt_clock_wanted(self):
        return bool(self.cfg["BT_CLOCK_ENABLED"]) and self.cfg["BT_CLOCK_MAC"] not in ("", "XX:XX:XX:XX:XX:XX")

    def _recover_clock(self):
        """WLED first - it answers at once when it is there - then the Bluetooth phone."""
        if self._try_wled_clock_sync():
            return
        if self._bt_clock_wanted():
            self._try_bt_clock_sync()
            return
        self._declare_clock("none", trusted=False)
        self._signal_clock_outcome(False)

    WLED_CLOCK_WAIT_SEC = 25
    WLED_CLOCK_EVERY_SEC = 600

    def _wled_clock_targets(self):
        """[(host, offset), ...]: the configured WLEDs whose timezone is known."""
        if not wled.enabled(self.cfg) or not self.cfg.get("WLED_CLOCK", True):
            return []
        memory = self.state.value("wled_clock") or {}
        found = []
        for host in wled.hosts(self.cfg):
            entry = memory.get(host)
            if isinstance(entry, dict) and isinstance(entry.get("offset"), int):
                found.append((host, entry["offset"]))
        return found

    def _last_known_time(self):
        """The newest time this machine has written down: WLED must not be behind it."""
        try:
            return os.path.getmtime(self.state.state_path)
        except OSError:
            return 0.0

    def _try_wled_clock_sync(self):
        targets = self._wled_clock_targets()
        if not targets:
            return False
        not_before = self._last_known_time()
        wait = min(self.WLED_CLOCK_WAIT_SEC, float(self.cfg["CLOCK_SYNC_GRACE_SEC"]))
        deadline = time.monotonic() + wait
        log.info("Asking WLED for the time (%s)", ", ".join(host for host, _ in targets))
        while time.monotonic() < deadline and not self._clock_ready.is_set():
            for host, offset in targets:
                try:
                    utc = wled.read_time(host, offset, not_before)
                except (OSError, ValueError):
                    continue
                if utc is not None:
                    return self._take_time(utc, "wled", "WLED " + host)
            time.sleep(3)
        log.warning("No WLED gave a time it could vouch for")
        return False

    def _take_time(self, utc, source, origin):
        """Sets the system clock from a UTC time another device gave."""
        with self._clock_lock:
            if self._clock_ready.is_set():
                return True
            offset = utc - time.time()
            formatted = datetime.fromtimestamp(utc, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            ok, detail = system_actions.set_clock(formatted, utc=True)
            if not ok:
                log.error("Failed to set system time from %s: %s", origin, detail)
                return False
            log.info("System time set from %s: %s UTC (offset %+.1fs)", origin, formatted, offset)
            self._declare_clock(source, offset_sec=offset)
            self._signal_clock_outcome(True)
            self._clock_ready.set()
            return True

    def _clock_trusted(self):
        if self._time_source not in (None, "none", "wled"):
            return True
        return system_actions.ntp_synchronized()

    def _share_clock_with_wled(self, cfg):
        """Gives each WLED the time while ours is the reliable one, and learns its timezone."""
        if not cfg.get("WLED_CLOCK", True) or time.monotonic() < self._wled_clock_next:
            return
        self._wled_clock_next = time.monotonic() + self.WLED_CLOCK_EVERY_SEC
        if not self._clock_trusted():
            return
        memory = dict(self.state.value("wled_clock") or {})
        for host in wled.hosts(cfg):
            try:
                offset = wled.push_time(host)
            except (OSError, ValueError) as e:
                log.debug("WLED %s did not take the time: %s", host, e)
                continue
            if offset is not None:
                memory[host] = {"offset": offset, "at": int(time.time())}
        self.state.set_value("wled_clock", memory)

    def _light_scene(self):
        """The moment the radio is in, as the lights see it."""
        mode = self.mode
        if self._powering_off or mode in ("shutting_down", "restarting"):
            return None
        if mode == "game":
            return "game"
        if mode == "cutoff_announce":
            return "cutoff"
        if self._duck_proc is not None or mode == "meme" or mode.startswith(("custom:", "button_announce:")):
            return "announce"
        if mode == "music":
            if self._paused:
                return "pause"
            return "start" if time.monotonic() < self._light_start_until else "play"
        return "idle"

    def _light_turn(self, cfg):
        self._follow_light_audio(cfg)
        if wled.enabled(cfg):
            self._share_clock_with_wled(cfg)

    def _follow_light_audio(self, cfg):
        """Runs the beat sender exactly while music is heard and it is wanted."""
        want = (wled.enabled(cfg) and bool(cfg.get("WLED_AUDIO_SYNC")) and self.mode == "music"
                and not self._paused and not self._muted)
        key = (tuple(wled.hosts(cfg)), int(cfg.get("WLED_AUDIO_DELAY_MS") or 0)) if want else None
        sync = self._light_audio
        if sync is not None and (key != self._light_audio_key or not sync.alive()):
            sync.stop()
            self._light_audio = sync = None
        if not want or sync is not None or time.monotonic() < self._light_audio_retry:
            return
        env = audio_env()
        source = stream.probe_source(env, kind=self._output_kind())
        self._light_audio_retry = time.monotonic() + (10 if source else 30)
        if not source:
            return
        self._light_audio = wled.AudioSync(source, env, key[0], key[1])
        self._light_audio_key = key
        self._light_audio.start()

    def _try_bt_clock_sync(self):
        from bt_clock import fetch_time_from_bt

        mac = self.cfg["BT_CLOCK_MAC"]
        log.info("Attempting to recover the time via Bluetooth (%s)...", mac)
        dt = fetch_time_from_bt(mac, timeout_sec=self.cfg["CLOCK_SYNC_GRACE_SEC"])
        if dt is None:
            log.warning("Failed to recover the time via Bluetooth")
            self._declare_clock("none", trusted=False)
            self._signal_clock_outcome(False)
            self._clock_ready.set()
            return

        # Taken before `date -s`: the statistics correct earlier timestamps by it.
        offset = dt.timestamp() - time.time()
        formatted = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        ok, detail = system_actions.set_clock(formatted)
        if ok:
            log.info("System time set via Bluetooth: %s (offset %+.1fs)", formatted, offset)
            self._declare_clock("bluetooth", offset_sec=offset)
            self._signal_clock_outcome(True)
            self._clock_ready.set()
        else:
            log.error("Failed to set system time: %s", detail)
            self._declare_clock("none", trusted=False)
            self._signal_clock_outcome(False)
            self._clock_ready.set()

    def start(self):
        os.makedirs(self.cfg["STATE_DIR"], exist_ok=True)
        self._install_signal_handlers()
        self.stats.open_session()
        # In a container "off" ends this process, not the machine: the hook closes
        # the session.
        system_actions.on_exit(self._on_process_end)
        self._init_clock_sync()
        self.mpv.start()
        self._apply_audio_output(force=True)
        self.mpv.set_replaygain(self.cfg["REPLAYGAIN_MODE"])
        self._apply_compression()
        self.mpv.on_event(self._on_mpv_event)
        self.mpv.observe(1, "time-pos")
        self.mpv.observe(2, "duration")
        self.mpv.observe(3, "pause")

        self._restore_base_volume()

        self._start_control_socket()
        self._start_watchdogs()
        self._lights.start()

        self.state.ensure_queue(
            self._playable_tracks(), self.cfg["MUSIC_ORDER_MODE"], self._music_dir(),
            self.cfg["MUSIC_KEEP_PROGRESS"], self.cfg["MUSIC_RESUME_MODE"],
            custom_order=music_lists.custom_order(self._active_list_entry()),
        )
        self._resume_armed = bool(self.cfg["MUSIC_KEEP_PROGRESS"])

        if self._start_mode() == "boot":
            self._music_started_mono = time.monotonic()
            self._start_music_faded(self._play_next_track)
        else:
            self._start_keepalive()

        self._scheduler_loop()

    def _install_signal_handlers(self):
        """A `systemctl stop/restart` sends SIGTERM."""
        def _on_term(signum, _frame):
            log.info("Signal %s received, shutting down cleanly", signum)
            self._stop_event.set()
            self._end_play("service_stop")
            self.stats.end_session("service_stop")
            self.stats.close()
            sys.exit(0)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _on_term)
            except (ValueError, OSError):
                log.warning("Could not install the handler for signal %s", sig)

    def _on_process_end(self):
        """Run when the platform ends this process on purpose."""
        self._end_play("service_stop")
        self.stats.end_session("service_stop")
        self.stats.close()

    def _reload_config(self):
        """Applies a settings save without a restart."""
        try:
            fresh = load_config(env_overrides=False)
        except Exception:  # noqa: BLE001
            log.exception("Could not re-read the configuration")
            return {"ok": False, "error": "config_unreadable"}
        # A running schedule keeps its own settings over the file's.
        fresh.update(self._schedule_overrides)
        return self._apply_config(fresh)

    def _apply_config(self, fresh):
        """Writes the changed values into the live configuration, with what
        each of them needs done."""
        applied, later = [], []
        for key, value in fresh.items():
            if self.cfg.get(key) == value:
                continue
            if key in RESTART_REQUIRED:
                later.append(key)
                continue
            self.cfg[key] = value
            applied.append(key)
        if "ANNOUNCE_ORDER_MODE" in applied:
            self.state.reset_click_bag()
        if "AUDIO_OUTPUT" in applied:
            self._apply_audio_output(force=True)
        if "REPLAYGAIN_MODE" in applied:
            try:
                self.mpv.set_replaygain(self.cfg["REPLAYGAIN_MODE"])
            except Exception:  # noqa: BLE001
                log.exception("Could not apply the ReplayGain mode")
        if "AUDIO_COMPRESSION" in applied or "AUDIO_EQUALIZER" in applied:
            self._apply_compression()
        if "SPEAKER_VOLUME_LINK" in applied:
            self._apply_volume_link()
        if "MUSIC_ORDER_MODE" in applied or "MUSIC_DIR" in applied:
            tracks = self._playable_tracks()
            if tracks:
                self._rebuild_queue(tracks)
        if applied:
            log.info("Settings applied without a restart: %s", ", ".join(sorted(applied)))
            self.stats.record("config_reloaded", label=", ".join(sorted(applied))[:200],
                              detail={"applied": sorted(applied), "restart": sorted(later)})
        return {"ok": True, "applied": sorted(applied), "restart": sorted(later)}

    def _get_music_list(self):
        tracks = get_music_list(self._music_dir(), self.cfg["MUSIC_CACHE_FILE"])
        self._track_count = len(tracks or [])
        if not tracks:
            log.warning("No tracks found in %s", self._music_dir())
        return tracks

    def _announcements(self):
        """The custom announcements and their volumes, re-read whenever the
        file changed. `reload_announcements` is the web interface's own shout,
        but this is what makes a hand edit over SSH - or a message that never
        arrived - harmless: a stale list answered "that announcement no longer
        exists" for a "Jouer" that had every right to work."""
        path = self.cfg["ANNOUNCEMENTS_FILE"]
        stamp = _file_stamp(path)
        if stamp != self._announcements_stamp:
            items = announcements.read_items(path)
            if items is None:
                log.error("Could not read %s: keeping the %d announcement(s) already "
                          "loaded", path, len(self._custom_announcements))
            else:
                self._custom_announcements = items
                self._announce_volumes = announcements.volumes(path)
            self._announcements_stamp = stamp
        return self._custom_announcements

    def _reload_announcements(self):
        """Re-reads them whatever the file's date says."""
        self._announcements_stamp = None
        return self._announcements()

    def _lists(self):
        """The music lists, re-read whenever the file changed: the web
        interface is the only writer, and it says so on the socket too."""
        path = self.cfg["MUSIC_LISTS_FILE"]
        stamp = _file_stamp(path)
        if stamp != self._lists_stamp:
            self._music_lists = music_lists.load(path)
            self._lists_stamp = stamp
        return self._music_lists

    def _active_list_entry(self):
        """The list the radio plays, or None when it plays everything."""
        list_id = self.state.active_list()
        if not list_id:
            return None
        entry = next((item for item in self._lists() if item["id"] == list_id), None)
        if entry is None:
            log.warning("The active music list %s is gone, playing everything", list_id)
            self.state.set_active_list(None)
        return entry

    def _genre_paths(self, genres):
        """The library's tracks for these genres: the tags are read by the web
        server into the same SQLite file, which the daemon opens read-mostly.
        Resolved at most once every few seconds - the status asks for the
        count on every poll, and this is a scan of every tag."""
        key = tuple(sorted(str(genre) for genre in (genres or [])))
        now = time.monotonic()
        cached = self._genre_resolved
        if cached and cached[0] == key and now - cached[1] < self.GENRE_CACHE_SEC:
            return cached[2]
        paths = self._read_genre_paths(key)
        self._genre_resolved = (key, now, paths)
        return paths

    def _read_genre_paths(self, genres):
        try:
            if self._list_library is None:
                self._list_library = library.Library(self.cfg["LIBRARY_DB_FILE"],
                                                     track_media.track_key)
            return self._list_library.paths_for_genres(list(genres))
        except Exception:  # noqa: BLE001 - a genre list must never stop the radio
            log.exception("Could not read the genres from the library")
            return []

    def _playable_tracks(self):
        """What the radio plays: the whole library minus the excluded tracks,
        or the active list as it stands - a list is an explicit choice, so it
        keeps the tracks excluded from the radio's own passes (the page marks
        them). The files are still there either way."""
        tracks = self._get_music_list()
        entry = self._active_list_entry()
        if entry:
            return music_lists.resolved(entry, tracks, self._genre_paths)
        hidden = self._hidden_paths()
        if not hidden:
            return tracks
        return [path for path in tracks if path not in hidden]

    def _hidden_paths(self):
        """The paths the radio must not pick by itself, or nothing at all."""
        try:
            return hidden_tracks.paths(self.cfg.get("HIDDEN_FILE") or "")
        except Exception:  # noqa: BLE001 - never keep the radio from playing
            log.exception("Could not read the hidden tracks")
            return set()

    def _reload_hidden(self):
        """Takes what was just excluded out of the pass under way, rather than
        rebuilding it: a rebuild would reshuffle what comes next and drop the
        songs asked for."""
        hidden = self._hidden_paths()
        removed = self.state.take_from_queue_many(hidden)
        return {"ok": True, "hidden": len(hidden), "removed": removed}

    def _active_list_status(self):
        entry = self._active_list_entry()
        if not entry:
            return None
        if entry.get("kind") == "genre":
            count = len(self._genre_paths(entry.get("genres") or []))
        else:
            count = len(self._playable_tracks())
        return {"id": entry["id"], "name": entry["name"], "kind": entry["kind"],
                "genres": entry.get("genres") or [], "tracks": count}

    def _rebuild_queue(self, tracks=None):
        """Restarts the playing pass from whatever is active now."""
        entry = self._active_list_entry()
        tracks = self._playable_tracks() if tracks is None else tracks
        self.state.rebuild_queue(tracks, self.cfg["MUSIC_ORDER_MODE"], self._music_dir(),
                                 music_lists.custom_order(entry))
        return tracks

    def _set_active_list(self, list_id, source, start=False):
        """Makes a list (None: everything) what the radio plays from the next
        track on, or right now with `start`."""
        list_id = str(list_id or "").strip() or None
        entry = None
        if list_id:
            entry = next((item for item in self._lists() if item["id"] == list_id), None)
            if entry is None:
                return {"ok": False, "error": "list_not_found"}
        self.state.set_active_list(entry["id"] if entry else None)
        tracks = self._rebuild_queue()
        if entry and not tracks:
            log.warning("The list %s holds no playable track", entry["id"])
        self.stats.record("list_selected", label=entry["name"] if entry else "all",
                          detail={"source": source, "tracks": len(tracks)})
        log.info("Playing %s (%d tracks)", entry["name"] if entry else "the whole library",
                 len(tracks))
        if start and tracks and self.mode != "shutting_down":
            # An explicit choice of what to hear now outranks a loop.
            self._loop_mode = "off"
            self._forced_next = None
            if self.mode in ("idle", "stopped"):
                self._start_or_restart_playback(log_label="list")
            else:
                self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
                self._restore_base_volume()
                self._play_next_track(user=True)
        self._bump_state()
        return {"ok": True, "data": {"active": self.state.active_list(), "tracks": len(tracks)}}

    def _audio_chain(self):
        return audio_chain(self.cfg.get("AUDIO_EQUALIZER"), self.cfg["AUDIO_COMPRESSION"])

    def _apply_compression(self):
        """Pushes the sound profile and the loudness filter to mpv, which holds
        them across every loadfile; an empty chain clears it."""
        chain = self._audio_chain()
        if chain and not self.mpv.set_audio_filter(chain):
            fallback = compression_filter(self.cfg["AUDIO_COMPRESSION"])
            log.warning("This mpv build does not take the filter %s, trying %s", chain, fallback or "none")
            if not fallback or not self.mpv.set_audio_filter(fallback):
                self.mpv.set_audio_filter("")
            return False
        if not chain:
            self.mpv.set_audio_filter("")
        return True

    def _play_next_track(self, user=False):
        """`user`: a "next" someone asked for (a click, the interface), as
        opposed to a song ending."""
        if self._restart_pending:
            self._do_planned_restart()
            return
        user = user or self._next_is_user
        self._next_is_user = False
        # Not at a start (the music fades in from silence) nor after an announcement.
        introduce = self.mode in ("music", "meme")
        natural = self.mode == "music" and not user
        forced, self._forced_next = self._forced_next, None
        if forced and os.path.exists(forced):
            self._play_with_intro(forced, introduce, natural)
            return
        last = self._last_music_track
        if last and self._loop_mode != "off" and os.path.exists(last):
            if self._loop_mode == "track" and not user:
                self._play_track(last)
                return
            if self._loop_mode == "album":
                following = self._album_neighbour(last, +1)
                if following:
                    self._play_track(following)
                    return
        tracks = self._playable_tracks()
        if not tracks:
            # Music was wanted: it starts once a rescan finds some.
            self._waiting_for_tracks = True
            if self.mode == "music":
                log.info("Nothing to play yet: waiting for music in the library")
                self._start_keepalive("idle", quiet=True)
            return
        self._waiting_for_tracks = False

        track = self.state.pop_next_track_or_none()
        if track is None:
            if not self.cfg["MUSIC_LOOP"]:
                self._enter_stopped_mode()
                return
            self._rebuild_queue(tracks)
            track = self.state.pop_next_track_or_none()
            if track is None:
                return

        self._play_with_intro(track, introduce, natural)

    def _library(self):
        if self._list_library is None:
            self._list_library = library.Library(self.cfg["LIBRARY_DB_FILE"], track_media.track_key)
        return self._list_library

    def _track_info(self, path):
        """The song's title and artist, from the library, else its file name."""
        try:
            item = self._library().item_for_path(path)
        except Exception:  # noqa: BLE001
            item = None
        title = (item or {}).get("title") or os.path.splitext(os.path.basename(path))[0]
        return {"title": title, "artist": (item or {}).get("artist")}

    def _dj_every(self):
        try:
            return max(0, int(self.cfg.get("DJ_ANNOUNCE_EVERY", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _intro_due(self, path):
        """Whether `path` will be introduced out loud before it plays."""
        if self.cfg.get("DEDICATIONS_ENABLED") and path in self.state.dedications():
            return True
        every = self._dj_every()
        return every > 0 and self._dj_count + 1 >= every

    def _intro_text(self, track, introduce, natural):
        """(what to say before `track`, and which of the two it is: "dedication"
        or "dj"), or ("", "") when nothing is said."""
        dedication = self.state.pop_dedication(track)
        if not introduce:
            return "", ""
        lang = self.cfg.get("SPEECH_LANGUAGE")
        info = self._track_info(track)
        if dedication and self.cfg.get("DEDICATIONS_ENABLED"):
            text = speech.dedication_sentence(info["title"], dedication.get("from"),
                                              dedication.get("text"), lang)
            if text:
                self.stats.record("dedication_played", label=os.path.basename(track),
                                  detail={"from": dedication.get("from")})
                return text, "dedication"
        every = self._dj_every()
        if every > 0 and natural:
            self._dj_count += 1
            if self._dj_count >= every:
                self._dj_count = 0
                return speech.track_sentence(info["title"], info["artist"], lang), "dj"
        return "", ""

    def _intro_audio(self, track, text, kind):
        """What is played before `track`: a prepared file when the settings say
        so, the radio's own voice otherwise. None when nothing is said."""
        if kind == "dj":
            mode = self.cfg.get("DJ_ANNOUNCE_MODE") or "spoken"
            if mode in ("files", "files_first"):
                prepared = dj_intro.find(track, self.cfg.get("DJ_ANNOUNCE_DIR"),
                                         self._music_dir())
                if prepared:
                    log.info("Introduction read from %s", prepared)
                    return prepared
                if mode == "files":
                    log.info("No prepared introduction for %s: playing it as it is",
                             os.path.basename(track))
                    return None
        if not text:
            return None
        return self._speech_text_file(text)

    def _play_with_intro(self, track, introduce=False, natural=False):
        """Plays `track`, saying an introduction first when a dedication or the
        radio host asks for it."""
        text, kind = self._intro_text(track, introduce, natural)
        path = self._intro_audio(track, text, kind)
        if not path:
            self._play_track(track)
            return
        log.info("Before the song: %s", text or path)
        if self._sound_volume is not None:
            self._restore_base_volume()
        self._xfade_in = 0.0
        self._forced_next = track
        self._resume_mode = "music"
        self._resume_track = None
        self._next_is_user = False
        self.mode = "meme"
        self._begin_play("speech", path)

    def _rescan_music(self, source):
        """Reads the music folder again; music that was waiting for it starts."""
        from config_and_scan import force_rescan
        force_rescan(self.cfg["MUSIC_CACHE_FILE"])
        # Read the list back now, or the interface shows the old count until the next track.
        self._get_music_list()
        log.info("Music rescanned: %d tracks", self._track_count)
        self.stats.record("music_rescan", detail={"source": source})
        if self._waiting_for_tracks and self._track_count and self.mode in ("idle", "stopped"):
            self._start_or_restart_playback(log_label="library")
        return self._track_count

    def _music_dir(self):
        """Where the library reads from: the USB key in use, or the folder the
        settings name. Everything that walks the library goes through here."""
        return (self._usb_music or {}).get("dir") or self.cfg["MUSIC_DIR"]

    def _check_usb_music(self):
        """A key plugged in or pulled out: the library follows it, and comes
        back to the internal folder as soon as it is gone."""
        remembered = self.state.value("usb_music") or None
        self._usb_devices = usb_storage.devices()
        entry = usb_storage.find(self._usb_devices, (remembered or {}).get("key"))
        if self._usb_music is not None:
            if entry is None:
                return self._release_usb_music("gone")
            if not usb_storage.is_mounted():
                return self._release_usb_music("unmounted")
            return False
        if remembered and entry is not None:
            return self._adopt_usb_music(entry, source="remembered")
        return False

    def _usb_switch_mode(self):
        """What the settings say to do with the song playing when a storage
        device becomes the library: "after" leaves it, "now" replaces it."""
        mode = str(self.cfg.get("USB_MUSIC_SWITCH") or "").strip().lower()
        return "now" if mode == "now" else "after"

    def _adopt_usb_music(self, entry, source="usb", change=None):
        """Mounts a key read-only and reads the library from it."""
        ok, said = system_actions.usb_mount(entry["device"])
        if not ok:
            self._usb_error = said or "mount_failed"
            log.warning("Could not mount %s (%s): %s",
                        entry["device"], entry.get("label") or "no label", self._usb_error)
            return False
        self._usb_error = None
        self._usb_music = {
            "dir": usb_storage.mount_point(), "device": entry["device"],
            "label": entry.get("label") or "", "model": entry.get("model") or "",
            "key": usb_storage.key_of(entry),
        }
        self.state.set_value("usb_music", {"key": self._usb_music["key"],
                                           "label": self._usb_music["label"],
                                           "model": self._usb_music["model"]})
        self._usb_tracks, self._usb_bytes = usb_storage.count_music(self._usb_music["dir"])
        log.info("Music read from the USB key '%s' (%s): %d tracks",
                 self._usb_music["label"] or self._usb_music["model"]
                 or self._usb_music["device"],
                 self._usb_music["device"], self._usb_tracks)
        self.stats.record("usb_music_used",
                          label=self._usb_music["label"] or self._usb_music["model"]
                          or self._usb_music["device"],
                          detail={"tracks": self._usb_tracks, "source": source})
        self._switch_music_source("usb_in", change=change or self._usb_switch_mode())
        return True

    def _release_usb_music(self, reason="", forget=False, change="after"):
        """Back to the internal folder. The key stays remembered, so plugging it
        in again resumes where it was - unless `forget` says otherwise.
        `change` is what the switch does with the song playing: a deliberate
        give-back fades it out like any other action, a key that vanished has
        no sound left to fade."""
        was = self._usb_music
        if usb_storage.is_mounted():
            ok, said = system_actions.usb_umount()
            if not ok:
                log.warning("Could not unmount the USB key: %s", said)
        if was is not None:
            self._usb_left_behind = (was or {}).get("dir")
            self._usb_music = None
            self._usb_tracks = 0
            self._usb_bytes = 0
            log.info("Back to the internal music folder (%s)", reason or "released")
            self.stats.record("usb_music_released",
                              label=(was or {}).get("label") or (was or {}).get("device") or "",
                              detail={"reason": reason})
        if forget:
            self.state.set_value("usb_music", None)
        if was is not None:
            self._switch_music_source("usb_out", change=change)
        return was is not None

    def _switch_music_source(self, source, change="after"):
        """Reads the library from its new folder; `change` is "after" (the song playing
        finishes) or "now" (it is replaced)."""
        was_on_the_key = bool(
            self._current_track and self._usb_left_behind
            and os.path.realpath(self._current_track).startswith(
                os.path.realpath(self._usb_left_behind) + os.sep))
        self._usb_left_behind = None
        self._rescan_music(source)
        with self._command_lock:
            self._rebuild_queue()
            if self.mode in ("music", "idle", "stopped"):
                if was_on_the_key:
                    log.info("The track was on the folder that just left: moving on")
                    if change == "now":
                        # Given back on purpose: fade it out like any action.
                        self._play_from_the_new_folder(start_if_idle=False)
                    else:
                        # The device went away with the file: nothing to fade.
                        self._play_next_track()
                elif change == "now":
                    self._play_from_the_new_folder()
        self._bump_state()

    def _play_from_the_new_folder(self, start_if_idle=True):
        """Plays a track of the folder the library reads now, straight away:
        the Next button's road, with the fade the settings give an action.
        From a standstill it starts the music instead - under the start fade -
        unless the caller only wants the track changed, which is what giving a
        folder back does."""
        if self.mode in ("idle", "stopped"):
            if start_if_idle:
                log.info("Starting the music from the folder the library reads now")
                self._start_or_restart_playback(log_label="usb")
            return
        log.info("Playing from the folder the library reads now, faded")
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
        self._restore_base_volume()
        self._play_next_track(user=True)

    def _usb_music_command(self, msg, source="web"):
        """Reads the library from a plugged key, or gives it back to the
        internal folder. `forget` also drops the key the daemon remembers, and
        `switch` says what to do with the song playing; without it, the setting
        USB_MUSIC_SWITCH does."""
        asked = str(msg.get("switch") or "").strip().lower()
        change = asked if asked in ("after", "now") else self._usb_switch_mode()
        if "key" not in msg and "device" not in msg and not msg.get("forget"):
            # A plain refresh: the interface wants the list of devices now.
            self._check_usb_music()
            return {"ok": True, "data": self._usb_music_status()}
        if msg.get("forget"):
            # Going back to the Pi's own folder, and not taking this key again
            # by itself: it stays offered, one click away, while it is plugged.
            self._release_usb_music("forgotten", forget=True, change="now")
            self._check_usb_music()
            return {"ok": True, "data": self._usb_music_status()}
        wanted = str(msg.get("key") or msg.get("device") or "").strip()
        self._usb_devices = usb_storage.devices()
        entry = usb_storage.find(self._usb_devices, wanted) if wanted.startswith("uuid:") \
            or wanted.startswith("label:") or wanted.startswith("path:") else None
        if entry is None:
            entry = next((d for d in self._usb_devices if d["device"] == wanted), None)
        if entry is None:
            self._usb_error = "no_such_device"
            return {"ok": False, "error": "no_such_device"}
        if self._usb_music is not None and self._usb_music.get("key") != usb_storage.key_of(entry):
            self._release_usb_music("switched", change="now")
        if self._usb_music is None:
            if not self._adopt_usb_music(entry, source=source, change=change):
                return {"ok": False, "error": "mount_failed", "detail": self._usb_error}
        elif change == "now":
            # The key is the library already: this only changes the song.
            with self._command_lock:
                self._play_from_the_new_folder()
        self._bump_state()
        return {"ok": True, "data": self._usb_music_status()}

    def _usb_music_status(self):
        """What the interface shows about it: the devices there are, the one in
        use, the one that would be taken again on the next plug, and how full
        the storage that is playing is."""
        remembered = self.state.value("usb_music") or None
        active_key = (self._usb_music or {}).get("key")
        # The song playing was read from the folder that was in use before: the
        # card offers to change it now rather than at the end of the song.
        track = self._current_track
        on_this_folder = bool(track and os.path.realpath(track).startswith(
            os.path.realpath(self._music_dir()) + os.sep))
        devices = []
        for entry in self._usb_devices:
            key = usb_storage.key_of(entry)
            devices.append({
                "key": key, "device": entry["device"], "label": entry["label"],
                "model": entry.get("model") or "", "fstype": entry["fstype"], "size": entry["size"],
                "remembered": bool(remembered and remembered.get("key") == key),
                "active": bool(active_key and active_key == key),
            })
        return {
            "active": self._usb_music is not None,
            "dir": (self._usb_music or {}).get("dir"),
            "internal": self.cfg["MUSIC_DIR"],
            "port_mode": self.cfg.get("USB_PORT_MODE") or "",
            "device": (self._usb_music or {}).get("device"),
            "label": (self._usb_music or {}).get("label"),
            "model": (self._usb_music or {}).get("model"),
            "tracks": self._usb_tracks,
            "bytes": self._usb_bytes,
            "space": usb_storage.space(self._music_dir()),
            "playing_from_other": bool(self._usb_music and track and not on_this_folder),
            "remembered": remembered,
            "error": self._usb_error,
            "devices": devices,
        }

    def _play_track(self, path, start=0.0):
        """Plays one music file, from `start` seconds."""
        resumed = self._resume_position(path)
        start = start or resumed
        log.info("Playing: %s%s", path, " from %.0fs" % start if start else "")
        if self._sound_volume is not None:
            # Back from an announcement that played at a volume of its own.
            self._restore_base_volume()
        self.mode = "music"
        self._pending_seek = start if start and start > 1 else None
        self._begin_play("music", path)
        if self._pending_seek:
            self._position = self._pending_seek
        self._tail_at = self._tail_for(path)
        self._tail_done = False
        self._xfade_checked = False
        fade_in, self._xfade_in = self._xfade_in, 0.0
        if fade_in:
            self._fade_in_after_crossfade(fade_in)
        self._hold_music_without_speaker()

    def _tail_for(self, path):
        if not self.cfg.get("SKIP_TRAILING_SILENCE"):
            return None
        try:
            return self._library().tail_for(path)
        except Exception:  # noqa: BLE001 - the end is then played out, as before
            return None

    def _crossfade_sec(self):
        try:
            return min(10.0, max(0.0, float(self.cfg.get("CROSSFADE_SEC", 0) or 0)))
        except (TypeError, ValueError):
            return 0.0

    XFADE_PRELOAD_SEC = 8

    def _near_end(self):
        """Called as the position moves: the trailing silence skipped, or the
        crossfade into the next song prepared, then started."""
        if self._play_kind != "music" or self._paused or self.mode != "music":
            return
        end = self._tail_at or self._duration
        if not end:
            return
        pos = self._position
        fade = self._crossfade_sec()
        if fade and end > fade * 3:
            if not self._xfade_checked and pos >= end - fade - self.XFADE_PRELOAD_SEC:
                self._xfade_checked = True
                if self._crossfade_ok():
                    self._prepare_tail(end - fade, fade)
            tail = self._xfade_proc
            if tail is not None and not tail.handing and pos >= end - fade:
                if tail.ready and self._crossfade_ok():
                    self._crossfade_to_next(fade, tail)
                    if tail.handing:
                        self._tail_done = True
                        return
                log.info("Crossfade not ready in time: the song plays out (%s)", tail.output())
                self._stop_tail()
        if self._tail_at and not self._tail_done and pos >= self._tail_at:
            self._tail_done = True
            log.info("Silence until the end of the song: moving on")
            self.mpv.seek_end()

    def _crossfade_ok(self):
        if self._restart_pending or self._speaker_lost_at is not None or self._duck_proc is not None:
            return False
        if self.state.is_pending_cutoff():
            return False
        following = self._upcoming_track()
        return bool(following) and not self._intro_due(following)

    def _mpv_level(self):
        if self._speaker_volume_linked():
            return self._music_gain() * self._duck_factor
        return (self._current_volume if self._current_volume is not None else self._target_volume()) \
            * self._duck_factor

    XFADE_SOCKET = "/tmp/rukebox_xfade.sock"

    def _prepare_tail(self, start, fade):
        """A disposable mpv, paused on the last seconds of this song: starting
        one takes seconds on a Pi Zero, so it is ready before it is needed."""
        self._stop_tail()
        # The fade is placed on the song's own clock: reset to 0, every frame
        # would sit before --start and mpv would drop them all.
        start = max(0.0, start)
        graph = ",".join(c for c in (self._audio_chain(),
                                     "afade=t=out:st=%.2f:d=%.2f:curve=qsin" % (start, fade)) if c)
        command = ["mpv", "--no-terminal", "--msg-level=all=warn", "--no-video", "--pause",
                   "--input-ipc-server=" + self.XFADE_SOCKET,
                   "--audio-device=" + (self._audio_device or "auto"),
                   "--volume=%.1f" % self._mpv_level(),
                   "--replaygain=%s" % (self.cfg.get("REPLAYGAIN_MODE") or "no"),
                   "--start=%.2f" % start, "--length=%.2f" % fade,
                   "--af=lavfi=[%s]" % graph, self._play_path]
        log.info("Crossfade prepared: %s", " ".join(command[3:-1]))
        self._xfade_proc = self._tail_player_cls(command, self.XFADE_SOCKET)
        if not self._xfade_proc.start():
            self._xfade_proc = None

    XFADE_LEAD_SEC = 0.5

    def _crossfade_to_next(self, fade, tail):
        """The end of this song goes on in the prepared player, fading out,
        while the main one moves to the next song and fades it in. Done in a
        thread: reading the main player's position waits on the very thread
        that delivers its events."""
        tail.handing = True
        self._start_hand_over(fade, tail, self._play_path)

    def _start_hand_over(self, *args):
        threading.Thread(target=self._hand_over, args=args, daemon=True).start()

    def _main_time(self):
        reply = self.mpv.request(["get_property", "time-pos"], timeout=1.0)
        data = reply.get("data") if isinstance(reply, dict) else None
        return float(data) if isinstance(data, (int, float)) else self._position

    def _hand_over(self, fade, tail, path):
        # The tail takes over at the exact point the main player has reached:
        # starting it at "end - fade" repeated, or skipped, a fraction of a second.
        at = self._main_time() + self.XFADE_LEAD_SEC
        if tail.cue(at):
            wait = at - self._main_time()
            if 0 < wait < 2:
                time.sleep(wait)
        else:
            log.info("Crossfade could not be lined up: the song plays out (%s)", tail.output())
            at = None
        moved_on = self._xfade_proc is not tail or self._play_path != path or self._paused
        if at is None or moved_on or not tail.play(self._mpv_level()):
            if self._xfade_proc is tail:
                self._stop_tail()
            self._tail_done = False
            return
        log.info("Crossfade over %.1fs into the next song", fade)
        self._xfade_in = fade
        self.mpv.set_volume(0)
        self.mpv.seek_end()

    def _fade_in_after_crossfade(self, fade):
        target = self._target_volume()
        self._stop_volume_glide()
        self._current_volume = 0
        self._shown_volume = target
        # Linked, the speaker already holds the volume: writing it again would
        # put mpv back at full volume for a moment - the step that was heard.
        self.mpv.set_volume(0)
        self._glide_volume(target, fade, curve=_crossfade_in_curve)

    def _stop_tail(self):
        """The end of the previous song, still fading out, silenced."""
        tail, self._xfade_proc = self._xfade_proc, None
        if tail is not None:
            tail.stop()

    def _resume_position(self, path):
        """Where to take a song up again: only the first one after a start or
        a standby, and only if it is the one that was cut short."""
        armed, self._resume_armed = self._resume_armed, False
        point = self.state.resume_point()
        self.state.clear_resume_point()
        if not armed or not point or point[0] != path:
            return 0.0
        if self.cfg.get("MUSIC_RESUME_MODE") != "same_position":
            return 0.0
        return max(0.0, point[1] - self.RESUME_REWIND_SEC)

    def _hold_music_without_speaker(self):
        """Keeps a song paused when it starts while the speaker is away."""
        # A click would otherwise undo the pause _on_speaker_lost() made.
        if not self.cfg.get("SPEAKER_LOSS_PAUSE"):
            return
        if self._output_kind() != "bluetooth":
            return
        if self._speaker_lost_at is None:
            return
        log.info("Speaker away: keeping the music paused until it comes back")
        self.mpv.set_pause(True)
        self._paused = True
        self._paused_for_speaker = True
        self._bump_state()

    def _album_neighbour(self, path, step):
        """The track `step` places from `path` in its own folder (the album),
        in natural order, wrapping round."""
        folder = os.path.dirname(path)
        album = [t for t in self._playable_tracks() if os.path.dirname(t) == folder]
        if not album:
            return None
        album = playlist.order_files(album, self._music_dir(), "ordered")
        if path not in album:
            return album[0]
        return album[(album.index(path) + step) % len(album)]

    def _library_path(self, raw):
        """`raw` as a real file of the music library, or None."""
        path = os.path.realpath(str(raw or ""))
        root = os.path.realpath(self._music_dir())
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return None
        tracks = self._get_music_list()
        by_real = {os.path.realpath(t): t for t in tracks}
        return by_real.get(path)

    def _next_tracks(self, count):
        """The next `count` songs as they would come: the forced one, then a
        looped song."""
        out = []
        first = self._upcoming_track()
        if first:
            out.append(first)
        if self._loop_mode == "track":
            return out[:count]
        if self._loop_mode == "album" and first:
            current = first
            while len(out) < count:
                current = self._album_neighbour(current, +1)
                if not current or current == out[0]:
                    break
                out.append(current)
            return out[:count]
        queue = list(self.state.data.get("play_queue") or [])
        if queue and first and queue[0] == first:
            queue = queue[1:]
        for path in queue:
            if len(out) >= count:
                break
            out.append(path)
        return out[:count]

    def _upcoming_track(self):
        """The song _play_next_track() would play after this one, or None."""
        if self._forced_next:
            return self._forced_next
        last = self._last_music_track
        if last and self._loop_mode == "track":
            return last
        if last and self._loop_mode == "album":
            try:
                return self._album_neighbour(last, +1)
            except Exception:  # noqa: BLE001
                return None
        return self.state.peek_next_track()

    PREVIOUS_RESTART_AFTER_SEC = 5

    def _step_back(self):
        """Prepares "previous" for the next _play_next_track(): the song from
        the top when it has played a few seconds (or nothing came before
        it), else the one before."""
        cur = self._last_music_track
        if not cur:
            return
        if self._position > self.PREVIOUS_RESTART_AFTER_SEC:
            self._forced_next = cur
            return
        if self._loop_mode == "album":
            self._forced_next = self._album_neighbour(cur, -1) or cur
            return
        if len(self._history) < 2 or self._history[-1] != cur:
            self._forced_next = cur
            return
        self._history.pop()
        self.state.push_front(cur)
        self._forced_next = self._history.pop()

    def _enter_stopped_mode(self):
        """MUSIC_LOOP=false and the list just finished playing through once."""
        log.info("Music list finished (MUSIC_LOOP=false): stopping, speaker stays active")
        self.stats.record("music_list_stopped")
        self._start_keepalive(target_mode="stopped")

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        changed = value != self._mode
        self._mode = value
        if changed:
            self._bump_state()

    def _bump_state(self):
        with self._state_cond:
            self._state_version += 1
            self._state_cond.notify_all()

    def _wait_state_change(self, since, timeout):
        """Blocks until the state version differs from `since`."""
        with self._state_cond:
            self._state_cond.wait_for(lambda: self._state_version != since, timeout=timeout)
            return self._state_version

    def _begin_play(self, kind, path):
        """Loads a file and starts counting its listening time."""
        self._end_play("replaced")
        if self._xfade_proc is not None and (kind != "music" or not self._xfade_proc.started):
            self._stop_tail()
        self._current_track = path
        if self._wired_output():
            self._apply_audio_output()
        if self._timer_due.get("resume"):
            self._set_timer("resume", None)
        if kind == "music":
            self._last_music_track = path
            if not self._history or self._history[-1] != path:
                self._history.append(path)
                del self._history[:-50]
            try:
                self.state.add_recent(path, self.cfg.get("RECENT_TRACKS_COUNT", 20))
            except Exception:
                log.exception("Could not record the recent track")
        elif kind != "restart_cue":
            self._last_sound = {"path": path, "kind": kind, "at": time.monotonic()}
        self._play_kind = kind
        self._play_path = path
        self.mpv.loadfile(path)
        # A sound with a volume of its own: the music keeps its fade-in volume.
        if self._sound_volume is not None and kind != "music":
            self.mpv.set_volume(self._sound_volume)
        self.mpv.set_pause(False)
        self._play_since = time.monotonic()
        self._position = 0.0
        self._bump_state()
        if not self._session_marked_used:
            self.stats.mark_used()
            self._session_marked_used = True

    def _end_play(self, reason):
        """Closes the accounting of what was playing; returns the seconds
        listened."""
        kind, path, since = self._play_kind, self._play_path, self._play_since
        self._play_kind = self._play_path = self._play_since = None
        if not kind or since is None:
            return 0.0

        seconds = max(0.0, time.monotonic() - since)
        if reason == "error":
            return seconds
        if kind == "music" and reason != "eof" and self.cfg.get("MUSIC_RESUME_MODE") == "same_position":
            self.state.set_resume_point(path, self._position)

        if kind == "game" or (path and os.path.dirname(path) == self._speech_dir()):
            return seconds
        name = os.path.basename(path) if path else None
        if kind == "music":
            event = "track_played"
            counters = {"tracks_played": 1, "seconds_music": seconds}
            daily = {"tracks_played": 1, "seconds_music": seconds}
        elif kind == "meme":
            event = "meme_played"
            counters = {"memes_played": 1, "seconds_meme": seconds}
            daily = {"seconds_meme": seconds}
        else:
            event = "announce_played"
            counters = {"announcements_played": 1, "seconds_announce": seconds}
            daily = {"seconds_announce": seconds}

        self.stats.record(
            event, label=name,
            detail={"kind": kind, "seconds": round(seconds, 1), "reason": reason},
            counters=counters, daily=daily, item=(kind, name), seconds=seconds,
        )
        return seconds

    def _record_playback_error(self, msg, path, seconds):
        name = os.path.basename(path) if path else "unknown"
        error = msg.get("file_error") or "unknown error"
        self._consecutive_play_errors += 1
        log.error(
            "Playback failed: %s (%s) - %d consecutive failure(s)",
            name, error, self._consecutive_play_errors,
        )
        if self._consecutive_play_errors == 1 and "output" in error:
            # "audio output initialization failed" does not say which part broke: dump the path once.
            try:
                log.warning("Audio path at the failure:\n%s", audio_diag.report(
                    cfg=self.cfg, status=self._build_status(), measure=0))
            except Exception:  # noqa: BLE001
                log.debug("Could not report the audio path", exc_info=True)
        self.stats.record(
            "playback_error", label=name,
            detail={
                "error": error,
                "kind": self._play_kind or self.mode,
                "seconds": round(seconds, 1),
                "consecutive": self._consecutive_play_errors,
            },
            counters={"playback_errors": 1}, daily={"playback_errors": 1},
            item=("error", name),
        )

    def _should_back_off(self):
        """A whole folder of unreadable files would otherwise be skipped
        through at full speed, hammering the SD card and the CPU of a Pi
        Zero for as long as it takes."""
        max_retries = self.cfg["PLAYBACK_ERROR_MAX_RETRIES"]
        if max_retries <= 0 or self._consecutive_play_errors < max_retries:
            return False

        delay = self.cfg["PLAYBACK_ERROR_BACKOFF_SEC"]
        failures = self._consecutive_play_errors
        self._consecutive_play_errors = 0

        self.stats.record(
            "playback_stalled",
            label="%d consecutive failures" % failures,
            detail={"backoff_sec": delay, "failures": failures},
            counters={"playback_stalls": 1}, daily={"playback_stalls": 1},
        )

        if delay <= 0:
            log.error(
                "%d consecutive playback failures, continuing immediately "
                "(PLAYBACK_ERROR_BACKOFF_SEC=0)", failures,
            )
            return False

        log.error(
            "%d consecutive playback failures, pausing for %ss before retrying",
            failures, delay,
        )
        if self._error_backoff_timer is not None:
            self._error_backoff_timer.cancel()
        self._error_backoff_timer = threading.Timer(delay, self._retry_after_backoff)
        self._error_backoff_timer.daemon = True
        self._error_backoff_timer.start()
        return True

    def _retry_after_backoff(self):
        if self._stop_event.is_set() or self.mode != "music":
            return
        log.info("Resuming playback after the error backoff")
        self._play_next_track()

    def _start_keepalive(self, target_mode="idle", quiet=False):
        """Waits silently: loops the keep-alive sound (or nothing) in
        `target_mode`."""
        if target_mode == "idle" and not quiet:
            if self.cfg["MUSIC_START_MODE"] == "scheduled":
                log.info(
                    "start_mode=scheduled: silent until %02d:%02d, or until the "
                    "first click (button, or 'Start' in the web interface)",
                    self.cfg["MUSIC_START_HOUR"], self.cfg["MUSIC_START_MINUTE"],
                )
            elif self.cfg["MUSIC_START_MODE"] == "bluetooth":
                log.info(
                    "start_mode=bluetooth: silent until the speaker (%s) is "
                    "connected, or until the first click (button, or 'Start' "
                    "in the web interface)", self.cfg.get("SPEAKER_MAC", "") or "no MAC configured",
                )
            else:
                log.info(
                    "start_mode=action: silent startup, waiting for the "
                    "first click (button, or 'Start' in the web interface)"
                )
        self._end_play("keepalive")
        self._current_track = None
        self.mode = target_mode
        keepalive = self.cfg["KEEPALIVE_SOUND"]
        self._sound_volume = self._source_volume("KEEPALIVE_SOUND")
        if not keepalive:
            log.info("Keep-alive sound disabled: silent while waiting")
            self.mpv.stop_playback()
        elif os.path.exists(keepalive):
            self.mpv.loadfile(keepalive)
            if self._sound_volume is not None:
                self.mpv.set_volume(self._sound_volume)
            self.mpv.set_loop("inf")
            self.mpv.set_pause(False)
        else:
            log.warning(
                "Keep-alive sound not found (%s): the speaker might "
                "power off on its own while waiting", keepalive,
            )

    KEEPALIVE_RETRY_SEC = 15

    def _keepalive_failed(self, error):
        self._keepalive_failures = getattr(self, "_keepalive_failures", 0) + 1
        if self._keepalive_failures == 1 or self._keepalive_failures % 20 == 0:
            log.warning("Keep-alive sound could not play (%s) - %d time(s); retrying every %ds",
                        error or "unknown error", self._keepalive_failures, self.KEEPALIVE_RETRY_SEC)
        if self.mode not in ("idle", "stopped"):
            return
        mode = self.mode

        def retry():
            with self._command_lock:
                if self.mode == mode:
                    self._start_keepalive(target_mode=mode, quiet=True)

        timer = threading.Timer(self.KEEPALIVE_RETRY_SEC, retry)
        timer.daemon = True
        timer.start()

    def _stop_keepalive(self):
        self.mpv.set_loop("no")

    def _restart_is_direct(self):
        """Nothing is playing, so there is no song to wait for."""
        return self.mode in ("idle", "stopped") or self._paused

    def _schedule_restart(self, on, target="service"):
        """"Restart the service (or the whole device) at the end of the song" -
        or at once when nothing is being played."""
        if not on:
            self._restart_pending = False
            self._bump_state()
            return
        self._restart_target = "reboot" if target == "reboot" else "service"
        if self._restart_is_direct():
            self._do_planned_restart()
            return
        self._restart_pending = True
        self._bump_state()
        log.info("%s planned for the end of the song",
                 "Reboot" if self._restart_target == "reboot" else "Service restart")

    def _do_planned_restart(self):
        """Plays RESTART_SOUND (optional, a System sound) then restarts the
        service."""
        self._restart_pending = False
        self.stats.record("daemon_restart", label="end_of_song")
        sound = self.cfg.get("RESTART_SOUND") or ""
        self.mode = "restarting"
        if sound and os.path.exists(sound):
            self._restore_base_volume()
            self._sound_volume = self._source_volume("RESTART_SOUND")
            self._begin_play("restart_cue", sound)
            return
        self._restart_now()

    def _restart_now(self):
        self._end_play("restart")
        if self._restart_target == "reboot":
            log.info("Rebooting (planned from the web interface)")
            self._powering_off = "reboot"
            self._bump_state()
            system_actions.reboot()
            return
        log.info("Restarting the service (planned from the web interface)")
        try:
            system_actions.restart_daemon()
        except OSError:
            log.exception("Could not restart the service")
            self.mode = "idle"

    def _start_or_restart_playback(self, log_label="idle"):
        """First click from idle, or a click/API call restarting playback after
        a non-looping list finished."""
        self._prepare_music_start(log_label)
        self._start_music_faded(self._play_next_track)

    def _prepare_music_start(self, log_label):
        log.info("Starting music (%s)", log_label)
        self.stats.record("music_started", label=log_label)
        self._music_started_mono = time.monotonic()
        self._bt_start_done = True
        was_stopped = self.mode == "stopped"
        self._stop_keepalive()
        if was_stopped:
            tracks = self._playable_tracks()
            if tracks:
                self._rebuild_queue(tracks)

    def _rise_queue_head(self):
        """MORNING_RISE_TRACKS: the first songs of a start go from the quietest to the loudest."""
        count = int(self.cfg.get("MORNING_RISE_TRACKS", 0) or 0)
        resuming = self._resume_armed and self.cfg.get("MUSIC_RESUME_MODE") in ("same_track", "same_position")
        if count < 2 or self._forced_next or resuming:
            return
        try:
            if self._list_library is None:
                self._list_library = library.Library(self.cfg["LIBRARY_DB_FILE"], track_media.track_key)
            levels = self._list_library.loudness_for(self.state.data["play_queue"][:count * 2 + 50])
        except Exception:  # noqa: BLE001 - a missing measurement must never stop the start
            log.exception("Could not read the loudness of the coming songs")
            return
        if levels and self.state.rise_head(min(count, 30), levels.get):
            log.info("The first %d songs rise from the quietest to the loudest", min(count, 30))

    def _start_music_faded(self, start):
        """Runs `start` (which begins the first song) under START_FADE_SEC."""
        self._rise_queue_head()
        try:
            fade = float(self.cfg.get("START_FADE_SEC", 0) or 0)
        except (TypeError, ValueError):
            fade = 0.0
        fade = min(max(fade, 0.0), 120.0)
        self._light_start_until = time.monotonic() + max(3.0, fade)
        # A volume handed to an idle Bluetooth link never reaches the speaker.
        self._sink_resync = self.SINK_RESYNC_TURNS
        self._open_quick_steps()
        if fade <= 0:
            start()
            return
        target = self._target_volume()
        self._stop_volume_glide()
        self._sound_volume = None
        self._current_volume = 0
        self._shown_volume = target
        self._write_level(target)
        self.mpv.set_volume(0)
        start()
        if self.mode == "music":
            log.info("Music starts with a %.0fs fade-in", fade)
            self._glide_volume(target, fade)
        else:
            self._restore_base_volume()

    def _stop_volume_glide(self):
        self._volume_glide_gen += 1
        self._volume_glide_target = None

    def _glide_volume(self, target, duration_sec, curve=None):
        """From where the volume is to `target` over duration_sec, in its own
        thread: the control command answers at once (a slider drag sends a
        request every 150ms), and the next request. When the speaker carries
        the volume, only mpv glides: twenty AVRCP round trips a second are not
        a fade, they are a stutter."""
        self._stop_volume_glide()
        gen = self._volume_glide_gen
        self._volume_glide_target = target
        linked = self._speaker_volume_linked()
        end = self._music_gain() if linked else target
        start = 0.0 if linked else (
            self._current_volume if self._current_volume is not None else end)
        steps = max(1, int(duration_sec * 20))

        def run():
            for i in range(1, steps + 1):
                time.sleep(duration_sec / steps)
                if gen != self._volume_glide_gen:
                    return
                share = curve(i / steps) if curve else i / steps
                vol = round(start + (end - start) * share, 1)
                if not linked:
                    self._current_volume = vol
                self.mpv.set_volume(vol)
            if gen == self._volume_glide_gen:
                self._volume_glide_target = None

        threading.Thread(target=run, daemon=True).start()

    def _fade_out_to_zero(self, duration_sec):
        if self._speaker_volume_linked():
            self._stop_volume_glide()
            gain = self._music_gain()
            steps = 20
            for i in range(steps, -1, -1):
                self.mpv.set_volume(round(gain * i / steps))
                time.sleep(duration_sec / steps)
            return
        self._stop_volume_glide()
        steps = 20
        start_volume = self._current_volume or self._target_volume()
        for i in range(steps, -1, -1):
            vol = round(start_volume * i / steps)
            self._current_volume = vol
            self.mpv.set_volume(vol)
            time.sleep(duration_sec / steps)

    def _fade_out_and_pause(self, duration_sec):
        """Helper shared by ALL actions that stop the music."""
        self._stop_tail()
        self._fade_out_to_zero(duration_sec)
        self.mpv.set_pause(True)
        self._end_play("interrupted")

    def _target_volume(self):
        """The volume to come back to after a fade, a track change, or at
        startup."""
        # _user_volume is the level chosen, _current_volume where the fade is.
        if self.cfg["VOLUME_MODE"] == "session" and self._user_volume is not None:
            return self._user_volume
        return self.cfg["BASE_VOLUME"]

    def _source_volume(self, key):
        """The volume an announcement source or a System sound plays at, or
        None when it follows the music (src/announcements.py)."""
        return announcements.source_volume(self._announce_volumes, key)

    def _restore_base_volume(self):
        self._stop_volume_glide()
        self._sound_volume = None
        self._current_volume = self._target_volume()
        self._shown_volume = self._current_volume
        self._write_level(self._current_volume)

    def _speaker_volume_linked(self):
        """Whether the speaker's own volume IS the radio's volume."""
        return bool(self.cfg.get("SPEAKER_VOLUME_LINK"))

    def _apply_volume_link(self):
        """The link just turned on or off: one volume must stay in charge."""
        if self._speaker_volume_linked():
            self._sink_level = None
            self._write_level(self._current_volume)
            return
        # Left at its last level, the speaker would multiply the software volume.
        self._set_sink_volume(100)
        if self._sound_volume is not None:
            self.mpv.set_volume(self._sound_volume)
        else:
            self._current_volume = self._target_volume()
            self.mpv.set_volume(self._current_volume)

    def _music_gain(self):
        """What mpv is set to when the speaker carries the volume: what an
        announcement or a System sound asks for, or everything."""
        return self._sound_volume if self._sound_volume is not None else 100.0

    def _set_sink_volume(self, vol):
        """Hands `vol` percent to the output itself (a Bluetooth speaker's own
        volume, over AVRCP)."""
        percent = max(0.0, min(100.0, float(vol)))
        if audio_diag.set_default_sink_volume(percent, env=audio_env()):
            self._sink_level = percent
            return True
        if not self._sink_warned:
            self._sink_warned = True
            log.warning("Could not set the speaker's own volume: the radio keeps "
                        "its software volume instead")
        return False

    def _write_level(self, vol):
        """Puts the radio at `vol` percent. Two models: the speaker's own
        volume is the volume, and mpv then carries only what a sound asks for -
        or the radio's software volume is, and the speaker's is a second one on
        top of it."""
        if self._speaker_volume_linked() and self._set_sink_volume(vol):
            self.mpv.set_volume(self._music_gain() * self._duck_factor)
            return
        self.mpv.set_volume(vol * self._duck_factor)

    def _volume_watch_loop(self):
        """Follows the speaker's own volume when the two are linked."""
        while not self._stop_event.wait(self.SINK_POLL_SEC):
            if self.mode == "shutting_down":
                return
            self._guarded("Volume watch", self._follow_sink_volume)

    def _guarded(self, name, turn):
        """One turn of a background loop: an error costs that turn, not the loop."""
        try:
            return turn()
        except Exception:  # noqa: BLE001
            log.exception("%s: this turn failed, the next one will try again", name)
            return None

    def _follow_sink_volume(self):
        """One watch turn: the speaker's own volume, when it is linked or locked."""
        linked = self._speaker_volume_linked()
        locked = bool(self.cfg.get("SPEAKER_VOLUME_LOCK"))
        quick = self._quick_step() > 0 and time.monotonic() < self._quick_until
        if not linked and not locked and not quick:
            self._sink_level = None
            self._sink_name = None
            self._sink_resync = 0
            return
        self._sink_turn += 1
        percent = None
        if self._sink_level is not None and not self._sink_resync \
                and self._sink_turn % self.SINK_NAME_EVERY:
            percent = self._read_sink_percent()
            if percent is None or abs(percent - self._sink_level) <= 1.0:
                return
        name = audio_diag.default_sink(env=audio_env())[0]
        if name and name != self._sink_name:
            # Another output (the speaker just connected): it knows nothing of our volume.
            self._sink_name = name
            self._sink_resync = self.SINK_RESYNC_TURNS
        if quick and not locked and self._sink_level is not None \
                and 0 < self._sink_resync < self.SINK_RESYNC_TURNS:
            # Already handed over once since the start: a change now is a press.
            pressed = self._read_sink_percent()
            if pressed is not None and abs(pressed - self._sink_level) > 1.0:
                self._sink_resync = 0
                self._quick_press(pressed, linked)
                return
        if self._sink_level is None or self._sink_resync:
            self._sink_resync = max(0, self._sink_resync - 1)
            if linked:
                self._assert_sink_volume(self._target_volume())
            else:
                # Locked without the link: the level the speaker came with is the one held.
                found = percent if percent is not None else self._read_sink_percent()
                if found is not None:
                    self._sink_level = found
            return
        if percent is None:
            percent = self._read_sink_percent()
        if percent is None or abs(percent - self._sink_level) <= 1.0:
            return
        name = audio_diag.default_sink(env=audio_env())[0]
        if name and not name.startswith("bluez_") and not self._wired_output():
            # The speaker is gone and PipeWire fell back to another output: not its volume.
            self._sink_name = name
            return
        if locked:
            log.info("Speaker volume buttons are locked: back to %.0f%%", self._sink_level)
            self._assert_sink_volume(self._sink_level)
            return
        if quick:
            self._quick_press(percent, linked)
            return
        if not linked:
            self._sink_level = percent
            return
        self._sink_level = percent
        self._adopt_volume(percent)

    def _quick_step(self):
        try:
            return max(0, min(50, int(self.cfg.get("SPEAKER_QUICK_STEP", 0) or 0)))
        except (TypeError, ValueError):
            return 0

    def _open_quick_steps(self):
        """The speaker's volume buttons take big steps for a moment."""
        if self._quick_step() > 0:
            self._quick_until = time.monotonic() + self.QUICK_WINDOW_SEC

    def _quick_press(self, percent, linked):
        """A press soon after the start: the step is SPEAKER_QUICK_STEP, in the
        press's direction, and the big steps last 10 seconds more."""
        step = self._quick_step()
        before = self._sink_level
        target = max(0.0, min(100.0, before + (step if percent > before else -step)))
        log.info("Speaker volume pressed right after the start: %.0f%% -> %.0f%%", before, target)
        self._assert_sink_volume(target)
        self._sink_level = target
        self._quick_until = time.monotonic() + self.QUICK_WINDOW_SEC
        if linked:
            self._adopt_volume(target)

    def _read_sink_percent(self):
        found = audio_diag.default_sink_volume(env=audio_env())
        if found is None:
            return None
        return max(0.0, min(100.0, round(found * 100.0, 1)))

    def _assert_sink_volume(self, vol):
        """Hands the interface's volume to the output, whatever the output
        believes it already has."""
        # PipeWire sends nothing to the speaker for a value it thinks is in place.
        vol = max(0.0, min(100.0, float(vol)))
        audio_diag.set_default_sink_volume(vol - 1 if vol >= 1 else vol + 1, env=audio_env())
        self._set_sink_volume(vol)

    def _adopt_volume(self, percent):
        """The speaker was moved by hand (or by another program): make that the
        volume, so the slider and the speaker cannot drift apart."""
        log.info("Speaker volume: %.0f%% (followed)", percent)
        self._user_volume = percent
        self._shown_volume = percent
        self._current_volume = percent
        now = time.monotonic()
        if now - self._last_volume_event > 30:
            self._last_volume_event = now
            self.stats.record("volume_set", label=str(round(percent)),
                              detail={"source": "speaker", "volume": round(percent)})
        self._bump_state()

    def _list_announce_files(self, directory, source_id=None):
        """Every audio file in `directory`, ordered per ANNOUNCE_ORDER_MODE."""
        if not os.path.isdir(directory):
            log.warning("Announcement folder not found: %s", directory)
            return []
        files = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if os.path.splitext(f)[1].lower() in announcements.AUDIO_EXTENSIONS
        ]
        custom_order = None
        if source_id:
            custom_order = track_order.get(self.cfg["TRACK_ORDER_FILE"], source_id)
        return playlist.order_files(files, directory, self.cfg["ANNOUNCE_ORDER_MODE"], custom_order)

    def _next_announce_file(self, source_id, folder):
        """ONE file of an announcement folder, in turn: asked for as "an
        announcement plays a single file each time"."""
        files = self._list_announce_files(folder, source_id=source_id)
        by_name = {os.path.basename(f): f for f in files}
        name = self.state.next_click_sound(source_id, lambda: [os.path.basename(f) for f in files])
        return [by_name[name]] if name in by_name else []

    def _play_announce_queue(self, mode_name, files, volume_key=None, after=None):
        self._announce_queue = list(files)
        self._after_action = after
        self._sound_volume = self._source_volume(volume_key) if volume_key else None
        if not self._announce_queue:
            log.warning("No announcement to play for %s, continuing directly", mode_name)
            self._after_announce_finished(mode_name)
            return
        self.mode = mode_name
        first = self._announce_queue.pop(0)
        self._begin_play(mode_name, first)

    def _resume_after_announce(self):
        """What happens once an announcement/sound (other than the cutoff,
        which shuts down instead) finishes: resume music, the normal case."""
        resume, self._resume_track = self._resume_track, None
        if self._resume_mode == "stopped":
            self._enter_stopped_mode()
        elif resume and resume[0] and os.path.exists(resume[0]):
            self._play_track(resume[0], start=resume[1])
        else:
            self._play_next_track()

    def _after_announce_finished(self, mode_name):
        if mode_name == "cutoff_announce":
            self._do_shutdown_sequence(tail=True)
        elif mode_name == "meme":
            self._resume_after_announce()
        elif mode_name.startswith("custom:"):
            action, self._after_action = self._after_action, None
            self._restore_base_volume()
            self._finish_scheduled_announcement(action)
        elif mode_name.startswith("button_announce:"):
            self._restore_base_volume()
            self._resume_after_announce()

    def _finish_scheduled_announcement(self, action):
        """What follows an announcement that started on its own: the music
        again, then the action chosen for it."""
        if action == "poweroff":
            log.info("After the announcement: switching the Pi off")
            self.mode = "shutting_down"
            self._do_shutdown_sequence(force=True, reason="announcement", tail=True)
            return
        if action == "standby":
            self._go_standby("announcement", fade=False)
            return
        self._resume_after_announce()
        if not action or action == "none":
            return
        log.info("After the announcement: %s", action)
        if action == "pause":
            if self.mode == "music":
                # Not _set_pause(): its fade would let the song be heard first.
                self.mpv.set_pause(True)
                self._paused = True
                self._bump_state()
                self.stats.record("playback_pause", label="paused", detail={"source": "announcement"})
            return
        self._perform_direct_action(action, "announcement")

    def _schedules(self):
        """The schedules, re-read whenever the file changed."""
        path = self.cfg.get("SCHEDULES_FILE") or ""
        stamp = _file_stamp(path)
        if stamp != self._schedules_stamp:
            items = schedules.read_items(path) if path else []
            if items is None:
                log.error("Could not read %s: keeping the %d schedule(s) already loaded",
                          path, len(self._schedule_items))
            else:
                self._schedule_items = items
            self._schedules_stamp = stamp
        return self._schedule_items

    def _schedule_tick(self, now):
        """Stops first, then whose settings hold now, then starts - so a
        start already plays with its schedule's settings."""
        if self.mode in ("shutting_down", "restarting"):
            return
        items = self._schedules()
        for item in items:
            if item.get("enabled", True) and schedules.stop_due(item, now) \
                    and self._schedule_once("stop", item, now):
                self._schedule_stop(item)
        if self.mode == "shutting_down":
            return
        self._follow_schedule(schedules.active(items, now))
        for item in items:
            if item.get("enabled", True) and schedules.start_due(item, now) \
                    and self._schedule_once("start", item, now):
                self._schedule_start(item)

    def _schedule_once(self, what, item, now):
        """True the first time in this minute. Kept in memory, not per day: a
        time moved later the same day must still fire."""
        key, minute = (what, item["id"]), now.strftime("%Y-%m-%d %H:%M")
        if self._schedule_fired.get(key) == minute:
            return False
        self._schedule_fired[key] = minute
        return True

    def _follow_schedule(self, active):
        """Applies the running schedule's settings, list and volume - or takes
        them back when no schedule runs any more."""
        signature = None if active is None else (
            active["id"], tuple(sorted((active.get("settings") or {}).items())), active.get("list"))
        if signature == self._schedule_signature:
            self._schedule_active = active
            return
        before = self._schedule_active
        self._schedule_signature = signature
        self._schedule_active = active
        had_volume = "BASE_VOLUME" in self._schedule_overrides
        self._schedule_overrides = schedules.overrides(active)
        try:
            fresh = load_config(env_overrides=False)
        except Exception:  # noqa: BLE001
            log.exception("Could not re-read the configuration for a schedule")
            fresh = None
        if fresh is not None:
            fresh.update(self._schedule_overrides)
            self._apply_config(fresh)
        if had_volume or "BASE_VOLUME" in self._schedule_overrides:
            volume = self.cfg["BASE_VOLUME"]
            if self.mode in ("music", "idle", "stopped"):
                self._set_user_volume(volume, "schedule")
            else:
                # An announcement is playing at its own level: the music's is for after it.
                self._user_volume = self._shown_volume = float(volume)

        wanted = active.get("list") if active else None
        if wanted is not None:
            if self._schedule_list_before is _NO_LIST:
                self._schedule_list_before = self.state.active_list()
            if (wanted or None) != self.state.active_list():
                self._set_active_list(wanted or None, "schedule")
        elif self._schedule_list_before is not _NO_LIST:
            previous, self._schedule_list_before = self._schedule_list_before, _NO_LIST
            if previous != self.state.active_list():
                self._set_active_list(previous, "schedule")

        if active and (not before or before["id"] != active["id"]):
            log.info("Schedule '%s' is running until %s", active["name"], active["end"].strftime("%H:%M"))
            self.stats.record("schedule_started", label=active["name"],
                              detail={"id": active["id"], "settings": sorted(self._schedule_overrides)})
        if before and (not active or before["id"] != active["id"]):
            log.info("Schedule '%s' is over", before["name"])
            self.stats.record("schedule_ended", label=before["name"], detail={"id": before["id"]})
        self._bump_state()

    def _schedule_start(self, item):
        log.info("Schedule '%s': start", item["name"])
        opening = self._schedule_announcement(item)
        if self.mode in ("idle", "stopped"):
            self.state.mark_triggered_today("last_music_start")
            if opening:
                self._start_music_after(opening)
            else:
                self._start_or_restart_playback(log_label="schedule")
        elif self.mode == "music":
            if self._paused:
                self._set_pause(False, "schedule")
            if opening:
                # Its own daily time, if it has one, stays its own: this play does not use it up.
                self._trigger_custom_announcement(opening, mark=False)

    def _schedule_announcement(self, item):
        """The announcement a schedule opens with, or None."""
        wanted = item.get("announcement")
        if not wanted:
            return None
        found = next((one for one in self._custom_announcements if one["id"] == wanted), None)
        if found is None:
            log.warning("Schedule '%s' opens with an announcement that no longer exists (%s)",
                        item["name"], wanted)
            return None
        return found if found.get("enabled", True) else None

    def _start_music_after(self, announcement):
        """Starts the day with an announcement: it plays first, the music follows."""
        self._prepare_music_start("schedule")
        self.stats.record(
            "custom_announce_triggered", label=announcement["name"],
            detail={"id": announcement["id"], "trigger": "schedule", "on_demand": False},
            counters={"custom_announces": 1}, daily={"custom_announces": 1},
        )
        self._resume_mode = "music"
        self._restore_base_volume()
        source_id = "custom:%s" % announcement["id"]
        self._play_announce_queue(source_id, self._next_announce_file(source_id, announcement["folder"]),
                                  volume_key=source_id, after=announcement.get("after_action"))

    def _schedule_stop(self, item):
        action = item.get("stop_action") or "pause"
        log.info("Schedule '%s': stop (%s)", item["name"], action)
        self.stats.record("schedule_stop", label=item["name"], detail={"id": item["id"], "action": action})
        if action == "poweroff":
            self._power_off_now(reason="schedule")
        elif action == "standby":
            self._go_standby("schedule")
        elif self.mode == "music" and not self._paused:
            self._set_pause(True, "schedule")

    def _schedule_status(self):
        active = self._schedule_active
        if not active:
            return None
        return {"id": active["id"], "name": active["name"], "until": active["end"].strftime("%H:%M"),
                "stop_action": active.get("stop_action") if active.get("stop") else None}

    def _next_schedule_status(self):
        found = schedules.next_start(self._schedule_items, datetime.now())
        if not found:
            return None
        item, when = found
        return {"id": item["id"], "name": item["name"], "at": when.strftime("%H:%M"),
                "date": when.strftime("%Y-%m-%d")}

    def _trigger_cutoff_event_exact(self):
        log.info("Triggering the cutoff (exact mode)")
        self._record_cutoff_trigger("exact")
        self._fade_out_and_pause(self.cfg["FADE_DURATION_SEC"])
        self._restore_base_volume()
        files = self._next_announce_file("cutoff", self.cfg["CUTOFF_ANNOUNCE_DIR"])
        self._play_announce_queue("cutoff_announce", files, volume_key="cutoff")
        self.state.mark_triggered_today("last_cutoff_trigger")

    def _trigger_cutoff_from_idle(self):
        """The scheduled cutoff arrives while nothing is really playing: either
        idle."""
        log.info(
            "Cutoff time reached with nothing really playing (%s): "
            "shutting down directly", self.mode,
        )
        self._record_cutoff_trigger("from_idle")
        self._stop_keepalive()
        files = self._next_announce_file("cutoff", self.cfg["CUTOFF_ANNOUNCE_DIR"])
        self._play_announce_queue("cutoff_announce", files, volume_key="cutoff")
        self.state.mark_triggered_today("last_cutoff_trigger")

    def _pending_cutoff_still_due(self):
        """A cutoff that has waited longer than any song is a leftover, not a cutoff."""
        age = self.state.pending_cutoff_age()
        if age is not None and age <= self.CUTOFF_PENDING_MAX_SEC:
            return True
        log.info("The cutoff waiting for the end of a track is %s old: dropped",
                 "%d min" % (age // 60) if age is not None else "of unknown age")
        self.state.set_pending_cutoff(False)
        return False

    def _arm_cutoff_end_of_track(self):
        log.info("Scheduled cutoff: waiting for the end of the current track")
        self._record_cutoff_trigger("end_of_track")
        self.state.set_pending_cutoff(True)
        self.state.mark_triggered_today("last_cutoff_trigger")

    def _trigger_custom_announcement(self, item, on_demand=False, mark=True):
        """A user-defined announcement type (src/announcements.py): fade out,
        play its folder in order, resume music."""
        log.info("Triggering custom announcement '%s'", item["name"])
        self.stats.record(
            "custom_announce_triggered", label=item["name"],
            detail={
                "id": item["id"],
                "trigger": item.get("trigger"),
                "on_demand": on_demand,
            },
            counters={"custom_announces": 1}, daily={"custom_announces": 1},
        )
        source_id = "custom:%s" % item["id"]
        spoken = self._speech_file(item.get("speech") or "none")
        files = ([spoken] if spoken else []) + self._next_announce_file(source_id, item["folder"])
        if self._play_ducked(source_id, files, volume_key=source_id,
                             after=None if on_demand else item.get("after_action")):
            if mark and not on_demand and item.get("trigger") == "time":
                self.state.mark_triggered_today("custom_%s" % item["id"])
            return
        self._resume_mode = self.mode
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"] if on_demand
                                 else self.cfg["FADE_DURATION_SEC"])
        self._restore_base_volume()
        self._play_announce_queue(source_id, files,
                                  volume_key=source_id,
                                  after=None if on_demand else item.get("after_action"))
        if mark and not on_demand and item.get("trigger") == "time":
            self.state.mark_triggered_today("custom_%s" % item["id"])

    def _record_cutoff_trigger(self, cutoff_mode):
        self.stats.record(
            "cutoff_triggered", label=cutoff_mode,
            detail={"scheduled": "%02d:%02d" % (self.cfg["CUTOFF_HOUR"], self.cfg["CUTOFF_MINUTE"])},
            counters={"cutoff_triggers": 1}, daily={"cutoff_triggers": 1},
        )

    def _start_cutoff_announce_now(self):
        log.info("End of current track -> starting the cutoff announcement")
        self.state.set_pending_cutoff(False)
        files = self._next_announce_file("cutoff", self.cfg["CUTOFF_ANNOUNCE_DIR"])
        self._play_announce_queue("cutoff_announce", files, volume_key="cutoff")

    # mpv reports a file over when it has handed the last samples on: the
    # speaker still has a second or two of them to play.
    SHUTDOWN_TAIL_SEC = 3.0

    def _do_shutdown_sequence(self, force=False, reason="cutoff", tail=False):
        if not force and not self.cfg["SHUTDOWN_AFTER_CUTOFF"]:
            self._cutoff_standby()
            return
        self._end_play("shutdown")
        log.info("Shutting down the Raspberry Pi")
        self.stats.record("shutdown", label=reason, detail={"poweroff": True})
        self.stats.end_session(reason)
        self._powering_off = True
        self._bump_state()
        time.sleep(self.SHUTDOWN_TAIL_SEC if tail else 2)

        mac = self.cfg["SPEAKER_MAC"]
        if mac and mac != "XX:XX:XX:XX:XX:XX":
            log.info("Disconnecting Bluetooth from %s", mac)
            self._bluetoothctl("disconnect", mac, timeout=10)
        if self._light_audio is not None:
            self._light_audio.stop()
        self._lights.switch_off()
        system_actions.power_off()

    def _cutoff_standby(self):
        """The cutoff with SHUTDOWN_AFTER_CUTOFF off: the Pi stays on and waits
        as it does at startup, the speaker still connected."""
        log.info("Cutoff: the Pi stays on, in standby")
        self._end_play("shutdown")
        self._set_timer("resume", None)
        self._set_timer("sleep", None)
        self._announce_queue = []
        self._forced_next = None
        self._resume_track = None
        self._pending_seek = None
        self._paused = False
        self._restore_base_volume()
        self.stats.record("standby", label="cutoff", detail={"source": "cutoff"})
        self._start_keepalive(target_mode="idle", quiet=True)
        self._bump_state()

    def _record_click(self, kind, source, action, target=None):
        """One row per button press, with what it actually did."""
        counters = {"clicks_speaker" if kind.startswith("speaker_") else "clicks_%s" % kind: 1}
        if source in ("flic", "gpio", "web"):
            counters["clicks_%s" % source] = 1
        if action == "ignored":
            counters["clicks_ignored"] = 1
        self.stats.record(
            "click", label=kind,
            detail={"source": source, "action": action, "target": target},
            counters=counters, daily={"clicks_%s" % kind: 1},
        )
        if action != "ignored":
            self._lights.flash("button")

    def _click_source_folder(self, source):
        return announcements.resolve_source_folder(self.cfg, self._custom_announcements, source)

    CLICK_ACTIONS = (
        "next", "previous", "sound",
        "playpause", "pause", "play",
        "loop_track", "loop_album", "loop_off",
        "volume_up", "volume_down", "sleep",
        "mute", "standby", "poweroff", "time",
        "off",
    )
    LONG_PRESS_ACTIONS = ("poweroff", "standby")
    SOUND_ACTIONS = ("next", "previous", "sound", "time")
    START_ACTIONS = ("play", "playpause")

    def _pick_click_sound(self, sound_source):
        """One file of the list `sound_source`, in the announcement order, or
        None."""
        if not sound_source or sound_source == "none":
            return None
        if sound_source.startswith("custom:"):
            item = next((i for i in self._custom_announcements
                         if i["id"] == sound_source[len("custom:"):]), None)
            if item and not self.state.chance_allows("manual:" + item["id"], item.get("manual_chance", "1/1")):
                log.info("Click sound '%s': not this time (%s)", item["name"], item.get("manual_chance"))
                return None
        folder = self._click_source_folder(sound_source)
        if not folder:
            log.warning("Click sound list '%s' does not resolve to a folder", sound_source)
            return None
        files = self._list_announce_files(folder, source_id=sound_source)
        by_name = {os.path.basename(f): f for f in files}
        name = self.state.next_click_sound(sound_source, lambda: [os.path.basename(f) for f in files])
        return by_name.get(name) if name else None

    def _perform_click_action(self, kind, source, action, sound_source):
        """The configurable half of a single/double click, once the idle-mode
        and busy-mode special cases."""
        if action not in self.CLICK_ACTIONS:
            log.error("%s click: unknown action '%s', treated as \"off\"", kind, action)
            action = "off"
        if action == "off":
            log.info("%s click: action is set to \"off\", ignoring", kind)
            self._record_click(kind, source, "ignored", target="off")
            return
        if action not in self.SOUND_ACTIONS:
            self._record_click(kind, source, self._perform_direct_action(action, source))
            return

        if action == "time":
            error = self._speak("time", source)
            self._record_click(kind, source, "ignored" if error else "time_then_resume", target=error)
            return
        chosen = self._pick_click_sound(sound_source)
        if action == "sound" and not chosen:
            log.info("%s click: no sound to play in '%s'", kind, sound_source)
            self._record_click(kind, source, "ignored", target="no_sound")
            return
        resume = (self._last_music_track, self._position) if action == "sound" else None
        if action == "previous":
            self._step_back()
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
        self._restore_base_volume()
        if not chosen:
            log.info("%s click -> %s track", kind, action)
            self._record_click(kind, source,
                               "next_track_no_sound" if action == "next" else "previous_track",
                               target=sound_source if sound_source not in ("", "none") else None)
            self._play_next_track(user=True)
            return
        log.info("%s click -> sound %s, then %s", kind, chosen,
                 "the same song" if action == "sound" else "the %s track" % action)
        self._record_click(kind, source,
                           {"next": "sound_then_next", "previous": "sound_then_previous",
                            "sound": "sound_then_resume"}[action],
                           target=os.path.basename(chosen))
        self._resume_mode = "music"
        self._resume_track = resume
        self._next_is_user = action == "next"
        self.mode = "meme"
        self._sound_volume = self._source_volume(sound_source)
        self._begin_play("meme", chosen)

    def _speech_dir(self):
        return os.path.join(self.cfg.get("STATE_DIR") or "/tmp", "speech")

    def _speech_file(self, kind, minutes=None):
        """A WAV of `kind` said now, named after its own words; None when it cannot be said."""
        return self._speech_text_file(
            speech.sentence(kind, datetime.now(), self.cfg.get("SPEECH_LANGUAGE"), minutes))

    def _speech_text_file(self, text):
        """A WAV of `text`, named after its own words; None when it cannot be said."""
        if not text:
            return None
        folder = self._speech_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            for old in os.listdir(folder):
                os.remove(os.path.join(folder, old))
        except OSError:
            log.warning("Cannot prepare %s", folder, exc_info=True)
        name = "".join(c for c in text if c not in "/\\").strip()[:120] + ".wav"
        path = os.path.join(folder, name)
        self._speech_busy += 1
        try:
            return path if speech.render(text, self.cfg.get("SPEECH_LANGUAGE"), path,
                                         self.cfg.get("PIPER_VOICE")) else None
        finally:
            self._speech_busy -= 1

    def _warm_speech(self, text):
        """Prepares a sentence that is coming, in the background."""
        text = (text or "").strip()
        if not text or text == self._speech_warm or self._speech_warming is not None:
            return False
        self._speech_warming = text
        threading.Thread(target=self._warm_speech_now, args=(text,), daemon=True).start()
        return True

    def _warm_speech_now(self, text):
        try:
            os.nice(10)
        except (AttributeError, OSError):
            # No os.nice on Windows, and no permission for it on some systems.
            pass
        try:
            folder = self._speech_dir()
            os.makedirs(folder, exist_ok=True)
            speech.render(text, self.cfg.get("SPEECH_LANGUAGE"),
                          os.path.join(folder, "warm.wav"), self.cfg.get("PIPER_VOICE"))
        except Exception:  # noqa: BLE001 - a preparation must never take the radio down
            log.debug("Could not prepare the sentence", exc_info=True)
        finally:
            self._speech_warming = None
            self._speech_warm = text

    def _warm_announcement_speech(self, item, now, lead_min=2):
        """Prepares an announcement's spoken time a few minutes before it is due."""
        kind = item.get("speech")
        if not kind or kind == "none":
            return False
        due = now.replace(hour=item["hour"], minute=item["minute"], second=0, microsecond=0)
        left = (due - now).total_seconds() / 60
        if not 0 < left <= lead_min:
            return False
        return self._warm_speech(
            speech.sentence(kind, due, self.cfg.get("SPEECH_LANGUAGE")))

    def _speak(self, kind, source, minutes=None):
        """Says the time (or the coming cutoff): the song pauses and comes back
        where it was; with nothing playing it is a cue on its own."""
        if self.mode not in ("music", "idle", "stopped"):
            return "busy"
        path = self._speech_file(kind, minutes)
        if not path:
            return "speech_unavailable"
        self.stats.record("speech_played", label=kind, detail={"source": source})
        return self._say_path(path)

    REMINDER_UNDER = 25

    def _say_path(self, path, under=None):
        log.info("Saying: %s", os.path.splitext(os.path.basename(path))[0])
        if self.mode != "music" or self._paused:
            self._play_cue_sound(path)
            return None
        if self._play_ducked("speech", [path], under=under):
            return None
        resume = (self._last_music_track, self._position)
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
        self._restore_base_volume()
        self._resume_mode = "music"
        self._resume_track = resume
        self._next_is_user = False
        self.mode = "meme"
        self._sound_volume = None
        self._begin_play("speech", path)
        return None

    def _game_clip(self, path, start, seconds):
        """A blind test's extract: the radio steps aside, the clip plays and
        pauses after `seconds`, and nothing follows on by itself."""
        path = self._library_path(path)
        if not path:
            return "not_found"
        if self.mode != "game":
            if self.mode not in ("music", "idle", "stopped"):
                return "busy"
            back = (self._last_music_track, self._position) if self.mode == "music" else None
            self._game_return = (self.mode, back)
            if self.mode == "music" and not self._paused:
                self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
            log.info("Blind test: the radio steps aside")
        self._restore_base_volume()
        self.mode = "game"
        self._sound_volume = None
        self._pending_seek = start if start and start > 1 else None
        self._begin_play("game", path)
        self._paused = False

        def hush():
            if self.mode == "game":
                self.mpv.set_pause(True)
                self._paused = True
        self._set_timer("game", max(3.0, min(float(seconds or 20), 60.0)), hush)
        return None

    def _game_end(self):
        """The blind test is over: the radio takes up what it was doing."""
        if self.mode != "game":
            return
        self._set_timer("game", None)
        self._end_play("stop")
        mode, back = getattr(self, "_game_return", None) or ("idle", None)
        self._game_return = None
        log.info("Blind test over: back to %s", mode)
        if mode == "music":
            self._resume_mode = "music"
            self._resume_track = back
            self._resume_after_announce()
        else:
            self._start_keepalive(mode, quiet=True)

    CARD_SHOWN_SEC = 120

    def _last_card_status(self):
        card = self._last_card
        if not card or time.monotonic() - card["mono"] > self.CARD_SHOWN_SEC:
            return None
        return {k: card[k] for k in ("id", "name", "known", "error")}

    def _card(self, card_id, source):
        """What the card held on the reader starts."""
        card = cards.load(self.cfg.get("CARDS_FILE") or "").get(card_id)
        self._last_card = {"id": card_id, "name": card["name"] if card else None, "known": bool(card),
                           "error": None, "mono": time.monotonic()}
        self.stats.record("card_read", label=card["name"] if card else card_id,
                          detail={"id": card_id, "known": bool(card)})
        error = "card_unknown" if not card else self._card_action(card, source)
        self._last_card["error"] = error
        self._bump_state()
        return error

    def _card_action(self, card, source):
        action, target = card["action"], card.get("target") or ""
        log.info("Card '%s': %s %s", card["name"], action, target)
        if action == "list":
            result = self._set_active_list(target or None, source, start=True)
            return None if result.get("ok") else result.get("error")
        if action == "folder":
            return self._play_folder(target, source)
        if action == "announcement":
            item = next((i for i in self._custom_announcements if i["id"] == target), None)
            if item is None:
                return "unknown_announcement"
            if self.mode not in ("music", "idle", "stopped"):
                return "busy"
            self._trigger_custom_announcement(item, on_demand=True)
            return None
        if target not in self.CLICK_ACTIONS:
            return "card_bad_action"
        if self.mode in ("idle", "stopped"):
            if target in self.START_ACTIONS or target in ("next", "sound"):
                self._start_or_restart_playback(log_label="card")
                return None
            if target == "time":
                return self._speak("time", source)
            if target == "poweroff":
                self._power_off_now(reason="button")
                return None
            return "not_playing_music"
        if self.mode != "music":
            return "busy"
        self._perform_click_action("card", source, target, "none")
        return None

    def _play_folder(self, folder, source):
        """Plays one folder of the library, in order; the radio goes on afterwards."""
        root = os.path.realpath(self._music_dir())
        folder = os.path.realpath(os.path.join(root, folder) if not folder.startswith("/") else folder)
        if folder != root and not folder.startswith(root + os.sep):
            return "not_found"
        tracks = [t for t in self._playable_tracks() if os.path.realpath(t).startswith(folder + os.sep)]
        if not tracks:
            return "not_found"
        self.state.rebuild_queue(tracks, "ordered", self._music_dir())
        self._loop_mode = "off"
        self._forced_next = None
        self.stats.record("folder_played", label=os.path.basename(folder),
                          detail={"source": source, "tracks": len(tracks)})
        if self.mode in ("idle", "stopped"):
            self.mode = "idle"
            self._start_or_restart_playback(log_label="card")
        elif self.mode == "music":
            self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
            self._restore_base_volume()
            self._play_next_track(user=True)
        else:
            return "busy"
        return None

    def _music_under(self):
        try:
            return max(0, min(80, int(self.cfg.get("ANNOUNCE_MUSIC_UNDER", 0) or 0)))
        except (TypeError, ValueError):
            return 0

    def _duck_possible(self, files, under=None):
        level = self._music_under() if under is None else under
        return (level > 0 and bool(files) and self.mode == "music"
                and not self._paused and self._duck_proc is None)

    def _set_duck(self, factor, seconds=0.8):
        """Brings the music's level to `factor` of itself, in small steps."""
        linked = self._speaker_volume_linked()
        start = self._duck_factor
        steps = 8
        for i in range(1, steps + 1):
            self._duck_factor = start + (factor - start) * i / steps
            base = self._music_gain() if linked else (self._current_volume or self._target_volume())
            self.mpv.set_volume(round(base * self._duck_factor, 1))
            time.sleep(seconds / steps)

    def _play_ducked(self, kind, files, volume_key=None, after=None, under=None):
        """Plays `files` over the music, the music kept under them; True when
        it does (False: the caller pauses the music as before)."""
        if not self._duck_possible(files, under):
            return False
        level = self._music_under() if under is None else under
        volume = self._source_volume(volume_key) if volume_key else None
        log.info("Playing over the music (kept at %d%%): %s", level,
                 ", ".join(os.path.basename(f) for f in files))
        self._duck_proc = "starting"
        self._stop_tail()

        def run():
            try:
                self._set_duck(level / 100.0)
                for path in files:
                    if self._duck_proc is None:
                        break
                    self._last_sound = {"path": path, "kind": kind, "at": time.monotonic()}
                    self._bump_state()
                    started = time.monotonic()
                    self._play_cue_blocking(path, volume)
                    if os.path.dirname(path) != self._speech_dir():
                        seconds = time.monotonic() - started
                        name = os.path.basename(path)
                        self.stats.record(
                            "announce_played", label=name,
                            detail={"kind": kind, "seconds": round(seconds, 1), "reason": "eof", "under": True},
                            counters={"announcements_played": 1, "seconds_announce": seconds},
                            daily={"seconds_announce": seconds}, item=(kind, name), seconds=seconds)
            finally:
                self._duck_proc = None
                self._set_duck(1.0)
            if after and after != "none":
                with self._command_lock:
                    log.info("After the announcement: %s", after)
                    self._perform_direct_action(after, "announcement")
                    self._bump_state()

        threading.Thread(target=run, daemon=True).start()
        return True

    def _play_cue_blocking(self, path, volume=None):
        command = ["mpv", "--no-terminal", "--really-quiet", "--audio-device=" + (self._audio_device or "auto")]
        if volume is not None:
            command.append("--volume=%.1f" % volume)
        command.append(path)
        try:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env=audio_env())
        except OSError:
            log.exception("Could not launch mpv for a sound over the music")
            return
        self._duck_proc = proc
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
        if self._duck_proc is proc:
            self._duck_proc = "between"

    def _skip_ducked(self):
        """The sound playing over the music, stopped."""
        proc = self._duck_proc
        if proc is None:
            return False
        self._duck_proc = None
        if hasattr(proc, "terminate"):
            proc.terminate()
        return True

    REMINDER_LATE_SEC = 600

    def _check_reminders(self):
        """Says the reminders that are due; one missed by more than ten
        minutes (the radio was off) is dropped."""
        now = time.time()
        for item in self.state.reminders():
            if item.get("at", 0) > now:
                break
            if now - item["at"] > self.REMINDER_LATE_SEC:
                log.info("Reminder missed, dropped: %s", item.get("text"))
                self.state.remove_reminder(item["id"])
                continue
            if self.mode not in ("music", "idle", "stopped") or self._duck_proc is not None:
                return
            text = speech.reminder_sentence(item.get("text"), self.cfg.get("SPEECH_LANGUAGE"))
            path = self._speech_text_file(text)
            self.state.remove_reminder(item["id"])
            self._bump_state()
            if not path:
                log.warning("Reminder could not be said: %s", item.get("text"))
                continue
            self.stats.record("reminder_said", label=speech.clean_text(item.get("text"), 80))
            self._say_path(path, under=self._music_under() or self.REMINDER_UNDER)
            return

    def _check_cutoff_warning(self, now):
        """Says the cutoff is coming, CUTOFF_WARNING_MIN minutes ahead, once a day."""
        minutes = int(self.cfg.get("CUTOFF_WARNING_MIN", 0) or 0)
        if minutes <= 0 or not self.cfg.get("CUTOFF_ENABLED", True) or self.mode != "music":
            return
        cutoff = now.replace(hour=self.cfg["CUTOFF_HOUR"], minute=self.cfg["CUTOFF_MINUTE"],
                             second=0, microsecond=0)
        left = (cutoff - now.replace(second=0, microsecond=0)).total_seconds() / 60
        if 0 < left - minutes <= 2:
            # A sentence said every day at a known minute: prepare it now, so the
            # minute itself is not spent loading a model.
            self._warm_speech(speech.sentence("cutoff", cutoff, self.cfg.get("SPEECH_LANGUAGE"),
                                              minutes=minutes))
        if left != minutes or self.state.already_triggered_today("cutoff_warning"):
            return
        self.state.mark_triggered_today("cutoff_warning")
        self._lights.flash("cutoff")
        self._speak("cutoff", "scheduler", minutes=minutes)

    def _perform_direct_action(self, action, source):
        """The actions that play no sound first."""
        if action in ("playpause", "pause", "play"):
            want = (not self._paused) if action == "playpause" else action == "pause"
            if want == self._paused:
                return "paused" if want else "resumed"
            self._set_pause(want, source)
            return "paused" if want else "resumed"
        if action.startswith("loop_"):
            self._set_loop_mode({"loop_track": "track", "loop_album": "album"}.get(action, "off"), source,
                                toggle=action != "loop_off")
            return "loop_" + self._loop_mode
        if action in ("volume_up", "volume_down"):
            step = max(1.0, min(50.0, float(self.cfg.get("VOLUME_STEP", 10) or 10)))
            base = self._shown_volume if self._shown_volume is not None else self._target_volume()
            self._set_user_volume(base + (step if action == "volume_up" else -step), source)
            return action
        if action == "mute":
            self._set_mute(not self._muted, source)
            return "muted" if self._muted else "unmuted"
        if action == "standby":
            return "standby" if self._go_standby(source) else "ignored"
        if action == "poweroff":
            self._power_off_now(reason="button")
            return "shutdown"
        if action == "sleep":
            if self._timer_due.get("sleep"):
                self._set_timer("sleep", None)
                self.stats.record("sleep_timer", label="cancelled", detail={"source": source})
                return "sleep_off"
            self._start_sleep_timer(None, source)
            return "sleep_on"
        return "ignored"

    def _play_announcement_now(self, folder_source):
        """Plays a built-in announcement folder on demand, from the web
        interface's "Announcement" button."""
        folder = self._click_source_folder(folder_source)
        if folder is None:
            return "unknown_source"
        files = self._next_announce_file(folder_source, folder)
        if not files:
            return "no_announcement"

        log.info("On-demand announcement from %s (%s)", folder, folder_source)
        self.stats.record(
            "announce_on_demand", label=folder_source,
            detail={"folder": folder, "files": len(files)},
        )
        if self._play_ducked("button_announce:on_demand", files, volume_key=folder_source):
            return None
        self._resume_mode = self.mode
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
        self._restore_base_volume()
        self._play_announce_queue("button_announce:on_demand", files, volume_key=folder_source)
        return None

    def _handle_single_click(self, source="unknown"):
        """From idle OR stopped (nothing is currently playing, whether because
        music never started or because a non-looping list finished):
        starts/restarts the music, always."""
        if self.mode in ("idle", "stopped"):
            self._record_click("single", source, "start_from_" + self.mode)
            self._start_or_restart_playback(log_label=self.mode)
            return
        if self.mode != "music":
            log.info("Single click ignored: an action is already in progress (%s)", self.mode)
            self._record_click("single", source, "ignored", target=self.mode)
            return
        self._perform_click_action(
            "single", source, self.cfg["SINGLE_CLICK_ACTION"], self.cfg["SINGLE_CLICK_SOURCE"],
        )

    SPEAKER_GESTURES = ("playpause", "next", "previous", "volumeup", "volumedown")

    def _handle_speaker_button(self, gesture, source="speaker"):
        """A button of the Bluetooth speaker, or a media key of a USB sound
        card: both arrive as the same key presses."""
        kind = "speaker_" + gesture
        key = gesture.upper()
        if self.mode in ("idle", "stopped"):
            if gesture == "playpause" and self.cfg.get("SPEAKER_PLAYPAUSE_ACTION") != "off":
                self._record_click(kind, source, "start_from_" + self.mode)
                self._start_or_restart_playback(log_label="speaker")
            else:
                self._record_click(kind, source, "ignored", target=self.mode)
            return
        if self.mode != "music":
            log.info("%s button %s ignored: an action is already in progress (%s)",
                     source, gesture, self.mode)
            self._record_click(kind, source, "ignored", target=self.mode)
            return
        self._perform_click_action(
            kind, source,
            self.cfg.get("SPEAKER_%s_ACTION" % key, "off"),
            self.cfg.get("SPEAKER_%s_SOURCE" % key, "none"),
        )

    def _handle_double_click(self, source="unknown"):
        """Whatever DOUBLE_CLICK_ACTION/SOURCE say."""
        if self.mode in ("idle", "stopped") and self.cfg["DOUBLE_CLICK_ACTION"] in self.START_ACTIONS:
            self._record_click("double", source, "start_from_" + self.mode)
            self._start_or_restart_playback(log_label=self.mode)
            return
        if self.mode in ("idle", "stopped"):
            log.info("Double click ignored: music isn't playing (%s)", self.mode)
            self._record_click("double", source, "ignored", target=self.mode)
            return
        if self.mode != "music":
            log.info("Double click ignored: an action is already in progress (%s)", self.mode)
            self._record_click("double", source, "ignored", target=self.mode)
            return
        self._perform_click_action(
            "double", source, self.cfg["DOUBLE_CLICK_ACTION"], self.cfg["DOUBLE_CLICK_SOURCE"],
        )

    def _handle_long_press(self, source="unknown"):
        """LONG_PRESS_ACTION: "poweroff" (default)."""
        if self.mode == "shutting_down":
            self._record_click("long", source, "ignored", target="shutting_down")
            return
        action = self.cfg.get("LONG_PRESS_ACTION", "poweroff")
        if action == "standby":
            log.info("Long press -> standby")
            done = self._go_standby(source)
            self._record_click("long", source, "standby" if done else "ignored", target=self.mode)
            return
        log.info("Long press -> progressive stop then shutdown")
        self._record_click("long", source, "shutdown", target=self.mode)
        self._power_off_now(reason="long_press")

    def _power_off_now(self, reason):
        """Fade out whatever plays (LONGPRESS_FADE_DURATION_SEC, the "fade
        before switching off"), then switch the Pi off."""
        if self.mode == "shutting_down":
            return
        if self.mode in ("idle", "stopped"):
            self.mode = "shutting_down"
            self._do_shutdown_sequence(force=True, reason=reason)
            return
        self.mode = "shutting_down"
        self._fade_out_and_pause(self.cfg["LONGPRESS_FADE_DURATION_SEC"])
        self._do_shutdown_sequence(force=True, reason=reason)

    def _go_standby(self, source="unknown", fade=True):
        """Standby: the same fade as before switching off, then the Pi stays on
        and waits exactly like at startup (keep-alive sound, mode idle)."""
        if self.mode in ("idle", "stopped", "shutting_down", "restarting"):
            return False
        log.info("Standby (%s)", source)
        song = self._last_music_track if self.mode == "music" else None
        self._set_timer("resume", None)
        self._set_timer("sleep", None)
        if fade:
            self._fade_out_and_pause(self.cfg["LONGPRESS_FADE_DURATION_SEC"])
        self._announce_queue = []
        self._forced_next = None
        self._resume_track = None
        self._pending_seek = None
        self._paused = False
        if song and os.path.exists(song):
            self.state.push_front(song)
            self._resume_armed = True
        self._restore_base_volume()
        self.stats.record("standby", label=source, detail={"source": source})
        self._start_keepalive(target_mode="idle", quiet=True)
        self._bump_state()
        return True

    def _set_mute(self, muted, source):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.mpv.set_mute(muted)
        self.stats.record("mute", label="on" if muted else "off", detail={"source": source})
        self._bump_state()

    def _on_mpv_event(self, msg):
        event = msg.get("event")

        if event == "property-change":
            name = msg.get("name")
            data = msg.get("data")
            if name == "time-pos" and isinstance(data, (int, float)):
                self._position = float(data)
                if self._play_kind == "music":
                    try:
                        self._near_end()
                    except Exception:  # noqa: BLE001 - the song then plays out
                        log.exception("Could not handle the end of the song")
            elif name == "duration" and isinstance(data, (int, float)):
                self._duration = float(data)
            elif name == "pause":
                if bool(data) != self._paused:
                    self._paused = bool(data)
                    self._bump_state()
            return

        if event == "file-loaded":
            seek, self._pending_seek = self._pending_seek, None
            if seek:
                self.mpv.seek(seek)
            return

        if event != "end-file":
            return

        reason = msg.get("reason", "eof")
        # "loadfile replace" ends the outgoing file with these: advancing would skip the new one.
        if reason in ("stop", "quit", "redirect"):
            return

        if reason == "error" and self._play_kind is None and self.mode in ("idle", "stopped", "shutting_down"):
            self._keepalive_failed(msg.get("file_error"))
            return

        if reason == "error":
            failed = self._play_path or self._current_track
            seconds = self._end_play("error")
            self._record_playback_error(msg, failed, seconds)
        else:
            self._end_play(reason)
            self._consecutive_play_errors = 0

        if self.mode == "restarting":
            self._restart_now()
            return

        if self.mode == "game":
            return

        if self.mode in ("idle", "stopped"):
            self._start_keepalive(target_mode=self.mode)
            return

        if self.mode == "music":
            if reason == "error" and self._should_back_off():
                return
            if self.state.is_pending_cutoff() and self.cfg["CUTOFF_MODE"] == "end_of_track" \
                    and self._pending_cutoff_still_due():
                self._start_cutoff_announce_now()
            else:
                self._play_next_track()
            return

        if (
            self.mode in ("cutoff_announce", "meme")
            or self.mode.startswith("custom:")
            or self.mode.startswith("button_announce:")
        ):
            if self._announce_queue:
                nxt = self._announce_queue.pop(0)
                self._begin_play(self.mode, nxt)
            else:
                self._after_announce_finished(self.mode)

    def _speaker_watch_needed(self):
        """Whether anything depends on the speaker check, beyond the
        statistics."""
        return (
            self.cfg["MUSIC_START_MODE"] == "bluetooth"
            or self.cfg.get("SPEAKER_LOSS_PAUSE")
            or self.cfg.get("SPEAKER_LOSS_SHUTDOWN_MIN", 0) > 0
            or self.cfg.get("SPEAKER_ABSENT_SHUTDOWN_MIN", 0) > 0
        )

    def _speaker_watch_interval(self):
        """How often to poll the speaker, 0 meaning "do not watch"."""
        interval = self.cfg["SPEAKER_WATCH_INTERVAL_SEC"]
        if interval > 0 or not self._speaker_watch_needed():
            return interval
        return float(DEFAULTS["SPEAKER_WATCH_INTERVAL_SEC"])

    def _start_watchdogs(self):
        threading.Thread(target=self._speaker_watch_loop, daemon=True).start()
        threading.Thread(target=self._volume_watch_loop, daemon=True).start()
        # No access point, no uap0: asking iw would only log a warning.
        if self.cfg["AP_WATCH_INTERVAL_SEC"] > 0 and platform_mod.has("access_point"):
            threading.Thread(target=self._ap_watch_loop, daemon=True).start()

    # flicd takes a controller for itself, so the Flic button competes with the speaker.
    FLIC_UNITS = ("flicd.service", "flic-bridge.service")
    FLIC_FLAG = "flic_held"

    def _flic_flag(self, verb):
        """systemd's own answer for flicd ("enabled", "active"...), or ""."""
        ok, out, _err = system_actions.systemctl(verb, "flicd.service", timeout=10)
        return out if ok else ""

    def _watch_flic(self):
        """Puts the Flic button on hold while the radio cannot spare a
        controller, and lets it go when one is free again. Asked for as:
        disable it on its own when the built-in radio is all there is, but not
        for good - and let the owner turn it off for real, which this never
        undoes."""
        usable, reason = audio_diag.flic_availability(not self._wired_output())
        enabled = self._flic_flag("is-enabled") == "enabled"
        if usable:
            if enabled and self.state.flag(self.FLIC_FLAG) and self._flic_flag("is-active") != "active":
                self._set_flic_services("start")
            return
        if not enabled or self._flic_flag("is-active") != "active":
            return
        if self._set_flic_services("stop"):
            log.info("Flic button on hold (%s): the speaker needs the only "
                     "Bluetooth controller there is", reason)

    def _set_flic_services(self, action):
        """Starts or stops both Flic units, through the narrow sudoers grant."""
        failures = []
        for unit in self.FLIC_UNITS:
            ok, _out, err = system_actions.systemctl(action, unit, timeout=30, sudo=True)
            if not ok:
                failures.append("%s: %s" % (unit, err[-120:] or "no answer"))
        if failures:
            if not self._flic_warned:
                self._flic_warned = True
                log.warning("Could not %s the Flic services: %s", action, "; ".join(failures))
            return False
        self.state.set_flag(self.FLIC_FLAG, action == "stop")
        if action == "start":
            log.info("Flic button back on: a controller is free again")
        return True

    def _speaker_watch_loop(self):
        """Watches the speaker connection: statistics, pause, power-off delays."""
        wait = 3.0
        while not self._stop_event.wait(wait):
            if self.mode == "shutting_down":
                return
            wait = self._speaker_watch_now() or 10.0

    def _speaker_watch_now(self):
        """One watch turn, never two at once: the loop and a nudge from the web server."""
        with self._speaker_watch_lock:
            return self._guarded("Speaker watch", self._speaker_watch_turn)

    def _speaker_watch_turn(self):
        """One turn of the watch; the seconds to wait before the next."""
        self._watch_flic()
        interval = self._speaker_watch_interval()
        wait = interval if interval > 0 else 30.0
        if interval <= 0:
            return wait
        mac = self.cfg.get("SPEAKER_MAC", "")
        if not mac or mac == "XX:XX:XX:XX:XX:XX":
            if self._speaker_warned != "no_mac":
                self._speaker_warned = "no_mac"
                if self._speaker_watch_needed():
                    log.warning("No speaker configured (speaker_mac): nothing to watch, "
                                "the speaker-dependent settings cannot act")
                else:
                    log.info("No speaker configured, connection monitoring idle")
            self._check_speaker_absent()
            return wait
        self._speaker_warned = None
        state = self._speaker_link(mac)
        if state["unknown"]:
            return wait  # no controller answered: not the same as "gone"
        if state["connected"] and state["controller"] != state["expected"]:
            self._move_speaker_to_its_controller(state)
        connected = state["connected"]

        # BlueZ still says "connected" while a wedged dongle carries nothing: no sink is the proof.
        silent = False
        if connected and self.mode == "music" and not self._wired_output():
            if audio_diag.bluetooth_sink_missing(env=audio_env()):
                self._sink_missing_checks += 1
            else:
                self._sink_missing_checks = 0
            silent = self._sink_missing_checks >= self.SINK_MISSING_CHECKS
        else:
            self._sink_missing_checks = 0
        audible = connected and not silent

        if connected:
            self._speaker_ever_connected = True
            self._note_speaker_battery(state.get("battery"))

        if self._speaker_was_connected is None:
            self._speaker_was_connected = audible
            if audible:
                self._start_music_on_speaker_connect(mac)
            else:
                self._speaker_lost_at = time.monotonic()
            self._check_speaker_absent()
            return wait

        if audible != self._speaker_was_connected:
            self._speaker_was_connected = audible
            if audible:
                self._on_speaker_back(mac)
            else:
                self._on_speaker_lost(mac, "disconnected" if not connected else "silent")

        if not audible:
            self._check_speaker_lost_too_long()
        self._check_speaker_absent()
        return wait

    def _note_speaker_battery(self, level):
        """Records each ten-percent step of the speaker's battery, and warns once when it runs low."""
        if level is None:
            return
        step = level // 10
        if step != self._battery_step:
            self._battery_step = step
            self.stats.record("speaker_battery", label="%d %%" % level, detail={"level": level})
        low = int(self.cfg.get("SPEAKER_BATTERY_LOW", 0) or 0)
        if low <= 0:
            return
        if level <= low and not self._battery_warned:
            self._battery_warned = True
            log.warning("Speaker battery low: %d%%", level)
            self.stats.record("speaker_battery_low", label="%d %%" % level, detail={"level": level})
            self._lights.flash("alert")
            sound = self.cfg.get("BATTERY_LOW_SOUND") or ""
            if sound:
                self._play_cue_sound(sound, "BATTERY_LOW_SOUND")
        elif level >= low + 10:
            self._battery_warned = False

    def _on_speaker_lost(self, mac, reason="disconnected"):
        if reason == "silent":
            log.warning("Speaker connected (%s) but the link carries nothing: PipeWire has no "
                        "Bluetooth output left, so the music was playing into silence", mac)
        else:
            log.warning("Speaker disconnected while running (%s)", mac)
        self._speaker_lost_at = time.monotonic()
        self._lights.flash("alert")
        self.stats.record(
            "speaker_silent" if reason == "silent" else "speaker_disconnected", label=mac,
            detail={
                "reason": reason,
                "mode": self.mode,
                "track": os.path.basename(self._current_track) if self._current_track else None,
            },
            counters={"speaker_drops": 1}, daily={"speaker_drops": 1},
        )
        if self._switch_to_fallback_output():
            return
        if self.cfg.get("SPEAKER_LOSS_PAUSE") and not self._wired_output() \
                and self.mode == "music" and not self._paused:
            log.info("Pausing the music until the speaker comes back")
            self.mpv.set_pause(True)
            self._paused = True
            self._paused_for_speaker = True
            self.stats.record("playback_pause", label="paused", detail={"source": "speaker_lost"})

    def _switch_to_fallback_output(self):
        """The speaker is gone: plays on, on the wired output chosen for that."""
        kind = self.cfg.get("AUDIO_FALLBACK_OUTPUT") or ""
        if kind not in ("jack", "usb", "hdmi") or self._wired_output() \
                or self.mode == "shutting_down":
            return False
        if not audio_output.find(kind, audio_output.list_sinks(env=audio_env())):
            log.info("Fallback output '%s' not found: the speaker loss is handled as usual", kind)
            return False
        log.info("Speaker gone: the sound moves to the fallback output (%s)", kind)
        self._output_override = kind
        self._apply_audio_output(force=True)
        self.stats.record("output_override", label=kind, detail={"source": "speaker_lost"})
        self._bump_state()
        return True

    def _on_speaker_back(self, mac):
        log.info("Speaker reconnected (%s)", mac)
        self._open_quick_steps()
        self._speaker_lost_at = None
        self.stats.record("speaker_reconnected", label=mac, counters={"speaker_recoveries": 1})
        # Also what puts the sound back on a wired output the speaker just took
        # it from, and clears a fallback output that is no longer needed.
        self._apply_audio_output(force=True)
        if self._paused_for_speaker:
            self._paused_for_speaker = False
            if self.mode == "music" and self._paused:
                self._resume_with_fade(self.cfg.get("SPEAKER_RESUME_FADE_SEC", 0))
                self.stats.record("playback_pause", label="resumed", detail={"source": "speaker_back"})
        self._start_music_on_speaker_connect(mac)

    def _set_pause(self, paused, source):
        """Pauses or resumes what is playing."""
        pausable = self.mode == "music" or self.mode == "meme" \
            or self.mode.startswith(("custom:", "button_announce:"))
        if not pausable:
            return "not_playing_music"
        if paused == self._paused:
            return None
        fade = min(max(float(self.cfg.get("PAUSE_FADE_SEC", 0) or 0), 0.0), 5.0)
        if paused:
            self._stop_tail()
        if self.mode == "music" and paused:
            if fade > 0:
                self._fade_out_to_zero(fade)
            self.mpv.set_pause(True)
        elif self.mode == "music":
            self._resume_with_fade(fade)
        else:
            if not paused:
                self._sink_resync = self.SINK_RESYNC_TURNS
            self.mpv.set_pause(paused)
        self._paused = paused
        if not paused and self._timer_due.get("resume"):
            self._set_timer("resume", None)
        self._bump_state()
        self.stats.record(
            "playback_pause", label="paused" if paused else "resumed",
            detail={"source": source},
        )
        return None

    def _set_loop_mode(self, mode, source, toggle=False):
        """Sets the loop mode: off, track or album."""
        if toggle and mode == self._loop_mode:
            mode = "off"
        if mode == self._loop_mode:
            return
        self._loop_mode = mode
        log.info("Loop: %s", mode)
        self.stats.record("loop_mode", label=mode, detail={"source": source})
        self._bump_state()

    def _set_user_volume(self, vol, source):
        vol = max(0.0, min(100.0, float(vol)))
        self._user_volume = vol
        self._shown_volume = vol
        fade = self.cfg.get("VOLUME_FADE_SEC", 0) or 0
        if self._speaker_volume_linked():
            # The speaker answers at once: there is nothing for a glide to do.
            self._stop_volume_glide()
            self._current_volume = vol
            self._write_level(vol)
        elif self.cfg.get("VOLUME_CHANGE") == "fade" and fade > 0 and not self._paused:
            self._glide_volume(vol, min(float(fade), 10.0))
        else:
            self._stop_volume_glide()
            self._current_volume = vol
            self._write_level(vol)
        self._bump_state()
        now = time.monotonic()
        if now - self._last_volume_event > 30:
            self._last_volume_event = now
            self.stats.record(
                "volume_set", label=str(round(vol)),
                detail={"source": source, "volume": round(vol)},
            )

    def _durations(self, key, fallback):
        """A duration list ("5,15,30,60"): whole minutes, 1-600, sorted, no
        duplicates. An empty or unusable setting falls back to the default
        rather than leaving the feature with nothing to offer."""
        out = []
        for part in str(self.cfg.get(key) or "").replace(";", ",").split(","):
            try:
                value = int(part.strip())
            except ValueError:
                continue
            if 1 <= value <= 600 and value not in out:
                out.append(value)
        return sorted(out) or list(fallback)

    def _pause_durations(self):
        """PAUSE_DURATIONS ("5,15,30,60")."""
        return self._durations("PAUSE_DURATIONS", (5, 15, 30, 60))

    def _sleep_durations(self):
        """SLEEP_DURATIONS ("30,60,90,120")."""
        return self._durations("SLEEP_DURATIONS", (30, 60, 90, 120))

    def _set_timer(self, name, delay, fn=None):
        """One named timer ("resume", "sleep")."""
        gen = self._timer_gen.get(name, 0) + 1
        self._timer_gen[name] = gen
        self._timer_due[name] = (time.monotonic() + delay) if delay else None
        self._bump_state()
        if not delay:
            return

        def run():
            with self._command_lock:
                if self._timer_gen.get(name) != gen:
                    return
                self._timer_due[name] = None
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("Timer %s failed", name)
                self._bump_state()

        timer = threading.Timer(delay, run)
        timer.daemon = True
        timer.start()

    def _timed_pause_over(self):
        if self.mode == "music" and self._paused:
            log.info("Timed pause over: resuming")
            self._set_pause(False, "timer")

    def _start_sleep_timer(self, minutes, source):
        if minutes is None:
            minutes = self._sleep_durations()[0]
        minutes = max(1, min(600, minutes))
        log.info("Sleep timer: the music pauses in %d min", minutes)
        self.stats.record("sleep_timer", label=str(minutes), detail={"source": source})
        self._set_timer("sleep", minutes * 60, self._sleep_timer_over)

    def _sleep_timer_over(self):
        """Pauses the song with a fade."""
        if self.mode == "music" and not self._paused:
            log.info("Sleep timer: pausing the music")
            fade = min(max(float(self.cfg.get("FADE_DURATION_SEC", 5) or 0), 0.0), 8.0)
            if fade > 0:
                self._fade_out_to_zero(fade)
            self.mpv.set_pause(True)
            self._paused = True
            self.stats.record("playback_pause", label="paused", detail={"source": "sleep_timer"})
        elif self.mode != "music" and self.mode not in ("idle", "stopped", "shutting_down"):
            self._set_timer("sleep", 30, self._sleep_timer_over)

    def _resume_with_fade(self, duration_sec):
        """Unpauses, rising from silence to the target volume over
        duration_sec."""
        target = self._target_volume()
        self._shown_volume = target
        linked = self._speaker_volume_linked()
        # A volume changed during the pause went to an idle link, i.e. nowhere.
        self._sink_resync = self.SINK_RESYNC_TURNS
        gain = self._music_gain() if linked else target
        if not duration_sec or duration_sec <= 0:
            self._current_volume = target
            if linked:
                self._write_level(target)
            else:
                self.mpv.set_volume(target)
            self.mpv.set_pause(False)
            self._paused = False
            return
        self._current_volume = 0
        self.mpv.set_volume(0)
        self.mpv.set_pause(False)
        self._paused = False
        steps = 20
        for i in range(1, steps + 1):
            if self._paused or self.mode != "music":
                return
            time.sleep(duration_sec / steps)
            vol = round(gain * i / steps)
            if not linked:
                self._current_volume = vol
            self.mpv.set_volume(vol)

    def _check_speaker_lost_too_long(self):
        if self._wired_output():
            return
        minutes = self.cfg.get("SPEAKER_LOSS_SHUTDOWN_MIN", 0)
        if minutes <= 0 or self._speaker_lost_at is None or not self._speaker_ever_connected:
            return
        if time.monotonic() - self._speaker_lost_at >= minutes * 60:
            self._power_off_for_speaker("speaker_lost", minutes)

    def _check_speaker_absent(self):
        if self._wired_output():
            return
        minutes = self.cfg.get("SPEAKER_ABSENT_SHUTDOWN_MIN", 0)
        if minutes <= 0 or self._speaker_ever_connected:
            return
        if time.monotonic() - self._started_monotonic >= minutes * 60:
            self._power_off_for_speaker("speaker_absent", minutes)

    def _power_off_for_speaker(self, reason, minutes):
        """The two power-off delays."""
        if self.mode == "shutting_down":
            return
        log.warning("%s for %s min: powering off", reason, minutes)
        was = self.mode
        self.mode = "shutting_down"
        if was not in ("idle", "stopped") and not self._paused:
            self._fade_out_and_pause(self.cfg["LONGPRESS_FADE_DURATION_SEC"])
        self._do_shutdown_sequence(force=True, reason=reason)

    def _start_mode(self):
        """The start mode in force, which is not always the chosen one."""
        # Waiting for a speaker means nothing with a wired output: it then means boot.
        chosen = self.cfg.get("MUSIC_START_MODE", "boot")
        if chosen == "bluetooth" and (self.cfg.get("AUDIO_OUTPUT") or "bluetooth") != "bluetooth":
            return "boot"
        return chosen

    def _start_music_on_speaker_connect(self, mac):
        """MUSIC_START_MODE=bluetooth."""
        if self._start_mode() != "bluetooth":
            return
        if self.mode not in ("idle", "stopped"):
            return
        first = not self._bt_start_done
        if not first and self.state.already_triggered_today("last_music_start"):
            log.info("Speaker connected (%s), start_mode=bluetooth: the music already "
                     "started today, not starting it again", mac)
            return
        log.info("Speaker connected (%s), start_mode=bluetooth: starting the music%s",
                 mac, " (first connection since startup)" if first else "")
        self.state.mark_triggered_today("last_music_start")
        self._start_or_restart_playback(log_label="bluetooth")

    def _ap_clients(self):
        """MAC addresses currently associated with the admin access point, or
        None if they could not be read."""
        iface = self.cfg.get("AP_INTERFACE", "uap0")
        base = ["iw", "dev", iface, "station", "dump"]
        sudo = ["sudo", "-n"] + base
        attempts = [sudo, base] if self._ap_iw_needs_sudo else [base, sudo]

        for argv in attempts:
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode != 0:
                continue
            self._ap_iw_needs_sudo = argv[0] == "sudo"
            return {
                line.split()[1].lower()
                for line in result.stdout.splitlines()
                if line.startswith("Station ") and len(line.split()) > 1
            }

        if not self._ap_warned:
            self._ap_warned = True
            log.warning(
                "Could not list the clients of the access point (%s): "
                "access point usage will not be recorded. Check that the "
                "interface exists and that the sudoers entry for 'iw' is "
                "installed (see scripts/install.sh).", iface,
            )
        return None

    def _ap_watch_loop(self):
        """Records who connects to the admin access point, so "was the Wi-Fi
        hotspot used, and when?" has an answer."""
        interval = self.cfg["AP_WATCH_INTERVAL_SEC"]
        while not self._stop_event.wait(interval):
            self._guarded("Access point watch", self._ap_watch_turn)

    def _ap_watch_turn(self):
        clients = self._ap_clients()
        if clients is None:
            return

        for mac in sorted(clients - self._ap_known_clients):
            log.info("Admin access point: client connected (%s)", mac)
            self.stats.record(
                "ap_client_connected", label=mac,
                counters={"ap_client_connections": 1},
                daily={"ap_client_connections": 1},
            )
            self._play_cue_sound(self.cfg["AP_CONNECT_SOUND"], "AP_CONNECT_SOUND")
        for mac in sorted(self._ap_known_clients - clients):
            log.info("Admin access point: client disconnected (%s)", mac)
            self.stats.record("ap_client_disconnected", label=mac)

        self._ap_known_clients = clients

    def _scheduler_loop(self):
        log.info(
            "Scheduler started (music start %s, cutoff %02d:%02d, mode=%s)",
            ("%02d:%02d" % (self.cfg["MUSIC_START_HOUR"], self.cfg["MUSIC_START_MINUTE"])
             if self.cfg["MUSIC_START_MODE"] == "scheduled"
             else self.cfg["MUSIC_START_MODE"]),
            self.cfg["CUTOFF_HOUR"], self.cfg["CUTOFF_MINUTE"],
            self.cfg["CUTOFF_MODE"],
        )
        while not self._stop_event.is_set():
            if not self._clock_ready.is_set():
                time.sleep(2)
                continue

            self._guarded("Scheduler", self._scheduler_tick)
            time.sleep(15)

    def _scheduler_tick(self):
        self._announcements()
        self._check_usb_music()
        now = datetime.now()
        with self._command_lock:
            self._schedule_tick(now)

        if (
            self.cfg["MUSIC_START_MODE"] == "scheduled"
            and now.hour == self.cfg["MUSIC_START_HOUR"]
            and now.minute == self.cfg["MUSIC_START_MINUTE"]
            and self.mode in ("idle", "stopped")
            and not self.state.already_triggered_today("last_music_start")
        ):
            log.info("Scheduled music start (%02d:%02d)",
                     self.cfg["MUSIC_START_HOUR"], self.cfg["MUSIC_START_MINUTE"])
            self.state.mark_triggered_today("last_music_start")
            self.stats.record("music_started", label="scheduled")
            self._start_or_restart_playback()

        with self._command_lock:
            self._check_cutoff_warning(now)
            self._check_reminders()

        if self._cutoff_due(now) and not self.state.already_triggered_today("last_cutoff_trigger"):
            if self.mode in ("idle", "stopped") or (self.mode == "music" and not self._current_track):
                self._trigger_cutoff_from_idle()
            elif self.cfg["CUTOFF_MODE"] == "exact":
                self._trigger_cutoff_event_exact()
            else:
                self._arm_cutoff_end_of_track()

        for item in self._custom_announcements:
            if not item.get("enabled", True):
                continue
            trigger = item.get("trigger", "manual")
            if trigger in ("after_music", "after_boot"):
                self._check_delay_announcement(item, trigger)
                continue
            if trigger != "time":
                continue
            self._warm_announcement_speech(item, now)
            if (
                now.hour == item["hour"]
                and now.minute == item["minute"]
                and self.mode in ("music", "stopped")
                and not self.state.already_triggered_today("custom_%s" % item["id"])
            ):
                if not self.state.chance_allows("auto:" + item["id"], item.get("auto_chance", "1/1")):
                    log.info("Announcement '%s': not this time (%s)", item["name"], item.get("auto_chance"))
                    self.state.mark_triggered_today("custom_%s" % item["id"])
                    self.stats.record("announce_skipped", label=item["name"], detail={"chance": item.get("auto_chance")})
                    continue
                self._trigger_custom_announcement(item)

    def _cutoff_due(self, now):
        """The cutoff minute, or one this loop was kept away from."""
        last, self._last_tick = self._last_tick, now
        if not self.cfg.get("CUTOFF_ENABLED", True):
            return False
        hour, minute = self.cfg["CUTOFF_HOUR"], self.cfg["CUTOFF_MINUTE"]
        if now.hour == hour and now.minute == minute:
            return True
        # Only a tick held past the minute: a start after it, or a clock set forward, is not a cutoff.
        cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return last is not None and last < cutoff <= now \
            and (now - last).total_seconds() <= self.CUTOFF_CATCH_UP_SEC

    def _boot_monotonic(self):
        """The Pi's own start on the monotonic clock."""
        if getattr(self, "_boot_mono", None) is None:
            try:
                with open("/proc/uptime") as f:
                    self._boot_mono = time.monotonic() - float(f.read().split()[0])
            except (OSError, ValueError, IndexError):
                self._boot_mono = self._started_monotonic
        return self._boot_mono

    def _check_delay_announcement(self, item, trigger):
        """Plays an after_music / after_boot announcement when one of its
        points is due."""
        ref = self._music_started_mono if trigger == "after_music" else self._boot_monotonic()
        if ref is None:
            return
        delay = max(1, int(item.get("delay_min", 30))) * 60
        limit = int(item.get("repeat_times", 1))
        signature = (trigger, delay, limit, ref)
        progress = self._delay_progress.get(item["id"])
        elapsed = time.monotonic() - ref
        due = int(elapsed // delay)
        if not progress or progress["sig"] != signature:
            progress = {"sig": signature, "done": due}
            self._delay_progress[item["id"]] = progress
        if due <= progress["done"] or (limit and progress["done"] >= limit):
            return
        if self.mode not in ("music", "stopped"):
            return
        progress["done"] = min(due, limit) if limit else due
        if not self.state.chance_allows("auto:" + item["id"], item.get("auto_chance", "1/1")):
            log.info("Announcement '%s': not this time (%s)", item["name"], item.get("auto_chance"))
            self.stats.record("announce_skipped", label=item["name"], detail={"chance": item.get("auto_chance")})
            return
        self._trigger_custom_announcement(item)

    def _interrupting_sound(self):
        """(file name without extension, announcement name) of what is playing
        instead of the music."""
        if not self._current_track or self.mode in ("music", "idle", "stopped", "shutting_down"):
            return None, None
        name = os.path.splitext(os.path.basename(self._current_track))[0]
        label = None
        if self.mode.startswith("custom:"):
            item_id = self.mode[len("custom:"):]
            item = next((i for i in self._custom_announcements if i["id"] == item_id), None)
            label = item["name"] if item else item_id
        return name, label

    def _last_sound_status(self):
        """The last sound between two songs, for the page: its file name."""
        if not self._last_sound:
            return None
        kind = self._last_sound["kind"]
        label = None
        if kind.startswith("custom:"):
            item_id = kind[len("custom:"):]
            item = next((i for i in self._custom_announcements if i["id"] == item_id), None)
            label = item["name"] if item else item_id
        return {
            "file": os.path.splitext(os.path.basename(self._last_sound["path"]))[0],
            "announcement": label,
            "meme": kind == "meme",
            "age": round(time.monotonic() - self._last_sound["at"], 2),
        }

    def _build_status(self):
        music_track = self._last_music_track
        sound, sound_label = self._interrupting_sound()
        return {
            "mode": self.mode,
            "volume": self._shown_volume,
            "base_volume": self.cfg["BASE_VOLUME"],
            "current_track": os.path.basename(music_track) if music_track else None,
            "current_track_path": music_track,
            "sound": sound,
            "sound_announcement": sound_label,
            "last_sound": self._last_sound_status(),
            "last_card": self._last_card_status(),
            "upcoming_track_path": self._upcoming_track(),
            "track_count": self._track_count,
            "active_list": self._active_list_status(),
            "schedule": self._schedule_status(),
            "schedule_next": self._next_schedule_status(),
            "music_started_today": self.state.already_triggered_today("last_music_start"),
            "version": self._state_version,
            "restart_pending": self._restart_pending,
            # A click's sentence cannot be prepared in advance: the interface says
            # it is being synthesised.
            "speech_preparing": self._speech_busy > 0 or self._speech_warming is not None,
            "restart_target": self._restart_target,
            "restart_direct": self._restart_is_direct(),
            "powering_off": self._powering_off,
            "position": round(self._position, 1),
            "duration": round(self._duration, 1),
            "paused": self._paused,
            "loop_mode": self._loop_mode,
            "muted": self._muted,
            "output_override": self._output_override,
            "usb_music": self._usb_music_status(),
            "pause_durations": self._pause_durations(),
            "sleep_durations": self._sleep_durations(),
            "timers": {name: (round(max(0.0, due - time.monotonic())) if due else None)
                       for name, due in (("resume", self._timer_due.get("resume")),
                                         ("sleep", self._timer_due.get("sleep")))},
            "music_order_mode": self.cfg["MUSIC_ORDER_MODE"],
            "previous_restart_sec": self.PREVIOUS_RESTART_AFTER_SEC,
            "music_loop": self.cfg["MUSIC_LOOP"],
            "music_start_mode": self.cfg["MUSIC_START_MODE"],
            # The start mode waits for a speaker while the sound goes to a wire: the
            # interface strikes it through.
            "music_start_mode_inert": self._start_mode() != self.cfg["MUSIC_START_MODE"],
            "music_start_time": "%02d:%02d" % (
                self.cfg["MUSIC_START_HOUR"], self.cfg["MUSIC_START_MINUTE"]),
            "clock_ready": self._clock_ready.is_set(),
            "system_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "has_rtc": os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"),
            "speaker_mac": self.cfg["SPEAKER_MAC"],
            "cutoff_mode": self.cfg["CUTOFF_MODE"],
            "cutoff_enabled": bool(self.cfg.get("CUTOFF_ENABLED", True)),
            "cutoff_hour": self.cfg["CUTOFF_HOUR"],
            "cutoff_minute": self.cfg["CUTOFF_MINUTE"],
            "stats_enabled": self.stats.enabled,
            "clock_source": self.stats.clock_source,
            "lights": {"scene": self._light_scene(),
                       "beat": self._light_audio is not None and self._light_audio.alive()},
            "consecutive_play_errors": self._consecutive_play_errors,
        }

    def _start_control_socket(self):
        sock_path = self.cfg["CONTROL_SOCKET"]
        if os.path.exists(sock_path):
            os.remove(sock_path)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(5)

        def handle_command(msg):
            cmd = msg.get("cmd")
            source = msg.get("source", "unknown")
            if source not in ("flic", "gpio", "web", "speaker", "usb", "push", "vote", "card",
                              "unknown"):
                source = "unknown"
            self._announcements()

            if cmd == "single_click":
                self._handle_single_click(source)
                return {"ok": True}
            if cmd == "double_click":
                self._handle_double_click(source)
                return {"ok": True}
            if cmd == "long_press":
                self._handle_long_press(source)
                return {"ok": True}
            if cmd == "start_music":
                if self.mode not in ("idle", "stopped"):
                    return {"ok": False, "error": "already_playing"}
                self._record_click("single", source, "start_from_" + self.mode)
                self._start_or_restart_playback(log_label=self.mode)
                return {"ok": True}
            if cmd == "next_track":
                if self.mode == "shutting_down":
                    return {"ok": False, "error": "shutting_down"}
                self.stats.record(
                    "track_skipped", label=source,
                    detail={"source": source, "mode": self.mode},
                )
                self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
                self._restore_base_volume()
                self._play_next_track()
                return {"ok": True}
            if cmd == "set_mute":
                on = msg.get("on")
                self._set_mute((not self._muted) if on == "toggle" else bool(on), source)
                return {"ok": True, "data": {"muted": self._muted}}
            if cmd == "poweroff":
                self._power_off_now(reason="interface")
                return {"ok": True}
            if cmd == "standby":
                if not self._go_standby(source):
                    return {"ok": False, "error": "nothing_playing"}
                return {"ok": True}
            if cmd == "toggle_pause":
                if self.mode in ("idle", "stopped"):
                    self._record_click("single", source, "start_from_" + self.mode)
                    self._start_or_restart_playback(log_label=self.mode)
                    return {"ok": True, "data": {"paused": False}}
                error = self._set_pause(not self._paused, source)
                if error:
                    return {"ok": False, "error": error}
                return {"ok": True, "data": {"paused": self._paused}}
            if cmd == "previous_track":
                if self.mode != "music":
                    return {"ok": False, "error": "not_playing_music"}
                self.stats.record("track_previous", label=source, detail={"source": source})
                self._step_back()
                self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
                self._restore_base_volume()
                self._play_next_track(user=True)
                return {"ok": True}
            if cmd == "timed_pause":
                try:
                    minutes = int(msg.get("minutes"))
                except (TypeError, ValueError):
                    minutes = None
                if minutes not in self._pause_durations():
                    return {"ok": False, "error": "invalid_value"}
                if self.mode != "music":
                    return {"ok": False, "error": "not_playing_music"}
                if not self._paused:
                    self._set_pause(True, source)
                self._set_timer("resume", minutes * 60, self._timed_pause_over)
                self.stats.record("timed_pause", label=str(minutes), detail={"source": source})
                return {"ok": True}
            if cmd == "sleep_timer":
                if not msg.get("on", True):
                    if self._timer_due.get("sleep"):
                        self._set_timer("sleep", None)
                        self.stats.record("sleep_timer", label="cancelled", detail={"source": source})
                    return {"ok": True}
                try:
                    minutes = int(msg["minutes"]) if msg.get("minutes") is not None else None
                except (TypeError, ValueError):
                    return {"ok": False, "error": "invalid_value"}
                if minutes is not None and not 1 <= minutes <= 600:
                    return {"ok": False, "error": "invalid_value"}
                self._start_sleep_timer(minutes, source)
                return {"ok": True}
            if cmd == "play_track":
                path = self._library_path(msg.get("path"))
                if not path:
                    return {"ok": False, "error": "not_found"}
                if self.mode in ("idle", "stopped"):
                    self._forced_next = path
                    self._start_or_restart_playback(log_label="replay")
                elif self.mode == "music":
                    self._forced_next = path
                    self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"])
                    self._restore_base_volume()
                    self._play_next_track(user=True)
                else:
                    return {"ok": False, "error": "busy"}
                self.state.take_from_queue(path)
                self.stats.record("track_replayed", label=os.path.basename(path), detail={"source": source})
                return {"ok": True}
            if cmd == "queue_track":
                path = self._library_path(msg.get("path"))
                if not path:
                    return {"ok": False, "error": "not_found"}
                dedication = msg.get("dedication") if isinstance(msg.get("dedication"), dict) else None
                text = speech.clean_text((dedication or {}).get("text"))
                if text and self.cfg.get("DEDICATIONS_ENABLED"):
                    self.state.set_dedication(path, {
                        "from": speech.clean_text((dedication or {}).get("from"), 40) or None,
                        "text": text})
                if self.mode in ("idle", "stopped"):
                    self._forced_next = path
                    if text and self.cfg.get("DEDICATIONS_ENABLED"):
                        # Said before the song, so no fade-in from silence under it.
                        self._prepare_music_start("request")
                        self._restore_base_volume()
                        self._forced_next = None
                        self.mode = "meme"
                        self._play_with_intro(path, introduce=True)
                    else:
                        self._start_or_restart_playback(log_label="request")
                    self.state.take_from_queue(path)
                    self.stats.record("track_queued", label=os.path.basename(path),
                                      detail={"source": source, "started": True})
                    return {"ok": True, "data": {"started": True}}
                position = self.state.enqueue_request(
                    path, msg.get("person") or None, bool(self.cfg.get("QUEUE_FAIR")))
                self.stats.record("track_queued", label=os.path.basename(path), detail={"source": source})
                self._bump_state()
                return {"ok": True, "data": {"started": False, "position": position + 1}}
            if cmd == "get_queue":
                try:
                    count = max(0, min(50, int(msg.get("n", 10))))
                except (TypeError, ValueError):
                    count = 10
                return {"ok": True, "data": {"paths": self._next_tracks(count),
                                             "requested": self.state.requested_paths(),
                                             "dedications": self.state.dedications()}}
            if cmd == "drop_dedication":
                if self.state.pop_dedication(str(msg.get("path") or "")) is None:
                    return {"ok": False, "error": "not_found"}
                self._bump_state()
                return {"ok": True}
            if cmd == "get_reminders":
                return {"ok": True, "data": {"items": self.state.reminders()}}
            if cmd == "add_reminder":
                text = speech.clean_text(msg.get("text"))
                try:
                    at = float(msg.get("at"))
                except (TypeError, ValueError):
                    return {"ok": False, "error": "reminder_bad_time"}
                if not text:
                    return {"ok": False, "error": "reminder_text_required"}
                if not time.time() - 60 <= at <= time.time() + 7 * 86400:
                    return {"ok": False, "error": "reminder_bad_time"}
                rid = self.state.add_reminder(at, text)
                if rid is None:
                    return {"ok": False, "error": "reminder_too_many"}
                log.info("Reminder at %s: %s", datetime.fromtimestamp(at).strftime("%H:%M"), text)
                self._bump_state()
                return {"ok": True, "data": {"id": rid}}
            if cmd == "delete_reminder":
                if not self.state.remove_reminder(str(msg.get("id") or "")):
                    return {"ok": False, "error": "not_found"}
                self._bump_state()
                return {"ok": True}
            if cmd == "set_output_override":
                kind = msg.get("output")
                if kind is not None and kind not in audio_output.KINDS:
                    return {"ok": False, "error": "bad_output"}
                self._output_override = kind
                log.info("Audio output override: %s", kind or "none")
                self.stats.record("output_override", label=kind or "none", detail={"source": source})
                self._apply_audio_output(force=True)
                self._bump_state()
                return {"ok": True}
            if cmd == "usb_music":
                return self._usb_music_command(msg, source)
            if cmd == "set_loop":
                mode = msg.get("mode")
                if mode == "cycle":
                    mode = {"off": "album", "album": "track"}.get(self._loop_mode, "off")
                if mode not in ("off", "track", "album"):
                    return {"ok": False, "error": "invalid_value"}
                self._set_loop_mode(mode, source)
                return {"ok": True, "data": {"loop_mode": self._loop_mode}}
            if cmd == "reload_schedules":
                self._schedules_stamp = False
                if self._clock_ready.is_set():
                    self._schedule_tick(datetime.now())
                return {"ok": True, "count": len(self._schedule_items)}
            if cmd == "reload_lists":
                self._lists_stamp = None
                return {"ok": True, "count": len(self._lists())}
            if cmd == "speaker_check":
                # The web server saw the speaker come or go before our own next turn.
                self._speaker_watch_now()
                return {"ok": True}
            if cmd == "reload_hidden":
                return self._reload_hidden()
            if cmd == "set_active_list":
                return self._set_active_list(msg.get("id"), source, bool(msg.get("start")))
            if cmd == "skip_sound":
                mode = self.mode
                if self._skip_ducked():
                    log.info("Sound over the music skipped")
                    self.stats.record("sound_skipped", label="under", detail={"source": source})
                    return {"ok": True}
                if not (mode == "meme" or mode.startswith(("custom:", "button_announce:"))):
                    return {"ok": False, "error": "nothing_to_skip"}
                log.info("Sound skipped (%s)", mode)
                self._announce_queue = []
                self._end_play("skipped")
                self._paused = False
                self.stats.record("sound_skipped", label=mode, detail={"source": source})
                self._after_announce_finished(mode)
                return {"ok": True}
            if cmd == "set_volume":
                try:
                    vol = max(0, min(100, float(msg.get("value"))))
                except (TypeError, ValueError):
                    return {"ok": False, "error": "invalid_value"}
                self._set_user_volume(vol, source)
                return {"ok": True, "volume": vol}
            if cmd == "rescan_music":
                return {"ok": True, "tracks": self._rescan_music(source)}
            if cmd == "get_status":
                return {"ok": True, "data": self._build_status()}
            if cmd == "wait_change":
                try:
                    since = int(msg.get("since"))
                except (TypeError, ValueError):
                    since = -1
                timeout = max(1.0, min(float(msg.get("timeout") or 20), 30.0))
                return {"ok": True, "data": {"version": self._wait_state_change(since, timeout)}}
            if cmd == "clock_set":
                try:
                    offset = float(msg.get("offset", 0.0))
                except (TypeError, ValueError):
                    offset = 0.0
                self._declare_clock(
                    msg.get("clock_source", "manual"), offset_sec=offset, trusted=True,
                )
                ready = getattr(self, "_clock_ready", None)
                if ready is not None:
                    ready.set()
                self._wled_clock_next = 0.0
                return {"ok": True}
            if cmd == "wled_refresh":
                self._wled_clock_next = 0.0
                self._lights.refresh()
                return {"ok": True}
            if cmd == "card":
                card_id = str(msg.get("id") or "").strip()
                if not card_id:
                    return {"ok": False, "error": "card_bad_id"}
                error = self._card(card_id, "card")
                return {"ok": False, "error": error} if error else {"ok": True}
            if cmd == "game_clip":
                try:
                    start = float(msg.get("start") or 0)
                    seconds = float(msg.get("seconds") or 20)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "bad_request"}
                error = self._game_clip(msg.get("path"), start, seconds)
                return {"ok": False, "error": error} if error else {"ok": True}
            if cmd == "game_end":
                self._game_end()
                return {"ok": True}
            if cmd == "speak":
                kind = msg.get("kind")
                if kind not in ("time", "time_date"):
                    return {"ok": False, "error": "unknown_speech"}
                error = self._speak(kind, msg.get("source") or "web")
                return {"ok": False, "error": error} if error else {"ok": True}
            if cmd == "test_system_sound":
                key = msg.get("key")
                if key not in SYSTEM_SOUNDS:
                    return {"ok": False, "error": "unknown_sound"}
                path = self.cfg.get(key) or ""
                if not path or not os.path.exists(path):
                    return {"ok": False, "error": "sound_missing"}
                self._play_cue_sound(path, key)
                return {"ok": True}
            if cmd == "schedule_restart":
                self._schedule_restart(bool(msg.get("on", True)), msg.get("target") or "service")
                return {"ok": True, "data": {"pending": self._restart_pending}}
            if cmd == "speaker_button":
                gesture = msg.get("gesture")
                if gesture not in self.SPEAKER_GESTURES:
                    return {"ok": False, "error": "unknown_gesture"}
                self._handle_speaker_button(gesture, source)
                return {"ok": True}
            if cmd == "test_click":
                kind = msg.get("kind") if msg.get("kind") in ("single", "double") else "single"
                if msg.get("kind") in ("speaker_" + g for g in self.SPEAKER_GESTURES):
                    kind = msg.get("kind")
                action = msg.get("action")
                sound = str(msg.get("sound") or "none")
                if action not in self.CLICK_ACTIONS:
                    return {"ok": False, "error": "unknown_action"}
                if self.mode != "music":
                    return {"ok": False, "error": "not_playing_music"}
                self._perform_click_action(kind, source, action, sound)
                return {"ok": True}
            if cmd == "reset_click_bag":
                self.state.reset_click_bag(msg.get("source_id"))
                return {"ok": True}
            if cmd == "reload_config":
                return self._reload_config()
            if cmd == "reload_announcements":
                return {"ok": True, "count": len(self._reload_announcements())}
            if cmd == "play_announcement":
                if self.mode != "music":
                    return {"ok": False, "error": "not_playing_music"}

                source, item_id, chooser = announcement_target(msg)

                if source:
                    if source not in announcements.BUILTIN_SOURCES:
                        return {"ok": False, "error": "unknown_source"}
                    error = self._play_announcement_now(source)
                    return {"ok": False, "error": error} if error else {"ok": True}

                item = next((i for i in self._custom_announcements if i["id"] == item_id), None)
                if item is None:
                    return {"ok": False, "error": "unknown_announcement"}
                if chooser and not self.state.chance_allows("manual:" + item["id"], item.get("manual_chance", "1/1")):
                    self.stats.record("announce_skipped", label=item["name"], detail={"chance": item.get("manual_chance")})
                    return {"ok": True, "data": {"skipped": True, "chance": item.get("manual_chance")}}
                self._trigger_custom_announcement(item, on_demand=True)
                return {"ok": True}
            if cmd == "reset_stats":
                scope = msg.get("scope", "all")
                if scope not in ("all", "events", "counters"):
                    return {"ok": False, "error": "invalid_scope"}
                ok = self.stats.reset(scope)
                if ok and scope == "all":
                    self._session_marked_used = False
                    if self._play_kind:
                        self._play_since = time.monotonic()
                        self.stats.mark_used()
                        self._session_marked_used = True
                return {"ok": bool(ok)}

            return {"ok": False, "error": "unknown_command"}

        def serve():
            while not self._stop_event.is_set():
                try:
                    conn, _ = server.accept()
                except OSError:
                    break
                threading.Thread(target=answer, args=(conn,), daemon=True).start()

        read_only = ("get_status", "wait_change", "get_queue")

        def answer(conn):
            with conn:
                data = conn.recv(8192)
                if not data:
                    return
                try:
                    msg = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    response = {"ok": False, "error": "invalid_json"}
                else:
                    try:
                        if msg.get("cmd") in read_only:
                            response = handle_command(msg)
                        else:
                            with self._command_lock:
                                response = handle_command(msg)
                    except Exception:  # noqa: BLE001
                        log.exception("Error handling command %s", msg.get("cmd"))
                        response = {"ok": False, "error": "command_failed"}
                try:
                    conn.sendall(json.dumps(response).encode("utf-8"))
                except OSError:
                    pass

        threading.Thread(target=serve, daemon=True).start()
        log.info("Control socket listening on %s", sock_path)


def main():
    cfg = load_config()
    daemon = RadioDaemon(cfg)
    try:
        daemon.start()
    except KeyboardInterrupt:
        daemon.stats.end_session("service_stop")
    finally:
        daemon.stats.close()


if __name__ == "__main__":
    main()

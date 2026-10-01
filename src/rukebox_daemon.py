#!/usr/bin/env python3
"""The radio daemon: playback state machine, scheduler and control socket."""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import announcements  # noqa: E402
import audio_diag  # noqa: E402
import audio_output  # noqa: E402
import bt_link  # noqa: E402
from config_and_scan import DEFAULTS, get_music_list, load_config  # noqa: E402
from config_schema import RESTART_REQUIRED, SYSTEM_SOUNDS  # noqa: E402
import hidden_tracks  # noqa: E402
import library  # noqa: E402
from mpv_controller import MPVController, audio_env, compression_filter  # noqa: E402
import music_lists  # noqa: E402
import playlist  # noqa: E402
from state import RadioState  # noqa: E402
from stats import StatsRecorder  # noqa: E402
import track_media  # noqa: E402
import track_order  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("radio")


def announcement_target(msg):
    """(source, item_id, chooser) for a play_announcement command.

    `source` carries two different things: the announcement to play, and where
    the command came from ("web", "flic", "gpio", "speaker", "push" - the web
    server adds source="web" to everything, and an update script pushing to the
    Pi adds "push"). Only a real announcement source counts
    as one here, or the row's own "Play" - which names its announcement by id
    - is answered "unknown source"."""
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


class RadioDaemon:
    # Two watch turns, so a track change or a PipeWire hiccup is not a dead link.
    SINK_MISSING_CHECKS = 2
    # A press on the speaker is only visible as a new level; 2s makes it feel answered.
    SINK_POLL_SEC = 2.0

    def __init__(self, cfg):
        self._state_cond = threading.Condition()
        self._state_version = 0
        self._mode = "idle"
        self._command_lock = threading.Lock()
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
        self._speaker_move_at = 0.0
        self._speaker_move_failed = False
        self._restart_pending = False
        self._audio_device = None
        self._audio_output_checked = 0.0
        self._audio_output_missing = None
        self._powering_off = False
        self._music_started_mono = None
        self._delay_progress = {}
        self._last_sound = None
        self._paused_for_speaker = False
        self._speaker_lost_at = None
        self._speaker_ever_connected = False
        self._bt_start_done = False
        self._started_monotonic = time.monotonic()
        self._ap_known_clients = set()
        self._ap_iw_needs_sudo = None
        self._ap_warned = False
        self._session_marked_used = False
        self._last_volume_event = 0.0
        self._volume_glide_gen = 0
        self._volume_glide_target = None
        self._sink_level = None
        self._sink_warned = False
        self._flic_warned = False

        self._custom_announcements = announcements.load(cfg["ANNOUNCEMENTS_FILE"])
        self._announce_volumes = announcements.volumes(cfg["ANNOUNCEMENTS_FILE"])
        self._announcements_stamp = None
        self._sound_volume = None
        self._music_lists = music_lists.load(cfg["MUSIC_LISTS_FILE"])
        self._lists_stamp = None
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

    def _wired_output(self):
        """True when the sound goes to a wired output of the Pi, not the
        Bluetooth speaker."""
        return self._output_kind() in ("jack", "usb", "hdmi")

    def _output_kind(self):
        return self._output_override or self.cfg.get("AUDIO_OUTPUT", "bluetooth")

    def _apply_audio_output(self, force=False):
        """Points mpv at the chosen output."""
        now = time.monotonic()
        if not force and now - self._audio_output_checked < self.AUDIO_OUTPUT_RECHECK_SEC:
            return
        self._audio_output_checked = now
        if self._output_override:
            planned = self.cfg.get("AUDIO_OUTPUT", "bluetooth")
            if audio_output.find(planned, audio_output.list_sinks(env=audio_env())):
                log.info("Audio output: '%s' is back, leaving '%s'", planned, self._output_override)
                self._output_override = None
                self._bump_state()
        kind = self._output_kind()
        sinks = audio_output.list_sinks(env=audio_env()) if self._wired_output() else []
        device, found = audio_output.mpv_device(kind, sinks)
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

        if os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"):
            log.info("Hardware RTC module detected, clock considered reliable")
            self.stats.set_clock("rtc", offset_sec=0.0)
            self._clock_ready.set()
            return

        log.warning("No hardware RTC detected, the clock is not guaranteed to be reliable")

        if self.cfg["BT_CLOCK_ENABLED"] and self.cfg["BT_CLOCK_MAC"] not in ("", "XX:XX:XX:XX:XX:XX"):
            threading.Thread(target=self._try_bt_clock_sync, daemon=True).start()
        else:
            log.warning("Bluetooth time recovery disabled or BT_CLOCK_MAC not configured")
            self.stats.set_clock("none", trusted=False)
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
                self.stats.set_clock("none", trusted=False)
                self._signal_clock_outcome(False)
                self._clock_ready.set()

        threading.Thread(target=grace_timeout, daemon=True).start()

    def _try_bt_clock_sync(self):
        from bt_clock import fetch_time_from_bt

        mac = self.cfg["BT_CLOCK_MAC"]
        log.info("Attempting to recover the time via Bluetooth (%s)...", mac)
        dt = fetch_time_from_bt(mac, timeout_sec=self.cfg["CLOCK_SYNC_GRACE_SEC"])
        if dt is None:
            log.warning("Failed to recover the time via Bluetooth")
            self.stats.set_clock("none", trusted=False)
            self._signal_clock_outcome(False)
            self._clock_ready.set()
            return

        # Measured before `date -s`: the statistics correct the wrong-clock timestamps with it.
        offset = dt.timestamp() - time.time()
        formatted = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        result = subprocess.run(
            ["sudo", "date", "-s", formatted], capture_output=True, text=True
        )
        if result.returncode == 0:
            log.info("System time set via Bluetooth: %s (offset %+.1fs)", formatted, offset)
            self.stats.set_clock("bluetooth", offset_sec=offset)
            self._signal_clock_outcome(True)
            self._clock_ready.set()
        else:
            log.error("Failed to set system time: %s", result.stderr.strip())
            self.stats.set_clock("none", trusted=False)
            self._signal_clock_outcome(False)
            self._clock_ready.set()

    def start(self):
        os.makedirs(self.cfg["STATE_DIR"], exist_ok=True)
        self._install_signal_handlers()
        self.stats.open_session()
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

        self.state.ensure_queue(
            self._playable_tracks(), self.cfg["MUSIC_ORDER_MODE"], self.cfg["MUSIC_DIR"],
            self.cfg["MUSIC_KEEP_PROGRESS"], self.cfg["MUSIC_RESUME_MODE"],
            custom_order=music_lists.custom_order(self._active_list_entry()),
        )

        if self.cfg["MUSIC_START_MODE"] == "boot":
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

    def _reload_config(self):
        """Applies a settings save without a restart."""
        try:
            fresh = load_config(env_overrides=False)
        except Exception:  # noqa: BLE001
            log.exception("Could not re-read the configuration")
            return {"ok": False, "error": "config_unreadable"}
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
        if "AUDIO_COMPRESSION" in applied:
            self._apply_compression()
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
        tracks = get_music_list(self.cfg["MUSIC_DIR"], self.cfg["MUSIC_CACHE_FILE"])
        self._track_count = len(tracks or [])
        if not tracks:
            log.warning("No tracks found in %s", self.cfg["MUSIC_DIR"])
        return tracks

    def _announcements(self):
        """The custom announcements and their volumes, re-read whenever the
        file changed. `reload_announcements` is the web interface's own shout,
        but this is what makes a hand edit over SSH - or a message that never
        arrived - harmless: a stale list answered "that announcement no longer
        exists" for a "Jouer" that had every right to work."""
        path = self.cfg["ANNOUNCEMENTS_FILE"]
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = None
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
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = None
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
        """What the radio plays: the active list's tracks, or the whole
        library when no list is active, minus what a duplicate check kept
        aside - the files are still there, the radio just stops choosing them
        by itself."""
        tracks = self._get_music_list()
        entry = self._active_list_entry()
        if entry:
            tracks = music_lists.resolved(entry, tracks, self._genre_paths)
        hidden = self._hidden_paths()
        if not hidden:
            return tracks
        return [path for path in tracks if path not in hidden]

    def _hidden_paths(self):
        """The paths a duplicate check kept aside, or nothing at all."""
        try:
            return hidden_tracks.paths(self.cfg.get("HIDDEN_FILE") or "")
        except Exception:  # noqa: BLE001 - never keep the radio from playing
            log.exception("Could not read the hidden tracks")
            return set()

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
        self.state.rebuild_queue(tracks, self.cfg["MUSIC_ORDER_MODE"], self.cfg["MUSIC_DIR"],
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

    def _apply_compression(self):
        """Pushes the loudness filter to mpv, which holds it across every
        loadfile; an empty chain clears it."""
        mode = self.cfg["AUDIO_COMPRESSION"]
        chain = compression_filter(mode)
        if chain and not self.mpv.set_audio_filter(chain):
            log.warning("This mpv build does not take the %s compression filter,"
                        " playing without it", mode)
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
        forced, self._forced_next = self._forced_next, None
        if forced and os.path.exists(forced):
            self._play_track(forced)
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
            return

        track = self.state.pop_next_track_or_none()
        if track is None:
            if not self.cfg["MUSIC_LOOP"]:
                self._enter_stopped_mode()
                return
            self._rebuild_queue(tracks)
            track = self.state.pop_next_track_or_none()
            if track is None:
                return

        self._play_track(track)

    def _play_track(self, path, start=0.0):
        """Plays one music file, from `start` seconds."""
        log.info("Playing: %s%s", path, " from %.0fs" % start if start else "")
        if self._sound_volume is not None:
            # Back from an announcement that played at a volume of its own.
            self._restore_base_volume()
        self.mode = "music"
        self._pending_seek = start if start and start > 1 else None
        self._begin_play("music", path)
        if self._pending_seek:
            self._position = self._pending_seek
        self._hold_music_without_speaker()

    def _hold_music_without_speaker(self):
        """Keeps a song paused when it starts while the speaker is away.

        A click - or an announcement - otherwise undoes the pause
        `_on_speaker_lost()` just made, and the radio plays to nothing for as
        long as the speaker is gone (measured: ten minutes, 2026-09-29).
        `_on_speaker_back()` lifts it like the pause it replaced."""
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
        album = playlist.order_files(album, self.cfg["MUSIC_DIR"], "ordered")
        if path not in album:
            return album[0]
        return album[(album.index(path) + step) % len(album)]

    def _library_path(self, raw):
        """`raw` as a real file of the music library, or None."""
        path = os.path.realpath(str(raw or ""))
        root = os.path.realpath(self.cfg["MUSIC_DIR"])
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

    def _schedule_restart(self, on):
        """"Restart the service at the end of the song"."""
        if not on:
            self._restart_pending = False
            self._bump_state()
            return
        if self.mode in ("idle", "stopped"):
            self._do_planned_restart()
            return
        self._restart_pending = True
        self._bump_state()
        log.info("Service restart planned for the end of the song")

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
        log.info("Restarting the service (planned from the web interface)")
        self._end_play("restart")
        try:
            subprocess.Popen(["sudo", "systemctl", "restart", "rukebox-daemon.service"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("Could not restart the service")
            self.mode = "idle"

    def _start_or_restart_playback(self, log_label="idle"):
        """First click from idle, or a click/API call restarting playback after
        a non-looping list finished."""
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
        self._start_music_faded(self._play_next_track)

    def _start_music_faded(self, start):
        """Runs `start` (which begins the first song) under START_FADE_SEC."""
        try:
            fade = float(self.cfg.get("START_FADE_SEC", 0) or 0)
        except (TypeError, ValueError):
            fade = 0.0
        fade = min(max(fade, 0.0), 120.0)
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

    def _glide_volume(self, target, duration_sec):
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
                vol = round(start + (end - start) * i / steps, 1)
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
            self.mpv.set_volume(self._music_gain())
            return
        self.mpv.set_volume(vol)

    def _volume_watch_loop(self):
        """Follows the speaker's own volume when the two are linked."""
        while not self._stop_event.wait(self.SINK_POLL_SEC):
            if self.mode == "shutting_down":
                return
            self._follow_sink_volume()

    def _follow_sink_volume(self):
        """One watch turn: the speaker's own volume, when the link is on."""
        if not self._speaker_volume_linked():
            self._sink_level = None
            return
        if self._sink_level is None:
            # Just linked or just started: the interface's own volume goes to the speaker.
            self._set_sink_volume(self._target_volume())
            return
        found = audio_diag.default_sink_volume(env=audio_env())
        if found is None:
            return
        percent = max(0.0, min(100.0, round(found * 100.0, 1)))
        if abs(percent - self._sink_level) <= 1.0:
            return
        self._sink_level = percent
        self._adopt_volume(percent)

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

    def _play_announce_queue(self, mode_name, files, volume_key=None):
        self._announce_queue = list(files)
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
            self._do_shutdown_sequence()
        elif mode_name == "meme":
            self._resume_after_announce()
        elif mode_name.startswith("custom:"):
            self._restore_base_volume()
            self._resume_after_announce()
        elif mode_name.startswith("button_announce:"):
            self._restore_base_volume()
            self._resume_after_announce()

    def _trigger_cutoff_event_exact(self):
        log.info("Triggering the cutoff (exact mode)")
        self._record_cutoff_trigger("exact")
        self._fade_out_and_pause(self.cfg["FADE_DURATION_SEC"])
        self._restore_base_volume()
        files = self._list_announce_files(self.cfg["CUTOFF_ANNOUNCE_DIR"], source_id="cutoff")
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
        files = self._list_announce_files(self.cfg["CUTOFF_ANNOUNCE_DIR"], source_id="cutoff")
        self._play_announce_queue("cutoff_announce", files, volume_key="cutoff")
        self.state.mark_triggered_today("last_cutoff_trigger")

    def _arm_cutoff_end_of_track(self):
        log.info("Scheduled cutoff: waiting for the end of the current track")
        self._record_cutoff_trigger("end_of_track")
        self.state.set_pending_cutoff(True)
        self.state.mark_triggered_today("last_cutoff_trigger")

    def _trigger_custom_announcement(self, item, on_demand=False):
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
        self._resume_mode = self.mode
        self._fade_out_and_pause(self.cfg["INTERACTIVE_FADE_DURATION_SEC"] if on_demand
                                 else self.cfg["FADE_DURATION_SEC"])
        self._restore_base_volume()
        source_id = "custom:%s" % item["id"]
        self._play_announce_queue(source_id, self._next_announce_file(source_id, item["folder"]),
                                  volume_key=source_id)
        if not on_demand and item.get("trigger") == "time":
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
        files = self._list_announce_files(self.cfg["CUTOFF_ANNOUNCE_DIR"], source_id="cutoff")
        self._play_announce_queue("cutoff_announce", files, volume_key="cutoff")

    def _do_shutdown_sequence(self, force=False, reason="cutoff"):
        self._end_play("shutdown")
        mac = self.cfg["SPEAKER_MAC"]
        if mac and mac != "XX:XX:XX:XX:XX:XX":
            log.info("Disconnecting Bluetooth from %s", mac)
            self._bluetoothctl("disconnect", mac, timeout=10)

        poweroff = bool(force or self.cfg["SHUTDOWN_AFTER_CUTOFF"])
        self.stats.record("shutdown", label=reason, detail={"poweroff": poweroff})
        self.stats.end_session(reason)

        if poweroff:
            log.info("Shutting down the Raspberry Pi")
            self._powering_off = True
            self._bump_state()
            time.sleep(2)
            subprocess.run(["sudo", "systemctl", "poweroff"], check=False)
        else:
            log.info("SHUTDOWN_AFTER_CUTOFF=false, the Pi stays on (mpv paused)")
            self.mpv.set_pause(True)

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

    def _click_source_folder(self, source):
        return announcements.resolve_source_folder(self.cfg, self._custom_announcements, source)

    CLICK_ACTIONS = (
        "next", "previous", "sound",
        "playpause", "pause", "play",
        "loop_track", "loop_album", "loop_off",
        "volume_up", "volume_down", "sleep",
        "mute", "standby", "poweroff",
        "off",
    )
    LONG_PRESS_ACTIONS = ("poweroff", "standby")
    SOUND_ACTIONS = ("next", "previous", "sound")
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

    SPEAKER_GESTURES = ("playpause", "next", "previous")

    def _handle_speaker_button(self, gesture, source="speaker"):
        """A button of the Bluetooth speaker."""
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
            log.info("Speaker %s ignored: an action is already in progress (%s)", gesture, self.mode)
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

    def _go_standby(self, source="unknown"):
        """Standby: the same fade as before switching off, then the Pi stays on
        and waits exactly like at startup (keep-alive sound, mode idle)."""
        if self.mode in ("idle", "stopped", "shutting_down", "restarting"):
            return False
        log.info("Standby (%s)", source)
        song = self._last_music_track if self.mode == "music" else None
        self._set_timer("resume", None)
        self._set_timer("sleep", None)
        self._fade_out_and_pause(self.cfg["LONGPRESS_FADE_DURATION_SEC"])
        self._announce_queue = []
        self._forced_next = None
        self._resume_track = None
        self._pending_seek = None
        self._paused = False
        if song and os.path.exists(song):
            self.state.push_front(song)
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

        if self.mode in ("idle", "stopped"):
            self._start_keepalive(target_mode=self.mode)
            return

        if self.mode == "music":
            if reason == "error" and self._should_back_off():
                return
            if self.state.is_pending_cutoff() and self.cfg["CUTOFF_MODE"] == "end_of_track":
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
        if self.cfg["AP_WATCH_INTERVAL_SEC"] > 0:
            threading.Thread(target=self._ap_watch_loop, daemon=True).start()

    # flicd takes a controller for itself, so the Flic button competes with the speaker.
    FLIC_UNITS = ("flicd.service", "flic-bridge.service")
    FLIC_FLAG = "flic_held"

    def _flic_flag(self, verb):
        """systemctl's own answer for flicd ("enabled", "active"...), or ""."""
        try:
            done = subprocess.run(["systemctl", verb, "flicd.service"],
                                  capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout.strip()

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
        try:
            done = subprocess.run(["sudo", "-n", "systemctl", action] + list(self.FLIC_UNITS),
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is None or done.returncode != 0:
            if not self._flic_warned:
                self._flic_warned = True
                log.warning("Could not %s the Flic services: %s", action,
                            (done.stderr or "").strip()[-200:] if done else "no answer")
            return False
        self.state.set_flag(self.FLIC_FLAG, action == "stop")
        if action == "start":
            log.info("Flic button back on: a controller is free again")
        return True

    def _speaker_watch_loop(self):
        """Watches the speaker connection: statistics, pause, power-off delays."""
        wait = 3.0
        warned = None
        while not self._stop_event.wait(wait):
            if self.mode == "shutting_down":
                return
            self._watch_flic()
            interval = self._speaker_watch_interval()
            wait = interval if interval > 0 else 30.0
            if interval <= 0:
                continue
            mac = self.cfg.get("SPEAKER_MAC", "")
            if not mac or mac == "XX:XX:XX:XX:XX:XX":
                if warned != "no_mac":
                    warned = "no_mac"
                    if self._speaker_watch_needed():
                        log.warning("No speaker configured (speaker_mac): nothing to watch, "
                                    "the speaker-dependent settings cannot act")
                    else:
                        log.info("No speaker configured, connection monitoring idle")
                self._check_speaker_absent()
                continue
            warned = None
            state = self._speaker_link(mac)
            if state["unknown"]:
                continue  # no controller answered: not the same as "gone"
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

            if self._speaker_was_connected is None:
                self._speaker_was_connected = audible
                if audible:
                    self._start_music_on_speaker_connect(mac)
                else:
                    self._speaker_lost_at = time.monotonic()
                self._check_speaker_absent()
                continue

            if audible != self._speaker_was_connected:
                self._speaker_was_connected = audible
                if audible:
                    self._on_speaker_back(mac)
                else:
                    self._on_speaker_lost(mac, "disconnected" if not connected else "silent")

            if not audible:
                self._check_speaker_lost_too_long()
            self._check_speaker_absent()

    def _on_speaker_lost(self, mac, reason="disconnected"):
        if reason == "silent":
            log.warning("Speaker connected (%s) but the link carries nothing: PipeWire has no "
                        "Bluetooth output left, so the music was playing into silence", mac)
        else:
            log.warning("Speaker disconnected while running (%s)", mac)
        self._speaker_lost_at = time.monotonic()
        self.stats.record(
            "speaker_silent" if reason == "silent" else "speaker_disconnected", label=mac,
            detail={
                "reason": reason,
                "mode": self.mode,
                "track": os.path.basename(self._current_track) if self._current_track else None,
            },
            counters={"speaker_drops": 1}, daily={"speaker_drops": 1},
        )
        if self.cfg.get("SPEAKER_LOSS_PAUSE") and not self._wired_output() \
                and self.mode == "music" and not self._paused:
            log.info("Pausing the music until the speaker comes back")
            self.mpv.set_pause(True)
            self._paused = True
            self._paused_for_speaker = True
            self.stats.record("playback_pause", label="paused", detail={"source": "speaker_lost"})

    def _on_speaker_back(self, mac):
        log.info("Speaker reconnected (%s)", mac)
        self._speaker_lost_at = None
        self.stats.record("speaker_reconnected", label=mac, counters={"speaker_recoveries": 1})
        if self._output_override and self.cfg.get("AUDIO_OUTPUT", "bluetooth") == "bluetooth":
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
        if self.mode == "music" and paused:
            if fade > 0:
                self._fade_out_to_zero(fade)
            self.mpv.set_pause(True)
        elif self.mode == "music":
            self._resume_with_fade(fade)
        else:
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

    def _start_music_on_speaker_connect(self, mac):
        """MUSIC_START_MODE=bluetooth."""
        if self.cfg["MUSIC_START_MODE"] != "bluetooth":
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
            clients = self._ap_clients()
            if clients is None:
                continue

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

            self._announcements()
            now = datetime.now()

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

            if (
                now.hour == self.cfg["CUTOFF_HOUR"]
                and now.minute == self.cfg["CUTOFF_MINUTE"]
                and not self.state.already_triggered_today("last_cutoff_trigger")
            ):
                if self.mode in ("idle", "stopped"):
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

            time.sleep(15)

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
            "upcoming_track_path": self._upcoming_track(),
            "track_count": self._track_count,
            "active_list": self._active_list_status(),
            "music_started_today": self.state.already_triggered_today("last_music_start"),
            "version": self._state_version,
            "restart_pending": self._restart_pending,
            "powering_off": self._powering_off,
            "position": round(self._position, 1),
            "duration": round(self._duration, 1),
            "paused": self._paused,
            "loop_mode": self._loop_mode,
            "muted": self._muted,
            "output_override": self._output_override,
            "pause_durations": self._pause_durations(),
            "sleep_durations": self._sleep_durations(),
            "timers": {name: (round(max(0.0, due - time.monotonic())) if due else None)
                       for name, due in (("resume", self._timer_due.get("resume")),
                                         ("sleep", self._timer_due.get("sleep")))},
            "music_order_mode": self.cfg["MUSIC_ORDER_MODE"],
            "previous_restart_sec": self.PREVIOUS_RESTART_AFTER_SEC,
            "music_loop": self.cfg["MUSIC_LOOP"],
            "music_start_mode": self.cfg["MUSIC_START_MODE"],
            "music_start_time": "%02d:%02d" % (
                self.cfg["MUSIC_START_HOUR"], self.cfg["MUSIC_START_MINUTE"]),
            "clock_ready": self._clock_ready.is_set(),
            "system_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "has_rtc": os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"),
            "speaker_mac": self.cfg["SPEAKER_MAC"],
            "cutoff_mode": self.cfg["CUTOFF_MODE"],
            "cutoff_hour": self.cfg["CUTOFF_HOUR"],
            "cutoff_minute": self.cfg["CUTOFF_MINUTE"],
            "stats_enabled": self.stats.enabled,
            "clock_source": self.stats.clock_source,
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
            if source not in ("flic", "gpio", "web", "speaker", "push", "unknown"):
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
                if self.mode in ("idle", "stopped"):
                    self._forced_next = path
                    self._start_or_restart_playback(log_label="request")
                    self.state.take_from_queue(path)
                    self.stats.record("track_queued", label=os.path.basename(path),
                                      detail={"source": source, "started": True})
                    return {"ok": True, "data": {"started": True}}
                position = self.state.enqueue_request(path)
                self.stats.record("track_queued", label=os.path.basename(path), detail={"source": source})
                self._bump_state()
                return {"ok": True, "data": {"started": False, "position": position + 1}}
            if cmd == "get_queue":
                try:
                    count = max(0, min(50, int(msg.get("n", 10))))
                except (TypeError, ValueError):
                    count = 10
                return {"ok": True, "data": {"paths": self._next_tracks(count),
                                             "requested": self.state.requested_paths()}}
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
            if cmd == "set_loop":
                mode = msg.get("mode")
                if mode == "cycle":
                    mode = {"off": "album", "album": "track"}.get(self._loop_mode, "off")
                if mode not in ("off", "track", "album"):
                    return {"ok": False, "error": "invalid_value"}
                self._set_loop_mode(mode, source)
                return {"ok": True, "data": {"loop_mode": self._loop_mode}}
            if cmd == "reload_lists":
                self._lists_stamp = None
                return {"ok": True, "count": len(self._lists())}
            if cmd == "reload_hidden":
                tracks = self._rebuild_queue()
                return {"ok": True, "tracks": len(tracks),
                        "hidden": len(self._hidden_paths())}
            if cmd == "set_active_list":
                return self._set_active_list(msg.get("id"), source, bool(msg.get("start")))
            if cmd == "skip_sound":
                mode = self.mode
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
                from config_and_scan import force_rescan
                force_rescan(self.cfg["MUSIC_CACHE_FILE"])
                # Read the list back now, or the interface shows the old count until the next track.
                self._get_music_list()
                log.info("Music rescanned: %d tracks", self._track_count)
                self.stats.record("music_rescan", detail={"source": source})
                return {"ok": True, "tracks": self._track_count}
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
                self.stats.set_clock(
                    msg.get("clock_source", "manual"), offset_sec=offset, trusted=True,
                )
                ready = getattr(self, "_clock_ready", None)
                if ready is not None:
                    ready.set()
                return {"ok": True}
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
                self._schedule_restart(bool(msg.get("on", True)))
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
                    except Exception as exc:  # noqa: BLE001
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

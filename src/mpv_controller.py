"""mpv driven through its JSON IPC socket, one file at a time."""

import json
import logging
import os
import queue
import socket
import subprocess
import threading
import time

log = logging.getLogger("mpv")

# acompressor, then a fixed gain, then alimiter: quiet music audible, loud music not clipping.
COMPRESSION_FILTERS = {
    "soft": ("acompressor=threshold=0.125:ratio=2.5:attack=20:release=400:makeup=1"
             ":knee=6:link=average,volume=5dB,alimiter=limit=0.95:level=false"),
    "strong": ("acompressor=threshold=0.063:ratio=4:attack=15:release=300:makeup=1"
               ":knee=6:link=average,volume=9dB,alimiter=limit=0.95:level=false"),
}


def compression_filter(mode):
    """The filter chain for a compression mode, "" when it is off or unknown."""
    return COMPRESSION_FILTERS.get(str(mode or "").strip().lower(), "")


def audio_env():
    """The environment mpv needs to reach the audio server, or None to inherit
    the current one unchanged."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        return None
    runtime = os.path.join("/run/user", str(os.getuid()))
    if not os.path.isdir(runtime):
        return None
    return {**os.environ, "XDG_RUNTIME_DIR": runtime}


class MPVController:
    def __init__(self, socket_path, mpv_binary="mpv", extra_args=None):
        self.socket_path = socket_path
        self.mpv_binary = mpv_binary
        self.extra_args = extra_args or []
        self.proc = None
        self.sock = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._event_callbacks = []
        self._listener_thread = None
        self._pending = {}
        self._pending_lock = threading.Lock()

    def start(self):
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass

        args = [
            self.mpv_binary,
            "--idle=yes",
            "--no-terminal",
            "--no-video",
            "--volume=100",
            f"--input-ipc-server={self.socket_path}",
        ] + self.extra_args

        log.info("Starting mpv: %s", " ".join(args))
        self.proc = subprocess.Popen(args, env=audio_env())

        for _ in range(50):
            if os.path.exists(self.socket_path):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("mpv never created its IPC socket")

        self._connect()
        self._listener_thread = threading.Thread(target=self._listen_events, daemon=True)
        self._listener_thread.start()

    def _connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        last_err = None
        for _ in range(50):
            try:
                self.sock.connect(self.socket_path)
                return
            except (FileNotFoundError, ConnectionRefusedError) as e:
                last_err = e
                time.sleep(0.1)
        raise RuntimeError(f"Could not connect to mpv socket: {last_err}")

    def _send(self, command):
        with self._lock:
            self._request_id += 1
            payload = json.dumps({"command": command, "request_id": self._request_id}) + "\n"
            try:
                self.sock.sendall(payload.encode("utf-8"))
            except OSError:
                log.exception("Failed to send mpv command, attempting to reconnect")
                self._connect()
                self.sock.sendall(payload.encode("utf-8"))

    def request(self, command, timeout=3.0):
        """Sends a command and waits for mpv's answer, or None if it never
        came - the one way to know whether a property write was refused."""
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            box = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._pending[request_id] = box
            payload = json.dumps({"command": command, "request_id": request_id}) + "\n"
            try:
                self.sock.sendall(payload.encode("utf-8"))
            except OSError:
                log.exception("Failed to send mpv command, attempting to reconnect")
                try:
                    self._connect()
                    self.sock.sendall(payload.encode("utf-8"))
                except OSError:
                    self._forget(request_id)
                    return None
        try:
            return box.get(timeout=max(0.1, float(timeout)))
        except queue.Empty:
            log.warning("mpv never answered %s", command[0])
            return None
        finally:
            self._forget(request_id)

    def _forget(self, request_id):
        with self._pending_lock:
            self._pending.pop(request_id, None)

    def set_audio_filter(self, chain):
        """Applies a libavfilter chain ("" clears it). False when mpv took
        neither syntax, i.e. a build without the filter."""
        chain = str(chain or "")
        if not chain:
            return self._set_af("")
        # The lavfi wrapper is the fallback for an mpv older than 0.25, or a name clash.
        return self._set_af(chain) or self._set_af("lavfi=[%s]" % chain)

    def _set_af(self, value):
        msg = self.request(["set_property", "af", value])
        if msg is None:
            return False
        if msg.get("error") != "success":
            log.warning("mpv refused the audio filter %s (%s)", value, msg.get("error"))
            return False
        return True

    def loadfile(self, path):
        log.info("loadfile: %s", path)
        self._send(["loadfile", path, "replace"])
        # The keep-alive sound loops; without this the next file would too.
        self._send(["set_property", "loop-file", "no"])

    def seek(self, seconds: float):
        """Jumps to `seconds` from the start of the file."""
        self._send(["seek", max(0.0, float(seconds)), "absolute"])

    def stop_playback(self):
        """Unloads the current file: silence, the process stays up."""
        self._send(["stop"])

    def set_pause(self, paused: bool):
        self._send(["set_property", "pause", paused])

    def set_mute(self, muted: bool):
        """Mutes or unmutes; the volume is left as it is."""
        self._send(["set_property", "mute", bool(muted)])

    def set_volume(self, volume: float):
        self._send(["set_property", "volume", max(0, min(100, volume))])

    def get_volume_blocking(self, default=100):
        return default

    def set_audio_device(self, device: str):
        """"auto" (the audio server's default) or "pipewire/<node>"."""
        self._send(["set_property", "audio-device", device])

    def set_replaygain(self, mode: str):
        self._send(["set_property", "replaygain-mode", mode])

    def set_loop(self, mode="no"):
        self._send(["set_property", "loop-file", mode])

    def observe(self, prop_id, name):
        """Ask mpv to push this property whenever it changes."""
        self._send(["observe_property", prop_id, name])

    def on_event(self, callback):
        self._event_callbacks.append(callback)

    def _listen_events(self):
        buf = b""
        while True:
            try:
                data = self.sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "event" in msg:
                    for cb in self._event_callbacks:
                        try:
                            cb(msg)
                        except Exception:
                            log.exception("Error in an mpv event callback")
                    continue
                with self._pending_lock:
                    box = self._pending.get(msg.get("request_id"))
                if box is not None:
                    box.put(msg)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

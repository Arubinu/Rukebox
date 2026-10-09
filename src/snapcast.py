"""Multiroom output: the radio's own snapserver, fed by a PipeWire sink that writes into its pipe,
and the clients listening to it, asked over snapserver's JSON-RPC."""

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger("snapcast")

SINK = "rukebox_snapcast"
CODECS = ("opus", "flac", "pcm")
SAMPLE_FORMAT = "48000:16:2"
BUFFER_MS = 1000
HTTP_PORT = 1780
SNAPWEB = "/usr/share/snapserver/snapweb"
# Never suspended: silence keeps flowing through a pause or a song change, so the clients
# stay in step instead of running dry and catching up again.
SINK_PROPERTIES = "device.description=Snapcast node.always-process=true session.suspend-timeout-seconds=0"


def available():
    return shutil.which("snapserver") is not None


def folder(state_dir):
    return os.path.join(state_dir, "snapcast")


def fifo(state_dir):
    return os.path.join(folder(state_dir), "fifo")


def config_text(state_dir, codec):
    """snapserver's configuration: one stream read from the pipe the sink writes into."""
    codec = codec if codec in CODECS else CODECS[0]
    lines = [
        "[server]",
        "datadir = %s" % folder(state_dir),
        "",
        "[http]",
        "enabled = true",
        "port = %d" % HTTP_PORT,
    ]
    if os.path.isdir(SNAPWEB):
        lines.append("doc_root = %s" % SNAPWEB)
    lines += [
        "",
        "[tcp]",
        "enabled = true",
        "",
        "[stream]",
        "source = pipe://%s?name=Rukebox&sampleformat=%s&mode=create" % (fifo(state_dir), SAMPLE_FORMAT),
        "codec = %s" % codec,
        "buffer = %d" % BUFFER_MS,
        "",
        "[logging]",
        "filter = *:warning",
        "",
    ]
    return "\n".join(lines)


class Server:
    """The snapserver process the radio starts, watches and stops."""

    RETRY_SEC = 30

    def __init__(self):
        self.proc = None
        self.codec = None
        self._next_try = 0.0

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure(self, state_dir, codec):
        """Running with this codec; a server that died is started again, not more than twice a minute."""
        if self.alive() and self.codec == codec:
            return True
        if self.alive():
            self.stop()
        if time.monotonic() < self._next_try:
            return False
        self._next_try = time.monotonic() + self.RETRY_SEC
        try:
            os.makedirs(folder(state_dir), exist_ok=True)
            conf = os.path.join(folder(state_dir), "snapserver.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(config_text(state_dir, codec))
            self.proc = subprocess.Popen(["snapserver", "-c", conf],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.codec = codec
            log.info("Snapcast: the server is on (codec %s)", codec)
            return True
        except OSError:
            log.exception("Snapcast: the server could not start")
            self.proc = None
            return False

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        log.info("Snapcast: the server is off")
        self.proc = None
        self._next_try = 0.0


def _modules(env):
    try:
        out = subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True,
                             timeout=5, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split("\t") for line in out.splitlines() if "\t" in line]


def sink_module(env, current=False):
    """The id of the pipe sink's module, or None when it is not loaded (with `current`, also
    when it was loaded without today's properties)."""
    for parts in _modules(env):
        if len(parts) >= 3 and parts[1] == "module-pipe-sink" and "sink_name=%s" % SINK in parts[2]:
            if current and "node.always-process" not in parts[2]:
                return None
            return parts[0]
    return None


def load_sink(state_dir, env):
    """The PipeWire sink whose sound goes into snapserver's pipe."""
    if sink_module(env, current=True):
        return True
    unload_sink(env)
    os.makedirs(folder(state_dir), exist_ok=True)
    rate, bits, channels = SAMPLE_FORMAT.split(":")
    args = ["pactl", "load-module", "module-pipe-sink", "file=%s" % fifo(state_dir), "sink_name=%s" % SINK,
            "format=s%sle" % bits, "rate=%s" % rate, "channels=%s" % channels,
            "sink_properties='%s'" % SINK_PROPERTIES]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        log.exception("Snapcast: the output could not be made")
        return False
    if done.returncode != 0:
        log.warning("Snapcast: the output could not be made: %s", (done.stderr or "").strip()[:200])
        return False
    return True


def unload_sink(env):
    found = sink_module(env)
    if found:
        subprocess.run(["pactl", "unload-module", found], capture_output=True, timeout=10, env=env)


def rpc(method, params=None, timeout=3):
    """snapserver's answer to one JSON-RPC call, or raises OSError."""
    body = json.dumps({"id": 1, "jsonrpc": "2.0", "method": method, "params": params or {}}).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:%d/jsonrpc" % HTTP_PORT, data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            data = json.loads(answer.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as e:
        raise OSError(str(e)) from e
    if "error" in data:
        raise OSError(str(data["error"].get("message") if isinstance(data["error"], dict) else data["error"]))
    return data.get("result") or {}


def clients(status):
    """The clients of Server.GetStatus, connected ones first, each as the page shows it."""
    out = []
    for group in ((status or {}).get("server") or {}).get("groups") or []:
        for client in group.get("clients") or []:
            config = client.get("config") or {}
            host = client.get("host") or {}
            volume = config.get("volume") or {}
            out.append({
                "id": client.get("id"),
                "name": config.get("name") or host.get("name") or client.get("id"),
                "host": host.get("name") or "",
                "ip": host.get("ip") or "",
                "connected": bool(client.get("connected")),
                "volume": int(volume.get("percent") or 0),
                "muted": bool(volume.get("muted")),
            })
    out.sort(key=lambda c: (not c["connected"], c["name"].casefold()))
    return out

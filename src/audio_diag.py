"""One snapshot of the audio path: what the radio plays into, through which
link, and what that link is really carrying.

English on purpose - it is a technical report made to be pasted into a message,
like the journal. The page shows it behind a translated button.
"""

import json
import logging
import os
import re
import socket
import subprocess
import time

import audio_output
import bt_link
import control_client

log = logging.getLogger("audio_diag")

# The bitpool the encoder and the speaker agreed on is exposed nowhere
# (PipeWire says "sbc", or "sbc_xq" for its high-bitpool variant), so the only
# way to see which quality a link is really running at is to measure what the
# radio sends per second and read the A2DP table backwards: at 48 kHz joint
# stereo, bitpool 35 is 229 kbit/s, 53 is 328, and 76 (SBC-XQ) is 452.
SBC_KBPS_BY_BITPOOL = ((452, 76), (328, 53), (229, 35))

MEASURE_SECONDS = 6

MPV_PROPERTIES = ("filename", "pause", "core-idle", "mute", "volume", "audio-codec-name",
                  "audio-params", "audio-out-params", "af")


def session_env():
    """The environment the user's PipeWire session needs: a system service has
    no XDG_RUNTIME_DIR, and pw-dump/wpctl then answer nothing at all."""
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    if not os.path.isdir(runtime):
        return env
    env["XDG_RUNTIME_DIR"] = runtime
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + os.path.join(runtime, "bus"))
    return env


def _run(cmd, timeout=6, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _needs_root(cmd, timeout=8):
    """hciconfig and the kernel log want root; the owner's Pi lets sudo run
    without a password, and a Pi that does not simply reports less."""
    result = _run(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        return result
    return _run(["sudo", "-n"] + cmd, timeout=timeout)


def parse_hciconfig(text):
    """[{"name", "bus", "address", "up", "tx_bytes"}] from hciconfig -a."""
    controllers = []
    current = None
    for line in str(text or "").splitlines():
        head = re.match(r"(hci\d+):\s*(.*)", line)
        if head:
            current = {"name": head.group(1), "bus": "", "address": "", "up": False, "tx_bytes": None}
            bus = re.search(r"Bus:\s*(\w+)", head.group(2))
            if bus:
                current["bus"] = bus.group(1)
            controllers.append(current)
            continue
        if current is None:
            continue
        if "RUNNING" in line or line.strip().startswith("UP"):
            current["up"] = True
        address = re.search(r"BD Address:\s*([0-9A-Fa-f:]{17})", line)
        if address:
            current["address"] = address.group(1).upper()
        tx = re.search(r"TX bytes:(\d+)", line)
        if tx:
            current["tx_bytes"] = int(tx.group(1))
    return controllers


def controllers():
    """Every controller, with the kernel's own timeout count for each."""
    result = _needs_root(["hciconfig", "-a"], timeout=10)
    found = parse_hciconfig(result.stdout if result is not None else "")
    marks = kernel_marks()
    for controller in found:
        controller["marks"] = marks.get(controller["name"])
    return found


def kernel_marks():
    """{"hci0": n} - the controller failing to answer the kernel (its own "tx
    timeout", or a command that never came back). The speaker failing to answer
    is "link tx timeout" and is deliberately not counted: an absent speaker is
    not a wedged radio."""
    result = _run(["journalctl", "-k", "--no-pager", "-o", "cat"], timeout=10)
    if result is None or result.returncode != 0:
        return {}
    counts = {}
    for line in result.stdout.splitlines():
        name = re.search(r"(hci\d+):", line)
        if not name:
            continue
        if "command" in line and "tx timeout" in line:
            counts[name.group(1)] = counts.get(name.group(1), 0) + 1
        elif "Opcode" in line and "failed: -110" in line:
            counts[name.group(1)] = counts.get(name.group(1), 0) + 1
    return counts


def default_sink(env=None):
    """PipeWire's default output right now, as (node name, description)."""
    result = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], env=env)
    if result is None or result.returncode != 0:
        return None, None
    name = description = None
    for line in result.stdout.splitlines():
        if "node.name" in line and name is None:
            name = line.split("=", 1)[1].strip().strip('"')
        elif "node.description" in line:
            description = line.split("=", 1)[1].strip().strip('"')
    return name, description


def default_sink_volume(env=None):
    """The default output's own volume (1.0 = what the radio sends). Bluetooth
    speakers put their AVRCP volume here, and anything below 1.0 means the
    interface's slider is only working with what is left."""
    result = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], env=env)
    if result is None or result.returncode != 0:
        return None
    match = re.search(r"Volume:\s*([0-9.]+)", result.stdout)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def set_default_sink_volume(percent, env=None):
    """Sets the default output's own volume, in percent. Always between 0 and
    1.0: PipeWire amplifies above it, and a speaker cannot be pushed past its
    own maximum anyway."""
    try:
        value = max(0.0, min(100.0, float(percent))) / 100.0
    except (TypeError, ValueError):
        return False
    result = _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%.2f" % value],
                  timeout=5, env=env)
    return result is not None and result.returncode == 0


def mpv_properties(path):
    """What mpv says it plays and through what, {} when it is not there."""
    props = {}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(4)
            sock.connect(path)
            for prop in MPV_PROPERTIES:
                sock.sendall((json.dumps({"command": ["get_property", prop]}) + "\n").encode("utf-8"))
                data = b""
                while not data.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    break
                try:
                    props[prop] = json.loads(data.decode("utf-8")).get("data")
                except ValueError:
                    props[prop] = None
    except OSError:
        return {}
    return props


def daemon_status(sock_path):
    """The daemon's own view of the speaker and of playback, {} when it is not
    answering."""
    answer = control_client.send_control_command(sock_path, "get_status", timeout=5)
    if not isinstance(answer, dict) or not answer.get("ok"):
        return {}
    return answer.get("data") or {}


def bitpool_from_kbps(kbps):
    """The SBC bitpool a measured link bitrate corresponds to, or None."""
    for rate, bitpool in SBC_KBPS_BY_BITPOOL:
        if kbps >= rate * 0.85:
            return bitpool
    return None


def speaker_link(cfg, status=None):
    """Which controller the speaker is on, from BlueZ: the sink's own
    api.bluez5.address is the *speaker's*, not the controller's, and only this
    says which radio the audio is going out of."""
    mac = str((status or {}).get("speaker_mac") or cfg.get("SPEAKER_MAC") or "")
    if not mac or mac == "XX:XX:XX:XX:XX:XX":
        return {}
    try:
        return bt_link.locate(mac, cfg.get("SPEAKER_BT_ADAPTER", "") or "")
    except Exception:  # noqa: BLE001
        log.debug("Could not ask BlueZ about the speaker", exc_info=True)
        return {}


def snapshot(cfg=None, status=None, measure=MEASURE_SECONDS, env=None):
    """Everything the audio path is made of, right now."""
    cfg = cfg or {}
    env = env or session_env()
    data = {
        "at": time.time(),
        "output": cfg.get("AUDIO_OUTPUT", "bluetooth"),
        "speaker": speaker_link(cfg, status),
        "sinks": audio_output.list_sinks(env=env),
        "default_sink": None,
        "default_description": None,
        "controllers": controllers(),
        "mpv": mpv_properties(cfg.get("MPV_SOCKET", "/tmp/mpvsocket")),
        "daemon": daemon_status(cfg.get("CONTROL_SOCKET", "/tmp/rukebox_control.sock"))
                     if status is None else status,
        "kbps": None,
        "kbps_seconds": 0,
    }
    data["default_sink"], data["default_description"] = default_sink(env)
    data["sink_volume"] = default_sink_volume(env)

    bluetooth = [s for s in data["sinks"] if s["kind"] == "bluetooth"]
    data["codec"] = bluetooth[0].get("codec") if bluetooth else None
    carried_by = str((data["speaker"] or {}).get("controller") or "").upper()
    for controller in data["controllers"]:
        controller["speaker"] = bool(carried_by) and controller["address"] == carried_by
    if bluetooth and measure:
        data["kbps"], data["kbps_seconds"] = measure_link(data, env, measure)
    return data


def measure_link(data, env, seconds):
    """(kbit/s leaving the radio, seconds actually measured): the audio is
    compressed, so this is the only honest measure of the link's quality."""
    controller = next((c for c in data["controllers"] if c.get("speaker")), None)
    if controller is None or controller.get("tx_bytes") is None:
        return None, 0
    before = controller["tx_bytes"]
    started = time.time()
    time.sleep(seconds)
    after = _controller_tx_bytes(controller["name"])
    if after is None or after < before:
        return None, 0
    elapsed = max(time.time() - started, 0.1)
    return round((after - before) * 8 / elapsed / 1000.0, 1), round(elapsed, 1)


def _controller_tx_bytes(name):
    result = _needs_root(["hciconfig", name], timeout=8)
    if result is None:
        return None
    match = re.search(r"TX bytes:(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def bluetooth_sink_missing(env=None):
    """True when the output is Bluetooth and PipeWire has no Bluetooth sink
    left: BlueZ still says "connected", so only this says the link carries
    nothing and the music is playing into the void."""
    return not any(s["kind"] == "bluetooth" for s in audio_output.list_sinks(env=env))


def _filters(mpv):
    chain = mpv.get("af") or []
    names = [str(entry.get("name")) for entry in chain if isinstance(entry, dict)]
    gain = []
    for entry in chain:
        if isinstance(entry, dict) and entry.get("name") == "volume":
            params = entry.get("params") or {}
            gain.append(str(params.get("@0") or ""))
    return names, [g for g in gain if g]


def verdict(data):
    """What the snapshot means, worst first."""
    lines = []
    default = data.get("default_sink") or ""
    if not data.get("sinks"):
        lines.append("PipeWire reports no output at all: nothing can play.")
    elif data.get("output") == "bluetooth" and not default.startswith("bluez_output"):
        lines.append(
            "The default output is not the speaker (%s): the music is playing into something "
            "the radio makes no sound with." % (data.get("default_description") or default or "?"))

    codec = data.get("codec")
    if codec:
        lines.append("Bluetooth codec: %s%s" % (
            codec, " (the high-bitpool SBC variant)" if codec == "sbc_xq" else ""))
    if codec in ("msbc", "cvsd"):
        lines.append("The link is on the headset profile (%s, telephone quality), not A2DP: "
                     "the music is going out through a speech codec." % codec)
    volume = data.get("sink_volume")
    if volume is not None and volume < 0.9:
        lines.append("The output's own volume is %.2f: the speaker is attenuating everything "
                     "before it is amplified, and the interface's slider only has that much "
                     "range to work with (wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0)." % volume)
    if data.get("kbps"):
        bitpool = bitpool_from_kbps(data["kbps"])
        detail = ("the SBC bitpool the encoder and the speaker agreed on is about %d, out of 76"
                  % bitpool) if bitpool else "no SBC bitpool matches this rate"
        lines.append("Link measured over %ss: %.0f kbit/s (%.0f kB/s) - %s"
                     % (data.get("kbps_seconds") or MEASURE_SECONDS,
                        data["kbps"], data["kbps"] / 8.0, detail))
        if bitpool == 35:
            lines.append("That is the lowest rung of SBC (76 is SBC-XQ): cymbals and stereo "
                         "width are what it costs.")
            lines.append("The speaker is the one choosing it, and it cannot be raised from the "
                         "Pi: offering only SBC-XQ made this speaker answer on the headset "
                         "profile instead (mSBC, telephone quality), and this PipeWire has no "
                         "AAC encoder to fall back on.")

    for controller in data.get("controllers") or []:
        marks = controller.get("marks")
        if marks and controller.get("speaker"):
            lines.append("%s (%s) carries the speaker: %d kernel timeout(s) - the radio did not "
                         "answer its own commands, which is what wedges a link." % (
                             controller["name"], controller.get("bus") or "?", marks))
        if controller.get("up") is False:
            lines.append("%s is DOWN." % controller["name"])

    names, gain = _filters(data.get("mpv") or {})
    if names:
        louder = " (%s)" % ", ".join(gain) if gain else ""
        lines.append("mpv filters: %s%s - the volume boost is ON, which squashes dynamics "
                     "and lifts everything before the limiter." % (", ".join(names), louder))
    if str(data.get("output")) == "bluetooth" and not data.get("kbps"):
        lines.append("The link traffic could not be measured (no root for hciconfig).")

    daemon = data.get("daemon") or {}
    if daemon:
        if daemon.get("mode") == "music" and daemon.get("paused"):
            lines.append("The daemon has the music paused.")
        if daemon.get("consecutive_play_errors"):
            lines.append("mpv has failed %s time(s) in a row: the output is the suspect."
                         % daemon["consecutive_play_errors"])
    return lines


def report(cfg=None, status=None, measure=MEASURE_SECONDS, env=None):
    """The snapshot as text, for a message or the journal."""
    data = snapshot(cfg=cfg, status=status, measure=measure, env=env)
    mpv = data.get("mpv") or {}
    params = mpv.get("audio-out-params") or mpv.get("audio-params") or {}
    daemon = data.get("daemon") or {}
    sinks = data.get("sinks") or []

    lines = ["Audio diagnostic - %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["at"]))]
    lines.append("output configured: %s" % data.get("output"))
    lines.append("default output: %s (%s), its own volume %s" % (
        data.get("default_description") or "none", data.get("default_sink") or "-",
        data.get("sink_volume")))
    lines.append("outputs PipeWire offers: %s" % (", ".join(
        "%s [%s]" % (s["description"], s["kind"]) for s in sinks) or "none"))
    lines.append("bluetooth codec: %s" % (data.get("codec") or "-"))
    speaker = data.get("speaker") or {}
    lines.append("speaker link: %s%s" % (
        "connected" if speaker.get("connected") else ("not connected" if speaker else "unknown"),
        ", via %s" % (speaker.get("controller") or "?") if speaker else ""))
    for controller in data.get("controllers") or []:
        lines.append("controller %s%s: %s, %s, tx %s bytes, %s kernel timeout(s)" % (
            controller["name"], " (speaker)" if controller.get("speaker") else "",
            controller.get("bus") or "?", "up" if controller.get("up") else "DOWN",
            controller.get("tx_bytes"), controller.get("marks")))
    if data.get("kbps"):
        lines.append("link measured: %.0f kbit/s (%.0f kB/s) over %ss" % (
            data["kbps"], data["kbps"] / 8.0, data.get("kbps_seconds")))
    if mpv:
        lines.append("mpv: %s, %s, %s Hz %s, %s, volume %s, mute %s" % (
            os.path.basename(str(mpv.get("filename") or "?")), mpv.get("audio-codec-name") or "?",
            params.get("samplerate") or "?", params.get("channels") or "?",
            "paused" if mpv.get("pause") else "playing", mpv.get("volume"), mpv.get("mute")))
        lines.append("mpv filters: %s" % (", ".join(_filters(mpv)[0]) or "none"))
    else:
        lines.append("mpv: not answering on its socket")
    if daemon:
        lines.append("daemon: mode %s, play errors in a row %s" % (
            daemon.get("mode"), daemon.get("consecutive_play_errors")))
    lines.append("system: %s, load %.2f, swap %s" % (_temperature(), os.getloadavg()[0], _swap()))

    findings = verdict(data)
    lines.append("")
    lines.append("findings:")
    if findings:
        lines.extend(" - " + line for line in findings)
    else:
        lines.append(" - nothing wrong in what could be read")
    return "\n".join(lines)


def _temperature():
    result = _run(["vcgencmd", "measure_temp"], timeout=5)
    if result is None or result.returncode != 0:
        return "temperature unknown"
    return result.stdout.strip().replace("temp=", "")


def _swap():
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            values = {line.split(":")[0]: line.split()[1] for line in handle if ":" in line}
        return "%s/%s MB" % (
            round((int(values.get("SwapTotal", 0)) - int(values.get("SwapFree", 0))) / 1024),
            round(int(values.get("SwapTotal", 0)) / 1024))
    except (OSError, ValueError, IndexError):
        return "unknown"


def main(argv):
    """Usage: audio_diag.py report [--measure SECONDS]"""
    measure = MEASURE_SECONDS
    if "--measure" in argv:
        try:
            measure = max(0, int(argv[argv.index("--measure") + 1]))
        except (IndexError, ValueError):
            print(__doc__)
            return 2
    cfg = {}
    try:  # the settings say which speaker and which output to look at
        from config_and_scan import load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001
        log.debug("No configuration to read", exc_info=True)
    print(report(cfg=cfg, measure=measure))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))

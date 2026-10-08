"""One snapshot of the personal Wi-Fi: the link, what it loses, and every time it dropped since
the Pi started - against what the Bluetooth radio was doing."""

import os
import re
import shutil
import time

import audio_diag

WIFI_INTERFACE = "wlan0"
PING_COUNT = 10
PING_BIG = 1400
# A drop this close to a Bluetooth page giving up is that page's doing.
PAGE_WINDOW_SEC = 3.0
DROPS_SHOWN = 8

DROP_RE = re.compile(r"^(\d+(?:\.\d+)?) .*CTRL-EVENT-DISCONNECTED .*?reason=(-?\d+)(.*)$")
STAMP_RE = re.compile(r"^(\d+(?:\.\d+)?) ")


def _tool(name):
    """iw lives in /usr/sbin, which an ssh session's PATH does not hold."""
    return shutil.which(name) or "/usr/sbin/" + name


def _out(cmd, timeout=8):
    result = audio_diag._run(cmd, timeout=timeout)
    return result.stdout if result is not None and result.returncode == 0 else ""


def parse_station(text):
    """What `iw dev <if> station dump` says of the link to the access point."""
    def field(label, pattern=r"(-?[\d.]+)"):
        found = re.search(r"%s:\s*%s" % (label, pattern), text or "")
        return float(found.group(1)) if found else None

    return {
        "associated": "Station " in (text or ""),
        "signal": field("signal"),
        "tx_bitrate": field("tx bitrate"),
        "rx_bitrate": field("rx bitrate"),
        "tx_failed": field("tx failed"),
        "connected_sec": field("connected time"),
    }


def parse_drops(text):
    """[{"at", "reason", "local"}] from wpa_supplicant's journal (-o short-unix)."""
    drops = []
    for line in (text or "").splitlines():
        found = DROP_RE.match(line)
        if found:
            drops.append({"at": float(found.group(1)), "reason": int(found.group(2)),
                          "local": "locally_generated=1" in found.group(3)})
    return drops


def parse_pages(text):
    """When a Bluetooth connection attempt gave up on a device that is not there."""
    pages = []
    for line in (text or "").splitlines():
        found = STAMP_RE.match(line)
        if found and "Host is down" in line:
            pages.append(float(found.group(1)))
    return pages


def drops_at_a_page(drops, pages, window=PAGE_WINDOW_SEC):
    return sum(1 for drop in drops if any(abs(drop["at"] - page) <= window for page in pages))


def parse_ping(text):
    """{"sent", "lost", "avg_ms"} from ping's own summary."""
    counts = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", text or "")
    if not counts:
        return None
    rtt = re.search(r"= [\d.]+/([\d.]+)/", text)
    sent, got = int(counts.group(1)), int(counts.group(2))
    return {"sent": sent, "lost": sent - got, "avg_ms": float(rtt.group(1)) if rtt else None}


def ping(host, size=None, count=PING_COUNT):
    cmd = ["ping", "-n", "-c", str(count), "-i", "0.2", "-W", "1"]
    if size:
        cmd += ["-s", str(size)]
    result = audio_diag._run(cmd + [host], timeout=count * 1.4 + 4)
    return parse_ping(result.stdout if result is not None else "")


def gateway(interface=WIFI_INTERFACE):
    found = re.search(r"default via (\S+)", _out(["ip", "route", "show", "default", "dev", interface]))
    return found.group(1) if found else None


def speaker_controller(cfg, found=None):
    """The controller the speaker goes through: the one named, else BlueZ's default."""
    found = audio_diag.controller_list() if found is None else found
    wanted = str((cfg or {}).get("SPEAKER_BT_ADAPTER") or "").strip()
    if not wanted:
        default = re.search(r"Controller (\S+) .*\[default\]", _out(["bluetoothctl", "list"], timeout=5))
        wanted = default.group(1) if default else ""
    for controller in found or []:
        if wanted and wanted.upper() in (controller["address"], controller["name"].upper()):
            return controller
    return None


def snapshot(cfg=None, pings=True):
    cfg = cfg or {}
    iw = _tool("iw")
    ap = str(cfg.get("AP_INTERFACE") or "uap0")
    journal = ["journalctl", "-b", "--no-pager", "-o", "short-unix"]
    data = {
        "at": time.time(),
        "interface": WIFI_INTERFACE,
        "station": parse_station(_out([iw, "dev", WIFI_INTERFACE, "station", "dump"])),
        "power_save": _out([iw, "dev", WIFI_INTERFACE, "get", "power_save"]).strip().split(": ")[-1],
        "ap_clients": _out([iw, "dev", ap, "station", "dump"]).count("Station "),
        "drops": parse_drops(_out(journal + ["-u", "wpa_supplicant"], timeout=15)),
        "pages": parse_pages(_out(journal + ["-u", "bluetooth"], timeout=15)),
        "speaker_controller": speaker_controller(cfg),
        "throttled": _out(["vcgencmd", "get_throttled"]).strip().replace("throttled=", ""),
        "gateway": gateway(),
        "daemon": audio_diag.daemon_status(cfg.get("CONTROL_SOCKET") or "/tmp/rukebox_control.sock"),
    }
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            data["uptime"] = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        data["uptime"] = None
    if pings and data["gateway"]:
        data["ping_small"] = ping(data["gateway"])
        data["ping_big"] = ping(data["gateway"], PING_BIG)
    return data


def _shared_radio(controller):
    return bool(controller) and controller.get("bus", "").upper() != "USB"


def verdict(data):
    lines = []
    station = data.get("station") or {}
    drops = data.get("drops") or []
    if not station.get("associated"):
        lines.append("the personal Wi-Fi is not connected right now")
    elif station.get("signal") is not None and station["signal"] < -70:
        lines.append("weak signal (%.0f dBm): move the Pi or the access point" % station["signal"])

    if drops:
        at_page = drops_at_a_page(drops, data.get("pages") or [])
        local = sum(1 for drop in drops if drop["local"])
        if at_page >= 2 and at_page * 2 >= len(drops):
            line = ("%d of the %d drops came within %.0fs of a Bluetooth connection attempt giving up"
                    % (at_page, len(drops), PAGE_WINDOW_SEC))
            if _shared_radio(data.get("speaker_controller")):
                line += (": the speaker goes through the built-in controller, which shares the Wi-Fi's"
                         " radio, and each attempt to reach an absent speaker takes the antenna."
                         " Switch the speaker on, or put it on a USB controller")
            lines.append(line)
        elif local * 2 >= len(drops):
            lines.append("the Pi itself gave the link up %d time(s) (reason 0, local): its radio lost"
                         " the access point's beacons" % local)
        else:
            reasons = sorted({drop["reason"] for drop in drops if not drop["local"]})
            lines.append("the access point ended the link %d time(s) (reason %s)" % (
                len(drops) - local, ", ".join(str(r) for r in reasons)))

    small, big = data.get("ping_small"), data.get("ping_big")
    if big and big["sent"] and big["lost"] * 10 >= big["sent"]:
        if small and not small["lost"]:
            lines.append("large frames are lost (%d of %d) while small ones pass: interference on"
                         " the air, not a fault on the wire" % (big["lost"], big["sent"]))
        else:
            lines.append("%d of %d large frames lost" % (big["lost"], big["sent"]))
    if data.get("power_save") == "on":
        lines.append("Wi-Fi power save is on: it delays what the Pi receives")
    if data.get("throttled") not in ("", "0x0", None):
        lines.append("the Pi reports under-voltage or throttling (%s)" % data["throttled"])
    return lines


def _ping_line(label, result):
    if not result:
        return "%s: no answer" % label
    return "%s: %d of %d lost%s" % (label, result["lost"], result["sent"],
                                    ", %.0f ms" % result["avg_ms"] if result["avg_ms"] is not None else "")


def report(cfg=None, pings=True):
    """The snapshot as text, for a message or the journal."""
    data = snapshot(cfg=cfg, pings=pings)
    station = data["station"]
    drops, pages = data["drops"], data["pages"]
    lines = ["Network diagnostic - %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["at"]))]
    if station["associated"]:
        lines.append("%s: signal %s dBm, tx %s MBit/s, last frame received at %s MBit/s, %s tx failed,"
                     " connected for %s s" % (
                         data["interface"], _number(station["signal"]), _number(station["tx_bitrate"]),
                         _number(station["rx_bitrate"]), _number(station["tx_failed"]),
                         _number(station["connected_sec"])))
    else:
        lines.append("%s: not connected" % data["interface"])
    lines.append("power save: %s, devices on the access point: %d" % (
        data["power_save"] or "?", data["ap_clients"]))
    if data.get("gateway"):
        lines.append(_ping_line("ping %s" % data["gateway"], data.get("ping_small")))
        lines.append(_ping_line("ping %s, %d bytes" % (data["gateway"], PING_BIG), data.get("ping_big")))
    else:
        lines.append("no route through %s" % data["interface"])
    uptime = data.get("uptime")
    lines.append("drops since the Pi started%s: %d, of which %d at a Bluetooth connection attempt"
                 " (%d attempts gave up)" % (
                     " %d min ago" % (uptime / 60) if uptime else "", len(drops),
                     drops_at_a_page(drops, pages), len(pages)))
    for drop in drops[-DROPS_SHOWN:]:
        lines.append("  %s reason %d%s" % (
            time.strftime("%H:%M:%S", time.localtime(drop["at"])), drop["reason"],
            ", by the Pi itself" if drop["local"] else ", by the access point"))
    controller = data.get("speaker_controller")
    if controller:
        lines.append("speaker's controller: %s (%s)%s" % (
            controller["name"], controller.get("bus") or "?",
            ", the Wi-Fi's own radio" if _shared_radio(controller) else ""))
    daemon = data.get("daemon") or {}
    if daemon:
        lines.append("daemon: mode %s%s" % (daemon.get("mode"), ", paused" if daemon.get("paused") else ""))
    lines.append("system: %s, load %.2f, throttled %s" % (
        audio_diag._temperature(), os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        data.get("throttled") or "?"))

    findings = verdict(data)
    lines.append("")
    lines.append("findings:")
    if findings:
        lines.extend(" - " + line for line in findings)
    else:
        lines.append(" - nothing wrong in what could be read")
    return "\n".join(lines)


def _number(value):
    return "?" if value is None else "%g" % value


def main(argv):
    """Usage: net_diag.py report [--no-ping]"""
    cfg = {}
    try:
        from config_and_scan import load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001
        pass
    print(report(cfg=cfg, pings="--no-ping" not in argv))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))

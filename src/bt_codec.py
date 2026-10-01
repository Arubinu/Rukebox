"""Which Bluetooth codecs the Pi offers the speaker.

PipeWire offers every codec listed here and the speaker picks the one it wants.
Offering one it cannot do is not harmless: measured on the owner's Pi, with
SBC-XQ offered alone the soundcore Select 4 Go answered on the headset profile
instead (mSBC, telephone quality) rather than falling back to SBC.

The list becomes a WirePlumber drop-in of its own, generated from the settings,
so the updater never overwrites it and a hand-edited YAML still wins at the
next boot."""

import logging
import os
import subprocess
import sys

log = logging.getLogger("bt_codec")

# PipeWire tries them in this order, best first.
CODECS = ("ldac", "aptx_hd", "aptx", "aac", "sbc_xq", "sbc", "faststream", "opus")

DROP_IN = "/etc/wireplumber/wireplumber.conf.d/20-rukebox-codecs.conf"


def parse(raw):
    """The codec names a setting holds, in the order above, unknown dropped."""
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    wanted = {str(part).strip().lower() for part in (raw or [])}
    return [codec for codec in CODECS if codec in wanted]


def render(codecs):
    """The drop-in's text for a codec list."""
    listed = codecs or ["sbc"]
    return (
        "# Generated from bt_audio_codecs in /etc/rukebox/rukebox.yaml: the\n"
        "# Bluetooth codecs the Pi offers the speaker. Never edit by hand - the\n"
        "# setting is in the web interface, Audio output > Audio diagnostic.\n"
        "monitor.bluez.properties = {\n"
        "  bluez5.enable-sbc-xq = %s\n"
        "  bluez5.codecs = [ %s ]\n"
        "}\n" % ("true" if "sbc_xq" in listed else "false", " ".join(listed))
    )


def write(path=None, codecs=None):
    """Writes the drop-in for the current setting; True when it changed."""
    if codecs is None:
        from config_and_scan import load_config

        codecs = parse(load_config().get("BT_AUDIO_CODECS"))
    text = render(codecs)
    path = path or DROP_IN
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    log.info("Bluetooth codecs offered: %s", ", ".join(codecs or ["sbc"]))
    return True


def restart_wireplumber(env=None, timeout=20):
    """Applies the change: the codecs are read when the monitor starts."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", "wireplumber"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("Could not restart WirePlumber", exc_info=True)
        return False
    if result.returncode != 0:
        log.warning("WirePlumber did not restart: %s", (result.stderr or "").strip())
        return False
    return True


def main(argv):
    """Usage: bt_codec.py write"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(argv) >= 1 and argv[0] == "write":
        changed = write()
        print("written" if changed else "already up to date")
        return 0
    print(main.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Interactive check of what only a real speaker can prove, run over SSH."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import announcements  # noqa: E402
import audio_diag  # noqa: E402
import bt_link  # noqa: E402
import config_and_scan  # noqa: E402
import config_bundle  # noqa: E402
import config_file  # noqa: E402
import config_schema  # noqa: E402
import control_client  # noqa: E402
import schedules  # noqa: E402

OUT_DIR = os.path.expanduser("~/rukebox-tests")
RELOADS = ("reload_config", "reload_schedules", "reload_announcements", "reload_lists", "reload_hidden")
TEST_NAME = "Hardware test (temporary)"
TEST_VOLUME = 25
SCHEDULE_VOLUME = 30

cfg = config_and_scan.load_config(env_overrides=False)
SOCK = cfg["CONTROL_SOCKET"]
ENV = audio_diag.session_env()


class Quit(Exception):
    pass


def say(text=""):
    print(text, flush=True)


def control(cmd, **kwargs):
    answer = control_client.send_control_command(SOCK, cmd, timeout=20, **kwargs)
    return answer if isinstance(answer, dict) else {"ok": False}


def status():
    answer = control("get_status")
    return answer.get("data") or {} if answer.get("ok") else {}


TOUCHED = {}


def set_settings(values):
    current = config_file.read_values()
    for key in values:
        TOUCHED.setdefault(key, current.get(key, ""))
    config_file.write_values({k: str(v).lower() if isinstance(v, bool) else str(v)
                              for k, v in values.items()})
    control("reload_config")
    time.sleep(1)


def put_back_settings():
    """What one test changed is put back before the next one starts."""
    if TOUCHED:
        config_file.write_values(dict(TOUCHED))
        TOUCHED.clear()
        control("reload_config")
        time.sleep(1)


def sink_volume():
    value = audio_diag.default_sink_volume(ENV)
    return None if value is None else round(value * 100)


def speaker():
    try:
        return bt_link.locate(cfg.get("SPEAKER_MAC", ""), cfg.get("SPEAKER_BT_ADAPTER", "") or "")
    except Exception:  # noqa: BLE001
        return {}


def wait_for(check, seconds, step=1.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(step)
    return None


def read_key(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        raise Quit()


def ask_run(title, what):
    say()
    say("=" * 70)
    say(title)
    say("-" * 70)
    say(what)
    while True:
        key = read_key("Enter = run, S = skip, Q = quit: ")
        if key in ("", "y"):
            return True
        if key == "s":
            return False
        if key == "q":
            raise Quit()


def ask_verdict(question):
    while True:
        key = read_key(question + " [y = yes, n = no, u = unsure]: ")
        if key in ("y", "n", "u"):
            return {"y": "passed", "n": "failed", "u": "inconclusive"}[key]
        if key == "q":
            raise Quit()


def pause_prompt(text):
    read_key(text + " (Enter when done) ")


def ensure_music():
    st = status()
    if st.get("mode") in ("idle", "stopped"):
        control("start_music")
        time.sleep(4)
    elif st.get("paused"):
        control("toggle_pause")
        time.sleep(2)
    return status().get("mode") == "music"


# ---------------------------------------------------------------- the tests

def t_connection():
    link = speaker()
    sink, desc = audio_diag.default_sink(ENV)
    say("Speaker: %s, controller %s" % (
        "connected" if link.get("connected") else "NOT connected", link.get("controller") or "-"))
    say("PipeWire output: %s" % (desc or sink or "none"))
    ok = bool(link.get("connected")) and bool(sink and sink.startswith("bluez"))
    return ("passed" if ok else "failed"), "connected=%s sink=%s" % (link.get("connected"), sink)


def t_sound():
    if not ensure_music():
        return "failed", "the music did not start"
    return ask_verdict("Do you hear the music on the speaker?"), ""


def t_volume():
    ensure_music()
    before = status().get("volume") or 50
    lower = max(5, int(before) - 12)
    say("Volume %s -> %s for 4 s, then back." % (before, lower))
    control("set_volume", value=lower)
    time.sleep(4)
    control("set_volume", value=before)
    time.sleep(2)
    return ask_verdict("Did you hear the sound go down, then up again?"), ""


def t_link():
    ensure_music()
    set_settings({"SPEAKER_VOLUME_LINK": True, "SPEAKER_VOLUME_LOCK": False})

    def levels():
        return status().get("volume"), sink_volume()

    def agree():
        shown, sink = levels()
        return shown is not None and sink is not None and abs(round(shown) - sink) <= 3

    # The speaker answers its own volume a moment after it is set.
    same = bool(wait_for(agree, 10))
    shown, sink = levels()
    say("Interface: %s, speaker: %s" % (shown, sink))
    pause_prompt("Press the speaker's volume + or - button once or twice.")
    moved = wait_for(lambda: abs((status().get("volume") or 0) - (shown or 0)) >= 1, 10)
    after = status().get("volume")
    say("The interface now shows %s." % after)
    ok = same and moved
    return ("passed" if ok else "failed"), "same level=%s, followed=%s" % (same, bool(moved))


def t_handover():
    set_settings({"SPEAKER_VOLUME_LINK": True, "SPEAKER_VOLUME_LOCK": False})
    ensure_music()
    shown = status().get("volume")
    pause_prompt("Switch the speaker off.")
    wait_for(lambda: not speaker().get("connected"), 60, 3)
    pause_prompt("Switch it back on now.")
    say("Waiting for it to come back (up to 2 min)...")
    if not wait_for(lambda: speaker().get("connected"), 120, 3):
        return "failed", "no reconnection within 2 min"
    ensure_music()
    time.sleep(10)
    sink = sink_volume()
    say("Interface: %s, speaker: %s" % (shown, sink))
    same = sink is not None and shown is not None and abs(round(shown) - sink) <= 2
    heard = ask_verdict("Did the sound come back at the same level as before, without touching the speaker?")
    return (heard if same else "failed"), "interface=%s speaker=%s" % (shown, sink)


def t_lock():
    ensure_music()
    # Linked, as on this radio: the speaker is held at the interface's volume.
    set_settings({"SPEAKER_VOLUME_LINK": True, "SPEAKER_VOLUME_LOCK": True})
    time.sleep(5)
    held = sink_volume()
    say("Level held: %s" % held)
    pause_prompt("Press the speaker's volume + several times.")
    back = wait_for(lambda: held is not None and sink_volume() is not None
                    and abs(sink_volume() - held) <= 2, 8)
    time.sleep(1)
    now = sink_volume()
    say("Level now: %s" % now)
    heard = ask_verdict("Did the sound go back to its level within 2-3 seconds?")
    return (heard if back else "failed"), "held=%s now=%s" % (held, now)


def _button_lines(since):
    out = subprocess.run(["journalctl", "-u", "rukebox-speaker-buttons", "--since", since,
                          "-o", "cat", "--no-pager"], capture_output=True, text=True).stdout
    return re.findall(r"Speaker button: (\w+)", out)


def t_buttons():
    ensure_music()
    since = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pause_prompt("Press the speaker's Play/Pause, then Next.")
    seen = wait_for(lambda: _button_lines(since), 15)
    say("Gestures received: %s" % (", ".join(seen) if seen else "none"))
    if not seen:
        return "failed", "no gesture in the log"
    return ask_verdict("Did the radio do the action set under \"Speaker buttons\","
                       " music included?"), \
        ", ".join(seen)


def t_loss():
    set_settings({"SPEAKER_LOSS_PAUSE": True})
    ensure_music()
    pause_prompt("Switch the speaker off while the music plays.")
    paused = wait_for(lambda: status().get("paused"), 60, 2)
    say("Music paused: %s" % bool(paused))
    pause_prompt("Switch the speaker back on.")
    resumed = wait_for(lambda: speaker().get("connected") and not status().get("paused"), 120, 3)
    say("Resumed: %s" % bool(resumed))
    ok = bool(paused) and bool(resumed)
    return ("passed" if ok else "failed"), "paused=%s resumed=%s" % (bool(paused), bool(resumed))


def t_chime():
    keys = ["AP_CONNECT_SOUND"] + [k for k in config_schema.SYSTEM_SOUNDS if k != "AP_CONNECT_SOUND"]
    key = next((k for k in keys if cfg.get(k) and os.path.exists(cfg[k])), None)
    if not key:
        return "inconclusive", "no system sound on the Pi"
    ensure_music()
    say("Sound played: %s" % os.path.basename(cfg[key]))
    say("First alone, music paused...")
    control("toggle_pause")
    time.sleep(2)
    answer = control("test_system_sound", key=key)
    time.sleep(3)
    control("toggle_pause")
    if not answer.get("ok"):
        return "failed", answer.get("error", "")
    alone = ask_verdict("Did you hear it alone?")
    time.sleep(2)
    say("Then over the music...")
    control("test_system_sound", key=key)
    time.sleep(3)
    over = ask_verdict("And over the music, without a cut?")
    if alone == "failed":
        return "failed", "not heard even alone: too quiet"
    return over, "alone=%s, over the music=%s" % (alone, over)


def _next_minute(lead=60):
    at = datetime.now() + timedelta(seconds=lead)
    return (at + timedelta(minutes=1)).replace(second=0, microsecond=0)


def t_announcement(created):
    ensure_music()
    at = _next_minute()
    item = announcements.add(cfg["ANNOUNCEMENTS_FILE"], {
        "name": TEST_NAME, "folder": cfg["MEME_DIR"], "trigger": "time",
        "hour": at.hour, "minute": at.minute, "after_action": "pause"})
    created["announcement"] = item["id"]
    control("reload_announcements")
    say("Announcement at %s, followed by a pause. Wait..." % at.strftime("%H:%M"))
    played = wait_for(lambda: str(status().get("mode", "")).startswith("custom:"),
                      (at - datetime.now()).total_seconds() + 45)
    paused = played and wait_for(lambda: status().get("mode") == "music" and status().get("paused"), 120)
    say("Played: %s, paused after: %s" % (bool(played), bool(paused)))
    ok = bool(played) and bool(paused)
    return ("passed" if ok else "failed"), "played=%s paused=%s" % (bool(played), bool(paused))


def t_schedule(created):
    ensure_music()
    control("standby")
    time.sleep(3)
    start = _next_minute(20)
    stop = start + timedelta(minutes=2)
    item = schedules.add(cfg["SCHEDULES_FILE"], {
        "name": TEST_NAME, "start": start.strftime("%H:%M"), "stop": stop.strftime("%H:%M"),
        "stop_action": "pause", "settings": {"BASE_VOLUME": str(SCHEDULE_VOLUME)}})
    created["schedule"] = item["id"]
    control("reload_schedules")
    say("Schedule %s -> %s, volume %d. About 3 minutes..." % (
        start.strftime("%H:%M"), stop.strftime("%H:%M"), SCHEDULE_VOLUME))
    began = wait_for(lambda: status().get("mode") == "music" and not status().get("paused"),
                     (start - datetime.now()).total_seconds() + 40)
    volume = status().get("volume")
    say("Started: %s, volume %s" % (bool(began), volume))
    ended = began and wait_for(lambda: status().get("paused") or status().get("mode") != "music",
                               (stop - datetime.now()).total_seconds() + 40)
    say("Stopped: %s" % bool(ended))
    ok = bool(began) and bool(ended) and volume is not None and round(volume) == SCHEDULE_VOLUME
    return ("passed" if ok else "failed"), "start=%s volume=%s end=%s" % (bool(began), volume, bool(ended))


def t_resume():
    set_settings({"MUSIC_RESUME_MODE": "same_position"})
    ensure_music()
    time.sleep(15)
    st = status()
    track, position = st.get("current_track"), st.get("position") or 0
    say("Playing: %s at %d s. Standby..." % (track, position))
    control("standby")
    time.sleep(5)
    control("start_music")
    time.sleep(6)
    st = status()
    same = st.get("current_track") == track
    say("Resumed: %s at %d s" % (st.get("current_track"), st.get("position") or 0))
    near = same and abs((st.get("position") or 0) - position) < 15
    heard = ask_verdict("Did the song pick up where it was (a few seconds earlier)?")
    return (heard if near else "failed"), "same=%s pos %s -> %s" % (same, position, st.get("position"))


def t_mute():
    ensure_music()
    control("set_mute", on=True)
    time.sleep(4)
    control("set_mute", on=False)
    time.sleep(1)
    return ask_verdict("Did the sound cut out for 4 s, then come back?"), ""


def t_diag():
    report = audio_diag.report(cfg)
    say(report)
    return "passed", "report saved"


TESTS = [
    ("connection", "1. Speaker connection",
     "Checks the speaker is connected and the sound goes to it.", t_connection),
    ("sound", "2. Sound comes out", "Starts the music if needed.", t_sound),
    ("volume", "3. Volume from the interface", "Lowers the volume for 4 s, then puts it back.", t_volume),
    ("link", "4. Volume linked to the speaker",
     "Turns on \"speaker volume = the radio's\"; you will press its buttons.", t_link),
    ("handover", "5. Volume handed over on connection",
     "You will switch the speaker off, then on again (about 2 min).", t_handover),
    ("lock", "6. Speaker volume lock",
     "Volume linked and locked: its volume buttons must no longer change anything.", t_lock),
    ("buttons", "7. Speaker buttons (AVRCP)",
     "You will press the speaker's Play/Pause and Next.", t_buttons),
    ("loss", "8. Speaker lost, then found again",
     "You will switch it off (the music must pause), then on again.", t_loss),
    ("chime", "9. System sound over the music",
     "Plays a system sound alone, then over the music.", t_chime),
    ("announcement", "10. Scheduled announcement followed by a pause",
     "Automatic, nothing to do: a temporary announcement is scheduled for the next"
     " minute, then the script checks it plays and the music pauses (1 to 2 min).", None),
    ("schedule", "11. A whole schedule",
     "Automatic, nothing to do: standby, then a temporary schedule starts the music"
     " at volume 30 and stops it 2 min later (about 3 min).", None),
    ("resume", "12. Resume at the same position", "Standby, then the music starts again.", t_resume),
    ("mute", "13. Mute", "Cuts the sound for 4 s.", t_mute),
    ("diag", "14. Audio diagnostic", "Measures the link's throughput (6 s) and shows the report.", t_diag),
]


# ------------------------------------------------------ snapshot and restore

def take_snapshot():
    st = status()
    active = st.get("active_list")
    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "bundle": config_bundle.export_bundle(cfg),
        "runtime": {
            "mode": st.get("mode"), "paused": st.get("paused"), "volume": st.get("volume"),
            "muted": st.get("muted"), "loop_mode": st.get("loop_mode"),
            "active_list": active.get("id") if isinstance(active, dict) else None,
            "sink_volume": sink_volume(),
        },
    }


def restore(snapshot, created=None):
    say()
    say("Putting back the settings from before the tests...")
    for kind, item_id in (created or {}).items():
        try:
            if kind == "announcement":
                announcements.delete(cfg["ANNOUNCEMENTS_FILE"], item_id)
            else:
                schedules.delete(cfg["SCHEDULES_FILE"], item_id)
        except (KeyError, ValueError, OSError):
            pass
    config_bundle.import_bundle(snapshot["bundle"], cfg)
    for cmd in RELOADS:
        control(cmd)
    time.sleep(1)

    run = snapshot.get("runtime") or {}
    st = status()
    if run.get("mode") in ("idle", "stopped") and st.get("mode") == "music":
        control("standby")
    elif run.get("mode") == "music":
        if st.get("mode") in ("idle", "stopped"):
            control("start_music")
            time.sleep(3)
            st = status()
        if bool(st.get("paused")) != bool(run.get("paused")):
            control("toggle_pause")
    if run.get("volume") is not None:
        control("set_volume", value=run["volume"])
    control("set_mute", on=bool(run.get("muted")))
    if run.get("loop_mode"):
        control("set_loop", mode=run["loop_mode"])
    if run.get("active_list") != ((status().get("active_list") or {}).get("id")):
        control("set_active_list", id=run.get("active_list"))
    if run.get("sink_volume") is not None and not cfg.get("SPEAKER_VOLUME_LINK"):
        audio_diag.set_default_sink_volume(run["sink_volume"], ENV)
    say("Settings put back.")


def _stop(*_):
    raise Quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", metavar="FILE", help="puts back a snapshot this script took")
    args = parser.parse_args()

    if args.restore:
        with open(args.restore, encoding="utf-8") as f:
            restore(json.load(f))
        return 0

    if not status():
        say("The rukebox-daemon service does not answer: nothing was changed.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_path = os.path.join(OUT_DIR, "before-%s.json" % stamp)
    snapshot = take_snapshot()
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    say("Rukebox tests with the speaker")
    say("Settings saved in %s" % snap_path)
    say("They are put back at the end, even if you stop with Q or Ctrl-C.")
    say("If the connection drops: python3 %s --restore %s" % (os.path.abspath(__file__), snap_path))

    # The base too: a standby or a start goes back to it.
    config_file.write_values({"BASE_VOLUME": str(TEST_VOLUME)})
    control("reload_config")
    control("set_volume", value=TEST_VOLUME)
    say("Test volume: %d %%." % TEST_VOLUME)

    signal.signal(signal.SIGHUP, _stop)
    signal.signal(signal.SIGTERM, _stop)

    results, created = [], {}
    try:
        for key, title, what, fn in TESTS:
            if not ask_run(title, what):
                results.append((title, "skipped", ""))
                continue
            try:
                if key == "announcement":
                    verdict, detail = t_announcement(created)
                elif key == "schedule":
                    verdict, detail = t_schedule(created)
                else:
                    verdict, detail = fn()
            except (Quit, KeyboardInterrupt):
                results.append((title, "interrupted", ""))
                raise
            except Exception as e:  # noqa: BLE001
                verdict, detail = "error", "%s: %s" % (type(e).__name__, e)
            say(">> %s %s" % (verdict.upper(), detail))
            results.append((title, verdict, detail))
            put_back_settings()
    except (Quit, KeyboardInterrupt):
        say()
        say("Stop requested.")
    finally:
        TOUCHED.clear()
        try:
            restore(snapshot, created)
        except Exception as e:  # noqa: BLE001
            say("Putting back failed (%s). Run again: python3 %s --restore %s"
                % (e, os.path.abspath(__file__), snap_path))
        report_path = os.path.join(OUT_DIR, "results-%s.txt" % stamp)
        lines = ["%-45s %-11s %s" % (t, v, d) for t, v, d in results]
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say()
        say("Summary:")
        for line in lines:
            say("  " + line)
        say("Saved in %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

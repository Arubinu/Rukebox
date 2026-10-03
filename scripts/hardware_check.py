#!/usr/bin/env python3
"""Interactive check of what only a real speaker can prove, run over SSH.

Every setting and runtime state it touches is saved first and put back at the
end, on Ctrl-C and on a dropped connection; `--restore FILE` puts back a
snapshot by hand if the script itself was killed."""
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
import control_client  # noqa: E402
import schedules  # noqa: E402

OUT_DIR = os.path.expanduser("~/rukebox-tests")
RELOADS = ("reload_config", "reload_schedules", "reload_announcements", "reload_lists", "reload_hidden")
TEST_NAME = "Test matériel (temporaire)"

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


def set_settings(values):
    config_file.write_values({k: str(v).lower() if isinstance(v, bool) else str(v)
                              for k, v in values.items()})
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
        key = read_key("Entrée = lancer, P = passer, Q = arrêter : ")
        if key in ("", "o"):
            return True
        if key == "p":
            return False
        if key == "q":
            raise Quit()


def ask_verdict(question):
    while True:
        key = read_key(question + " [o = oui, n = non, p = je ne sais pas] : ")
        if key in ("o", "n", "p"):
            return {"o": "réussi", "n": "échoué", "p": "non conclu"}[key]
        if key == "q":
            raise Quit()


def pause_prompt(text):
    read_key(text + " (Entrée quand c'est fait) ")


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
    say("Enceinte : %s, contrôleur %s" % (
        "connectée" if link.get("connected") else "NON connectée", link.get("controller") or "-"))
    say("Sortie PipeWire : %s" % (desc or sink or "aucune"))
    ok = bool(link.get("connected")) and bool(sink and sink.startswith("bluez"))
    return ("réussi" if ok else "échoué"), "connectée=%s sink=%s" % (link.get("connected"), sink)


def t_sound():
    if not ensure_music():
        return "échoué", "la musique n'a pas démarré"
    return ask_verdict("Entends-tu la musique sur l'enceinte ?"), ""


def t_volume():
    ensure_music()
    before = status().get("volume") or 50
    lower = max(5, int(before) - 20)
    say("Volume %s -> %s pendant 4 s, puis retour." % (before, lower))
    control("set_volume", value=lower)
    time.sleep(4)
    control("set_volume", value=before)
    time.sleep(2)
    return ask_verdict("As-tu entendu le son baisser puis remonter ?"), ""


def t_link():
    ensure_music()
    set_settings({"SPEAKER_VOLUME_LINK": True, "SPEAKER_VOLUME_LOCK": False})
    time.sleep(5)
    shown, sink = status().get("volume"), sink_volume()
    say("Interface : %s, enceinte : %s" % (shown, sink))
    same = shown is not None and sink is not None and abs(round(shown) - sink) <= 2
    pause_prompt("Appuie une ou deux fois sur le bouton volume + ou - de l'enceinte.")
    moved = wait_for(lambda: abs((status().get("volume") or 0) - (shown or 0)) >= 1, 10)
    after = status().get("volume")
    say("L'interface affiche maintenant %s." % after)
    ok = same and moved
    return ("réussi" if ok else "échoué"), "même niveau=%s, suivi=%s" % (same, bool(moved))


def t_handover():
    set_settings({"SPEAKER_VOLUME_LINK": True, "SPEAKER_VOLUME_LOCK": False})
    ensure_music()
    shown = status().get("volume")
    pause_prompt("Éteins l'enceinte.")
    wait_for(lambda: not speaker().get("connected"), 60, 3)
    pause_prompt("Rallume-la maintenant.")
    say("J'attends qu'elle revienne (jusqu'à 2 min)...")
    if not wait_for(lambda: speaker().get("connected"), 120, 3):
        return "échoué", "pas de reconnexion en 2 min"
    ensure_music()
    time.sleep(10)
    sink = sink_volume()
    say("Interface : %s, enceinte : %s" % (shown, sink))
    same = sink is not None and shown is not None and abs(round(shown) - sink) <= 2
    heard = ask_verdict("Le son est-il revenu au même niveau qu'avant, sans toucher l'enceinte ?")
    return (heard if same else "échoué"), "interface=%s enceinte=%s" % (shown, sink)


def t_lock():
    ensure_music()
    set_settings({"SPEAKER_VOLUME_LINK": False, "SPEAKER_VOLUME_LOCK": True})
    time.sleep(4)
    held = sink_volume()
    say("Niveau tenu : %s" % held)
    pause_prompt("Appuie plusieurs fois sur le volume + de l'enceinte.")
    back = wait_for(lambda: held is not None and sink_volume() is not None
                    and abs(sink_volume() - held) <= 2, 8)
    time.sleep(1)
    now = sink_volume()
    say("Niveau maintenant : %s" % now)
    heard = ask_verdict("Le son est-il revenu à son niveau en 2-3 secondes ?")
    return (heard if back else "échoué"), "tenu=%s maintenant=%s" % (held, now)


def _button_lines(since):
    out = subprocess.run(["journalctl", "-u", "rukebox-speaker-buttons", "--since", since,
                          "-o", "cat", "--no-pager"], capture_output=True, text=True).stdout
    return re.findall(r"Speaker button: (\w+)", out)


def t_buttons():
    ensure_music()
    since = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pause_prompt("Appuie sur Lecture/Pause, puis sur Suivant de l'enceinte.")
    seen = wait_for(lambda: _button_lines(since), 15)
    say("Gestes reçus : %s" % (", ".join(seen) if seen else "aucun"))
    if not seen:
        return "échoué", "aucun geste dans le journal"
    return ask_verdict("La radio a-t-elle réagi comme réglé dans « Boutons de l'enceinte » ?"), \
        ", ".join(seen)


def t_loss():
    set_settings({"SPEAKER_LOSS_PAUSE": True})
    ensure_music()
    pause_prompt("Éteins l'enceinte pendant que la musique joue.")
    paused = wait_for(lambda: status().get("paused"), 60, 2)
    say("Musique en pause : %s" % bool(paused))
    pause_prompt("Rallume l'enceinte.")
    resumed = wait_for(lambda: speaker().get("connected") and not status().get("paused"), 120, 3)
    say("Reprise : %s" % bool(resumed))
    ok = bool(paused) and bool(resumed)
    return ("réussi" if ok else "échoué"), "pause=%s reprise=%s" % (bool(paused), bool(resumed))


def t_chime():
    ensure_music()
    answer = control("test_system_sound", key="AP_CONNECT_SOUND")
    if not answer.get("ok"):
        return "échoué", answer.get("error", "")
    time.sleep(2)
    return ask_verdict("As-tu entendu le petit carillon PAR-DESSUS la musique, sans coupure ?"), ""


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
    say("Annonce à %s, suivie d'une pause. Attends..." % at.strftime("%H:%M"))
    played = wait_for(lambda: str(status().get("mode", "")).startswith("custom:"),
                      (at - datetime.now()).total_seconds() + 45)
    paused = played and wait_for(lambda: status().get("mode") == "music" and status().get("paused"), 120)
    say("Jouée : %s, pause ensuite : %s" % (bool(played), bool(paused)))
    ok = bool(played) and bool(paused)
    return ("réussi" if ok else "échoué"), "jouée=%s pause=%s" % (bool(played), bool(paused))


def t_schedule(created):
    ensure_music()
    control("standby")
    time.sleep(3)
    start = _next_minute(20)
    stop = start + timedelta(minutes=2)
    item = schedules.add(cfg["SCHEDULES_FILE"], {
        "name": TEST_NAME, "start": start.strftime("%H:%M"), "stop": stop.strftime("%H:%M"),
        "stop_action": "pause", "settings": {"BASE_VOLUME": "40"}})
    created["schedule"] = item["id"]
    control("reload_schedules")
    say("Planning %s -> %s, volume 40. Environ 3 minutes..." % (
        start.strftime("%H:%M"), stop.strftime("%H:%M")))
    began = wait_for(lambda: status().get("mode") == "music" and not status().get("paused"),
                     (start - datetime.now()).total_seconds() + 40)
    volume = status().get("volume")
    say("Démarrée : %s, volume %s" % (bool(began), volume))
    ended = began and wait_for(lambda: status().get("paused") or status().get("mode") != "music",
                               (stop - datetime.now()).total_seconds() + 40)
    say("Arrêtée : %s" % bool(ended))
    ok = bool(began) and bool(ended) and volume is not None and round(volume) == 40
    return ("réussi" if ok else "échoué"), "début=%s volume=%s fin=%s" % (bool(began), volume, bool(ended))


def t_resume():
    set_settings({"MUSIC_RESUME_MODE": "same_position"})
    ensure_music()
    time.sleep(15)
    st = status()
    track, position = st.get("current_track"), st.get("position") or 0
    say("En cours : %s à %d s. Veille..." % (track, position))
    control("standby")
    time.sleep(5)
    control("start_music")
    time.sleep(6)
    st = status()
    same = st.get("current_track") == track
    say("Repris : %s à %d s" % (st.get("current_track"), st.get("position") or 0))
    near = same and abs((st.get("position") or 0) - position) < 15
    heard = ask_verdict("La chanson a-t-elle repris là où elle était (quelques secondes avant) ?")
    return (heard if near else "échoué"), "même=%s pos %s -> %s" % (same, position, st.get("position"))


def t_mute():
    ensure_music()
    control("set_mute", on=True)
    time.sleep(4)
    control("set_mute", on=False)
    time.sleep(1)
    return ask_verdict("Le son s'est-il coupé 4 s puis est-il revenu ?"), ""


def t_diag():
    report = audio_diag.report(cfg)
    say(report)
    return "réussi", "rapport enregistré"


TESTS = [
    ("connection", "1. Connexion de l'enceinte",
     "Vérifie que l'enceinte est connectée et que le son part vers elle.", t_connection),
    ("sound", "2. Le son sort", "Lance la musique si besoin.", t_sound),
    ("volume", "3. Volume depuis l'interface", "Baisse le volume 4 s puis le remet.", t_volume),
    ("link", "4. Volume lié à l'enceinte",
     "Active « volume de l'enceinte = celui de la radio » ; il faudra appuyer sur ses boutons.", t_link),
    ("handover", "5. Remise du volume à la connexion",
     "Il faudra éteindre puis rallumer l'enceinte (environ 2 min).", t_handover),
    ("lock", "6. Verrou du volume de l'enceinte",
     "Ses boutons de volume ne doivent plus rien changer.", t_lock),
    ("buttons", "7. Boutons de l'enceinte (AVRCP)",
     "Il faudra appuyer sur Lecture/Pause et Suivant de l'enceinte.", t_buttons),
    ("loss", "8. Enceinte perdue puis retrouvée",
     "Il faudra l'éteindre (la musique doit se mettre en pause) puis la rallumer.", t_loss),
    ("chime", "9. Son système par-dessus la musique",
     "Joue le carillon de connexion au Wi-Fi pendant la musique.", t_chime),
    ("announcement", "10. Annonce programmée suivie d'une pause",
     "Crée une annonce temporaire dans 1-2 min (dossier des sons des boutons).", None),
    ("schedule", "11. Planning complet",
     "Met en veille, puis un planning temporaire démarre (volume 40) et s'arrête 2 min après.", None),
    ("resume", "12. Reprise à la même position", "Veille puis redémarrage de la musique.", t_resume),
    ("mute", "13. Muet", "Coupe le son 4 s.", t_mute),
    ("diag", "14. Diagnostic audio", "Mesure le débit du lien (6 s) et affiche le rapport.", t_diag),
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
    say("Remise des réglages d'avant les tests...")
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
    say("Réglages remis.")


def _stop(*_):
    raise Quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", metavar="FICHIER", help="remet un instantané pris par ce script")
    args = parser.parse_args()

    if args.restore:
        with open(args.restore, encoding="utf-8") as f:
            restore(json.load(f))
        return 0

    if not status():
        say("Le service rukebox-daemon ne répond pas : rien n'a été changé.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_path = os.path.join(OUT_DIR, "avant-%s.json" % stamp)
    snapshot = take_snapshot()
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    say("Tests de la Rukebox avec l'enceinte")
    say("Réglages sauvegardés dans %s" % snap_path)
    say("Ils sont remis à la fin, même si tu arrêtes avec Q ou Ctrl-C.")
    say("Si la connexion coupe : python3 %s --restore %s" % (os.path.abspath(__file__), snap_path))

    signal.signal(signal.SIGHUP, _stop)
    signal.signal(signal.SIGTERM, _stop)

    results, created = [], {}
    try:
        for key, title, what, fn in TESTS:
            if not ask_run(title, what):
                results.append((title, "passé", ""))
                continue
            try:
                if key == "announcement":
                    verdict, detail = t_announcement(created)
                elif key == "schedule":
                    verdict, detail = t_schedule(created)
                else:
                    verdict, detail = fn()
            except (Quit, KeyboardInterrupt):
                results.append((title, "interrompu", ""))
                raise
            except Exception as e:  # noqa: BLE001
                verdict, detail = "erreur", "%s: %s" % (type(e).__name__, e)
            say(">> %s %s" % (verdict.upper(), detail))
            results.append((title, verdict, detail))
    except (Quit, KeyboardInterrupt):
        say()
        say("Arrêt demandé.")
    finally:
        try:
            restore(snapshot, created)
        except Exception as e:  # noqa: BLE001
            say("La remise a échoué (%s). Relance : python3 %s --restore %s"
                % (e, os.path.abspath(__file__), snap_path))
        report_path = os.path.join(OUT_DIR, "resultats-%s.txt" % stamp)
        lines = ["%-45s %-11s %s" % (t, v, d) for t, v, d in results]
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say()
        say("Résumé :")
        for line in lines:
            say("  " + line)
        say("Enregistré dans %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

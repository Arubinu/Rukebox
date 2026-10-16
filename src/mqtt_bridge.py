#!/usr/bin/env python3
"""Home Assistant over MQTT: the radio's state published, its entities discovered, their commands run."""

import json
import logging
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_file  # noqa: E402
import json_file  # noqa: E402
import mqtt_client  # noqa: E402
import machine as platform_mod  # noqa: E402
from config_and_scan import load_config  # noqa: E402
from control_client import send_control_command  # noqa: E402

log = logging.getLogger("mqtt")

IDLE_SEC = 10
RETRY_SEC = (5, 10, 30, 60)
SETTINGS = ("MQTT_ENABLED", "MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_TLS",
            "MQTT_TOPIC", "MQTT_DISCOVERY_PREFIX", "UPNP_NAME", "SPEECH_LANGUAGE")
# What Home Assistant may ask, as the daemon's own commands.
BUTTONS = ("toggle_pause", "next_track", "previous_track", "start_music", "skip_sound", "standby")

NAMES = {
    "title": {"en": "Title", "fr": "Titre", "de": "Titel", "es": "Título", "it": "Titolo", "nl": "Titel"},
    "artist": {"en": "Artist", "fr": "Artiste", "de": "Interpret", "es": "Artista", "it": "Artista", "nl": "Artiest"},
    "mode": {"en": "State", "fr": "État", "de": "Zustand", "es": "Estado", "it": "Stato", "nl": "Toestand"},
    "playing": {"en": "Playing", "fr": "En lecture", "de": "Spielt", "es": "Reproduciendo", "it": "In riproduzione",
                "nl": "Speelt"},
    "speaker": {"en": "Speaker", "fr": "Enceinte", "de": "Lautsprecher", "es": "Altavoz", "it": "Altoparlante",
                "nl": "Luidspreker"},
    "volume": {"en": "Volume", "fr": "Volume", "de": "Lautstärke", "es": "Volumen", "it": "Volume", "nl": "Volume"},
    "mute": {"en": "Mute", "fr": "Muet", "de": "Stumm", "es": "Silencio", "it": "Muto", "nl": "Dempen"},
    "toggle_pause": {"en": "Play / pause", "fr": "Lecture / pause", "de": "Wiedergabe / Pause",
                     "es": "Reproducir / pausa", "it": "Riproduci / pausa", "nl": "Afspelen / pauze"},
    "next_track": {"en": "Next", "fr": "Suivant", "de": "Weiter", "es": "Siguiente", "it": "Successivo",
                   "nl": "Volgende"},
    "previous_track": {"en": "Previous", "fr": "Précédent", "de": "Zurück", "es": "Anterior", "it": "Precedente",
                       "nl": "Vorige"},
    "start_music": {"en": "Start the music", "fr": "Lancer la musique", "de": "Musik starten",
                    "es": "Iniciar la música", "it": "Avvia la musica", "nl": "Muziek starten"},
    "skip_sound": {"en": "Skip the announcement", "fr": "Passer l'annonce", "de": "Ansage überspringen",
                   "es": "Saltar el anuncio", "it": "Salta l'annuncio", "nl": "Aankondiging overslaan"},
    "standby": {"en": "Standby", "fr": "Veille", "de": "Standby", "es": "En espera", "it": "Standby",
                "nl": "Stand-by"},
}
ICONS = {"title": "mdi:music", "artist": "mdi:account-music", "mode": "mdi:radio", "toggle_pause": "mdi:play-pause",
         "next_track": "mdi:skip-next", "previous_track": "mdi:skip-previous", "start_music": "mdi:play",
         "skip_sound": "mdi:bullhorn-outline", "standby": "mdi:power-sleep", "volume": "mdi:volume-high",
         "mute": "mdi:volume-off"}


def node_id():
    """This radio's id in topics and in Home Assistant: its host name, made topic-safe."""
    return "rukebox_" + (re.sub(r"[^a-z0-9_]+", "_", socket.gethostname().lower()).strip("_") or "radio")


def status_file(cfg):
    return os.path.join(cfg["STATE_DIR"], "mqtt_status.json")


_written = {}


def write_status(cfg, state, error=None):
    """What the interface shows: connected, or why not. Only a change is written (an SD card wears)."""
    path = status_file(cfg)
    if _written.get(path) == (state, error) and os.path.exists(path):
        return
    try:
        json_file.write(path, {"state": state, "error": error, "at": int(time.time())})
        _written[path] = (state, error)
    except OSError:
        log.debug("Could not write the MQTT status")


def state_of(status):
    """The radio's state as one JSON document, from the daemon's status."""
    mode = status.get("mode") or "idle"
    return {
        "mode": mode,
        "title": status.get("track_title") or "",
        "artist": status.get("track_artist") or "",
        "volume": round(float(status.get("volume") or 0)),
        "muted": bool(status.get("muted")),
        "playing": mode == "music" and not status.get("paused"),
        "speaker": bool(status.get("speaker_connected")),
    }


def discovery(cfg, node, base):
    """{config topic: config document} for every entity of this radio."""
    lang = cfg.get("SPEECH_LANGUAGE") if cfg.get("SPEECH_LANGUAGE") in NAMES["title"] else "en"
    prefix = (cfg.get("MQTT_DISCOVERY_PREFIX") or "homeassistant").strip("/")
    device = {"identifiers": [node], "name": cfg.get("UPNP_NAME") or "Rukebox", "manufacturer": "Rukebox",
              "model": platform_mod.name()}
    common = {"availability_topic": base + "/availability", "device": device}

    def entity(component, key, **extra):
        doc = dict(common, name=NAMES[key][lang], unique_id="%s_%s" % (node, key), object_id="%s_%s" % (node, key))
        if key in ICONS:
            doc["icon"] = ICONS[key]
        doc.update(extra)
        return "%s/%s/%s/%s/config" % (prefix, component, node, key), doc

    state = {"state_topic": base + "/state"}
    items = [
        entity("sensor", "title", value_template="{{ value_json.title }}", **state),
        entity("sensor", "artist", value_template="{{ value_json.artist }}", **state),
        entity("sensor", "mode", value_template="{{ value_json.mode }}", **state),
        entity("binary_sensor", "playing", value_template="{{ 'ON' if value_json.playing else 'OFF' }}",
               device_class="running", **state),
        entity("binary_sensor", "speaker", value_template="{{ 'ON' if value_json.speaker else 'OFF' }}",
               device_class="connectivity", **state),
        entity("number", "volume", command_topic=base + "/set/volume", min=0, max=100, step=1, mode="slider",
               unit_of_measurement="%", value_template="{{ value_json.volume }}", **state),
        entity("switch", "mute", command_topic=base + "/set/mute", payload_on="ON", payload_off="OFF",
               value_template="{{ 'ON' if value_json.muted else 'OFF' }}", **state),
    ]
    items += [entity("button", name, command_topic=base + "/set/" + name, payload_press="PRESS")
              for name in BUTTONS]
    return dict(items)


def command(cfg, name, payload):
    """Runs what Home Assistant asked: the daemon's answer, or None for an unknown command."""
    sock = cfg["CONTROL_SOCKET"]
    if name == "volume":
        try:
            return send_control_command(sock, "set_volume", value=float(payload), source="mqtt")
        except ValueError:
            return {"ok": False, "error": "invalid_value"}
    if name == "mute":
        return send_control_command(sock, "set_mute", on=payload.strip().upper() == "ON", source="mqtt")
    if name in BUTTONS:
        return send_control_command(sock, name, source="mqtt")
    return None


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.node = node_id()
        self.base = "%s/%s" % ((cfg.get("MQTT_TOPIC") or "rukebox").strip("/"), self.node)
        self.client = mqtt_client.Client(
            cfg["MQTT_HOST"], int(cfg.get("MQTT_PORT") or 1883), client_id=self.node,
            username=cfg.get("MQTT_USERNAME") or "", password=cfg.get("MQTT_PASSWORD") or "",
            tls=bool(cfg.get("MQTT_TLS")), will=(self.base + "/availability", "offline", True))
        self.stopped = threading.Event()
        self.published = None

    def publish_state(self):
        result = send_control_command(self.cfg["CONTROL_SOCKET"], "get_status", timeout=5)
        if not result.get("ok"):
            return
        state = state_of(result.get("data") or {})
        if state != self.published:
            self.client.publish(self.base + "/state", json.dumps(state), retain=True)
            self.published = state

    def watch(self):
        """Publishes the state each time the daemon says it changed."""
        version = -1
        while not self.stopped.is_set():
            try:
                self.publish_state()
                # The daemon answers within 20 s whatever happens; the socket waits a little longer.
                answer = send_control_command(self.cfg["CONTROL_SOCKET"], "wait_change", timeout=35,
                                              since=version)
                version = int(((answer.get("data") or {}).get("version")) or version) if answer.get("ok") else version
                if not answer.get("ok"):
                    self.stopped.wait(5)
            except mqtt_client.MqttError:
                self.stopped.set()
            except Exception:  # noqa: BLE001
                log.exception("Could not publish the state")
                self.stopped.wait(5)

    def run(self, still_wanted):
        """Until the link breaks or `still_wanted()` turns false."""
        self.client.connect()
        try:
            self.client.publish(self.base + "/availability", "online", retain=True)
            for topic, doc in discovery(self.cfg, self.node, self.base).items():
                self.client.publish(topic, json.dumps(doc), retain=True)
            self.client.subscribe([self.base + "/set/#"])
            write_status(self.cfg, "connected")
            log.info("Connected to %s:%s as %s", self.cfg["MQTT_HOST"], self.cfg.get("MQTT_PORT"), self.node)
            watcher = threading.Thread(target=self.watch, daemon=True)
            watcher.start()
            while not self.stopped.is_set() and still_wanted():
                for topic, payload in self.client.poll(1.0):
                    name = topic[len(self.base + "/set/"):]
                    if command(self.cfg, name, payload) is None:
                        log.info("Unknown command on %s", topic)
            if not self.stopped.is_set():
                self.client.publish(self.base + "/availability", "offline", retain=True)
        finally:
            self.stopped.set()
            self.client.close()


def wanted(cfg):
    return bool(cfg.get("MQTT_ENABLED")) and bool(str(cfg.get("MQTT_HOST") or "").strip())


def current():
    return {key: value for key, value in load_config(env_overrides=False).items() if key in SETTINGS or key in (
        "CONTROL_SOCKET", "STATE_DIR")}


def _mtime():
    try:
        return os.path.getmtime(config_file.find_yaml_file())
    except OSError:
        return None


def unchanged(cfg):
    """A check for the bridge's loop: false once a setting it uses has changed in the file."""
    seen = [_mtime()]

    def check():
        stamp = _mtime()
        if stamp == seen[0]:
            return True
        seen[0] = stamp
        return current() == cfg
    return check


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] mqtt: %(message)s")
    failures = 0
    while True:
        cfg = current()
        if not wanted(cfg):
            write_status(cfg, "off")
            failures = 0
            time.sleep(IDLE_SEC)
            continue
        bridge = Bridge(cfg)
        try:
            # A changed setting reconnects with it; the file is looked at every second.
            bridge.run(unchanged(cfg))
            failures = 0
        except mqtt_client.MqttError as error:
            write_status(cfg, "error", str(error))
            log.warning("MQTT: %s", error)
            failures += 1
            time.sleep(RETRY_SEC[min(failures, len(RETRY_SEC)) - 1])


if __name__ == "__main__":
    main()

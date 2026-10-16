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
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audio_output  # noqa: E402
import config_file  # noqa: E402
import json_file  # noqa: E402
import mqtt_client  # noqa: E402
import music_lists  # noqa: E402
import machine as platform_mod  # noqa: E402
import tag_editor  # noqa: E402
import track_media  # noqa: E402
from config_and_scan import load_config  # noqa: E402
from control_client import send_control_command  # noqa: E402
from mpv_controller import audio_env  # noqa: E402
from version import read_version_file  # noqa: E402

log = logging.getLogger("mqtt")

IDLE_SEC = 10
RETRY_SEC = (5, 10, 30, 60)
# Outputs and lists change rarely: looked at again at most this often.
CHOICES_SEC = 60
RELEASE_SEC = 6 * 3600
SETTINGS = ("MQTT_ENABLED", "MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_TLS",
            "MQTT_TOPIC", "MQTT_DISCOVERY_PREFIX", "UPNP_NAME", "SPEECH_LANGUAGE", "MUSIC_DIR",
            "COVER_PRIORITY", "AUDIO_OUTPUT", "UPDATE_GITHUB_REPO", "UPDATE_VERSION_FILE")
# What Home Assistant may ask, as the daemon's own commands.
BUTTONS = ("toggle_pause", "next_track", "previous_track", "start_music", "skip_sound", "standby")
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

NAMES = {
    "title": {"en": "Title", "fr": "Titre", "de": "Titel", "es": "Título", "it": "Titolo", "nl": "Titel"},
    "artist": {"en": "Artist", "fr": "Artiste", "de": "Interpret", "es": "Artista", "it": "Artista", "nl": "Artiest"},
    "album": {"en": "Album", "fr": "Album", "de": "Album", "es": "Álbum", "it": "Album", "nl": "Album"},
    "duration": {"en": "Duration", "fr": "Durée", "de": "Dauer", "es": "Duración", "it": "Durata", "nl": "Duur"},
    "cover": {"en": "Cover", "fr": "Pochette", "de": "Cover", "es": "Carátula", "it": "Copertina", "nl": "Hoes"},
    "mode": {"en": "State", "fr": "État", "de": "Zustand", "es": "Estado", "it": "Stato", "nl": "Toestand"},
    "playing": {"en": "Playing", "fr": "En lecture", "de": "Spielt", "es": "Reproduciendo", "it": "In riproduzione",
                "nl": "Speelt"},
    "speaker": {"en": "Speaker", "fr": "Enceinte", "de": "Lautsprecher", "es": "Altavoz", "it": "Altoparlante",
                "nl": "Luidspreker"},
    "volume": {"en": "Volume", "fr": "Volume", "de": "Lautstärke", "es": "Volumen", "it": "Volume", "nl": "Volume"},
    "mute": {"en": "Mute", "fr": "Muet", "de": "Stumm", "es": "Silencio", "it": "Muto", "nl": "Dempen"},
    "output": {"en": "Audio output", "fr": "Sortie audio", "de": "Audioausgang", "es": "Salida de audio",
               "it": "Uscita audio", "nl": "Audio-uitgang"},
    "list": {"en": "Music list", "fr": "Liste de musique", "de": "Musikliste", "es": "Lista de música",
             "it": "Lista musicale", "nl": "Muzieklijst"},
    "update": {"en": "Rukebox", "fr": "Rukebox", "de": "Rukebox", "es": "Rukebox", "it": "Rukebox", "nl": "Rukebox"},
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
# A select's options are words: what each one stands for, in each language.
OUTPUT_LABELS = {
    "auto": {"en": "As set", "fr": "Selon les réglages", "de": "Laut Einstellungen", "es": "Según los ajustes",
             "it": "Come da impostazioni", "nl": "Volgens instellingen"},
    "bluetooth": {"en": "Bluetooth", "fr": "Bluetooth", "de": "Bluetooth", "es": "Bluetooth", "it": "Bluetooth",
                  "nl": "Bluetooth"},
    "jack": {"en": "Headphone jack", "fr": "Prise jack", "de": "Klinke", "es": "Jack", "it": "Jack", "nl": "Jack"},
    "usb": {"en": "USB sound card", "fr": "Carte son USB", "de": "USB-Soundkarte", "es": "Tarjeta de sonido USB",
            "it": "Scheda audio USB", "nl": "USB-geluidskaart"},
    "hdmi": {"en": "HDMI", "fr": "HDMI", "de": "HDMI", "es": "HDMI", "it": "HDMI", "nl": "HDMI"},
    "docker": {"en": "Container output", "fr": "Sortie du conteneur", "de": "Container-Ausgang",
               "es": "Salida del contenedor", "it": "Uscita del container", "nl": "Containeruitgang"},
    "snapcast": {"en": "Multiroom", "fr": "Multiroom", "de": "Multiroom", "es": "Multisala", "it": "Multiroom",
                 "nl": "Multiroom"},
}
LIBRARY_LABEL = {"en": "Whole library", "fr": "Toute la bibliothèque", "de": "Ganze Bibliothek",
                 "es": "Toda la biblioteca", "it": "Tutta la libreria", "nl": "Hele bibliotheek"}
ICONS = {"title": "mdi:music", "artist": "mdi:account-music", "album": "mdi:album", "duration": "mdi:timer-outline",
         "mode": "mdi:radio", "toggle_pause": "mdi:play-pause", "next_track": "mdi:skip-next",
         "previous_track": "mdi:skip-previous", "start_music": "mdi:play", "skip_sound": "mdi:bullhorn-outline",
         "standby": "mdi:power-sleep", "volume": "mdi:volume-high", "mute": "mdi:volume-off",
         "output": "mdi:speaker", "list": "mdi:playlist-music"}
_BADGE_RE = re.compile(r"tree--hash-([0-9a-f]{7,64})-", re.IGNORECASE)


def node_id():
    """This radio's id in topics and in Home Assistant: its host name, made topic-safe."""
    return "rukebox_" + (re.sub(r"[^a-z0-9_]+", "_", socket.gethostname().lower()).strip("_") or "radio")


def language(cfg):
    lang = cfg.get("SPEECH_LANGUAGE")
    return lang if lang in NAMES["title"] else "en"


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


def choices(cfg, kinds=None, lists=None):
    """{"output": {label: kind or None}, "list": {label: list id or None}}: what each
    select offers. `kinds` are the outputs there now, `lists` the music lists."""
    lang = language(cfg)
    outputs = {OUTPUT_LABELS["auto"][lang]: None}
    wanted = list(kinds or [])
    if cfg.get("AUDIO_OUTPUT") in audio_output.KINDS:
        wanted.append(cfg["AUDIO_OUTPUT"])
    for kind in audio_output.KINDS:
        if kind in wanted:
            outputs[OUTPUT_LABELS[kind][lang]] = kind
    music = {LIBRARY_LABEL[lang]: None}
    for item in lists or []:
        name = str(item.get("name") or item.get("id") or "").strip()
        if name and name not in music:
            music[name] = item.get("id")
    return {"output": outputs, "list": music}


def _label(options, value):
    return next((label for label, wanted in options.items() if wanted == value), next(iter(options)))


def state_of(status, tags=None, offered=None):
    """The radio's state as one JSON document, from the daemon's status and the
    playing song's own tags."""
    mode = status.get("mode") or "idle"
    tags = tags or {}
    path = status.get("current_track_path") or ""
    title = tags.get("title") or status.get("track_title") or (
        os.path.splitext(os.path.basename(path))[0] if path else "")
    duration = status.get("duration")
    state = {
        "mode": mode,
        "title": title,
        "artist": tags.get("artist") or status.get("track_artist") or "",
        "album": tags.get("album") or "",
        "duration": round(float(duration)) if path and duration else None,
        "volume": round(float(status.get("volume") or 0)),
        "muted": bool(status.get("muted")),
        "playing": mode == "music" and not status.get("paused"),
        "speaker": bool(status.get("speaker_connected")),
    }
    if offered:
        active = status.get("active_list") or {}
        state["output"] = _label(offered["output"], status.get("output_override"))
        state["list"] = _label(offered["list"], active.get("id"))
    return state


def discovery(cfg, node, base, offered=None):
    """{config topic: config document} for every entity of this radio."""
    lang = language(cfg)
    offered = offered or choices(cfg)
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
        entity("sensor", "album", value_template="{{ value_json.album }}", **state),
        entity("sensor", "duration", value_template="{{ value_json.duration }}", device_class="duration",
               unit_of_measurement="s", **state),
        entity("sensor", "mode", value_template="{{ value_json.mode }}", **state),
        entity("binary_sensor", "playing", value_template="{{ 'ON' if value_json.playing else 'OFF' }}",
               device_class="running", **state),
        entity("binary_sensor", "speaker", value_template="{{ 'ON' if value_json.speaker else 'OFF' }}",
               device_class="connectivity", **state),
        entity("number", "volume", command_topic=base + "/set/volume", min=0, max=100, step=1, mode="slider",
               unit_of_measurement="%", value_template="{{ value_json.volume }}", **state),
        entity("switch", "mute", command_topic=base + "/set/mute", payload_on="ON", payload_off="OFF",
               value_template="{{ 'ON' if value_json.muted else 'OFF' }}", **state),
        entity("select", "output", command_topic=base + "/set/output", options=list(offered["output"]),
               value_template="{{ value_json.output }}", **state),
        entity("select", "list", command_topic=base + "/set/list", options=list(offered["list"]),
               value_template="{{ value_json.list }}", **state),
        # The picture itself, published once per song: Home Assistant may not be able to log in here.
        entity("image", "cover", image_topic=base + "/cover", content_type="image/jpeg"),
        entity("update", "update", state_topic=base + "/update", device_class="firmware"),
    ]
    items += [entity("button", name, command_topic=base + "/set/" + name, payload_press="PRESS")
              for name in BUTTONS]
    return dict(items)


def command(cfg, name, payload, offered=None):
    """Runs what Home Assistant asked: the daemon's answer, or None for an unknown command."""
    sock = cfg["CONTROL_SOCKET"]
    if name == "volume":
        try:
            return send_control_command(sock, "set_volume", value=float(payload), source="mqtt")
        except ValueError:
            return {"ok": False, "error": "invalid_value"}
    if name == "mute":
        return send_control_command(sock, "set_mute", on=payload.strip().upper() == "ON", source="mqtt")
    if name in ("output", "list"):
        options = (offered or choices(cfg))[name]
        if payload not in options:
            return {"ok": False, "error": "invalid_value"}
        if name == "output":
            return send_control_command(sock, "set_output_override", output=options[payload], source="mqtt")
        return send_control_command(sock, "set_active_list", id=options[payload], source="mqtt")
    if name in BUTTONS:
        return send_control_command(sock, name, source="mqtt")
    return None


def cover_jpeg(path, cfg):
    """The cover the page shows for that song as a JPEG, or the radio's own picture."""
    found = track_media.cover(path, cfg.get("MUSIC_DIR"), cfg.get("COVER_PRIORITY")) if path else None
    data = found[0] if found else None
    if not data:
        try:
            with open(os.path.join(WEB_DIR, "icon-512.png"), "rb") as f:
                data = f.read()
        except OSError:
            return None
    if track_media._image_mime(data) == "image/jpeg":
        return data
    try:
        return tag_editor.picture_jpeg(data)
    except tag_editor.TagError:
        return None


def installed_version(cfg):
    """(the release this radio runs, or its tree hash; its full tree hash)."""
    data = read_version_file(cfg.get("UPDATE_VERSION_FILE") or "")
    return data.get("release") or data.get("tree_hash_short") or "", (data.get("tree_hash") or "").lower()


def latest_release(repo, timeout=10):
    """{"tag", "url", "notes"} of the repository's latest release, or None."""
    if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return None
    request = urllib.request.Request("https://api.github.com/repos/%s/releases/latest" % repo,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "Rukebox"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            info = json.loads(reply.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not info.get("tag_name"):
        return None
    return {"tag": info["tag_name"], "url": info.get("html_url") or "", "notes": info.get("body") or ""}


def update_state(cfg, release):
    """The update entity's document: a radio that runs the tagged tree runs that release."""
    installed, tree = installed_version(cfg)
    doc = {"installed_version": installed, "title": "Rukebox"}
    if release:
        badge = _BADGE_RE.search(release["notes"])
        if badge and tree.startswith(badge.group(1).lower()):
            doc["installed_version"] = release["tag"]
        doc["latest_version"] = release["tag"]
        doc["release_url"] = release["url"]
    return doc


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
        self.cover_of = False
        self.offered = choices(cfg)
        self.offered_at = float("-inf")
        self.release = None
        self.release_at = float("-inf")
        self.lock = threading.Lock()

    def refresh_choices(self, force=False):
        """The outputs and lists offered, republished when they changed."""
        now = time.monotonic()
        if not force and now - self.offered_at < CHOICES_SEC:
            return
        self.offered_at = now
        kinds = [sink["kind"] for sink in audio_output.list_sinks(env=audio_env())]
        offered = choices(self.cfg, kinds, music_lists.load())
        if offered == self.offered and not force:
            return
        self.offered = offered
        prefix = (self.cfg.get("MQTT_DISCOVERY_PREFIX") or "homeassistant").strip("/")
        for topic, doc in discovery(self.cfg, self.node, self.base, offered).items():
            if topic.startswith(prefix + "/select/"):
                self.client.publish(topic, json.dumps(doc), retain=True)

    def refresh_release(self):
        now = time.monotonic()
        if now - self.release_at < RELEASE_SEC:
            return
        self.release_at = now
        found = latest_release((self.cfg.get("UPDATE_GITHUB_REPO") or "").strip())
        if found:
            self.release = found
        self.client.publish(self.base + "/update", json.dumps(update_state(self.cfg, self.release)), retain=True)

    def publish_state(self):
        result = send_control_command(self.cfg["CONTROL_SOCKET"], "get_status", timeout=5)
        if not result.get("ok"):
            return
        status = result.get("data") or {}
        path = status.get("current_track_path") or ""
        tags = track_media.tags(path) if path else {}
        with self.lock:
            self.refresh_choices()
            state = state_of(status, tags, self.offered)
            if state != self.published:
                self.client.publish(self.base + "/state", json.dumps(state), retain=True)
                self.published = state
            if path != self.cover_of:
                picture = cover_jpeg(path, self.cfg)
                if picture:
                    self.client.publish(self.base + "/cover", picture, retain=True)
                self.cover_of = path
            self.refresh_release()

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
            with self.lock:
                self.offered_at = time.monotonic()
                kinds = [sink["kind"] for sink in audio_output.list_sinks(env=audio_env())]
                self.offered = choices(self.cfg, kinds, music_lists.load())
                for topic, doc in discovery(self.cfg, self.node, self.base, self.offered).items():
                    self.client.publish(topic, json.dumps(doc), retain=True)
            self.client.subscribe([self.base + "/set/#"])
            write_status(self.cfg, "connected")
            log.info("Connected to %s:%s as %s", self.cfg["MQTT_HOST"], self.cfg.get("MQTT_PORT"), self.node)
            watcher = threading.Thread(target=self.watch, daemon=True)
            watcher.start()
            while not self.stopped.is_set() and still_wanted():
                for topic, payload in self.client.poll(1.0):
                    name = topic[len(self.base + "/set/"):]
                    if command(self.cfg, name, payload, self.offered) is None:
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

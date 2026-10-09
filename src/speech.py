"""Spoken sentences (the time, the date, the coming cutoff), rendered offline."""

import hashlib
import logging
import os
import shutil
import subprocess

import paths

log = logging.getLogger("speech")

LANGUAGES = ("en", "fr", "de", "es", "it", "nl")
KINDS = ("none", "time", "time_date")
PICO_VOICES = {"en": "en-GB", "fr": "fr-FR", "de": "de-DE", "es": "es-ES", "it": "it-IT"}
RENDER_TIMEOUT_SEC = 20
# Piper runs slower than real time on a small board, model load included.
PIPER_TIMEOUT_SEC = 120
# "medium" everywhere: the small models are barely faster (the load dominates)
# and lack phonemes French needs.
PIPER_VOICES = {
    "en": "en_US-lessac-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "es": "es_ES-davefx-medium",
    "it": "it_IT-paola-medium",
    "nl": "nl_NL-alex-medium",
}
# Under the state root, which an update does not replace.
PIPER_DIR_NAME = "piper"
PIPER_BINARY = "piper"
# Keyed by engine, so a machine that gains a better voice does not replay the
# old one.
CACHE_DIR_NAME = "speech-cache"
CACHE_KEEP = 200

WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "it": ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"),
    "nl": ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
}
MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"),
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
           "agosto", "settembre", "ottobre", "novembre", "dicembre"),
    "nl": ("januari", "februari", "maart", "april", "mei", "juni", "juli",
           "augustus", "september", "oktober", "november", "december"),
}


def language(value):
    value = str(value or "").strip().lower()[:2]
    return value if value in LANGUAGES else "en"


def time_sentence(when, lang):
    h, m = when.hour, when.minute
    if lang == "fr":
        if h == 0:
            hour = "minuit"
        elif h == 12:
            hour = "midi"
        else:
            hour = "%d heure%s" % (h, "" if h == 1 else "s")
        return "Il est %s%s." % (hour, " %d" % m if m else "")
    if lang == "de":
        return "Es ist %d Uhr%s." % (h, " %d" % m if m else "")
    if lang == "es":
        start = "Es la una" if h == 1 else "Son las %d" % h
        return "%s%s." % (start, " y %d" % m if m else " en punto")
    if lang == "it":
        if h == 0:
            start = "È mezzanotte"
        elif h == 1:
            start = "È l'una"
        else:
            start = "Sono le %d" % h
        return "%s%s." % (start, " e %d" % m if m else "")
    if lang == "nl":
        return "Het is %d uur%s." % (h, " %d" % m if m else "")
    return "It is %d:%02d." % (h, m) if m else "It is %d o'clock." % h


def date_sentence(when, lang):
    day = WEEKDAYS[lang][when.weekday()]
    month = MONTHS[lang][when.month - 1]
    d = when.day
    return {
        "en": "Today is %s, %s %d." % (day, month, d),
        "fr": "Nous sommes %s %s %s." % (day, "1er" if d == 1 else d, month),
        "de": "Heute ist %s, der %d. %s." % (day, d, month),
        "es": "Hoy es %s %d de %s." % (day, d, month),
        "it": "Oggi è %s %d %s." % (day, d, month),
        "nl": "Het is vandaag %s %d %s." % (day, d, month),
    }[lang]


def cutoff_sentence(minutes, lang):
    one = minutes == 1
    return {
        "en": "The radio stops in %d minute%s." % (minutes, "" if one else "s"),
        "fr": "La radio s'arrête dans %d minute%s." % (minutes, "" if one else "s"),
        "de": "Das Radio hört in %d Minute%s auf." % (minutes, "" if one else "n"),
        "es": "La radio se apaga en %d minuto%s." % (minutes, "" if one else "s"),
        "it": "La radio si spegne tra %d minut%s." % (minutes, "o" if one else "i"),
        "nl": "De radio stopt over %d minu%s." % (minutes, "ut" if one else "ten"),
    }[lang]


def clean_text(text, limit=160):
    """Free text made safe to say: one line, no control characters, at most
    `limit` characters."""
    text = "".join(c if c.isprintable() else " " for c in str(text or ""))
    return " ".join(text.split())[:limit].strip()


def _stop(text):
    return text if text[-1:] in ".!?…" else text + "."


def track_sentence(title, artist, lang):
    """"Up next: title, by artist." - a radio host's introduction."""
    lang = language(lang)
    title = clean_text(title, 120)
    artist = clean_text(artist, 80)
    if not title:
        return ""
    head = {"en": "Up next: ", "fr": "Et maintenant : ", "de": "Und jetzt: ",
            "es": "Y ahora: ", "it": "E ora: ", "nl": "En nu: "}[lang]
    by = {"en": ", by ", "fr": ", de ", "de": " von ", "es": ", de ", "it": ", di ", "nl": ", van "}[lang]
    return _stop(head + title + (by + artist if artist else ""))


def dedication_sentence(title, sender, message, lang):
    """The message someone left with a song, then the song's name."""
    lang = language(lang)
    message = clean_text(message)
    sender = clean_text(sender, 40)
    title = clean_text(title, 120)
    if not message:
        return ""
    if sender:
        head = {"en": "A dedication from %s: ", "fr": "Une dédicace de %s : ",
                "de": "Eine Widmung von %s: ", "es": "Una dedicatoria de %s: ",
                "it": "Una dedica da %s: ", "nl": "Een opdracht van %s: "}[lang] % sender
    else:
        head = {"en": "A dedication: ", "fr": "Une dédicace : ", "de": "Eine Widmung: ",
                "es": "Una dedicatoria: ", "it": "Una dedica: ", "nl": "Een opdracht: "}[lang]
    tail = ""
    if title:
        tail = " " + {"en": "Here is %s.", "fr": "Voici %s.", "de": "Hier ist %s.",
                      "es": "Aquí está %s.", "it": "Ecco %s.", "nl": "Hier is %s."}[lang] % title
    return _stop(head + message) + tail


def voice_dedication_sentence(sender, lang):
    """What is said before a recorded dedication."""
    lang = language(lang)
    sender = clean_text(sender, 40)
    if sender:
        return {"en": "A dedication from %s.", "fr": "Une dédicace de %s.", "de": "Eine Widmung von %s.",
                "es": "Una dedicatoria de %s.", "it": "Una dedica da %s.",
                "nl": "Een opdracht van %s."}[lang] % sender
    return {"en": "A dedication.", "fr": "Une dédicace.", "de": "Eine Widmung.",
            "es": "Una dedicatoria.", "it": "Una dedica.", "nl": "Een opdracht."}[lang]


def reminder_sentence(text, lang):
    lang = language(lang)
    text = clean_text(text)
    if not text:
        return ""
    head = {"en": "Reminder: ", "fr": "Rappel : ", "de": "Erinnerung: ",
            "es": "Recordatorio: ", "it": "Promemoria: ", "nl": "Herinnering: "}[lang]
    return _stop(head + text)


def sentence(kind, when, lang, minutes=None):
    """The words for `kind` (time, time_date, cutoff), or "" for none."""
    lang = language(lang)
    if kind == "time":
        return time_sentence(when, lang)
    if kind == "time_date":
        return time_sentence(when, lang) + " " + date_sentence(when, lang)
    if kind == "cutoff" and minutes:
        return cutoff_sentence(int(minutes), lang)
    return ""


def engines():
    """The speech programs installed here, best first - what the radio says with."""
    found = ["piper"] if _any_piper_model() else []
    return found + [name for name in ("pico2wave", "espeak-ng") if shutil.which(name)]


def _any_piper_model():
    """Whether a model is installed, whichever voice it is for."""
    try:
        return any(name.endswith(".onnx") for name in os.listdir(piper_folder()))
    except OSError:
        return False


def default_voice(value):
    """The Piper voice a language gets, or "" for one there is none for."""
    return PIPER_VOICES.get(language(value), "")


def piper_folder():
    return os.path.join(paths.state_dir(), PIPER_DIR_NAME)


def piper_files(voice):
    """(program, model, its configuration) for one voice."""
    folder = piper_folder()
    return (os.path.join(folder, PIPER_BINARY),
            os.path.join(folder, voice + ".onnx"),
            os.path.join(folder, voice + ".onnx.json"))


def piper_ready(voice):
    """Whether that voice can be spoken here: the program AND its model."""
    if not voice:
        return False
    binary, model, config = piper_files(voice)
    return os.access(binary, os.X_OK) and os.path.exists(model) and os.path.exists(config)


def engine(lang, choice=None):
    """Which program says a sentence here, as a name - the cache is keyed by it."""
    voice = (choice or "").strip() or default_voice(lang)
    if piper_ready(voice):
        return "piper:" + voice
    if lang in PICO_VOICES and shutil.which("pico2wave"):
        return "pico2wave"
    if shutil.which("espeak-ng"):
        return "espeak-ng"
    return ""


def _cache_path(text, lang, choice):
    name = hashlib.sha1(
        ("%s\0%s\0%s" % (engine(lang, choice), lang, text)).encode("utf-8")
    ).hexdigest()
    return os.path.join(paths.state_dir(), CACHE_DIR_NAME, name + ".wav")


def _from_cache(text, lang, choice, path):
    source = _cache_path(text, lang, choice)
    if not os.path.exists(source):
        return False
    try:
        shutil.copyfile(source, path)
    except OSError:
        log.debug("Could not reuse the spoken sentence", exc_info=True)
        return False
    return os.path.getsize(path) > 44


def _remember(text, lang, choice, path):
    """Keeps a copy of what was just said, and drops the oldest when it grows."""
    target = _cache_path(text, lang, choice)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(path, target)
        folder = os.path.dirname(target)
        entries = [os.path.join(folder, n) for n in os.listdir(folder)]
        if len(entries) > CACHE_KEEP:
            entries.sort(key=os.path.getmtime)
            for old in entries[:len(entries) - CACHE_KEEP]:
                os.remove(old)
    except OSError:
        log.debug("Could not keep the spoken sentence", exc_info=True)


def render(text, lang, path, choice=None):
    """Writes `text` spoken to the WAV `path`; True when it was."""
    lang = language(lang)
    if not text:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if _from_cache(text, lang, choice, path):
        return True
    stdin = text.encode("utf-8")
    commands = []
    voice = (choice or "").strip() or default_voice(lang)
    if piper_ready(voice):
        binary, model, config = piper_files(voice)
        common = ["--model", model, "--config", config]
        # 2023.11 writes with --output_file; piper 1.2 and later with -f.
        commands.append(([binary] + common + ["--output_file", path], PIPER_TIMEOUT_SEC, stdin))
        commands.append(([binary] + common + ["-f", path], PIPER_TIMEOUT_SEC, stdin))
    if lang in PICO_VOICES and shutil.which("pico2wave"):
        commands.append((["pico2wave", "-l", PICO_VOICES[lang], "-w", path, text],
                         RENDER_TIMEOUT_SEC, None))
    if shutil.which("espeak-ng"):
        commands.append((["espeak-ng", "-v", lang, "-w", path, text], RENDER_TIMEOUT_SEC, None))
    for command, timeout, input_bytes in commands:
        try:
            done = subprocess.run(command, capture_output=True, timeout=timeout, input=input_bytes)
        except (OSError, subprocess.TimeoutExpired):
            log.warning("%s did not answer", command[0])
            continue
        if done.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 44:
            # Only the best program's own voice is kept: a sentence said by a
            # fallback is said again, so a repaired voice is heard at once.
            if commands and command[0] == commands[0][0][0]:
                _remember(text, lang, choice, path)
            return True
        log.warning("%s failed: %s", command[0], done.stderr.decode(errors="replace").strip()[:200])
    if not commands:
        log.warning("No speech program installed (piper, pico2wave or espeak-ng)")
    return False

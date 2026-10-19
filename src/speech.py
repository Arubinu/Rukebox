"""Spoken sentences (the time, the date, the coming cutoff), rendered offline."""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile

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


# The ways a radio host brings a song in; the first one of each list is the plainest.
INTROS = {
    "en": ("Up next: {title}, by {artist}.", "Here's {artist}, with {title}.",
           "Coming up: {title}, from {artist}.", "Right now on the radio: {artist}, {title}.",
           "Let's keep it going with {title}, by {artist}.", "This one is {title}, by {artist}.",
           "Turn it up for {artist}: {title}."),
    "fr": ("Et maintenant : {title}, de {artist}.", "Tout de suite : {artist}, avec {title}.",
           "On enchaîne avec {title}, de {artist}.", "Place à {artist}, avec {title}.",
           "On continue avec {title}, signé {artist}.", "À suivre : {title}, par {artist}.",
           "Voici {artist}, avec {title}."),
    "de": ("Und jetzt: {title} von {artist}.", "Hier kommt {artist} mit {title}.",
           "Weiter geht's mit {title} von {artist}.", "Als Nächstes: {title} von {artist}.",
           "Jetzt für euch: {artist} mit {title}.", "Gleich geht's weiter: {title} von {artist}."),
    "es": ("Y ahora: {title}, de {artist}.", "Aquí llega {artist} con {title}.",
           "Seguimos con {title}, de {artist}.", "A continuación: {title}, de {artist}.",
           "Suena ahora {title}, de {artist}.", "Para ti, {artist} con {title}."),
    "it": ("E ora: {title}, di {artist}.", "Ecco {artist} con {title}.",
           "Continuiamo con {title}, di {artist}.", "A seguire: {title}, di {artist}.",
           "Adesso in onda {title}, di {artist}.", "Per voi, {artist} con {title}."),
    "nl": ("En nu: {title} van {artist}.", "Hier is {artist} met {title}.",
           "We gaan verder met {title} van {artist}.", "Hierna: {title} van {artist}.",
           "Nu op de radio: {artist} met {title}.", "Speciaal voor jou: {title} van {artist}."),
}
INTROS_NO_ARTIST = {
    "en": ("Up next: {title}.", "Coming up: {title}.", "Here's {title}.", "Right now: {title}."),
    "fr": ("Et maintenant : {title}.", "Tout de suite : {title}.", "Voici {title}.", "On enchaîne avec {title}."),
    "de": ("Und jetzt: {title}.", "Als Nächstes: {title}.", "Hier kommt {title}.", "Weiter geht's mit {title}."),
    "es": ("Y ahora: {title}.", "A continuación: {title}.", "Aquí llega {title}.", "Seguimos con {title}."),
    "it": ("E ora: {title}.", "A seguire: {title}.", "Ecco {title}.", "Continuiamo con {title}."),
    "nl": ("En nu: {title}.", "Hierna: {title}.", "Hier is {title}.", "We gaan verder met {title}."),
}
AND_WORD = {"en": "and", "fr": "et", "de": "und", "es": "y", "it": "e", "nl": "en"}


def _french_contractions(text):
    """"de Angèle" as "d'Angèle", "de Les Rita Mitsouko" as "des Rita Mitsouko"."""
    text = re.sub(r"\b([dD])e (?=[AEIOUYÀÂÉÈÊËÎÏÔÛÙaeiouyàâéèêëîïôûù])", r"\1'", text)
    for word, joined in (("de", "des"), ("à", "aux")):
        text = re.sub(r"\b%s Les " % word, joined + " ", text)
    for word, joined in (("de", "du"), ("à", "au")):
        text = re.sub(r"\b%s Le " % word, joined + " ", text)
    return text


def spoken_names(text, lang):
    """What a voice reads aloud of a credit: "&" as the language's "and", "feat." as "featuring"."""
    text = re.sub(r"\s*&\s*", " %s " % AND_WORD[language(lang)], text)
    return re.sub(r"\b(?:feat|ft)\.?(?=\s)", "featuring", text, flags=re.IGNORECASE).strip()


# Words that are common in song titles, and the letters only one language writes.
LANGUAGE_WORDS = {
    "en": """the a an and of to in on at me my you your i i'm it it's is are be was love baby girl boy night
             day time heart now all we us our they what who why when where how don't can't won't never ever
             just like up down get got go gonna wanna come back home with without for from this that one no
             yes oh let feel good bad life world dance dream want need know say tell make take give run stop
             light fire sun moon sky rain star stars blue black white red man woman people little sweet crazy
             wild free alone together tonight forever laughing smile kiss hold hands eyes high low money party
             summer again more over out into her his him she he hello goodbye paradise queen king style
             heaven hell angel soul spirit rock roll song songs music boogie groove funk beat feeling feelings
             lonely happy sad young old new last first story road street city town river ocean wind storm
             thunder rose roses flower garden dreams believe remember forget wonder wonderful beautiful
             thinking talking walking running waiting dancing living loving smells teen everything everybody
             nothing somebody someone anything something away around through tears cry crying lies truth lady
             mister mama daddy friend friends brother sister kids child children sugar honey hot cold
             bright dark shadow shine breathe breaking broken lost found inside outside better best
             stay leave fall rise wake sleep walk talk call gold diamond diamonds heartbreak hey yesterday
             respect imagine lucky shape deep thriller jude""".split(),
    "fr": """le la les un une des du de et ou je tu il elle nous vous ils elles mon ma mes ton ta tes son sa
             ses ce cette qui que quoi pas plus avec sans pour dans sur sous est suis sont toi moi amour coeur
             cœur vie jour nuit soleil aime ciel mer monde temps encore toujours jamais belle beau petit petite
             femme homme enfant chanson danse rêve au aux en ne oui non mais comme quand où chez tout tous rien
             demain hier ici là alors fille garçon pays ville mal bien veux sais va vais fait""".split(),
    "de": """der die das den dem des ein eine einen und oder ich du er sie es wir ihr mein meine dein deine
             nicht kein keine ist bin bist sind mit ohne für auf aus bei nach von zu im am liebe herz nacht tag
             leben zeit welt immer nie wieder noch heute morgen schön alles nichts wenn wie was wer wo warum
             mädchen mann frau kind sommer sonne himmel lied tanz traum dich mich dir mir""".split(),
    "es": """el la los las un una unos y o de del al en con sin por para que qué yo tú tu mi mis te me se nos
             es soy eres está estoy amor corazón vida noche día sol luna cielo mar mundo tiempo siempre nunca
             más bonita bonito chica mujer hombre quiero baila bailar canción sueño no sí como cuando donde
             porque ya aquí ahora todo nada contigo""".split(),
    "it": """il lo la i gli le un uno una e ed o di del della dei delle a al alla da in con su per tra fra che
             chi io tu lui lei noi voi mio mia tuo tua mi ti ci si non è sei sono amore cuore vita notte giorno
             sole luna cielo mare mondo tempo sempre mai più bella bello ragazza donna uomo voglio canzone
             sogno come quando dove perché ancora tutto niente questa questo ciao""".split(),
    "nl": """de het een en of ik jij je hij zij ze wij we mijn jouw jou niet geen is ben bent zijn met zonder
             voor op in van naar bij uit liefde hart nacht dag leven tijd wereld altijd nooit weer nog vandaag
             morgen mooi alles niets als hoe wat wie waar waarom meisje man vrouw kind zomer zon lied dans
             droom hou houden ga gaan wil kom komt""".split(),
}
_LANGUAGE_LETTERS = {"fr": "çœêîûëÿé", "de": "äöüß", "es": "ñ¿¡áíó", "it": "ìò"}
# Endings English words have and the other five rarely do.
_ENGLISH_ENDINGS = re.compile(r"^[a-z]{2,}(?:ing|ly|ness|ight|ful|less|ty|y)$")
_CONTRACTIONS = {"en": ("'s", "n't", "'m", "'re", "'ll", "'ve", "'d", "in'"),
                 "fr": ("l'", "d'", "j'", "qu'", "c'", "n'", "s'", "m'", "t'"),
                 "it": ("l'", "un'", "dell'", "all'", "nell'", "dall'", "sull'")}


def guess_language(text, fallback):
    """The language a title is written in, or `fallback` without clear evidence:
    a title is a few words, so a doubt keeps the voice the radio speaks with."""
    fallback = language(fallback)
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?|'[^\W\d_]+", str(text or "").lower().replace("’", "'"))
    scores = dict.fromkeys(LANGUAGES, 0)
    for lang, known in LANGUAGE_WORDS.items():
        # A plural of a known word counts as that word.
        scores[lang] += 2 * len({word for word in words if word in known or word.rstrip("s") in known})
    for lang, endings in _CONTRACTIONS.items():
        scores[lang] += 2 * sum(1 for word in words for ending in endings
                                if word.endswith(ending) or word.startswith(ending))
    scores["en"] += 2 * len({word for word in words if _ENGLISH_ENDINGS.match(word)})
    for lang, letters in _LANGUAGE_LETTERS.items():
        scores[lang] += 3 * len({c for c in str(text or "").lower() if c in letters})
    ranked = sorted(LANGUAGES, key=lambda lang: scores[lang], reverse=True)
    best, second = ranked[0], ranked[1]
    if scores[best] < 2 or scores[best] == scores[second] or scores[best] - scores[fallback] < 2:
        return fallback
    return best


def track_sentence(title, artist, lang, pick=0):
    """"Up next: title, by artist." - a radio host's introduction, the `pick`-th way."""
    return "".join(text for text, _lang in track_parts(title, artist, lang, pick))


def intro_count(lang, with_artist=True):
    return len((INTROS if with_artist else INTROS_NO_ARTIST)[language(lang)])


def track_parts(title, artist, lang, pick=0, can_say=None):
    """The introduction as [(text, language)]: the title in its own language when
    `can_say(language)` answers that a voice for it is there, the rest in `lang`."""
    lang = language(lang)
    title = clean_text(title, 120)
    artist = spoken_names(clean_text(artist, 80), lang)
    if not title:
        return []
    said = guess_language(title, lang)
    if said != lang and not (can_say and can_say(said)):
        said = lang
    title = spoken_names(title, said)
    forms = INTROS[lang] if artist else INTROS_NO_ARTIST[lang]
    form = forms[pick % len(forms)].replace("{artist}", artist)
    if lang == "fr":
        form = _french_contractions(form)
    parts = []
    head, _, tail = form.partition("{title}")
    for text, in_lang in ((head, lang), (title, said), (tail, lang)):
        if not text:
            continue
        if parts and parts[-1][1] == in_lang:
            parts[-1] = (parts[-1][0] + text, in_lang)
        else:
            parts.append((text, in_lang))
    return parts


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


GAME_WORDS = {
    "start": {"en": "The blind test begins!", "fr": "C'est parti pour le blind test !",
              "de": "Der Blindtest beginnt!", "es": "¡Empieza el test a ciegas!",
              "it": "Inizia il blind test!", "nl": "De blinde test begint!"},
    "end": {"en": "The blind test is over.", "fr": "Le blind test est terminé.",
            "de": "Der Blindtest ist vorbei.", "es": "El test a ciegas ha terminado.",
            "it": "Il blind test è finito.", "nl": "De blinde test is voorbij."},
    "winner": {"en": "Well done,", "fr": "Bravo à", "de": "Glückwunsch an", "es": "Enhorabuena a",
               "it": "Complimenti a", "nl": "Gefeliciteerd,"},
    "right": {"en": "Right answer from", "fr": "Bonne réponse de", "de": "Richtige Antwort von",
              "es": "Respuesta correcta de", "it": "Risposta giusta di", "nl": "Goed antwoord van"},
    "nobody": {"en": "Nobody found it.", "fr": "Personne n'a trouvé.", "de": "Niemand hat es erraten.",
               "es": "Nadie lo ha adivinado.", "it": "Nessuno l'ha indovinato.", "nl": "Niemand had het goed."},
    "and": {"en": "and", "fr": "et", "de": "und", "es": "y", "it": "e", "nl": "en"},
    "place3": {"en": "Third place:", "fr": "Troisième place :", "de": "Dritter Platz:", "es": "Tercer puesto:",
               "it": "Terzo posto:", "nl": "Derde plaats:"},
    "place2": {"en": "Second place:", "fr": "Deuxième place :", "de": "Zweiter Platz:", "es": "Segundo puesto:",
               "it": "Secondo posto:", "nl": "Tweede plaats:"},
    "place1": {"en": "And first place:", "fr": "Et la première place :", "de": "Und der erste Platz:",
               "es": "Y el primer puesto:", "it": "E il primo posto:", "nl": "En de eerste plaats:"},
    "with": {"en": "with", "fr": "avec", "de": "mit", "es": "con", "it": "con", "nl": "met"},
    "point": {"en": "point", "fr": "point", "de": "Punkt", "es": "punto", "it": "punto", "nl": "punt"},
    "points": {"en": "points", "fr": "points", "de": "Punkten", "es": "puntos", "it": "punti", "nl": "punten"},
    "answer": {"en": "That was %s, by %s.", "fr": "C'était %s, de %s.", "de": "Das war %s von %s.",
               "es": "Era %s, de %s.", "it": "Era %s, di %s.", "nl": "Dat was %s van %s."},
    "ask_song": {"en": "Which song is it?", "fr": "Quelle est cette chanson ?", "de": "Welcher Titel ist das?",
                 "es": "¿Qué canción es?", "it": "Qual è questa canzone?", "nl": "Welk nummer is dit?"},
    "ask_artist": {"en": "Who is the artist?", "fr": "Qui est l'artiste ?", "de": "Wer ist der Interpret?",
                   "es": "¿Quién es el artista?", "it": "Chi è l'artista?", "nl": "Wie is de artiest?"},
    "ask_year": {"en": "Which decade is it from?", "fr": "De quelle décennie ?", "de": "Aus welchem Jahrzehnt?",
                 "es": "¿De qué década es?", "it": "Di quale decennio è?", "nl": "Uit welk decennium?"},
    "ask_genre": {"en": "Which genre is it?", "fr": "Quel est le genre ?", "de": "Welches Genre ist das?",
                  "es": "¿Qué género es?", "it": "Qual è il genere?", "nl": "Welk genre is dit?"},
    "ask_album": {"en": "Which album is it from?", "fr": "De quel album ?", "de": "Von welchem Album?",
                  "es": "¿De qué álbum es?", "it": "Da quale album?", "nl": "Van welk album?"},
    "fact_year": {"en": "Released in %s.", "fr": "Sorti en %s.", "de": "Erschienen %s.",
                  "es": "Publicada en %s.", "it": "Uscita nel %s.", "nl": "Uitgebracht in %s."},
    "fact_genre": {"en": "Genre: %s.", "fr": "Genre : %s.", "de": "Genre: %s.", "es": "Género: %s.",
                   "it": "Genere: %s.", "nl": "Genre: %s."},
    "fact_album": {"en": "From the album %s.", "fr": "Extrait de l'album %s.", "de": "Vom Album %s.",
                   "es": "Del álbum %s.", "it": "Dall'album %s.", "nl": "Van het album %s."},
}


def game_parts(kind, people, lang):
    """What the blind test says, as parts: {"text"} and {"name", "person"} (said by the person's
    own recording when there is one). kind: start, round (people = the right answers, fastest
    first) or podium (people = game.podium())."""
    lang = language(lang)
    words = {key: value[lang] for key, value in GAME_WORDS.items()}

    def names(group):
        out = []
        for i, one in enumerate(group or []):
            if i:
                out.append({"text": words["and"] if i == len(group) - 1 else ","})
            out.append({"name": clean_text(one.get("name"), 40) or "?", "person": one.get("person")})
        return out

    named = names(people)
    if kind == "ask":
        # people: [{"kind"}], what the round asks about.
        asked = (people or [{}])[0].get("kind") or "song"
        return [{"text": words.get("ask_" + asked, words["ask_song"])}]
    if kind == "answer":
        # people: [{"title", "artist", "kind", "year", "genre", "album"}], the song that was playing.
        song = (people or [{}])[0]
        said = [{"text": words["answer"] % (clean_text(song.get("title"), 80) or "?",
                                            clean_text(song.get("artist"), 60) or "?")}]
        asked = song.get("kind")
        if asked in ("year", "genre", "album") and song.get(asked):
            said.append({"text": words["fact_" + asked] % clean_text(str(song[asked]), 80)})
        return said
    if kind == "podium":
        # people: the places, first place first; said from the last one up, for the suspense.
        said = [{"text": words["end"]}]
        places = list(people or [])
        for rank in range(len(places), 0, -1):
            place = places[rank - 1]
            lead = words["winner"] if len(places) == 1 else words["place%d" % rank]
            n = int(place.get("points") or 0)
            said += [{"text": lead}] + names(place.get("people")) + [
                {"text": ", %s %d %s%s" % (words["with"], n, words["point" if n == 1 else "points"],
                                           "!" if rank == 1 else ".")}]
        return said
    if kind == "start":
        return [{"text": words["start"]}]
    if kind == "round":
        return [{"text": words["right"]}] + named + [{"text": "."}] if named else [{"text": words["nobody"]}]
    return []


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
    scratch = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(path, scratch)
        # Renamed into place: two copies of one sentence at once never mix into a broken WAV.
        os.replace(scratch, target)
        folder = os.path.dirname(target)
        entries = [os.path.join(folder, n) for n in os.listdir(folder)]
        if len(entries) > CACHE_KEEP:
            entries.sort(key=os.path.getmtime)
            for old in entries[:len(entries) - CACHE_KEEP]:
                os.remove(old)
    except OSError:
        log.debug("Could not keep the spoken sentence", exc_info=True)
        try:
            os.remove(scratch)
        except OSError:
            pass


def title_voices(main_lang):
    """[{"lang", "voice", "installed"}] of the other languages' Piper voices."""
    main = language(main_lang)
    return [{"lang": lang, "voice": PIPER_VOICES[lang], "installed": piper_ready(PIPER_VOICES[lang])}
            for lang in LANGUAGES if lang != main]


def remove_voice(lang):
    """Deletes one language's Piper voice; True when there was one."""
    voice = PIPER_VOICES.get(language(lang))
    removed = False
    for path in piper_files(voice)[1:] if voice else ():
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    return removed


def can_switch(lang, main_lang, choice=None):
    """Whether a part in `lang` would sound like the rest: the same kind of voice,
    never a robotic fallback in the middle of a Piper sentence."""
    main = engine(language(main_lang), choice).split(":")[0]
    return bool(main) and engine(language(lang)).split(":")[0] == main


def render_parts(parts, lang, path, choice=None):
    """Writes [(text, language)] as one WAV, each part in its own voice; True when it was."""
    lang = language(lang)
    parts = [(text.strip().lstrip(",;: ").strip(), language(in_lang)) for text, in_lang in parts]
    # A part that is only punctuation is a pause, not something to say.
    parts = [(text, in_lang) for text, in_lang in parts if any(c.isalnum() for c in text)]
    if not parts:
        return False
    if all(in_lang == lang for _text, in_lang in parts):
        return render(" ".join(text for text, _lang in parts), lang, path, choice)
    base = path[:-4] if path.endswith(".wav") else path
    pieces = []
    try:
        for index, (text, in_lang) in enumerate(parts):
            piece = "%s.part%d.wav" % (base, index)
            if not render(text, in_lang, piece, choice if in_lang == lang else None):
                return render(" ".join(text for text, _lang in parts), lang, path, choice)
            pieces.append(piece)
        inputs = []
        for piece in pieces:
            inputs += ["-i", piece]
        # Two voices need not share a sample rate: both are brought to one before joining.
        chain = "".join("[%d:a]aresample=22050,aformat=channel_layouts=mono[a%d];" % (i, i)
                        for i in range(len(pieces)))
        chain += "".join("[a%d]" % i for i in range(len(pieces))) + "concat=n=%d:v=0:a=1[out]" % len(pieces)
        try:
            done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + inputs
                                  + ["-filter_complex", chain, "-map", "[out]", path],
                                  capture_output=True, timeout=RENDER_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is not None and done.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 44:
            return True
        log.warning("Could not join the parts of a sentence: saying it in one voice")
        return render(" ".join(text for text, _lang in parts), lang, path, choice)
    finally:
        for piece in pieces:
            try:
                os.remove(piece)
            except OSError:
                pass


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


def warm(text, lang, choice=None):
    """Renders a sentence into the cache before it is asked for; True when it is ready."""
    text = str(text or "").strip()
    if not text:
        return False
    if os.path.exists(_cache_path(text, language(lang), choice)):
        return True
    handle, scratch = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    try:
        return render(text, lang, scratch, choice)
    finally:
        try:
            os.remove(scratch)
        except OSError:
            pass

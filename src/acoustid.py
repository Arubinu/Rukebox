"""Recognising a song by its sound: Chromaprint's fingerprint, AcoustID's lookup.

`fpcalc` (apt: libchromaprint-tools) reads the fingerprint on the radio
itself; AcoustID matches it to MusicBrainz recordings over the internet, with
the application key the owner registered (acoustid.org/new-application). The
Cover Art Archive gives the album's front picture by its release group.
"""

import json
import logging
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("acoustid")

LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
COVER_URL = "https://coverartarchive.org/release-group/%s/front-500"
USER_AGENT = "Rukebox (https://github.com/Arubinu/Rukebox)"
FINGERPRINT_SEC = 120
TIMEOUT_SEC = 20
COVER_MAX_BYTES = 8 * 1024 * 1024
CANDIDATES_MAX = 6


class AcoustidError(Exception):
    """A lookup that could not be made; the message is the error code."""


def available():
    return shutil.which("fpcalc") is not None


def fingerprint(path):
    """(duration in seconds, fingerprint) of the first two minutes."""
    try:
        proc = subprocess.run(["fpcalc", "-json", "-length", str(FINGERPRINT_SEC), path],
                              capture_output=True, timeout=60)
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise AcoustidError("fingerprint_failed")
    if not data.get("fingerprint") or not data.get("duration"):
        raise AcoustidError("fingerprint_failed")
    return int(round(float(data["duration"]))), data["fingerprint"]


def _year(releases):
    years = [r.get("date", {}).get("year") for r in releases or [] if isinstance(r, dict)]
    years = [y for y in years if isinstance(y, int)]
    return str(min(years)) if years else ""


def _artist(artists):
    out = ""
    for one in artists or []:
        out += (one.get("name") or "") + (one.get("joinphrase") or "")
    return out.strip()


def candidates(answer):
    """The answer of a lookup as [{score, title, artist, album, year, release_group}],
    the best first, an album before a single or a compilation."""
    found = []
    for result in answer.get("results") or []:
        score = float(result.get("score") or 0)
        for recording in result.get("recordings") or []:
            title = recording.get("title") or ""
            artist = _artist(recording.get("artists"))
            groups = recording.get("releasegroups") or [{}]
            for group in groups:
                found.append({
                    "score": round(score, 3),
                    "title": title,
                    "artist": _artist(group.get("artists")) if not artist else artist,
                    "album": group.get("title") or "",
                    "year": _year(group.get("releases")),
                    "release_group": group.get("id") or "",
                    "album_type": group.get("type") or "",
                    "secondary": bool(group.get("secondarytypes")),
                })
    found = [one for one in found if one["title"]]
    found.sort(key=lambda one: (-one["score"], one["secondary"], one["album_type"] != "Album",
                                one["year"] or "9999"))
    seen, out = set(), []
    for one in found:
        mark = (one["title"].casefold(), one["artist"].casefold(), one["album"].casefold())
        if mark in seen:
            continue
        seen.add(mark)
        out.append({key: one[key] for key in ("score", "title", "artist", "album", "year",
                                             "release_group")})
        if len(out) == CANDIDATES_MAX:
            break
    return out


def lookup(key, duration, print_):
    """What AcoustID knows of that fingerprint."""
    if not key:
        raise AcoustidError("acoustid_no_key")
    body = urllib.parse.urlencode({
        "client": key, "duration": duration, "fingerprint": print_,
        "meta": "recordings releasegroups releases",
    }).encode("ascii")
    request = urllib.request.Request(LOOKUP_URL, data=body, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as reply:
            answer = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read().decode("utf-8"))
        except ValueError:
            answer = {}
        log.warning("AcoustID refused the lookup: %s", answer.get("error") or e.code)
        raise AcoustidError("acoustid_refused")
    except (urllib.error.URLError, OSError, ValueError):
        raise AcoustidError("acoustid_offline")
    if answer.get("status") != "ok":
        log.warning("AcoustID refused the lookup: %s", answer.get("error"))
        raise AcoustidError("acoustid_refused")
    return candidates(answer)


def cover(release_group):
    """The front picture of a release group, as bytes."""
    if not release_group or not all(c.isalnum() or c == "-" for c in release_group):
        raise AcoustidError("bad_release_group")
    request = urllib.request.Request(COVER_URL % release_group, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as reply:
            data = reply.read(COVER_MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise AcoustidError("cover_not_found" if e.code == 404 else "acoustid_offline")
    except (urllib.error.URLError, OSError):
        raise AcoustidError("acoustid_offline")
    if len(data) > COVER_MAX_BYTES:
        raise AcoustidError("bad_picture")
    return data

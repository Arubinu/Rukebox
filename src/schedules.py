"""Schedules: start the music, stop it, or both, on chosen days or one date, with settings that
only hold while the schedule runs."""

import calendar
import logging
import os
import re
import time
from datetime import date as date_type, timedelta

import config_document
import config_schema
import json_file

log = logging.getLogger("schedules")

BLOCK = "schedules"

STOP_ACTIONS = ("pause", "standby", "poweroff")
SAY_MAX = 200
# What is said before a schedule ends: a few of them, up to four hours ahead, 0 being the end itself.
END_REMINDERS_MAX = 6
END_MINUTES_MAX = 240
END_KINDS = ("text", "voice", "announcement")
VOICE_MAX_SEC = 30.0
NAME_MAX = 40
VALUE_MAX = 200
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_SECTIONS = ("playback", "fades", "buttons", "schedule", "playback_errors", "audio")
# What a schedule starts with is the schedule's own; the rest is read by another process.
_NOT_OVERRIDABLE = frozenset({
    "MUSIC_START_MODE", "MUSIC_START_HOUR", "MUSIC_START_MINUTE",
    "UPCOMING_TRACKS_COUNT", "RECENT_TRACKS_COUNT", "MUSIC_KEEP_PROGRESS", "MUSIC_RESUME_MODE",
    "DLNA_RENDERER", "DLNA_RECEIVE",
}) | config_schema.RESTART_REQUIRED | config_schema.GPIO_BUTTON_SETTINGS

OVERRIDABLE = frozenset(s.env for s in config_schema.SETTINGS
                        if (s.section in _SECTIONS or s.env.startswith("WLED_SCENE_"))
                        and s.env not in _NOT_OVERRIDABLE)
# A party left running "until stopped" still ends a day later.
PARTY_MAX_SEC = 24 * 3600


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "schedule"


def read_items(path=None):
    """Every schedule in the order of the document, or None when it cannot be read."""
    data = config_document.read(BLOCK, path)
    if data is None:
        return None
    items = data.get("schedules")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def load(path=None):
    return read_items(path) or []


def _write(path, items):
    config_document.write(BLOCK, {"schedules": items}, path)


def _time(raw):
    """"07:30", or None for no time at all."""
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if not _TIME_RE.match(text):
        raise ValueError("schedule_bad_time")
    return text


def _settings(raw):
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("schedule_bad_setting")
    clean = {}
    for key, value in raw.items():
        setting = config_schema.find_setting(str(key))
        if setting is None or setting.env not in OVERRIDABLE:
            raise ValueError("schedule_bad_setting")
        text = config_schema.to_raw(setting, value)
        if len(text) > VALUE_MAX or "\n" in text or "\r" in text:
            raise ValueError("schedule_bad_setting")
        if setting.type in ("int", "float"):
            try:
                float(text)
            except ValueError:
                raise ValueError("schedule_bad_setting")
        clean[setting.path] = text
    return clean


def _before_end(raw):
    """The reminders said before the end, latest first; one per minute count."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > END_REMINDERS_MAX:
        raise ValueError("schedule_bad_reminder")
    clean = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("schedule_bad_reminder")
        try:
            minutes = int(entry.get("min"))
        except (TypeError, ValueError):
            raise ValueError("schedule_bad_reminder")
        kind = str(entry.get("kind") or "")
        if not 0 <= minutes <= END_MINUTES_MAX or kind not in END_KINDS:
            raise ValueError("schedule_bad_reminder")
        reminder = {"min": minutes, "kind": kind}
        if kind == "text":
            text = " ".join(str(entry.get("text") or "").split())
            if not text:
                raise ValueError("schedule_bad_reminder")
            if len(text) > SAY_MAX:
                raise ValueError("schedule_say_too_long")
            reminder["text"] = text
        elif kind == "announcement":
            wanted = str(entry.get("announcement") or "").strip()
            if not re.match(r"^[a-z0-9-]{1,80}$", wanted):
                raise ValueError("schedule_bad_announcement")
            reminder["announcement"] = wanted
        clean[minutes] = reminder
    return [clean[minutes] for minutes in sorted(clean, reverse=True)]


def validate(data):
    """Raises ValueError carrying an error CODE, like the other JSON stores."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("schedule_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("schedule_name_too_long")

    # A party has no time of its own: it runs when started from the player.
    party = bool(data.get("party"))
    start, stop = (None, None) if party else (_time(data.get("start")), _time(data.get("stop")))
    if not start and not stop and not party:
        raise ValueError("schedule_no_time")
    if start and start == stop:
        raise ValueError("schedule_bad_time")

    day = None if party else (str(data.get("date") or "").strip() or None)
    if day:
        try:
            date_type.fromisoformat(day)
        except ValueError:
            raise ValueError("schedule_bad_date")

    days = data.get("days") or []
    if not isinstance(days, (list, tuple)):
        raise ValueError("schedule_bad_days")
    try:
        days = sorted({int(d) for d in days})
    except (TypeError, ValueError):
        raise ValueError("schedule_bad_days")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("schedule_bad_days")

    action = str(data.get("stop_action") or "pause").strip()
    if action not in STOP_ACTIONS:
        raise ValueError("schedule_bad_action")

    music = data.get("list")
    music = None if music is None else str(music).strip()

    announcement = str(data.get("announcement") or "").strip() or None
    say = " ".join(str(data.get("say") or "").split())
    if len(say) > SAY_MAX:
        raise ValueError("schedule_say_too_long")
    if announcement and (len(announcement) > 80 or not re.match(r"^[a-z0-9-]+$", announcement)):
        raise ValueError("schedule_bad_announcement")

    clean = {
        "name": name,
        "enabled": bool(data.get("enabled", True)),
        "date": day,
        # The same day every year: a birthday, a feast.
        "yearly": bool(day and data.get("yearly")),
        "days": [] if day else days,
        "start": start,
        "stop": stop,
        "stop_action": action,
        "list": music,
        "announcement": announcement if start or party else None,
        "say": (say or None) if start or party else None,
        # Only a schedule that has an end can count down to it.
        "before_end": _before_end(data.get("before_end")) if stop or party else [],
        "settings": _settings(data.get("settings")),
    }
    if party:
        clean.update(party=True, days=[], guests=bool(data.get("guests")))
    return clean


def _one_party(items, clean, own_id=None):
    if clean.get("party") and any(item.get("party") and item["id"] != own_id for item in items):
        raise ValueError("schedule_party_exists")


def add(data, path=None):
    clean = validate(data)
    with config_document.lock(path):
        items = _strict(path)
        _one_party(items, clean)
        taken = {item["id"] for item in items}
        new_id = base_id = _slugify(clean["name"])
        suffix = 2
        while new_id in taken:
            new_id = "%s-%d" % (base_id, suffix)
            suffix += 1
        clean["id"] = new_id
        clean["created_at"] = time.time()
        items.append(clean)
        _write(path, items)
    return clean


def _strict(path):
    """A document that cannot be read is never replaced by an empty one."""
    items = read_items(path)
    if items is None:
        raise ValueError("file_unreadable")
    return items


def update(schedule_id, data, path=None):
    """Partial update: fields left out of `data` keep their current value."""
    with config_document.lock(path):
        items = _strict(path)
        for i, item in enumerate(items):
            if item["id"] == schedule_id:
                merged = dict(item)
                merged.update(data)
                clean = validate(merged)
                _one_party(items, clean, schedule_id)
                clean["id"] = schedule_id
                clean["created_at"] = item.get("created_at", time.time())
                items[i] = clean
                _write(path, items)
                return clean
    raise KeyError(schedule_id)


def party_item(items):
    """The schedule a party wears, or None when none is set."""
    return next((item for item in items or [] if item.get("party") and item.get("enabled", True)), None)


def _party_file(state_dir):
    return os.path.join(state_dir, "party.json")


def party_state(state_dir, now=None):
    """The party running now ({started, until, open, guests}), or None."""
    data = json_file.read(_party_file(state_dir))
    try:
        until = float((data or {}).get("until") or 0)
    except (TypeError, ValueError):
        return None
    return data if until > (now or time.time()) else None


def start_party(state_dir, hours, guests, now=None):
    """`hours` 0 is until stopped."""
    now = now or time.time()
    data = {"started": now, "until": now + (hours * 3600 if hours else PARTY_MAX_SEC),
            "open": not hours, "guests": bool(guests)}
    path = _party_file(state_dir)
    with json_file.lock(path):
        json_file.write(path, data)
    return data


def end_party(state_dir):
    path = _party_file(state_dir)
    with json_file.lock(path):
        if not os.path.exists(path):
            return False
        os.remove(path)
    return True


def voice_path(state_dir, schedule_id):
    """Where a schedule's recorded message lives, or None for an id that cannot name a file."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(schedule_id or ""))[:64]
    return os.path.join(state_dir, "schedule-voices", safe + ".wav") if safe else None


def voice(state_dir, schedule_id):
    found = voice_path(state_dir, schedule_id)
    return found if found and os.path.isfile(found) else None


def end_voice_path(state_dir, schedule_id, minutes):
    """Where the message recorded for `minutes` before the schedule's end lives."""
    return voice_path(state_dir, "%s-end-%d" % (schedule_id, int(minutes)))


def end_voice(state_dir, schedule_id, minutes):
    found = end_voice_path(state_dir, schedule_id, minutes)
    return found if found and os.path.isfile(found) else None


def remove_voices(state_dir, schedule_id):
    """Every recording of a schedule: its message and those before its end."""
    for path in [voice_path(state_dir, schedule_id)] + [
            end_voice_path(state_dir, schedule_id, minutes) for minutes in range(END_MINUTES_MAX + 1)]:
        if path and os.path.isfile(path):
            os.remove(path)


def delete(schedule_id, path=None):
    with config_document.lock(path):
        items = _strict(path)
        remaining = [item for item in items if item["id"] != schedule_id]
        if len(remaining) == len(items):
            raise KeyError(schedule_id)
        _write(path, remaining)


def reorder(order, path=None):
    """Puts the schedules named in `order` first, in that order; the first of
    the list wins when two weekly schedules overlap."""
    if not isinstance(order, (list, tuple)):
        raise ValueError("schedule_bad_order")
    wanted = [str(one) for one in order]
    with config_document.lock(path):
        items = _strict(path)
        by_id = {item["id"]: item for item in items}
        first = [by_id[one] for one in dict.fromkeys(wanted) if one in by_id]
        rest = [item for item in items if item["id"] not in wanted]
        _write(path, first + rest)
        return first + rest


def save_all(items, path=None):
    """Replaces every schedule (a configuration import)."""
    with config_document.lock(path):
        _write(path, items)


def _clock(text):
    hour, minute = text.split(":")
    return timedelta(hours=int(hour), minutes=int(minute))


def day_matches(item, day):
    """A date beats the weekdays; neither means every day."""
    if item.get("date"):
        if not item.get("yearly"):
            return item["date"] == day.isoformat()
        month, date = int(item["date"][5:7]), int(item["date"][8:10])
        if (month, date) == (2, 29) and not calendar.isleap(day.year):
            date = 28          # a 29 February comes on the 28th in the other years
        return (day.month, day.day) == (month, date)
    days = item.get("days") or []
    return not days or day.weekday() in days


def _midnight(now):
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def start_due(item, now):
    """This very minute is the schedule's start."""
    start = item.get("start")
    return bool(start) and now.strftime("%H:%M") == start and day_matches(item, now.date())


def stop_due(item, now):
    """This very minute is the schedule's stop. A stop at or before the start
    belongs to the day after the one the schedule started on."""
    stop, start = item.get("stop"), item.get("start")
    if not stop or now.strftime("%H:%M") != stop:
        return False
    day = now.date()
    if start and _clock(stop) <= _clock(start):
        day -= timedelta(days=1)
    return day_matches(item, day)


def window(item, now):
    """(begin, end) of the run `now` falls in, or None. Without a stop the
    schedule runs to the end of its day; without a start it is only a stop."""
    start = item.get("start")
    if not start:
        return None
    for back in (0, 1):
        day = _midnight(now) - timedelta(days=back)
        if not day_matches(item, day.date()):
            continue
        begin = day + _clock(start)
        if item.get("stop"):
            end = day + _clock(item["stop"])
            if end <= begin:
                end += timedelta(days=1)
        else:
            end = day + timedelta(days=1)
        if begin <= now < end:
            return begin, end
    return None


def active(items, now):
    """The schedule running now: one set for a date before the weekly ones,
    then the first of the list."""
    running = []
    for index, item in enumerate(items or []):
        if not item.get("enabled", True):
            continue
        found = window(item, now)
        if found:
            running.append((0 if item.get("date") else 1, index, item, found))
    if not running:
        return None
    _, _, item, (begin, end) = min(running, key=lambda entry: entry[:2])
    return dict(item, begin=begin, end=end)


def overrides(item):
    """The schedule's settings as the typed values the daemon's config holds."""
    out = {}
    for key, raw in ((item or {}).get("settings") or {}).items():
        setting = config_schema.find_setting(key)
        if setting is not None and setting.env in OVERRIDABLE:
            out[setting.env] = config_schema.coerce(setting, raw)
    return out


def next_start(items, now, horizon_days=8):
    """(item, when) of the next start to come, or None."""
    best = None
    for item in items or []:
        if not item.get("enabled", True) or not item.get("start"):
            continue
        for ahead in range(horizon_days):
            day = _midnight(now) + timedelta(days=ahead)
            when = day + _clock(item["start"])
            if when > now and day_matches(item, day.date()):
                if best is None or when < best[1]:
                    best = (item, when)
                break
    return best


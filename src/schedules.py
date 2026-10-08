"""Schedules: start the music, stop it, or both, on chosen days or one date, with settings that
only hold while the schedule runs."""

import logging
import re
import time
from datetime import date as date_type, timedelta

import config_schema
import json_file

log = logging.getLogger("schedules")

STOP_ACTIONS = ("pause", "standby", "poweroff")
NAME_MAX = 40
VALUE_MAX = 200
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_SECTIONS = ("playback", "fades", "buttons", "schedule", "playback_errors", "audio")
# What a schedule starts with is the schedule's own; the rest is read by another process.
_NOT_OVERRIDABLE = frozenset({
    "MUSIC_START_MODE", "MUSIC_START_HOUR", "MUSIC_START_MINUTE",
    "UPCOMING_TRACKS_COUNT", "RECENT_TRACKS_COUNT", "MUSIC_KEEP_PROGRESS", "MUSIC_RESUME_MODE",
}) | config_schema.RESTART_REQUIRED | config_schema.GPIO_BUTTON_SETTINGS

_BY_ENV = {s.env: s for s in config_schema.SETTINGS}
OVERRIDABLE = frozenset(s.env for s in config_schema.SETTINGS
                        if s.section in _SECTIONS and s.env not in _NOT_OVERRIDABLE)


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "schedule"


def read_items(path):
    """Every schedule in the order of the file, or None when it cannot be read."""
    data = json_file.read(path)
    if data is None:
        return None
    items = data.get("schedules")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def load(path):
    return read_items(path) or []


def _write(path, items):
    json_file.write(path, {"schedules": items})


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
        setting = _BY_ENV.get(str(key))
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
        clean[setting.env] = text
    return clean


def validate(data):
    """Raises ValueError carrying an error CODE, like the other JSON stores."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("schedule_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("schedule_name_too_long")

    start, stop = _time(data.get("start")), _time(data.get("stop"))
    if not start and not stop:
        raise ValueError("schedule_no_time")
    if start and start == stop:
        raise ValueError("schedule_bad_time")

    day = str(data.get("date") or "").strip() or None
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
    if announcement and (len(announcement) > 80 or not re.match(r"^[a-z0-9-]+$", announcement)):
        raise ValueError("schedule_bad_announcement")

    return {
        "name": name,
        "enabled": bool(data.get("enabled", True)),
        "date": day,
        "days": [] if day else days,
        "start": start,
        "stop": stop,
        "stop_action": action,
        "list": music,
        "announcement": announcement if start else None,
        "settings": _settings(data.get("settings")),
    }


def add(path, data):
    clean = validate(data)
    with json_file.lock(path):
        items = _strict(path)
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
    """A file that cannot be read is never replaced by an empty one."""
    items = read_items(path)
    if items is None:
        raise ValueError("file_unreadable")
    return items


def update(path, schedule_id, data):
    """Partial update: fields left out of `data` keep their current value."""
    with json_file.lock(path):
        items = _strict(path)
        for i, item in enumerate(items):
            if item["id"] == schedule_id:
                merged = dict(item)
                merged.update(data)
                clean = validate(merged)
                clean["id"] = schedule_id
                clean["created_at"] = item.get("created_at", time.time())
                items[i] = clean
                _write(path, items)
                return clean
    raise KeyError(schedule_id)


def delete(path, schedule_id):
    with json_file.lock(path):
        items = _strict(path)
        remaining = [item for item in items if item["id"] != schedule_id]
        if len(remaining) == len(items):
            raise KeyError(schedule_id)
        _write(path, remaining)


def reorder(path, order):
    """Puts the schedules named in `order` first, in that order; the first of
    the list wins when two weekly schedules overlap."""
    if not isinstance(order, (list, tuple)):
        raise ValueError("schedule_bad_order")
    wanted = [str(one) for one in order]
    with json_file.lock(path):
        items = _strict(path)
        by_id = {item["id"]: item for item in items}
        first = [by_id[one] for one in dict.fromkeys(wanted) if one in by_id]
        rest = [item for item in items if item["id"] not in wanted]
        _write(path, first + rest)
        return first + rest


def save_all(path, items):
    """Replaces every schedule (a configuration import)."""
    with json_file.lock(path):
        _write(path, items)


def _clock(text):
    hour, minute = text.split(":")
    return timedelta(hours=int(hour), minutes=int(minute))


def day_matches(item, day):
    """A date beats the weekdays; neither means every day."""
    if item.get("date"):
        return item["date"] == day.isoformat()
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
        setting = _BY_ENV.get(key)
        if setting is not None and key in OVERRIDABLE:
            out[key] = config_schema.coerce(setting, raw)
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


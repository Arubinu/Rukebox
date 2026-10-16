"""The daily volume curve: a share of the chosen volume, hour by hour.

The curve is the `volume_curve` block of rukebox.yaml: points on a 24-hour
clock, and the level between two of them is the straight line from one to the
other. Two points at the same minute make a cut - a rise or a fall with no
fade at all - and the line from the last point of the day back to the first
one crosses midnight, so a curve may start at 21:00 and end at 07:00 the next
morning without anything special.

The maths lives here, away from the daemon, so the interface and the tests can
ask the same questions: what the level is at a time, where the changes are,
and when the next point comes.
"""

import logging

import config_document

log = logging.getLogger("volume_curve")

BLOCK = "volume_curve"
MINUTES_PER_DAY = 24 * 60
# What the curve gives, applied by the daemon over the volume chosen.
LEVEL_MIN = 0
LEVEL_MAX = 100
POINTS_MAX = 48


def _minute(value):
    try:
        minute = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return minute if 0 <= minute < MINUTES_PER_DAY else None


def _level(value):
    try:
        level = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(LEVEL_MIN, min(LEVEL_MAX, level))


def clean_points(raw):
    """The `points` of the block as [(minute, level)], in clock order.

    The order of two points at the same minute is kept as the file holds them:
    the second one is where the cut lands. An entry that makes no sense is
    dropped rather than refused, like every other list the interface writes.
    """
    if not isinstance(raw, list):
        return []
    points = []
    for item in raw:
        if isinstance(item, dict):
            minute, level = _minute(item.get("at")), _level(item.get("level"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            minute, level = _minute(item[0]), _level(item[1])
        else:
            continue
        if minute is None or level is None:
            continue
        points.append((minute, level))
    points.sort(key=lambda one: one[0])
    return points[:POINTS_MAX]


def load(path=None):
    """The points of the curve: [] when there is none, None when the document
    cannot be read."""
    data = config_document.read(BLOCK, path)
    if data is None:
        return None
    return clean_points((data or {}).get("points"))


def save(points, path=None):
    """Replaces the curve."""
    config_document.write(BLOCK, {"points": [{"at": minute, "level": level}
                                             for minute, level in clean_points(points)]}, path)


def level_at(points, minute):
    """The level the curve gives at that minute, or None when it has no point."""
    if not points:
        return None
    minute = int(minute) % MINUTES_PER_DAY
    for index, (at, level) in enumerate(points):
        nxt, next_level = points[(index + 1) % len(points)]
        span = (nxt - at) % MINUTES_PER_DAY
        if span == 0:
            continue          # a cut: the second point of the pair is the level
        walked = (minute - at) % MINUTES_PER_DAY
        if walked < span:
            return round(level + (next_level - level) * walked / span, 3)
    return float(points[-1][1])


def is_cut(points, minute):
    """Whether the level changes at that minute with no fade at all."""
    minute = int(minute) % MINUTES_PER_DAY
    return sum(1 for at, _level in points if at == minute) > 1


def next_point(points, minute):
    """The next minute the curve names strictly after that one, or None."""
    if not points:
        return None
    minute = int(minute) % MINUTES_PER_DAY
    ahead = sorted({(at - minute) % MINUTES_PER_DAY for at, _level in points} - {0})
    return ahead[0] if ahead else None


def changes(points):
    """[(at, level, from_at, seconds)] for every segment where the level moves.

    A segment of 0 seconds is a cut; the ones that hold the level still are not
    worth a line in a summary, and neither is a day with no point at all.
    """
    out = []
    for index, (at, level) in enumerate(points):
        previous_at, previous_level = points[index - 1]
        span = (at - previous_at) % MINUTES_PER_DAY
        if previous_level == level and span:
            continue
        out.append({"at": at, "level": level, "from": previous_level,
                    "from_at": previous_at, "to": level, "seconds": span * 60})
    return out


def describe(points):
    """The curve in words, for the interface: each change as a line.

    `rise` when the level goes up, `fall` when it goes down, `cut` when the
    seconds are 0 - a change of level in the blink of an eye.
    """
    lines = []
    for change in changes(points):
        if change["seconds"] == 0:
            kind = "cut"
        elif change["to"] > change["from"]:
            kind = "rise"
        else:
            kind = "fall"
        lines.append(dict(change, kind=kind))
    return lines

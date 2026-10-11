"""A blind test: an extract plays, everyone with the page open picks the song among four, and
the right answers score - the fastest one scores twice. Played aloud (mode "oral"), the answers
are called out in the room and the host marks, if anyone, who found it."""

import random
import re
import threading
import time

import json_file
import library

CHOICES = 4
REVEAL_SEC = 6
ROUNDS = (5, 10, 15, 20)
CLIP_SECONDS = (10, 15, 20, 30)
VIEWER_SEC = 5
JOIN_SEC = 45
MODES = ("phones", "oral")
PACES = ("auto", "host")
ORAL_NAMES = 12
ORAL_REVEAL_SEC = 10
# What a round may ask about the extract; "year" is asked as a decade.
KINDS = ("song", "artist", "year", "genre", "album")
# With the hint, a right answer before it is worth more than one after it.
HINT_POINTS = {"before": 2, "fastest": 3, "after": 1}
ASKED_KEPT = 2000


def label(track):
    return "%s - %s" % (track["title"], track["artist"])


def genres(track):
    """The genres a track is tagged with ("Rock; Pop" is both)."""
    return [g.strip() for g in re.split(r"[;,/]", str(track.get("genre") or "")) if g.strip()]


def year_of(track):
    match = re.match(r"\d{4}", str(track.get("year") or ""))
    return int(match.group(0)) if match else None


def decade(track):
    year = year_of(track)
    return year // 10 * 10 if year and 1900 <= year <= 2099 else None


def fact(track, kind):
    """What `kind` asks about `track`, or None when its tags do not say."""
    if kind == "song":
        return label(track)
    if kind == "artist":
        return track.get("artist") or None
    if kind == "album":
        return (track.get("album") or "").strip() or None
    if kind == "genre":
        return (genres(track) or [None])[0]
    if kind == "year":
        return decade(track)
    return None


def check(tracks, kinds, rounds):
    """(the tracks a game asking `kinds` can use, None), or (None, an error code): a kind whose
    tags are too scarce for four different choices, or too few tracks for the rounds."""
    usable = []
    for kind in kinds:
        have = [t for t in tracks if fact(t, kind) is not None]
        values = {library.fold(str(fact(t, kind))) for t in have}
        # Decades need only one known year: the wrong ones can be any other decade. Songs are
        # counted with the rounds below.
        if kind != "song" and (not have or (kind != "year" and len(values) < CHOICES)):
            return None, "game_too_few_" + kind
        usable.append(kind)
    kept = [t for t in tracks if any(fact(t, kind) is not None for kind in usable)]
    if len(kept) < max(CHOICES, rounds):
        return None, "game_not_enough_tracks"
    return kept, None


def asked(path):
    """The paths of the songs already asked, oldest first."""
    doc = json_file.read(path) or {}
    paths = doc.get("paths")
    return [str(p) for p in paths] if isinstance(paths, list) else []


def remember(path, track_path):
    with json_file.lock(path):
        paths = [p for p in asked(path) if p != track_path] + [track_path]
        json_file.write(path, {"paths": paths[-ASKED_KEPT:]})


def forget(path):
    with json_file.lock(path):
        json_file.write(path, {"paths": []})


def oral_id(name):
    """The person a name typed for a game aloud stands for."""
    return "name:" + library.fold(name)[:40]


class Game:
    def __init__(self, tracks, rounds=10, clip_sec=20, rng=None, join_sec=JOIN_SEC, now=None,
                 mode="phones", pace="auto", names=None, kinds=None, hint=False, asked_before=None,
                 remember=False):
        self.rng = rng or random.Random()
        self.rounds = rounds
        self.clip_sec = clip_sec
        self.mode = mode if mode in MODES else MODES[0]
        self.pace = pace if pace in PACES else PACES[0]
        self.kinds = [k for k in KINDS if k in (kinds or ["song"])] or ["song"]
        # The hint shows on the phones, halfway through the extract.
        self.hint = bool(hint) and self.mode == "phones"
        self.remember = bool(remember)
        # The host's taps: cut the extract short, move on, and who found it (in the order marked).
        self.cut = False
        self.advance = False
        self.marks = []
        self.think_until = None
        self._lock = threading.RLock()
        seen, self.tracks = set(), []
        for track in tracks:
            key = library.fold(track["title"])
            if key and key not in seen:
                seen.add(key)
                self.tracks.append(track)
        self.rng.shuffle(self.tracks)
        if asked_before:
            # The songs never asked first (still shuffled), then those asked longest ago.
            rank = {p: i for i, p in enumerate(asked_before)}
            self.tracks.sort(key=lambda t: rank.get(t["path"], -1))
        # Before the first round, everyone looking says whether they play or watch.
        self.state = "joining"
        self.join_until = (now if now is not None else time.monotonic()) + join_sec
        self.go_now = False
        self.players = set()
        self.spectators = set()
        self.round = 0
        self.question = None
        self.answers = {}
        self.reveal = None
        self.scores = {}
        self.names = {}
        self.viewers = {}
        self.error = None
        self.stopped = False
        self.ended_at = None
        if self.mode == "oral":
            # Nobody to wait for: the names, if any, are only there to keep a score.
            self.go_now = True
            for name in names or []:
                person = oral_id(name)
                if person != "name:" and person not in self.players:
                    self.players.add(person)
                    self.names[person] = name
                    self.scores[person] = 0

    @staticmethod
    def playable(tracks, clip_sec):
        """The library rows a question can be made of."""
        return [t for t in tracks if t.get("title") and t.get("artist")
                and (t.get("duration") or 0) >= clip_sec + 15]

    def enough(self):
        return len(self.tracks) >= max(CHOICES, self.rounds)

    def next_question(self, now=None):
        """The next extract and its four choices, or None when the game is over."""
        with self._lock:
            if self.stopped or self.round >= self.rounds or self.round >= len(self.tracks):
                return None
            track = self.tracks[self.round]
            self.round += 1
            kind = self.rng.choice([k for k in self.kinds if fact(track, k) is not None] or ["song"])
            choices, answer = self._choices(track, kind)
            duration = float(track.get("duration") or 0)
            start = min(max(duration * 0.3, 10.0), max(0.0, duration - self.clip_sec - 10))
            self.question = {
                "path": track["path"],
                "start": round(start, 1),
                "kind": kind,
                "choices": choices,
                "answer": answer,
                "fact": fact(track, kind),
                "title": track["title"],
                "artist": track["artist"],
                "album": fact(track, "album"),
                "genre": fact(track, "genre"),
                "year": year_of(track),
                "started": now if now is not None else time.monotonic(),
            }
            self.answers = {}
            self.reveal = None
            self.cut = self.advance = False
            self.marks = []
            self.think_until = None
            self.state = "playing"
            return self.question

    def _choices(self, track, kind):
        """Four choices for `kind` about `track`, and the index of the right one."""
        if kind == "song":
            decoys = [t for t in self.tracks if t is not track]
            other_artists = [t for t in decoys if library.fold(t["artist"]) != library.fold(track["artist"])]
            pool = other_artists if len(other_artists) >= CHOICES - 1 else decoys
            picked = self.rng.sample(pool, CHOICES - 1) + [track]
            self.rng.shuffle(picked)
            return [label(t) for t in picked], picked.index(track)
        right = fact(track, kind)
        if kind == "year":
            known = sorted({d for d in (decade(t) for t in self.tracks) if d is not None and d != right})
            near = [right + step for step in (-10, 10, -20, 20, -30, 30) if 1900 <= right + step <= 2090]
            pool = known if len(known) >= CHOICES - 1 else sorted(set(known) | set(near))
            picked = self.rng.sample(pool, CHOICES - 1) + [right]
            self.rng.shuffle(picked)
            return [str(d) for d in picked], picked.index(right)
        # A genre the song also has is no wrong answer: "Rock; Pop" asked as Rock never offers Pop.
        taken = {library.fold(g) for g in genres(track)} if kind == "genre" else {library.fold(right)}
        pool, seen = [], set(taken)
        for other in self.tracks:
            value = fact(other, kind)
            if value is not None and library.fold(value) not in seen:
                seen.add(library.fold(value))
                pool.append(value)
        picked = self.rng.sample(pool, min(CHOICES - 1, len(pool))) + [right]
        self.rng.shuffle(picked)
        return picked, picked.index(right)

    def hint_text(self, now=None):
        """The hint, once halfway through the extract: {"letter"} or {"era": "before"|"after"}."""
        if not self.hint or self.state != "playing" or not self.question:
            return None
        now = now if now is not None else time.monotonic()
        if now - self.question["started"] < self.clip_sec / 2:
            return None
        if self.question["kind"] == "year":
            return {"era": "before" if self.question["fact"] < 2000 else "after"}
        word = self.question["title"] if self.question["kind"] == "song" else str(self.question["fact"])
        letter = next((c for c in word if c.isalnum()), "?")
        return {"letter": letter.upper()}

    def think(self, seconds, now=None):
        """Aloud: the extract is over, the room thinks before the answer is said."""
        with self._lock:
            self.state = "thinking"
            self.think_until = (now if now is not None else time.monotonic()) + seconds

    def tell(self):
        """Aloud: the answer is said; the host may now mark who found it."""
        with self._lock:
            if not self.question:
                return
            self.reveal = {"answer": self.question["answer"], "gains": {}, "fastest": None, "right": []}
            self.state = "reveal"

    def mark(self, name, on):
        """Aloud: `name` found it (or not after all). An error code, or None."""
        with self._lock:
            person = oral_id(str(name or ""))
            if self.mode != "oral" or self.state != "reveal":
                return "game_not_asking"
            if person not in self.players:
                return "not_found"
            if on and person not in self.marks:
                self.marks.append(person)
            elif not on and person in self.marks:
                self.marks.remove(person)
            return None

    def score_marks(self):
        """Aloud: the round's points, from the host's marks - the first one marked was the fastest."""
        with self._lock:
            if self.mode != "oral" or not self.reveal or self.reveal.get("scored"):
                return
            gains = {p: (2 if i == 0 else 1) for i, p in enumerate(self.marks)}
            for person, points in gains.items():
                self.scores[person] = self.scores.get(person, 0) + points
            self.reveal.update(gains=gains, right=list(self.marks), scored=True,
                               fastest=self.marks[0] if self.marks else None)

    def join(self, person, name, play):
        """`person` plays (True) or watches (False); one may change one's mind at any time."""
        with self._lock:
            if name:
                self.names[person] = name
            if play:
                self.spectators.discard(person)
                self.players.add(person)
                self.scores.setdefault(person, 0)
            else:
                self.players.discard(person)
                self.spectators.add(person)

    def merge(self, was, now):
        """Two devices just became one person: `was` goes on under `now`, its points added,
        a single answer kept for the round (the first one given)."""
        if not was or not now or was == now:
            return
        with self._lock:
            if was in self.players:
                self.players.discard(was)
                self.spectators.discard(now)
                self.players.add(now)
            elif was in self.spectators:
                self.spectators.discard(was)
                if now not in self.players:
                    self.spectators.add(now)
            if was in self.scores:
                self.scores[now] = self.scores.get(now, 0) + self.scores.pop(was)
            if was in self.names:
                name = self.names.pop(was)
                if not self.names.get(now):
                    self.names[now] = name
            if was in self.answers:
                given = self.answers.pop(was)
                if now not in self.answers or given[1] < self.answers[now][1]:
                    self.answers[now] = given
            if was in self.viewers:
                self.viewers[now] = max(self.viewers.pop(was), self.viewers.get(now, 0))
            if self.reveal:
                gains = self.reveal.get("gains") or {}
                if was in gains:
                    gains[now] = max(gains.pop(was), gains.get(now, 0))
                right = [now if p == was else p for p in self.reveal.get("right") or []]
                self.reveal["right"] = list(dict.fromkeys(right))
                if self.reveal.get("fastest") == was:
                    self.reveal["fastest"] = now

    def _looking(self, now):
        return {p for p, at in self.viewers.items() if now - at <= VIEWER_SEC}

    def ready(self, now=None):
        """The game may begin: asked to, the wait is over, or everyone looking has chosen
        with someone to play."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            if self.go_now or now >= self.join_until:
                return True
            return bool(self.players) and self._looking(now) <= (self.players | self.spectators)

    def answer(self, person, name, choice, now=None):
        """An error code, or None once the answer is taken."""
        with self._lock:
            self.names[person] = name
            if self.state != "playing" or not self.question:
                return "game_not_asking"
            if person not in self.players:
                return "game_not_player"
            if person in self.answers:
                return "game_already_answered"
            if not isinstance(choice, int) or not 0 <= choice < CHOICES:
                return "bad_request"
            now = now if now is not None else time.monotonic()
            self.answers[person] = (choice, now - self.question["started"])
            self.scores.setdefault(person, 0)
            return None

    def seen(self, person, name, now=None):
        with self._lock:
            self.viewers[person] = now if now is not None else time.monotonic()
            if name:
                self.names[person] = name

    def everyone_answered(self, now=None):
        """Every player looking at the game has answered."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            looking = self._looking(now) & self.players
            return bool(self.answers) and looking <= set(self.answers)

    def close_round(self):
        """Scores the round: one point a right answer, one more for the fastest."""
        with self._lock:
            if self.mode == "oral":
                self.tell()
                return self.reveal
            if not self.question or self.state != "playing":
                return self.reveal
            right = sorted((t, p) for p, (c, t) in self.answers.items() if c == self.question["answer"])
            gains = {p: 0 for p in self.answers}
            if self.hint:
                # Halfway is when the hint shows: from then on a right answer scores less.
                half = self.clip_sec / 2
                for t, person in right:
                    gains[person] = HINT_POINTS["before" if t < half else "after"]
                if right and right[0][0] < half:
                    gains[right[0][1]] = HINT_POINTS["fastest"]
                else:
                    right = [(None, None)] + right
            else:
                for _, person in right:
                    gains[person] = 1
                if right:
                    gains[right[0][1]] = 2
            for person, points in gains.items():
                self.scores[person] = self.scores.get(person, 0) + points
            self.reveal = {"answer": self.question["answer"], "gains": gains,
                           "fastest": right[0][1] if right else None, "right": [p for _, p in right if p]}
            self.state = "reveal"
            return self.reveal

    def right_people(self):
        """The round's right answers, fastest first: [{"person", "name"}]."""
        with self._lock:
            return [{"person": p, "name": self.names.get(p)} for p in (self.reveal or {}).get("right") or []]

    def winners(self):
        """Who has the most points (several on a tie), or nobody when no one scored."""
        with self._lock:
            best = max(self.scores.values(), default=0)
            return [{"person": p, "name": self.names.get(p)}
                    for p, pts in sorted(self.scores.items(), key=lambda kv: self.names.get(kv[0]) or "")
                    if best > 0 and pts == best]

    def podium(self):
        """Up to three places, by points (several people share a place on a tie), first place
        first: [{"points", "people": [{"person", "name"}]}]. Nobody without a point."""
        with self._lock:
            levels = sorted({pts for pts in self.scores.values() if pts > 0}, reverse=True)[:3]
            return [{"points": level,
                     "people": [{"person": p, "name": self.names.get(p)}
                                for p, pts in sorted(self.scores.items(), key=lambda kv: self.names.get(kv[0]) or "")
                                if pts == level]}
                    for level in levels]

    def finish(self, error=None):
        with self._lock:
            self.state = "over"
            self.error = error
            self.ended_at = time.monotonic()

    def stop(self):
        with self._lock:
            self.stopped = True

    def view(self, person, now=None, wins=None):
        """What `person` may see: never the answer before the reveal. `wins`: games won, by person."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            mine = self.answers.get(person)
            looking = self._looking(now)
            out = {
                "state": self.state,
                "role": "player" if person in self.players else "spectator" if person in self.spectators else None,
                "players": len(self.players),
                "spectators": len(self.spectators),
                "undecided": len(looking - self.players - self.spectators),
                "join_left": max(0, round(self.join_until - now)) if self.state == "joining" else None,
                "mode": self.mode,
                "pace": self.pace,
                "kinds": list(self.kinds),
                "kind": self.question["kind"] if self.question else None,
                "hint_on": self.hint,
                "hint": self.hint_text(now),
                "think_left": max(0, round(self.think_until - now)) if self.state == "thinking" else None,
                "round": self.round,
                "rounds": self.rounds,
                "clip_sec": self.clip_sec,
                "error": self.error,
                "answered": len(self.answers),
                "mine": mine[0] if mine else None,
                "choices": self.question["choices"] if self.question and self.mode == "phones" else [],
                "remaining": None,
                "scores": sorted(({"name": self.names.get(p) or "?", "points": pts, "me": p == person,
                                   "wins": (wins or {}).get(p, 0)}
                                  for p, pts in self.scores.items()),
                                 key=lambda s: (-s["points"], s["name"])),
            }
            if self.state == "playing" and self.question:
                out["remaining"] = max(0, round(self.clip_sec - (now - self.question["started"])))
            if self.state == "over":
                out["podium"] = [{"points": place["points"], "names": [one["name"] or "?" for one in place["people"]]}
                                 for place in self.podium()]
            if self.mode == "oral":
                out["oral_names"] = [{"name": self.names.get(p) or "?", "marked": p in self.marks,
                                      "first": bool(self.marks) and self.marks[0] == p}
                                     for p in sorted(self.players, key=lambda q: (self.names.get(q) or "").casefold())]
            if self.state in ("reveal", "over") and self.reveal and self.question:
                out["answer_label"] = label(self.question)
                out["answer_fact"] = self.question["fact"]
            if self.state in ("reveal", "over") and self.reveal:
                out["answer"] = self.reveal["answer"]
                out["gain"] = self.reveal["gains"].get(person)
                out["fastest"] = self.names.get(self.reveal["fastest"]) if self.reveal["fastest"] else None
            return out


def wins(path):
    """{person: {"wins", "name"}} of every game won, kept for good."""
    doc = json_file.read(path) or {}
    people = doc.get("people")
    return people if isinstance(people, dict) else {}


def record_wins(path, people):
    """One more win for each of `people` ([{"person", "name"}])."""
    if not people:
        return
    with json_file.lock(path):
        everyone = wins(path)
        for one in people:
            entry = everyone.setdefault(str(one["person"]), {"wins": 0, "name": None})
            entry["wins"] = int(entry.get("wins") or 0) + 1
            entry["name"] = one.get("name") or entry.get("name")
        json_file.write(path, {"people": everyone})


def carry_wins(path, was, now):
    """Linking two devices adds up their wins under the person that stays."""
    if not was or not now or was == now:
        return
    with json_file.lock(path):
        everyone = wins(path)
        gone = everyone.pop(str(was), None)
        if not gone:
            return
        entry = everyone.setdefault(str(now), {"wins": 0, "name": gone.get("name")})
        entry["wins"] = int(entry.get("wins") or 0) + int(gone.get("wins") or 0)
        json_file.write(path, {"people": everyone})

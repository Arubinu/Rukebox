"""A blind test: an extract plays, everyone with the page open picks the song among four, and
the right answers score - the fastest one scores twice. Played aloud (mode "oral"), the answers
are called out in the room and the host marks, if anyone, who found it."""

import random
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


def label(track):
    return "%s - %s" % (track["title"], track["artist"])


def oral_id(name):
    """The person a name typed for a game aloud stands for."""
    return "name:" + library.fold(name)[:40]


class Game:
    def __init__(self, tracks, rounds=10, clip_sec=20, rng=None, join_sec=JOIN_SEC, now=None,
                 mode="phones", pace="auto", names=None):
        self.rng = rng or random.Random()
        self.rounds = rounds
        self.clip_sec = clip_sec
        self.mode = mode if mode in MODES else MODES[0]
        self.pace = pace if pace in PACES else PACES[0]
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
            decoys = [t for t in self.tracks if t is not track]
            other_artists = [t for t in decoys if library.fold(t["artist"]) != library.fold(track["artist"])]
            pool = other_artists if len(other_artists) >= CHOICES - 1 else decoys
            picked = self.rng.sample(pool, CHOICES - 1) + [track]
            self.rng.shuffle(picked)
            duration = float(track.get("duration") or 0)
            start = min(max(duration * 0.3, 10.0), max(0.0, duration - self.clip_sec - 10))
            self.question = {
                "path": track["path"],
                "start": round(start, 1),
                "choices": [label(t) for t in picked],
                "answer": picked.index(track),
                "title": track["title"],
                "artist": track["artist"],
                "started": now if now is not None else time.monotonic(),
            }
            self.answers = {}
            self.reveal = None
            self.cut = self.advance = False
            self.marks = []
            self.think_until = None
            self.state = "playing"
            return self.question

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
            for _, person in right:
                gains[person] = 1
            if right:
                gains[right[0][1]] = 2
            for person, points in gains.items():
                self.scores[person] = self.scores.get(person, 0) + points
            self.reveal = {"answer": self.question["answer"], "gains": gains,
                           "fastest": right[0][1] if right else None, "right": [p for _, p in right]}
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

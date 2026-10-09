"""A blind test: an extract plays, everyone with the page open picks the song among four, and
the right answers score - the fastest one scores twice."""

import random
import threading
import time

import library

CHOICES = 4
REVEAL_SEC = 6
ROUNDS = (5, 10, 15, 20)
CLIP_SECONDS = (10, 15, 20, 30)
VIEWER_SEC = 5
JOIN_SEC = 45


def label(track):
    return "%s - %s" % (track["title"], track["artist"])


class Game:
    def __init__(self, tracks, rounds=10, clip_sec=20, rng=None, join_sec=JOIN_SEC, now=None):
        self.rng = rng or random.Random()
        self.rounds = rounds
        self.clip_sec = clip_sec
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
                "started": now if now is not None else time.monotonic(),
            }
            self.answers = {}
            self.reveal = None
            self.state = "playing"
            return self.question

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

    def finish(self, error=None):
        with self._lock:
            self.state = "over"
            self.error = error
            self.ended_at = time.monotonic()

    def stop(self):
        with self._lock:
            self.stopped = True

    def view(self, person, now=None):
        """What `person` may see: never the answer before the reveal."""
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
                "round": self.round,
                "rounds": self.rounds,
                "clip_sec": self.clip_sec,
                "error": self.error,
                "answered": len(self.answers),
                "mine": mine[0] if mine else None,
                "choices": self.question["choices"] if self.question else [],
                "remaining": None,
                "scores": sorted(({"name": self.names.get(p) or "?", "points": pts, "me": p == person}
                                  for p, pts in self.scores.items()),
                                 key=lambda s: (-s["points"], s["name"])),
            }
            if self.state == "playing" and self.question:
                out["remaining"] = max(0, round(self.clip_sec - (now - self.question["started"])))
            if self.state in ("reveal", "over") and self.reveal:
                out["answer"] = self.reveal["answer"]
                out["gain"] = self.reveal["gains"].get(person)
                out["fastest"] = self.names.get(self.reveal["fastest"]) if self.reveal["fastest"] else None
            return out

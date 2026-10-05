"""Persistent daemon state (play queue, daily triggers...), stored as JSON."""

import json
import random
import os
import threading
import time
from datetime import date

import playlist


class RadioState:
    def __init__(self, state_path):
        self.state_path = state_path
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {
                "play_queue": [],
                "last_track": None,
                "last_cutoff_trigger": None,
                "pending_cutoff": False,
                "click_bags": {},
                "chance_bags": {},
                "recent": [],
                "requests": [],
            }
        self.data.setdefault("play_queue", [])
        self.data.setdefault("last_track", None)
        self.data.setdefault("pending_cutoff", False)
        self.data.setdefault("click_bags", {})
        self.data.setdefault("chance_bags", {})
        self.data.setdefault("recent", [])
        self.data.setdefault("requests", [])
        self.data.setdefault("active_list", None)

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.state_path)

    def ensure_queue(self, all_tracks, order_mode, music_dir, keep_progress, resume_mode="next_track",
                     custom_order=None):
        """Called once at daemon startup, before the first track of the session
        is chosen."""
        with self._lock:
            if keep_progress:
                self.data["play_queue"] = [
                    t for t in self.data["play_queue"] if t in all_tracks
                ]
            else:
                self.data["play_queue"] = []
                self.data["last_track"] = None

            if not self.data["play_queue"]:
                self.data["play_queue"] = playlist.order_files(
                    all_tracks, music_dir, order_mode, custom_order)
            elif (
                keep_progress
                and resume_mode in ("same_track", "same_position")
                and self.data.get("last_track") in all_tracks
                and self.data["play_queue"][0] != self.data["last_track"]
            ):
                self.data["play_queue"].insert(0, self.data["last_track"])
            self._save()

    def set_resume_point(self, path, seconds):
        """Where a song was when it was cut short."""
        with self._lock:
            self.data["resume_point"] = {"path": path, "seconds": round(float(seconds), 1)}
            self._save()

    def resume_point(self):
        """(path, seconds) of the song that was cut short, or None."""
        point = self.data.get("resume_point")
        if not isinstance(point, dict) or not point.get("path"):
            return None
        try:
            return point["path"], float(point.get("seconds") or 0)
        except (TypeError, ValueError):
            return None

    def clear_resume_point(self):
        with self._lock:
            if self.data.get("resume_point"):
                self.data["resume_point"] = None
                self._save()

    def pop_next_track_or_none(self):
        """Pops and returns the next track, or None if the queue is currently
        empty."""
        with self._lock:
            if not self.data["play_queue"]:
                return None
            track = self.data["play_queue"].pop(0)
            self.data["last_track"] = track
            self._save()
            return track

    def rebuild_queue(self, all_tracks, order_mode, music_dir, custom_order=None):
        """Starts a fresh pass - called when the queue has just run out and
        MUSIC_LOOP is true."""
        with self._lock:
            self.data["play_queue"] = playlist.order_files(
                all_tracks, music_dir, order_mode, custom_order)
            self._save()

    def rise_head(self, count, level):
        """Orders the next `count` songs from the quietest to the loudest
        (level(path): a number, or None when unknown - those keep the end).
        Songs asked for stay first, in their own order."""
        with self._lock:
            queue = self.data["play_queue"]
            asked = set(self.data.get("requests") or [])
            start = 0
            while start < len(queue) and queue[start] in asked:
                start += 1
            head = queue[start:start + count]
            if len(head) < 2:
                return False
            known = sorted((p for p in head if level(p) is not None), key=level)
            queue[start:start + count] = known + [p for p in head if level(p) is None]
            self._save()
            return True

    def has_queued_tracks(self):
        return bool(self.data["play_queue"])

    def active_list(self):
        """The id of the music list being played, or None for the whole
        library."""
        return self.data.get("active_list") or None

    def set_active_list(self, list_id):
        with self._lock:
            self.data["active_list"] = list_id or None
            self._save()
        return self.data["active_list"]

    def add_recent(self, path, limit):
        """The music track that just started, newest first, at most `limit`
        kept (RECENT_TRACKS_COUNT)."""
        with self._lock:
            recent = list(self.data.get("recent") or [])
            if recent and isinstance(recent[0], dict) and recent[0].get("path") == path:
                recent = recent[1:]
            recent = [{"path": path, "at": time.time()}] + recent
            self.data["recent"] = recent[:max(0, int(limit or 0))]
            self._save()

    def recent_paths(self):
        """The recent tracks' paths, newest first."""
        return [r.get("path") for r in (self.data.get("recent") or [])
                if isinstance(r, dict) and r.get("path")]

    def push_front(self, path):
        """Puts `path` back at the head of the play queue."""
        with self._lock:
            queue = self.data["play_queue"]
            if not queue or queue[0] != path:
                queue.insert(0, path)
            self._save()

    def take_from_queue(self, path):
        """Removes `path` from the play queue."""
        with self._lock:
            self.data["play_queue"] = [p for p in self.data["play_queue"] if p != path]
            self.data["requests"] = [p for p in self.data["requests"] if p != path]
            self._save()

    def enqueue_request(self, path, person=None, fair=False):
        """A song asked for ("Next"). `fair`: the asked-for songs take turns
        between people - one each, then the next round - rather than first
        come, first served."""
        with self._lock:
            queue = [p for p in self.data["play_queue"] if p != path]
            wanted = set(self.data["requests"])
            count = 0
            while count < len(queue) and queue[count] in wanted:
                count += 1
            by = dict(self.data.get("request_by") or {})
            at = count
            if fair and person:
                seen, rounds = {}, []
                for p in queue[:count]:
                    who = by.get(p) or p
                    rounds.append(seen.get(who, 0))
                    seen[who] = seen.get(who, 0) + 1
                mine = seen.get(person, 0)
                at = 0
                for i, r in enumerate(rounds):
                    if r <= mine:
                        at = i + 1
            queue.insert(at, path)
            self.data["play_queue"] = queue
            self.data["requests"] = queue[:count + 1]
            by = {p: by[p] for p in self.data["requests"] if p in by}
            if person:
                by[path] = person
            self.data["request_by"] = by
            self._save()
            return at

    def set_dedication(self, path, entry):
        """A message to say before `path` plays ({"from", "text"})."""
        with self._lock:
            self.data.setdefault("dedications", {})[path] = entry
            self._save()

    def pop_dedication(self, path):
        with self._lock:
            entry = (self.data.get("dedications") or {}).pop(path, None)
            if entry is not None:
                self._save()
            return entry

    def dedications(self):
        return dict(self.data.get("dedications") or {})

    REMINDERS_MAX = 20

    def reminders(self):
        return sorted((dict(r) for r in self.data.get("reminders") or [] if isinstance(r, dict)),
                      key=lambda r: r.get("at", 0))

    def add_reminder(self, at, text):
        """A sentence to say at `at` (epoch seconds); its id, or None when
        there are too many already."""
        with self._lock:
            items = list(self.data.get("reminders") or [])
            if len(items) >= self.REMINDERS_MAX:
                return None
            rid = "%x" % int(time.time() * 1000)
            while any(r.get("id") == rid for r in items):
                rid += "x"
            items.append({"id": rid, "at": float(at), "text": text})
            self.data["reminders"] = items
            self._save()
            return rid

    def remove_reminder(self, rid):
        with self._lock:
            items = list(self.data.get("reminders") or [])
            kept = [r for r in items if r.get("id") != rid]
            self.data["reminders"] = kept
            self._save()
            return len(kept) != len(items)

    def requested_paths(self):
        """The asked-for songs still waiting."""
        queue = self.data.get("play_queue") or []
        wanted = set(self.data.get("requests") or [])
        out = []
        for p in queue:
            if p not in wanted:
                break
            out.append(p)
        return out

    def peek_next_track(self):
        """The track the queue will give next, without taking it."""
        queue = self.data.get("play_queue") or []
        return queue[0] if queue else None

    def next_click_sound(self, source_id, build):
        """The next file name for a click playing one sound of the list
        `source_id`."""
        with self._lock:
            bags = self.data.setdefault("click_bags", {})
            current = set(build())
            bag = [name for name in bags.get(source_id, []) if name in current]
            if not bag:
                bag = list(build())
            if not bag:
                return None
            name = bag.pop(0)
            bags[source_id] = bag
            self._save()
            return name

    def chance_allows(self, key, ratio):
        """Whether this trigger of `key` should play, for a ratio "k/n"."""
        try:
            k, n = (int(x) for x in str(ratio).split("/"))
        except ValueError:
            return True
        if k >= n or n <= 1:
            return True
        with self._lock:
            bags = self.data.setdefault("chance_bags", {})
            bag = bags.get(key)
            if not bag or bag.get("ratio") != ratio or not bag.get("draws"):
                draws = [True] * k + [False] * (n - k)
                random.shuffle(draws)
                bag = {"ratio": ratio, "draws": draws}
            allowed = bag["draws"].pop(0)
            bags[key] = bag
            self._save()
            return allowed

    def reset_click_bag(self, source_id=None):
        """Forgets where a list (or every list) was."""
        with self._lock:
            bags = self.data.setdefault("click_bags", {})
            if source_id is None:
                bags.clear()
            else:
                bags.pop(source_id, None)
            self._save()

    def already_triggered_today(self, key):
        today = date.today().isoformat()
        return self.data.get(key) == today

    def mark_triggered_today(self, key):
        with self._lock:
            self.data[key] = date.today().isoformat()
            self._save()

    def reset_daily_triggers_if_new_day(self):
        """Call periodically: in case the Pi stays on for several days, the
        'already_triggered_today' keys are already based on today's date so
        nothing to do, but kept for future use."""
        today = date.today().isoformat()
        if self.data.get("last_cutoff_trigger") != today:
            with self._lock:
                self.data["pending_cutoff"] = False
                self._save()

    def set_pending_cutoff(self, value: bool):
        with self._lock:
            self.data["pending_cutoff"] = value
            self.data["pending_cutoff_at"] = time.time() if value else None
            self._save()

    def pending_cutoff_age(self):
        """Seconds since the cutoff began waiting for the end of a track, None when unknown."""
        at = self.data.get("pending_cutoff_at")
        return None if not isinstance(at, (int, float)) else max(0.0, time.time() - at)

    def is_pending_cutoff(self):
        return self.data.get("pending_cutoff", False)

    def flag(self, key, default=False):
        """A plain on/off fact the daemon has to remember across restarts."""
        return self.data.get(key, default)

    def set_flag(self, key, value):
        with self._lock:
            self.data[key] = bool(value)
            self._save()

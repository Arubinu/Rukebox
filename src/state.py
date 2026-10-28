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
        # A song is in the queue once: a copy further on would play it twice in the pass.
        self.data["play_queue"] = list(dict.fromkeys(self.data["play_queue"] or []))
        self._drop_stale_requests()
        self.data.setdefault("last_track", None)
        self.data.setdefault("pending_cutoff", False)
        self.data.setdefault("click_bags", {})
        self.data.setdefault("chance_bags", {})
        self.data.setdefault("recent", [])
        self.data.setdefault("requests", [])

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

    def _drop_stale_requests(self):
        """Keeps as requests only the head of the queue: a stale one would look already waiting."""
        queue = self.data.get("play_queue") or []
        asked = set(self.data.get("requests") or [])
        kept = self.requested_paths()
        # A song cut short put back in front of them, as earlier versions left it: it heads them.
        if not kept and len(queue) > 1 and queue[1] in asked:
            kept = [queue[0]]
            for p in queue[1:]:
                if p not in asked:
                    break
                kept.append(p)
        self.data["requests"] = kept
        kept = set(kept)
        by = self.data.get("request_by") or {}
        self.data["request_by"] = {p: who for p, who in by.items() if p in kept}

    def pop_next_track_or_none(self):
        """Pops and returns the next track, or None if the queue is currently
        empty."""
        with self._lock:
            if not self.data["play_queue"]:
                return None
            track = self.data["play_queue"].pop(0)
            self.data["last_track"] = track
            # Kept for push_front(): a song asked for and cut short is asked for again.
            asked = track in (self.data.get("requests") or [])
            self.data["last_request"] = {"path": track, "by": (self.data.get("request_by") or {}).get(track)} \
                if asked else None
            self._drop_stale_requests()
            self._save()
            return track

    def rebuild_queue(self, all_tracks, order_mode, music_dir, custom_order=None, avoid=None):
        """Starts a fresh pass - called when the queue has just run out and
        MUSIC_LOOP is true. `avoid`: the song playing, kept from opening the new pass."""
        with self._lock:
            queue = playlist.order_files(all_tracks, music_dir, order_mode, custom_order)
            if avoid and len(queue) > 1 and queue[0] == avoid:
                queue = queue[1:] + queue[:1]
            self.data["play_queue"] = queue
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

    def selection(self):
        """{"play": [list ids], "exclude": [list ids]}: what the radio plays by
        itself (no list: the whole library), minus what the excluded lists hold."""
        found = self.data.get("selection")
        if not isinstance(found, dict):
            # Written by a version that played one list at most.
            old = self.data.get("active_list")
            found = {"play": [old] if old else [], "exclude": []}
        return {key: [str(i) for i in found.get(key) or [] if i] for key in ("play", "exclude")}

    def set_selection(self, play=None, exclude=None):
        """Changes the lists played and/or excluded (None leaves that side alone)."""
        with self._lock:
            now = self.selection()
            if play is not None:
                now["play"] = list(dict.fromkeys(str(i) for i in play if i))
            if exclude is not None:
                now["exclude"] = list(dict.fromkeys(str(i) for i in exclude if i))
            self.data["selection"] = now
            self.data.pop("active_list", None)
            self._save()
        return self.selection()

    def active_list(self):
        """The first list being played, or None for the whole library."""
        play = self.selection()["play"]
        return play[0] if play else None

    def set_active_list(self, list_id):
        """Plays one list (None: the whole library); the exclusions stay."""
        play = self.set_selection(play=[list_id] if list_id else [])["play"]
        return play[0] if play else None

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
        """Puts `path` back at the head of the play queue, and out of the rest of it:
        a pass drawn while it played holds it too."""
        with self._lock:
            waiting = [p for p in self.requested_paths() if p != path]
            self.data["play_queue"] = [path] + [p for p in self.data["play_queue"] if p != path]
            last = self.data.get("last_request") or {}
            # Asked for, it is asked for again; a pass song in front of songs asked for counts as
            # one too, or they would no longer head the queue.
            if last.get("path") == path or waiting:
                self.data["requests"] = [path] + [p for p in self.data["requests"] if p != path]
                if last.get("path") == path and last.get("by"):
                    self.data.setdefault("request_by", {})[path] = last["by"]
            self._save()

    def take_from_queue(self, path):
        """Removes `path` from the play queue."""
        self.take_from_queue_many([path])

    def take_from_queue_many(self, paths):
        """Removes several paths at once, for one write: what excluding a
        track needs - the rest of the pass, and the songs asked for, must not
        move. Returns how many were still in the queue."""
        wanted = {p for p in (paths or []) if p}
        if not wanted:
            return 0
        with self._lock:
            queue = list(self.data["play_queue"])
            kept = [p for p in queue if p not in wanted]
            if len(kept) == len(queue):
                return 0
            self.data["play_queue"] = kept
            self.data["requests"] = [p for p in self.data["requests"] if p not in wanted]
            self._save()
            return len(queue) - len(kept)

    def move_in_queue(self, path, after):
        """Puts `path` after `after` ("": first); its new place, or None when either is gone."""
        # Among the requests it becomes one, past them it stops being one, at their edge it stays.
        with self._lock:
            queue = list(self.data["play_queue"])
            if path not in queue or (after and (after == path or after not in queue)):
                return None
            asked = set(self.data["requests"])
            was_asked = path in asked
            asked.discard(path)
            queue.remove(path)
            at = queue.index(after) + 1 if after else 0
            count = 0
            while count < len(queue) and queue[count] in asked:
                count += 1
            if at < count or (at == count and was_asked):
                asked.add(path)
            queue.insert(at, path)
            self.data["play_queue"] = queue
            self.data["requests"] = [p for p in queue if p in asked]
            if path not in asked:
                (self.data.get("request_by") or {}).pop(path, None)
            self._save()
            return at

    def enqueue_requests(self, paths, person=None):
        """Several requests at once, after the ones waiting; how many were added."""
        with self._lock:
            asked = set(self.data["requests"])
            wanted = [p for p in dict.fromkeys(paths) if p not in asked]
            if not wanted:
                return 0
            taken = set(wanted)
            queue = [p for p in self.data["play_queue"] if p not in taken]
            count = 0
            while count < len(queue) and queue[count] in asked:
                count += 1
            queue[count:count] = wanted
            self.data["play_queue"] = queue
            self.data["requests"] = queue[:count + len(wanted)]
            by = {p: who for p, who in (self.data.get("request_by") or {}).items() if p in asked}
            if person:
                by.update((p, person) for p in wanted)
            self.data["request_by"] = by
            self._save()
            return len(wanted)

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

    # What the radio works out by itself, which erasing a run puts back as it was at its start.
    RUN_MEMORY = (("play_queue", list), ("requests", list), ("recent", list),
                  ("request_by", dict), ("click_bags", dict), ("chance_bags", dict),
                  ("last_track", None), ("last_request", None), ("resume_point", None))
    # The day's cutoff is neither put back nor taken away: erasing must not arm it
    # again, nor drop one already waiting for the end of a track.
    RUN_KEEP = ("last_cutoff_trigger",)

    def _day_markers(self):
        """The keys holding today's date: what the radio did once today, so far."""
        today = date.today().isoformat()
        return {key: value for key, value in self.data.items()
                if value == today and key not in self.RUN_KEEP}

    def run_memory(self):
        """A copy of what the radio worked out by itself, for a run that may have
        to be erased: it is put back to this, exactly."""
        with self._lock:
            memory = {}
            for key, empty in self.RUN_MEMORY:
                value = self.data.get(key)
                if value is None and empty is not None:
                    value = empty()
                memory[key] = value
            return json.loads(json.dumps({"memory": memory, "days": self._day_markers()}))

    def restore_run(self, memory):
        """Puts the radio's own memory back to `memory` (run_memory()); False when there is nothing."""
        if not isinstance(memory, dict) or "memory" not in memory:
            return False
        with self._lock:
            doc = json.loads(json.dumps(memory))  # well apart from the live one
            for key, empty in self.RUN_MEMORY:
                value = doc["memory"].get(key)
                if value is None and empty is not None:
                    value = empty()
                self.data[key] = value
            self.data["play_queue"] = list(dict.fromkeys(self.data["play_queue"]))
            self._drop_stale_requests()
            for key in self._day_markers():
                self.data.pop(key, None)
            self.data.update(doc.get("days") or {})
            self._save()
        return True

    def forget_run(self):
        """Puts the radio's own memory back to nothing, for a run whose memory was never kept."""
        with self._lock:
            for key, empty in self.RUN_MEMORY:
                self.data[key] = empty() if empty is not None else None
            for key in self._day_markers():
                self.data.pop(key, None)
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

    ASLEEP_KEY = "asleep_at_stop"

    def note_asleep(self, boot_id):
        """The service stops while the radio sleeps: the next start of the same boot sleeps on."""
        self.set_value(self.ASLEEP_KEY, {"boot": boot_id, "at": time.time()})

    def take_asleep(self, boot_id, within):
        """Whether this start follows a stop while asleep, in this boot and within `within`
        seconds; the note is used once either way."""
        note = self.data.get(self.ASLEEP_KEY)
        if note is None:
            return False
        self.set_value(self.ASLEEP_KEY, None)
        if not isinstance(note, dict) or note.get("boot") != boot_id:
            return False
        try:
            return 0 <= time.time() - float(note.get("at") or 0) <= within
        except (TypeError, ValueError):
            return False

    def value(self, key, default=None):
        """A plain fact of any kind the daemon remembers across restarts."""
        return self.data.get(key, default)

    def set_value(self, key, value):
        with self._lock:
            self.data[key] = value
            self._save()

"""Suggestion box: devices, reserved names, suggestions and votes (SQLite)."""

import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata

KINDS = ("music", "announcement")
STATUSES = ("open", "added", "declined")
TEXT_MIN = 2
TEXT_MAX = {"music": 200, "announcement": 500}
NAME_MAX = 24
NAME_KEY_MIN = 2
SUGGESTIONS_PER_DAY = 20
DAY = 86400
SEEN_WRITE_SEC = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    mac TEXT,
    name TEXT,
    first_seen REAL,
    last_seen REAL,
    last_ip TEXT
);
CREATE INDEX IF NOT EXISTS devices_mac ON devices(mac);
CREATE TABLE IF NOT EXISTS names (
    key TEXT PRIMARY KEY,
    display TEXT NOT NULL,
    device_id TEXT NOT NULL,
    claimed_at REAL
);
CREATE TABLE IF NOT EXISTS name_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    old_name TEXT,
    new_name TEXT,
    at REAL,
    mac TEXT,
    ip TEXT,
    by_owner INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    text_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at REAL,
    status TEXT NOT NULL DEFAULT 'open'
);
-- Every MAC a device was seen with: phones randomise theirs, and a ban
-- must follow the device, not one address.
CREATE TABLE IF NOT EXISTS device_macs (
    device_id TEXT NOT NULL,
    mac TEXT NOT NULL,
    last_seen REAL,
    PRIMARY KEY (device_id, mac)
);
CREATE INDEX IF NOT EXISTS device_macs_mac ON device_macs(mac);
-- What the owner decided about a device: banned until (-1 = for good),
-- its captive portal (always / never; none = the general rule), whether
-- its current name was generated (free to change at once), whether it is
-- spared the guest credits (free_credits), and whether the owner pinned
-- its name so the device may no longer change it itself (name_locked).
-- portal_released_at is the last "Finish connecting" tap: it has to
-- outlive a restart, and the Pi restarts every day.
-- linked_to is the device whose name, votes, credits and ban this one
-- shares: one person, several devices. The ban, the credits, the name and
-- its lock are read on that device's row; the portal stays each device's own.
CREATE TABLE IF NOT EXISTS device_state (
    device_id TEXT PRIMARY KEY,
    banned_until REAL,
    portal TEXT,
    generated_name INTEGER NOT NULL DEFAULT 0,
    free_credits INTEGER NOT NULL DEFAULT 0,
    name_locked INTEGER NOT NULL DEFAULT 0,
    portal_released_at REAL,
    linked_to TEXT
);
CREATE TABLE IF NOT EXISTS votes (
    suggestion_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    value INTEGER NOT NULL,
    at REAL,
    PRIMARY KEY (suggestion_id, device_id)
);
"""

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


class SuggestionError(Exception):
    """A refusal, carrying an API error code."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def mac_for_ip(ip, arp_path="/proc/net/arp"):
    """The MAC address the Pi has for this client, from its ARP table, or None."""
    if not ip:
        return None
    try:
        with open(arp_path) as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    mac = parts[3].lower()
                    if _MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
                        return mac
    except OSError:
        pass
    return None


def _clean(text, limit):
    """NFKC, no control or invisible characters, single spaces."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs"))
    text = " ".join(text.split())
    return text[:limit].strip()


def name_key(name):
    """What makes two names "the same name"."""
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(name or "")))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return "".join(ch for ch in folded.casefold() if ch.isalnum())


def _text_key(text):
    return name_key(text)


class SuggestionBox:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._db.executescript(SCHEMA)
        self._ensure_columns()
        self._db.commit()

    # CREATE TABLE IF NOT EXISTS never adds a column to a table that already exists.
    _ADDED_COLUMNS = {
        "device_state": (("free_credits", "INTEGER NOT NULL DEFAULT 0"),
                         ("name_locked", "INTEGER NOT NULL DEFAULT 0"),
                         ("portal_released_at", "REAL"),
                         ("linked_to", "TEXT")),
        "name_changes": (("by_owner", "INTEGER NOT NULL DEFAULT 0"),),
    }

    def _ensure_columns(self):
        """Adds the columns an older database does not have yet."""
        for table, columns in self._ADDED_COLUMNS.items():
            have = {row[1] for row in self._db.execute("PRAGMA table_info(%s)" % table)}
            for name, kind in columns:
                if name not in have:
                    self._db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, kind))

    def resolve_device(self, token, mac, ip, alt_token=None):
        """The device behind this request, as a dict, plus the token the
        browser should hold (a new one, or the one of the device its MAC
        belongs to)."""
        now = time.time()
        with self._lock:
            row = None
            if token:
                row = self._db.execute("SELECT * FROM devices WHERE token = ?", (token,)).fetchone()
            issue = None
            if row is None and alt_token:
                row = self._db.execute("SELECT * FROM devices WHERE token = ?", (alt_token,)).fetchone()
                if row is not None:
                    issue = row["token"]
            if row is None and mac:
                row = self._db.execute(
                    "SELECT * FROM devices WHERE mac = ? ORDER BY last_seen DESC LIMIT 1", (mac,)).fetchone()
                if row is not None:
                    issue = row["token"]
            if row is None:
                device_id = secrets.token_hex(4)
                new_token = secrets.token_hex(16)
                self._db.execute(
                    "INSERT INTO devices (id, token, mac, name, first_seen, last_seen, last_ip)"
                    " VALUES (?, ?, ?, NULL, ?, ?, ?)", (device_id, new_token, mac, now, now, ip))
                if mac:
                    self._db.execute("INSERT OR REPLACE INTO device_macs (device_id, mac, last_seen) VALUES (?, ?, ?)",
                                     (device_id, mac, now))
                self._db.commit()
                return {"id": device_id, "person": device_id, "mac": mac, "name": None, "ip": ip}, new_token
            if (mac and mac != row["mac"]) or ip != row["last_ip"] or now - (row["last_seen"] or 0) > SEEN_WRITE_SEC:
                self._db.execute("UPDATE devices SET last_seen = ?, last_ip = ?, mac = COALESCE(?, mac) WHERE id = ?",
                                 (now, ip, mac, row["id"]))
                if mac:
                    self._db.execute("INSERT OR REPLACE INTO device_macs (device_id, mac, last_seen) VALUES (?, ?, ?)",
                                     (row["id"], mac, now))
                self._db.commit()
            person = self.person_id(row["id"])
            name = row["name"] if person == row["id"] else self._name_of(person)
            return {"id": row["id"], "person": person, "mac": mac or row["mac"], "name": name, "ip": ip}, issue

    def person_id(self, device_id):
        """The device whose name, votes and credits this one shares: itself
        unless it was linked to another."""
        return self._state(device_id).get("linked_to") or device_id

    def _pid(self, device):
        return device.get("person") or self.person_id(device["id"])

    def _name_of(self, device_id):
        row = self._db.execute("SELECT name FROM devices WHERE id = ?", (device_id,)).fetchone()
        return row["name"] if row else None

    def _member_ids(self, person):
        """The person's devices, the one that carries the identity first."""
        return [person] + [r["device_id"] for r in self._db.execute(
            "SELECT s.device_id FROM device_state s JOIN devices d ON d.id = s.device_id"
            " WHERE s.linked_to = ? ORDER BY d.first_seen", (person,)).fetchall()]

    def _macs_of(self, device_ids):
        macs = []
        for device_id in device_ids:
            found = [m["mac"] for m in self._db.execute(
                "SELECT mac FROM device_macs WHERE device_id = ?", (device_id,)).fetchall()]
            row = self._db.execute("SELECT mac FROM devices WHERE id = ?", (device_id,)).fetchone()
            if row and row["mac"]:
                found.append(row["mac"])
            macs.extend(mac for mac in found if mac not in macs)
        return macs

    def linked_devices(self, device_id):
        """The other devices of the same person."""
        out = []
        for other in self._member_ids(self.person_id(device_id)):
            if other == device_id:
                continue
            row = self._db.execute("SELECT * FROM devices WHERE id = ?", (other,)).fetchone()
            if row:
                out.append({"device_id": other, "mac": row["mac"], "ip": row["last_ip"],
                            "last_seen": row["last_seen"]})
        return out

    def _move_identity(self, src, dst, with_name):
        """Caller holds the lock and commits. Everything a person owns goes
        from one device's row to another's: what `dst` already has wins,
        except a ban, which is never lost by linking."""
        db = self._db
        db.execute("UPDATE suggestions SET device_id = ? WHERE device_id = ?", (dst, src))
        db.execute("DELETE FROM votes WHERE device_id = ? AND suggestion_id IN"
                   " (SELECT suggestion_id FROM votes WHERE device_id = ?)", (src, dst))
        db.execute("UPDATE votes SET device_id = ? WHERE device_id = ?", (dst, src))
        # Nobody votes on their own suggestion, and two devices of one person could have.
        db.execute("DELETE FROM votes WHERE device_id = ? AND suggestion_id IN"
                   " (SELECT id FROM suggestions WHERE device_id = ?)", (dst, dst))
        db.execute("UPDATE names SET device_id = ? WHERE device_id = ?", (dst, src))
        db.execute("UPDATE name_changes SET device_id = ? WHERE device_id = ?", (dst, src))
        had = self._state(src)
        if with_name:
            db.execute("UPDATE devices SET name = (SELECT name FROM devices WHERE id = ?) WHERE id = ?", (src, dst))
            self._set_state(dst, banned_until=had.get("banned_until"), free_credits=had.get("free_credits") or 0,
                            name_locked=had.get("name_locked") or 0, generated_name=had.get("generated_name") or 0)
        else:
            mine, theirs = self._state(dst).get("banned_until"), had.get("banned_until")
            if theirs is not None and mine != -1 and (theirs == -1 or mine is None or theirs > mine):
                self._set_state(dst, banned_until=theirs)
        db.execute("UPDATE devices SET name = NULL WHERE id = ?", (src,))
        self._set_state(src, banned_until=None, free_credits=0, name_locked=0, generated_name=0)

    def link(self, device_id, to_id):
        """Makes two devices one person: `device_id` (and whatever was linked
        to it) takes the name, votes, credits and ban of `to_id`. Answers the
        device that now carries the identity."""
        with self._lock:
            if not self.device_by_id(device_id) or not self.device_by_id(to_id):
                raise SuggestionError("not_found")
            src, dst = self.person_id(device_id), self.person_id(to_id)
            if src == dst:
                raise SuggestionError("already_linked")
            members = self._member_ids(src)
            self._move_identity(src, dst, with_name=False)
            for member in members:
                self._set_state(member, linked_to=dst)
            self._db.commit()
            return dst

    def unlink(self, device_id):
        """Takes one device out of its person: it leaves with nothing, as a
        device the Rukebox has just met; the others keep the name and the rest."""
        with self._lock:
            if not self._detach(device_id):
                raise SuggestionError("not_linked")
            self._db.commit()

    def _detach(self, device_id):
        """Caller holds the lock and commits. False when it was linked to nothing."""
        person = self.person_id(device_id)
        members = self._member_ids(person)
        if len(members) < 2:
            return False
        if device_id == person:
            heir = members[1]
            self._move_identity(person, heir, with_name=True)
            self._set_state(heir, linked_to=None)
            for member in members[2:]:
                self._set_state(member, linked_to=heir)
        else:
            self._set_state(device_id, linked_to=None)
        return True

    def rename_wait(self, device, interval_sec):
        """Seconds before this device may change its name again. The owner's
        own renames do not count: the interval is there to stop a phone from
        flapping, not to make the owner's naming wait."""
        if not device.get("name") or interval_sec <= 0 or self.is_generated(device["id"]):
            return 0
        row = self._db.execute("SELECT MAX(at) FROM name_changes WHERE device_id = ? AND by_owner = 0",
                               (self._pid(device),)).fetchone()
        last = row[0] or 0
        return max(0, int(round(last + interval_sec - time.time())))

    def set_name(self, device, name, interval_sec=0, generated=False, by_owner=False):
        """Takes a name for a device; interval_sec is the minimum time between
        two changes. by_owner is the owner naming it from the interface, which
        the lock does not stop."""
        person = self._pid(device)
        if not by_owner and self.name_locked(person):
            raise SuggestionError("name_locked")
        display = _clean(name, NAME_MAX)
        key = name_key(display)
        if len(key) < NAME_KEY_MIN:
            raise SuggestionError("name_too_short")
        if generated and device.get("name"):
            return device["name"]
        if device.get("name") == display:
            return display
        was_generated = self.is_generated(person)
        now = time.time()
        with self._lock:
            owner = self._db.execute("SELECT device_id FROM names WHERE key = ?", (key,)).fetchone()
            if owner is not None and owner["device_id"] != person:
                raise SuggestionError("name_taken")
            wait = self.rename_wait(device, interval_sec)
            if wait > 0:
                raise SuggestionError("rename_too_soon", wait)
            if owner is None:
                self._db.execute("INSERT INTO names (key, display, device_id, claimed_at) VALUES (?, ?, ?, ?)",
                                 (key, display, person, now))
            else:
                self._db.execute("UPDATE names SET display = ? WHERE key = ?", (display, key))
            self._db.execute("UPDATE devices SET name = ? WHERE id = ?", (display, person))
            self._db.execute(
                "INSERT INTO name_changes (device_id, old_name, new_name, at, mac, ip, by_owner)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (person, device.get("name"), display, now, device.get("mac"), device.get("ip"),
                 1 if by_owner else 0))
            if was_generated and device.get("name") and name_key(device["name"]) != key:
                self._db.execute("DELETE FROM names WHERE key = ? AND device_id = ?",
                                 (name_key(device["name"]), person))
            self._set_state(person, generated_name=1 if generated else 0)
            self._db.commit()
        device["name"] = display
        return display

    def _set_state(self, device_id, **fields):
        """Caller holds the lock and commits."""
        self._db.execute("INSERT OR IGNORE INTO device_state (device_id) VALUES (?)", (device_id,))
        for key, value in fields.items():
            assert key in ("banned_until", "portal", "generated_name", "free_credits", "name_locked",
                           "portal_released_at", "linked_to")
            self._db.execute("UPDATE device_state SET %s = ? WHERE device_id = ?" % key, (value, device_id))

    def _state(self, device_id):
        row = self._db.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,)).fetchone()
        return dict(row) if row else {}

    def is_generated(self, device_id):
        return bool(self._state(self.person_id(device_id)).get("generated_name"))

    def device_by_id(self, device_id):
        row = self._db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return self._as_device(row) if row else None

    def _as_device(self, row):
        """A device row, named after its person."""
        device = dict(row)
        device["person"] = self.person_id(device["id"])
        if device["person"] != device["id"]:
            device["name"] = self._name_of(device["person"])
        return device

    def device_by_mac(self, mac):
        """The device last seen with this MAC, or None."""
        row = self._db.execute(
            "SELECT device_id FROM device_macs WHERE mac = ? ORDER BY last_seen DESC LIMIT 1", (mac,)).fetchone()
        if row is None:
            row = self._db.execute(
                "SELECT id AS device_id FROM devices WHERE mac = ? ORDER BY last_seen DESC LIMIT 1", (mac,)).fetchone()
        return self.device_by_id(row["device_id"]) if row else None

    def mac_known(self, mac):
        """This MAC has opened the page before."""
        return self.device_by_mac(mac) is not None

    def ensure_device_for_mac(self, mac, ip=None):
        """A device for a MAC that never opened the page."""
        device = self.device_by_mac(mac)
        if device:
            return device
        created, _ = self.resolve_device(None, mac, ip)
        return self.device_by_id(created["id"])

    def forget_device(self, device_id):
        """Forgets a device: its state, its addresses, the names it reserved and
        the history of them. What it suggested stays - that is music, not a
        device - and a ban goes with the rest: forgotten means the Rukebox has
        never met it, so it is greeted as a new device if it comes back."""
        with self._lock:
            self._forget(device_id)
            self._db.commit()

    def _forget(self, device_id):
        """Caller holds the lock and commits."""
        self._detach(device_id)
        self._db.execute("DELETE FROM device_state WHERE device_id = ?", (device_id,))
        self._db.execute("DELETE FROM device_macs WHERE device_id = ?", (device_id,))
        self._db.execute("DELETE FROM names WHERE device_id = ?", (device_id,))
        self._db.execute("DELETE FROM name_changes WHERE device_id = ?", (device_id,))
        self._db.execute("DELETE FROM devices WHERE id = ?", (device_id,))

    def forget_unnamed(self, keep=None):
        """Forgets every device that never took a name - a passer-by, a phone
        that only ever checked for a portal. `keep` spares one of them, the
        device doing the sweeping. Answers how many."""
        with self._lock:
            ids = [row["id"] for row in self._db.execute(
                "SELECT id FROM devices WHERE name IS NULL OR name = ''").fetchall()]
            # A linked device has no name of its own: its person's is the one it goes by.
            ids = [device_id for device_id in ids
                   if device_id != keep and not self._name_of(self.person_id(device_id))]
            for device_id in ids:
                self._forget(device_id)
            self._db.commit()
            return len(ids)

    def ban_until(self, device_id):
        """None (not banned), -1 (for good) or the time it ends."""
        until = self._state(self.person_id(device_id)).get("banned_until")
        if until is None:
            return None
        if until != -1 and until < time.time():
            return None
        return until

    def set_ban(self, device_id, until):
        with self._lock:
            self._set_state(self.person_id(device_id), banned_until=until)
            self._db.commit()

    def banned_devices(self):
        now = time.time()
        rows = self._db.execute(
            "SELECT s.device_id, s.banned_until, d.name, d.mac, d.last_seen FROM device_state s"
            " JOIN devices d ON d.id = s.device_id"
            " WHERE s.banned_until = -1 OR s.banned_until > ?", (now,)).fetchall()
        out = []
        for r in rows:
            members = self._member_ids(r["device_id"])
            out.append({"device_id": r["device_id"], "name": r["name"], "until": r["banned_until"],
                        "macs": self._macs_of(members), "members": members, "last_seen": r["last_seen"]})
        return out

    def banned_macs(self):
        return {mac for d in self.banned_devices() for mac in d["macs"]}

    def set_portal(self, device_id, mode):
        """The device's captive portal: always, never, or None for the general
        rule."""
        with self._lock:
            self._set_state(device_id, portal=mode if mode in ("always", "never") else None)
            self._db.commit()

    def note_portal_release(self, mac, device_id=None, when=None):
        """Remembers a tap on "Finish connecting" against the device itself.
        In memory it only lasted as long as the service, and the Pi restarts
        every day - which asked a device that had already finished to finish
        again."""
        device = self.device_by_id(device_id) if device_id else None
        if device is None:
            device = self.ensure_device_for_mac(mac)
        with self._lock:
            self._set_state(device["id"], portal_released_at=time.time() if when is None else when)
            self._db.commit()

    def portal_released_at(self, mac):
        """When this device last tapped "Finish connecting", or None."""
        device = self.device_by_mac(mac)
        return self._state(device["id"]).get("portal_released_at") if device else None

    def forget_portal_release(self, mac):
        """Undoes the tap, so the portal holds the device again. True when
        there was one to undo."""
        device = self.device_by_mac(mac)
        if device is None:
            return False
        with self._lock:
            had = self._state(device["id"]).get("portal_released_at") is not None
            self._set_state(device["id"], portal_released_at=None)
            self._db.commit()
            return had

    def set_free_credits(self, device_id, on):
        """The owner spares this device the guest credits."""
        with self._lock:
            self._set_state(self.person_id(device_id), free_credits=1 if on else 0)
            self._db.commit()

    def has_free_credits(self, device_id):
        return bool(self._state(self.person_id(device_id)).get("free_credits"))

    def set_name_locked(self, device_id, on):
        """The owner pins this device's name: it may no longer change it."""
        with self._lock:
            self._set_state(self.person_id(device_id), name_locked=1 if on else 0)
            self._db.commit()

    def name_locked(self, device_id):
        return bool(self._state(self.person_id(device_id)).get("name_locked"))

    def portal_for_mac(self, mac):
        device = self.device_by_mac(mac) if mac else None
        return self._state(device["id"]).get("portal") if device else None

    def device_summary(self, device):
        """What the list of connected devices shows about a known device."""
        state = self._state(device["id"])
        person = self._pid(device)
        shared = state if person == device["id"] else self._state(person)
        return {
            "device_id": device["id"],
            "person": person,
            "name": device.get("name"),
            "generated": bool(shared.get("generated_name")),
            "banned_until": self.ban_until(device["id"]),
            "portal": state.get("portal"),
            "free_credits": bool(shared.get("free_credits")),
            "name_locked": bool(shared.get("name_locked")),
            "linked": self.linked_devices(device["id"]),
            "first_seen": device.get("first_seen"),
            "last_seen": device.get("last_seen"),
        }

    def seen_devices(self, since, limit=200):
        """The devices seen at or after `since`, most recent first, with what
        the page is allowed to show about them."""
        rows = self._db.execute(
            "SELECT * FROM devices WHERE last_seen IS NOT NULL AND last_seen >= ?"
            " ORDER BY last_seen DESC LIMIT ?", (since, limit)).fetchall()
        out = []
        for row in rows:
            entry = self.device_summary(self._as_device(row))
            entry["mac"] = row["mac"]
            entry["ip"] = row["last_ip"]
            macs = [m["mac"] for m in self._db.execute(
                "SELECT mac FROM device_macs WHERE device_id = ?", (row["id"],)).fetchall()]
            if row["mac"] and row["mac"] not in macs:
                macs.append(row["mac"])
            entry["macs"] = macs
            out.append(entry)
        return out

    def people(self):
        """Everyone who ever took a name, one entry per device: its current
        name, every name it went by (oldest first, with the date it took
        it)."""
        devices = self._db.execute(
            "SELECT * FROM devices WHERE id IN (SELECT DISTINCT device_id FROM name_changes)"
            " ORDER BY last_seen DESC").fetchall()
        people = []
        for d in devices:
            changes = self._db.execute(
                "SELECT new_name, at FROM name_changes WHERE device_id = ? ORDER BY at, id", (d["id"],)).fetchall()
            reserved = self._db.execute(
                "SELECT key, display FROM names WHERE device_id = ? ORDER BY claimed_at", (d["id"],)).fetchall()
            current_key = name_key(d["name"]) if d["name"] else None
            people.append({
                "device_id": d["id"], "name": d["name"], "mac": d["mac"], "ip": d["last_ip"],
                "first_seen": d["first_seen"], "last_seen": d["last_seen"],
                "names": [{"name": c["new_name"], "at": c["at"]} for c in changes],
                "reserved": [{"key": r["key"], "display": r["display"], "in_use": r["key"] == current_key}
                             for r in reserved],
                "suggestions": self._db.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE device_id = ?", (d["id"],)).fetchone()[0],
                "devices": len(self._member_ids(d["id"])),
            })
        return people

    def release_name(self, key):
        """Frees a reserved name (the owner's decision)."""
        with self._lock:
            row = self._db.execute(
                "SELECT d.name FROM names n LEFT JOIN devices d ON d.id = n.device_id WHERE n.key = ?",
                (key,)).fetchone()
            if row is None:
                raise SuggestionError("not_found")
            if row["name"] and name_key(row["name"]) == key:
                raise SuggestionError("name_in_use")
            self._db.execute("DELETE FROM names WHERE key = ?", (key,))
            self._db.commit()

    def add(self, device, kind, text):
        if kind not in KINDS:
            raise SuggestionError("bad_kind")
        if not device.get("name"):
            raise SuggestionError("name_required")
        text = _clean(text, TEXT_MAX[kind])
        key = _text_key(text)
        if len(key) < TEXT_MIN:
            raise SuggestionError("suggestion_too_short")
        now = time.time()
        with self._lock:
            same = self._db.execute(
                "SELECT id FROM suggestions WHERE kind = ? AND text_key = ? AND status = 'open'",
                (kind, key)).fetchone()
            if same is not None:
                raise SuggestionError("suggestion_exists", same["id"])
            today = self._db.execute(
                "SELECT COUNT(*) FROM suggestions WHERE device_id = ? AND created_at > ?",
                (self._pid(device), now - DAY)).fetchone()[0]
            if today >= SUGGESTIONS_PER_DAY:
                raise SuggestionError("too_many_suggestions")
            cur = self._db.execute(
                "INSERT INTO suggestions (kind, text, text_key, device_id, author, created_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, 'open')", (kind, text, key, self._pid(device), device["name"], now))
            self._db.commit()
            return cur.lastrowid

    def _get(self, suggestion_id):
        try:
            suggestion_id = int(suggestion_id)
        except (TypeError, ValueError):
            raise SuggestionError("not_found")
        row = self._db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            raise SuggestionError("not_found")
        return row

    def vote(self, device, suggestion_id, value):
        """Votes 1 (up), -1 (down) or 0 (takes the vote back)."""
        if value not in (1, -1, 0):
            raise SuggestionError("bad_vote")
        with self._lock:
            row = self._get(suggestion_id)
            if row["device_id"] == self._pid(device):
                raise SuggestionError("own_suggestion")
            if row["status"] != "open":
                raise SuggestionError("suggestion_closed")
            if value == 0:
                self._db.execute("DELETE FROM votes WHERE suggestion_id = ? AND device_id = ?",
                                 (row["id"], self._pid(device)))
            else:
                self._db.execute(
                    "INSERT INTO votes (suggestion_id, device_id, value, at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(suggestion_id, device_id) DO UPDATE SET value = excluded.value, at = excluded.at",
                    (row["id"], self._pid(device), value, time.time()))
            self._db.commit()

    def delete(self, device, suggestion_id, admin=False):
        with self._lock:
            row = self._get(suggestion_id)
            if not admin and row["device_id"] != self._pid(device):
                raise SuggestionError("not_yours")
            self._db.execute("DELETE FROM votes WHERE suggestion_id = ?", (row["id"],))
            self._db.execute("DELETE FROM suggestions WHERE id = ?", (row["id"],))
            self._db.commit()

    def set_status(self, suggestion_id, status):
        if status not in STATUSES:
            raise SuggestionError("bad_status")
        with self._lock:
            row = self._get(suggestion_id)
            self._db.execute("UPDATE suggestions SET status = ? WHERE id = ?", (status, row["id"]))
            self._db.commit()

    def list(self, device, admin=False):
        """Open ones first, best score first."""
        rows = self._db.execute(
            "SELECT s.*,"
            " (SELECT COUNT(*) FROM votes v WHERE v.suggestion_id = s.id AND v.value > 0) AS up,"
            " (SELECT COUNT(*) FROM votes v WHERE v.suggestion_id = s.id AND v.value < 0) AS down,"
            " (SELECT v.value FROM votes v WHERE v.suggestion_id = s.id AND v.device_id = ?) AS mine"
            " FROM suggestions s", (self._pid(device),)).fetchall()
        items = []
        for r in rows:
            item = {
                "id": r["id"], "kind": r["kind"], "text": r["text"], "author": r["author"],
                "created_at": r["created_at"], "status": r["status"],
                "up": r["up"], "down": r["down"], "my_vote": r["mine"] or 0,
                "mine": r["device_id"] == self._pid(device),
            }
            if admin:
                item["device"] = r["device_id"]
            items.append(item)
        items.sort(key=lambda i: (i["status"] != "open", -(i["up"] - i["down"]) if i["status"] == "open" else 0,
                                  -(i["created_at"] or 0)))
        return items

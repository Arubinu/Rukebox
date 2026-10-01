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
CREATE TABLE IF NOT EXISTS device_state (
    device_id TEXT PRIMARY KEY,
    banned_until REAL,
    portal TEXT,
    generated_name INTEGER NOT NULL DEFAULT 0,
    free_credits INTEGER NOT NULL DEFAULT 0,
    name_locked INTEGER NOT NULL DEFAULT 0
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

    # Columns that arrived after the first installs. CREATE TABLE IF NOT EXISTS
    # does not touch a table that already exists, and the owner's Pi has one
    # with real data in it, so a missing column is added here.
    _ADDED_COLUMNS = {
        "device_state": (("free_credits", "INTEGER NOT NULL DEFAULT 0"),
                         ("name_locked", "INTEGER NOT NULL DEFAULT 0")),
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
                return {"id": device_id, "mac": mac, "name": None, "ip": ip}, new_token
            if (mac and mac != row["mac"]) or ip != row["last_ip"] or now - (row["last_seen"] or 0) > SEEN_WRITE_SEC:
                self._db.execute("UPDATE devices SET last_seen = ?, last_ip = ?, mac = COALESCE(?, mac) WHERE id = ?",
                                 (now, ip, mac, row["id"]))
                if mac:
                    self._db.execute("INSERT OR REPLACE INTO device_macs (device_id, mac, last_seen) VALUES (?, ?, ?)",
                                     (row["id"], mac, now))
                self._db.commit()
            return {"id": row["id"], "mac": mac or row["mac"], "name": row["name"], "ip": ip}, issue

    def rename_wait(self, device, interval_sec):
        """Seconds before this device may change its name again. The owner's
        own renames do not count: the interval is there to stop a phone from
        flapping, not to make the owner's naming wait."""
        if not device.get("name") or interval_sec <= 0 or self.is_generated(device["id"]):
            return 0
        row = self._db.execute("SELECT MAX(at) FROM name_changes WHERE device_id = ? AND by_owner = 0",
                               (device["id"],)).fetchone()
        last = row[0] or 0
        return max(0, int(round(last + interval_sec - time.time())))

    def set_name(self, device, name, interval_sec=0, generated=False, by_owner=False):
        """Takes a name for a device; interval_sec is the minimum time between
        two changes. by_owner is the owner naming it from the interface, which
        the lock does not stop."""
        if not by_owner and self.name_locked(device["id"]):
            raise SuggestionError("name_locked")
        display = _clean(name, NAME_MAX)
        key = name_key(display)
        if len(key) < NAME_KEY_MIN:
            raise SuggestionError("name_too_short")
        if generated and device.get("name"):
            return device["name"]
        if device.get("name") == display:
            return display
        was_generated = self.is_generated(device["id"])
        now = time.time()
        with self._lock:
            owner = self._db.execute("SELECT device_id FROM names WHERE key = ?", (key,)).fetchone()
            if owner is not None and owner["device_id"] != device["id"]:
                raise SuggestionError("name_taken")
            wait = self.rename_wait(device, interval_sec)
            if wait > 0:
                raise SuggestionError("rename_too_soon", wait)
            if owner is None:
                self._db.execute("INSERT INTO names (key, display, device_id, claimed_at) VALUES (?, ?, ?, ?)",
                                 (key, display, device["id"], now))
            else:
                self._db.execute("UPDATE names SET display = ? WHERE key = ?", (display, key))
            self._db.execute("UPDATE devices SET name = ? WHERE id = ?", (display, device["id"]))
            self._db.execute(
                "INSERT INTO name_changes (device_id, old_name, new_name, at, mac, ip, by_owner)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device["id"], device.get("name"), display, now, device.get("mac"), device.get("ip"),
                 1 if by_owner else 0))
            if was_generated and device.get("name") and name_key(device["name"]) != key:
                self._db.execute("DELETE FROM names WHERE key = ? AND device_id = ?",
                                 (name_key(device["name"]), device["id"]))
            self._set_state(device["id"], generated_name=1 if generated else 0)
            self._db.commit()
        device["name"] = display
        return display

    def _set_state(self, device_id, **fields):
        """Caller holds the lock and commits."""
        self._db.execute("INSERT OR IGNORE INTO device_state (device_id) VALUES (?)", (device_id,))
        for key, value in fields.items():
            assert key in ("banned_until", "portal", "generated_name", "free_credits", "name_locked")
            self._db.execute("UPDATE device_state SET %s = ? WHERE device_id = ?" % key, (value, device_id))

    def _state(self, device_id):
        row = self._db.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,)).fetchone()
        return dict(row) if row else {}

    def is_generated(self, device_id):
        return bool(self._state(device_id).get("generated_name"))

    def device_by_id(self, device_id):
        row = self._db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return dict(row) if row else None

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

    def ban_until(self, device_id):
        """None (not banned), -1 (for good) or the time it ends."""
        until = self._state(device_id).get("banned_until")
        if until is None:
            return None
        if until != -1 and until < time.time():
            return None
        return until

    def set_ban(self, device_id, until):
        with self._lock:
            self._set_state(device_id, banned_until=until)
            self._db.commit()

    def banned_devices(self):
        now = time.time()
        rows = self._db.execute(
            "SELECT s.device_id, s.banned_until, d.name, d.mac, d.last_seen FROM device_state s"
            " JOIN devices d ON d.id = s.device_id"
            " WHERE s.banned_until = -1 OR s.banned_until > ?", (now,)).fetchall()
        out = []
        for r in rows:
            macs = [m["mac"] for m in self._db.execute(
                "SELECT mac FROM device_macs WHERE device_id = ?", (r["device_id"],)).fetchall()]
            if r["mac"] and r["mac"] not in macs:
                macs.append(r["mac"])
            out.append({"device_id": r["device_id"], "name": r["name"], "until": r["banned_until"],
                        "macs": macs, "last_seen": r["last_seen"]})
        return out

    def banned_macs(self):
        return {mac for d in self.banned_devices() for mac in d["macs"]}

    def set_portal(self, device_id, mode):
        """The device's captive portal: always, never, or None for the general
        rule."""
        with self._lock:
            self._set_state(device_id, portal=mode if mode in ("always", "never") else None)
            self._db.commit()

    def set_free_credits(self, device_id, on):
        """The owner spares this device the guest credits."""
        with self._lock:
            self._set_state(device_id, free_credits=1 if on else 0)
            self._db.commit()

    def has_free_credits(self, device_id):
        return bool(self._state(device_id).get("free_credits"))

    def set_name_locked(self, device_id, on):
        """The owner pins this device's name: it may no longer change it."""
        with self._lock:
            self._set_state(device_id, name_locked=1 if on else 0)
            self._db.commit()

    def name_locked(self, device_id):
        return bool(self._state(device_id).get("name_locked"))

    def portal_for_mac(self, mac):
        device = self.device_by_mac(mac) if mac else None
        return self._state(device["id"]).get("portal") if device else None

    def device_summary(self, device):
        """What the list of connected devices shows about a known device."""
        state = self._state(device["id"])
        return {
            "device_id": device["id"],
            "name": device.get("name"),
            "generated": bool(state.get("generated_name")),
            "banned_until": self.ban_until(device["id"]),
            "portal": state.get("portal"),
            "free_credits": bool(state.get("free_credits")),
            "name_locked": bool(state.get("name_locked")),
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
            entry = self.device_summary(dict(row))
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
                (device["id"], now - DAY)).fetchone()[0]
            if today >= SUGGESTIONS_PER_DAY:
                raise SuggestionError("too_many_suggestions")
            cur = self._db.execute(
                "INSERT INTO suggestions (kind, text, text_key, device_id, author, created_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, 'open')", (kind, text, key, device["id"], device["name"], now))
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
            if row["device_id"] == device["id"]:
                raise SuggestionError("own_suggestion")
            if row["status"] != "open":
                raise SuggestionError("suggestion_closed")
            if value == 0:
                self._db.execute("DELETE FROM votes WHERE suggestion_id = ? AND device_id = ?",
                                 (row["id"], device["id"]))
            else:
                self._db.execute(
                    "INSERT INTO votes (suggestion_id, device_id, value, at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(suggestion_id, device_id) DO UPDATE SET value = excluded.value, at = excluded.at",
                    (row["id"], device["id"], value, time.time()))
            self._db.commit()

    def delete(self, device, suggestion_id, admin=False):
        with self._lock:
            row = self._get(suggestion_id)
            if not admin and row["device_id"] != device["id"]:
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
            " FROM suggestions s", (device["id"],)).fetchall()
        items = []
        for r in rows:
            item = {
                "id": r["id"], "kind": r["kind"], "text": r["text"], "author": r["author"],
                "created_at": r["created_at"], "status": r["status"],
                "up": r["up"], "down": r["down"], "my_vote": r["mine"] or 0,
                "mine": r["device_id"] == device["id"],
            }
            if admin:
                item["device"] = r["device_id"]
            items.append(item)
        items.sort(key=lambda i: (i["status"] != "open", -(i["up"] - i["down"]) if i["status"] == "open" else 0,
                                  -(i["created_at"] or 0)))
        return items

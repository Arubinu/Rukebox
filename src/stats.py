"""Usage statistics in SQLite."""

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import date

log = logging.getLogger("stats")

SCHEMA_VERSION = 1

PENDING_DAY = "pending"
DAILY_KEEP_DAYS = 1100

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL NOT NULL,   -- epoch, daemon start (corrected once the clock is known)
    boot_at      REAL,            -- epoch, system boot, derived from /proc/uptime
    ended_at     REAL,
    end_reason   TEXT,            -- cutoff | long_press | service_stop | unclean | ...
    clock_source TEXT,            -- rtc | bluetooth | manual | none
    clock_ok     INTEGER NOT NULL DEFAULT 0,
    used         INTEGER NOT NULL DEFAULT 0   -- 1 as soon as something actually played
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ts         REAL NOT NULL,
    clock_ok   INTEGER NOT NULL DEFAULT 0,
    type       TEXT NOT NULL,
    label      TEXT,
    detail     TEXT              -- small JSON blob, optional
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

CREATE TABLE IF NOT EXISTS counters (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily (
    day   TEXT NOT NULL,
    key   TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);

CREATE TABLE IF NOT EXISTS monthly (
    month   TEXT NOT NULL,       -- YYYY-MM, kept for good: what the recap reads
    kind    TEXT NOT NULL,       -- track | first (the first song of a day)
    name    TEXT NOT NULL,
    count   REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (month, kind, name)
);

CREATE TABLE IF NOT EXISTS items (
    kind    TEXT NOT NULL,       -- music | meme | cutoff_announce | custom:<id> | ... | error
    name    TEXT NOT NULL,       -- file basename
    count   REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    last_at REAL,
    PRIMARY KEY (kind, name)
);
"""

KNOWN_COUNTERS = [
    "sessions_started", "sessions_used", "sessions_unclean",
    "seconds_music", "seconds_meme", "seconds_announce",
    "tracks_played", "memes_played", "announcements_played",
    "clicks_single", "clicks_double", "clicks_long", "clicks_speaker",
    "clicks_flic", "clicks_gpio", "clicks_web", "clicks_ignored",
    "playback_errors", "playback_stalls",
    "speaker_drops", "speaker_recoveries",
    "ap_client_connections", "web_sessions",
    "clock_rtc", "clock_bluetooth", "clock_manual", "clock_host", "clock_unreliable",
    "cutoff_triggers", "custom_announces",
    "shutdowns_cutoff", "shutdowns_longpress", "shutdowns_service",
    "shutdowns_speaker",
]


def _system_boot_time():
    """Epoch of the last system boot, from /proc/uptime."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            uptime = float(f.read().split()[0])
        return time.time() - uptime
    except (OSError, ValueError, IndexError):
        return None


class StatsRecorder:
    """Records and reads the usage statistics (SQLite)."""

    def __init__(self, db_path, retention_days=90, max_events=20000, enabled=True):
        self.db_path = db_path
        self.retention_days = float(retention_days)
        self.max_events = int(max_events)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._conn = None
        self._session_id = None
        self._clock_ok = False
        self._clock_source = None
        self._owns_session = False

        if not self.enabled:
            return
        try:
            self._connect()
        except Exception:  # noqa: BLE001
            log.exception("Statistics unavailable (could not open %s)", db_path)
            self.enabled = False

    def _connect(self):
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path, timeout=10.0, check_same_thread=False, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT INTO schema_info(key, value) VALUES('version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_info(key, value) VALUES('created_at', ?)",
            (str(time.time()),),
        )
        self._fill_monthly_from_events()

    @property
    def clock_source(self):
        return self._clock_source

    @property
    def clock_ok(self):
        return self._clock_ok

    @property
    def session_id(self):
        return self._session_id

    def _fill_monthly_from_events(self):
        """A database from before the monthly table: the events it still keeps fill it, once."""
        try:
            if self._conn.execute("SELECT 1 FROM schema_info WHERE key = 'monthly_filled'").fetchone():
                return
            self._conn.execute(
                "INSERT OR IGNORE INTO monthly(month, kind, name, count, seconds) "
                "SELECT strftime('%Y-%m', ts, 'unixepoch', 'localtime'), 'track', label, COUNT(*), "
                "  COALESCE(SUM(json_extract(detail, '$.seconds')), 0) "
                "FROM events WHERE type = 'track_played' AND clock_ok = 1 AND label IS NOT NULL "
                "GROUP BY 1, 3")
            self._conn.execute(
                "INSERT OR IGNORE INTO monthly(month, kind, name, count) "
                "SELECT strftime('%Y-%m', day), 'first', label, COUNT(*) FROM ("
                "  SELECT date(ts, 'unixepoch', 'localtime') AS day, label, "
                "  ROW_NUMBER() OVER (PARTITION BY date(ts, 'unixepoch', 'localtime') ORDER BY ts) AS rn "
                "  FROM events WHERE type = 'track_played' AND clock_ok = 1 AND label IS NOT NULL"
                ") WHERE rn = 1 GROUP BY 1, 3")
            self._conn.execute("INSERT INTO schema_info(key, value) VALUES('monthly_filled', '1')")
        except sqlite3.Error:
            log.exception("Could not fill the monthly recap from the events")

    def _today(self):
        """Day bucket for a rollup: the real date once the clock is trusted,
        the 'pending' bucket before that."""
        if not self._clock_ok:
            return PENDING_DAY
        return date.today().isoformat()

    def _bump_counters(self, mapping):
        for key, amount in mapping.items():
            self._conn.execute(
                "INSERT INTO counters(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                (key, float(amount)),
            )

    def _bump_daily(self, mapping, day=None):
        day = day or self._today()
        for key, amount in mapping.items():
            self._conn.execute(
                "INSERT INTO daily(day, key, value) VALUES(?, ?, ?) "
                "ON CONFLICT(day, key) DO UPDATE SET value = value + excluded.value",
                (day, key, float(amount)),
            )

    def _bump_item(self, kind, name, count=0.0, seconds=0.0, ts=None):
        self._conn.execute(
            "INSERT INTO items(kind, name, count, seconds, last_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, name) DO UPDATE SET "
            "  count = count + excluded.count, "
            "  seconds = seconds + excluded.seconds, "
            "  last_at = excluded.last_at",
            (kind, name, float(count), float(seconds), ts if ts is not None else time.time()),
        )

    def _bump_monthly(self, name, seconds, daily):
        """A song for the recap; the first one of its day is remembered too."""
        month = date.today().strftime("%Y-%m")
        self._conn.execute(
            "INSERT INTO monthly(month, kind, name, count, seconds) VALUES(?, 'track', ?, 1, ?) "
            "ON CONFLICT(month, kind, name) DO UPDATE SET count = count + 1, "
            "seconds = seconds + excluded.seconds", (month, name, float(seconds or 0)))
        played = self._conn.execute(
            "SELECT value FROM daily WHERE day = ? AND key = 'tracks_played'",
            (date.today().isoformat(),)).fetchone()
        if (daily or {}).get("tracks_played") and played is not None and played["value"] <= 1:
            self._conn.execute(
                "INSERT INTO monthly(month, kind, name, count) VALUES(?, 'first', ?, 1) "
                "ON CONFLICT(month, kind, name) DO UPDATE SET count = count + 1", (month, name))

    def record(self, event_type, label=None, detail=None, counters=None, daily=None,
               item=None, seconds=0.0):
        """Single entry point for "something happened"."""
        if not self.enabled:
            return
        try:
            with self._lock:
                now = time.time()
                self._conn.execute("BEGIN")
                try:
                    self._conn.execute(
                        "INSERT INTO events(session_id, ts, clock_ok, type, label, detail) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            self._session_id, now, 1 if self._clock_ok else 0,
                            event_type, label,
                            json.dumps(detail, ensure_ascii=False) if detail else None,
                        ),
                    )
                    if counters:
                        self._bump_counters(counters)
                    if daily:
                        self._bump_daily(daily)
                    if item:
                        kind, name = item
                        self._bump_item(kind, name, count=1.0, seconds=seconds, ts=now)
                        if kind == "music" and name and self._clock_ok:
                            self._bump_monthly(name, seconds, daily)
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
        except Exception:  # noqa: BLE001
            log.exception("Could not record the event '%s'", event_type)

    def open_session(self, clock_ok=False, clock_source=None):
        """Called once at daemon startup."""
        if not self.enabled:
            return
        orphans = 0
        try:
            with self._lock:
                self._clock_ok = bool(clock_ok)
                self._clock_source = clock_source
                orphans = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE ended_at IS NULL"
                ).fetchone()["n"]
                if orphans:
                    self._conn.execute(
                        "UPDATE sessions SET ended_at = started_at, end_reason = 'unclean' "
                        "WHERE ended_at IS NULL"
                    )
                    self._bump_counters({"sessions_unclean": orphans})

                cur = self._conn.execute(
                    "INSERT INTO sessions(started_at, boot_at, clock_source, clock_ok) "
                    "VALUES(?, ?, ?, ?)",
                    (time.time(), _system_boot_time(), clock_source, 1 if clock_ok else 0),
                )
                self._session_id = cur.lastrowid
                self._owns_session = True
                self._bump_counters({"sessions_started": 1})
                self._bump_daily({"sessions_started": 1})
        except Exception:  # noqa: BLE001
            log.exception("Could not open a statistics session")
            return

        if orphans:
            self.record(
                "session_unclean",
                label=f"{orphans} session(s) not closed properly",
                detail={"count": orphans},
            )
        self.record("session_start", label=clock_source or "pending")
        self._prune()

    def mark_used(self):
        """Flags the current session as "actually used" (something played, or a
        button was pressed)."""
        if not self.enabled or self._session_id is None:
            return
        try:
            with self._lock:
                changed = self._conn.execute(
                    "UPDATE sessions SET used = 1 WHERE id = ? AND used = 0",
                    (self._session_id,),
                ).rowcount
                if changed:
                    self._bump_counters({"sessions_used": 1})
                    self._bump_daily({"sessions_used": 1})
        except Exception:  # noqa: BLE001
            log.exception("Could not flag the session as used")

    def end_session(self, reason):
        if not self.enabled or self._session_id is None:
            return
        counter = {
            "cutoff": "shutdowns_cutoff",
            "long_press": "shutdowns_longpress",
            "service_stop": "shutdowns_service",
            "speaker_lost": "shutdowns_speaker",
            "speaker_absent": "shutdowns_speaker",
        }.get(reason)
        self.record(
            "session_end", label=reason,
            counters={counter: 1} if counter else None,
        )
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                    (time.time(), reason, self._session_id),
                )
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            log.exception("Could not close the statistics session")

    def attach_current_session(self):
        """Used by the web server: its own events belong to whatever session
        the daemon currently has open."""
        if not self.enabled:
            return
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, clock_ok, clock_source FROM sessions "
                    "WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    self._session_id = row["id"]
                    self._clock_ok = bool(row["clock_ok"])
                    self._clock_source = row["clock_source"]
        except Exception:  # noqa: BLE001
            log.exception("Could not attach to the current session")

    def set_clock(self, source, offset_sec=0.0, trusted=True):
        """Declares how (and whether) the time was established."""
        if not self.enabled:
            return
        counter = {
            "rtc": "clock_rtc",
            "bluetooth": "clock_bluetooth",
            "manual": "clock_manual",
            # A container's clock is its host's: not ours to set, and not a doubt.
            "host": "clock_host",
        }.get(source, "clock_unreliable")
        offset = float(offset_sec or 0.0)
        try:
            with self._lock:
                self._clock_ok = bool(trusted)
                self._clock_source = source
                self._conn.execute("BEGIN")
                try:
                    if self._session_id is not None:
                        if offset:
                            self._conn.execute(
                                "UPDATE events SET ts = ts + ? WHERE session_id = ? AND clock_ok = 0",
                                (offset, self._session_id),
                            )
                            self._conn.execute(
                                "UPDATE sessions SET started_at = started_at + ?, "
                                "boot_at = boot_at + ? WHERE id = ?",
                                (offset, offset, self._session_id),
                            )
                        if trusted:
                            self._conn.execute(
                                "UPDATE events SET clock_ok = 1 WHERE session_id = ?",
                                (self._session_id,),
                            )
                        self._conn.execute(
                            "UPDATE sessions SET clock_source = ?, clock_ok = ? WHERE id = ?",
                            (source, 1 if trusted else 0, self._session_id),
                        )
                    today = date.today().isoformat()
                    self._conn.execute(
                        "INSERT INTO daily(day, key, value) "
                        "SELECT ?, key, value FROM daily WHERE day = ? "
                        "ON CONFLICT(day, key) DO UPDATE SET value = value + excluded.value",
                        (today, PENDING_DAY),
                    )
                    self._conn.execute("DELETE FROM daily WHERE day = ?", (PENDING_DAY,))
                    self._bump_counters({counter: 1})
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
        except Exception:  # noqa: BLE001
            log.exception("Could not record the clock status")

        self.record(
            "clock_ready" if trusted else "clock_unreliable",
            label=source,
            detail={"offset_sec": round(offset, 3)},
        )

    def _prune(self):
        if not self.enabled:
            return
        try:
            with self._lock:
                cutoff_ts = time.time() - self.retention_days * 86400
                cutoff_day = date.fromtimestamp(time.time() - max(self.retention_days, DAILY_KEEP_DAYS)
                                                * 86400).isoformat()
                self._conn.execute("BEGIN")
                try:
                    self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff_ts,))
                    self._conn.execute(
                        "DELETE FROM events WHERE id <= COALESCE("
                        "  (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?), -1)",
                        (self.max_events,),
                    )
                    self._conn.execute(
                        "DELETE FROM daily WHERE day < ? AND day <> ?", (cutoff_day, PENDING_DAY),
                    )
                    self._conn.execute(
                        "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                        (cutoff_ts,),
                    )
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("PRAGMA incremental_vacuum")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            log.exception("Could not prune old statistics")

    def reset_counters(self, keys):
        """Puts some lifetime counters back to zero."""
        if not self.enabled:
            return None
        wanted = [k for k in dict.fromkeys(keys or []) if k in KNOWN_COUNTERS]
        if not wanted:
            return []
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM counters WHERE key IN (%s)" % ",".join("?" * len(wanted)),
                    wanted,
                )
        except Exception:  # noqa: BLE001
            log.exception("Could not reset counters %s", wanted)
            return None
        self.record("counters_reset", label=", ".join(wanted)[:200], detail={"keys": wanted})
        return wanted

    def delete_rows(self, target, keys):
        """Removes specific rows from one of the listed tables, as opposed to
        reset()'s all-or-nothing wipe."""
        if not self.enabled or not keys:
            return 0
        try:
            with self._lock:
                self._conn.execute("BEGIN")
                try:
                    if target in ("events", "sessions"):
                        ids = []
                        for key in keys:
                            try:
                                ids.append(int(key))
                            except (TypeError, ValueError):
                                continue
                        if not ids:
                            self._conn.execute("ROLLBACK")
                            return 0
                        if target == "sessions":
                            open_ids = {
                                row["id"] for row in self._rows(
                                    "SELECT id FROM sessions WHERE ended_at IS NULL"
                                )
                            }
                            ids = [i for i in ids if i not in open_ids]
                            if not ids:
                                self._conn.execute("ROLLBACK")
                                return 0
                        placeholders = ",".join("?" * len(ids))
                        cursor = self._conn.execute(
                            "DELETE FROM %s WHERE id IN (%s)" % (target, placeholders),
                            ids,
                        )
                        removed = cursor.rowcount or 0
                        if target == "sessions":
                            self._conn.execute(
                                "UPDATE events SET session_id = NULL "
                                "WHERE session_id IN (%s)" % placeholders,
                                ids,
                            )
                    elif target == "items":
                        removed = 0
                        for key in keys:
                            if not isinstance(key, (list, tuple)) or len(key) != 2:
                                continue
                            cursor = self._conn.execute(
                                "DELETE FROM items WHERE kind = ? AND name = ?",
                                (str(key[0]), str(key[1])),
                            )
                            removed += cursor.rowcount or 0
                    else:
                        self._conn.execute("ROLLBACK")
                        return 0
                    self._conn.execute("COMMIT")
                except Exception:  # noqa: BLE001
                    self._conn.execute("ROLLBACK")
                    raise
        except Exception:  # noqa: BLE001
            log.exception("Could not delete %s rows", target)
            return 0
        if removed:
            self._record_deletion(target, removed)
        return removed

    def _record_deletion(self, label, removed):
        """What the log says about a deletion - and nothing when the log itself
        is what was emptied: an audit line landing in the very list being
        cleaned is one line the owner then has to delete again."""
        if label == "events":
            return
        self.record("stats_rows_deleted", label=label, detail={"count": removed})

    _LIST_SCOPES = {
        "sessions": ("sessions", "ended_at IS NOT NULL"),
        "played": ("items", "kind != 'error'"),
        "errors": ("items", "kind = 'error'"),
        "events": ("events", "1=1"),
    }

    def _events_filter(self, event_type=None, query=None):
        """(where, params) for the event log's filters, shared by the page, the
        count and the deletion, so what is shown and what is counted agree."""
        clauses, params = [], []
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        if query:
            like = "%" + query + "%"
            clauses.append("(label LIKE ? OR type LIKE ? OR detail LIKE ?)")
            params.extend([like, like, like])
        return (" AND ".join(clauses) or "1=1"), tuple(params)

    def count_rows(self, scope, event_type=None, query=None):
        """How many rows a list holds in the database."""
        if not self.enabled or scope not in self._LIST_SCOPES:
            return 0
        table, where = self._LIST_SCOPES[scope]
        params = ()
        if scope == "events":
            where, params = self._events_filter(event_type, query)
        try:
            return self._rows("SELECT COUNT(*) AS n FROM %s WHERE %s" % (table, where), params)[0]["n"]
        except Exception:  # noqa: BLE001
            log.exception("Could not count %s", scope)
            return 0

    def delete_all(self, scope, event_type=None):
        """Empties one whole list (optionally, for events, one type of it),
        including the rows the interface never loaded."""
        if not self.enabled or scope not in self._LIST_SCOPES:
            return 0
        table, where = self._LIST_SCOPES[scope]
        params = ()
        if scope == "events" and event_type:
            where, params = "type = ?", (event_type,)
        try:
            with self._lock:
                self._conn.execute("BEGIN")
                try:
                    cursor = self._conn.execute("DELETE FROM %s WHERE %s" % (table, where), params)
                    removed = cursor.rowcount or 0
                    if scope == "sessions":
                        self._conn.execute(
                            "UPDATE events SET session_id = NULL WHERE session_id IS NOT NULL "
                            "AND session_id NOT IN (SELECT id FROM sessions)"
                        )
                    self._conn.execute("COMMIT")
                except Exception:  # noqa: BLE001
                    self._conn.execute("ROLLBACK")
                    raise
        except Exception:  # noqa: BLE001
            log.exception("Could not empty %s", scope)
            return 0
        if removed:
            self._record_deletion(scope, removed)
        return removed

    def sessions_page(self, limit=50, before_id=None):
        """Sessions, newest first, older than `before_id`."""
        if not self.enabled:
            return []
        sql = ("SELECT id, started_at, boot_at, ended_at, end_reason, clock_source, "
               "       clock_ok, used FROM sessions")
        params = []
        if before_id is not None:
            sql += " WHERE id < ?"
            params.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        try:
            return self._rows(sql, tuple(params))
        except Exception:  # noqa: BLE001
            log.exception("Could not read the sessions")
            return []

    def reset(self, scope="all"):
        """Wipes the statistics from the web interface."""
        if not self.enabled:
            return False
        had_session = self._owns_session and self._session_id is not None
        try:
            with self._lock:
                self._conn.execute("BEGIN")
                try:
                    if scope in ("all", "events"):
                        self._conn.execute("DELETE FROM events")
                    if scope in ("all", "counters"):
                        self._conn.execute("DELETE FROM counters")
                        self._conn.execute("DELETE FROM daily")
                        self._conn.execute("DELETE FROM items")
                        self._conn.execute("DELETE FROM monthly")
                    if scope == "all":
                        self._conn.execute("DELETE FROM sessions")
                        self._session_id = None
                        self._owns_session = False
                    self._conn.execute(
                        "INSERT INTO schema_info(key, value) VALUES('reset_at', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(time.time()),),
                    )
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("PRAGMA incremental_vacuum")
        except Exception:  # noqa: BLE001
            log.exception("Could not reset the statistics")
            return False

        if scope == "all" and had_session:
            self.open_session(clock_ok=self._clock_ok, clock_source=self._clock_source)
        self.record("stats_reset", label=scope)
        return True

    def _rows(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def counters(self):
        if not self.enabled:
            return {}
        values = {key: 0.0 for key in KNOWN_COUNTERS}
        try:
            for row in self._rows("SELECT key, value FROM counters"):
                values[row["key"]] = row["value"]
        except Exception:  # noqa: BLE001
            log.exception("Could not read the counters")
        return values

    def summary(self, top_limit=10, recent_sessions=10):
        if not self.enabled:
            return {"enabled": False}
        try:
            counters = self.counters()
            info = {r["key"]: r["value"] for r in self._rows("SELECT key, value FROM schema_info")}

            sessions = self._rows(
                "SELECT id, started_at, boot_at, ended_at, end_reason, clock_source, "
                "       clock_ok, used "
                "FROM sessions ORDER BY id DESC LIMIT ?",
                (recent_sessions,),
            )
            current = self._rows(
                "SELECT id, started_at, boot_at, clock_source, clock_ok, used "
                "FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            )

            top = {}
            for kind in ("music", "meme", "cutoff_announce", "error"):
                top[kind] = self._rows(
                    "SELECT name, count, seconds, last_at FROM items WHERE kind = ? "
                    "ORDER BY count DESC, seconds DESC LIMIT ?",
                    (kind, top_limit),
                )
            top["custom_announce"] = [
                dict(row, kind=row["kind"][len("custom:"):])
                for row in self._rows(
                    "SELECT kind, name, count, seconds, last_at FROM items "
                    "WHERE kind LIKE 'custom:%' ORDER BY count DESC, seconds DESC LIMIT ?",
                    (top_limit * 4,),
                )
            ]

            event_count = self._rows("SELECT COUNT(*) AS n FROM events")[0]["n"]
            first_event = self._rows("SELECT MIN(ts) AS t FROM events")[0]["t"]

            db_size = 0
            for suffix in ("", "-wal", "-shm"):
                path = self.db_path + suffix
                if os.path.exists(path):
                    db_size += os.path.getsize(path)

            return {
                "enabled": True,
                "generated_at": time.time(),
                "created_at": float(info["created_at"]) if info.get("created_at") else None,
                "reset_at": float(info["reset_at"]) if info.get("reset_at") else None,
                "since": first_event,
                "counters": counters,
                "sessions": {
                    "current": current[0] if current else None,
                    "recent": sessions,
                },
                "totals": {
                    "sessions": self.count_rows("sessions"),
                    "played": self.count_rows("played"),
                    "errors": self.count_rows("errors"),
                },
                "top": top,
                "event_count": event_count,
                "db_size_bytes": db_size,
                "retention_days": self.retention_days,
            }
        except Exception:  # noqa: BLE001
            log.exception("Could not build the statistics summary")
            return {"enabled": False, "error": "query_failed"}

    def events(self, limit=100, event_type=None, since=None, before_id=None, query=None):
        if not self.enabled:
            return []
        sql = ("SELECT id, session_id, ts, clock_ok, type, label, detail "
               "FROM events WHERE 1=1")
        where, params = self._events_filter(event_type, query)
        if where != "1=1":
            sql += " AND " + where
        params = list(params)
        if since is not None:
            sql += " AND ts >= ?"
            params.append(float(since))
        if before_id is not None:
            sql += " AND id < ?"
            params.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        try:
            rows = self._rows(sql, tuple(params))
        except Exception:  # noqa: BLE001
            log.exception("Could not read the event log")
            return []
        for row in rows:
            if row["detail"]:
                try:
                    row["detail"] = json.loads(row["detail"])
                except json.JSONDecodeError:
                    pass
        return rows

    def event_types(self):
        if not self.enabled:
            return []
        try:
            return [r["type"] for r in self._rows(
                "SELECT type FROM events GROUP BY type ORDER BY type"
            )]
        except Exception:  # noqa: BLE001
            return []

    def daily_series(self, days=14):
        """Returns one entry per day."""
        if not self.enabled:
            return []
        days = max(1, min(int(days), 365))
        try:
            rows = self._rows(
                "SELECT day, key, value FROM daily WHERE day <> ? ORDER BY day", (PENDING_DAY,)
            )
        except Exception:  # noqa: BLE001
            log.exception("Could not read the daily rollups")
            return []

        by_day = {}
        for row in rows:
            by_day.setdefault(row["day"], {})[row["key"]] = row["value"]

        today = date.today().toordinal()
        series = []
        for offset in range(days - 1, -1, -1):
            day = date.fromordinal(today - offset).isoformat()
            values = by_day.get(day, {})
            series.append({
                "day": day,
                "seconds_music": values.get("seconds_music", 0.0),
                "seconds_meme": values.get("seconds_meme", 0.0),
                "seconds_announce": values.get("seconds_announce", 0.0),
                "tracks_played": values.get("tracks_played", 0.0),
                "clicks": sum(v for k, v in values.items() if k.startswith("clicks_")),
                "playback_errors": values.get("playback_errors", 0.0),
                "sessions_started": values.get("sessions_started", 0.0),
                "web_sessions": values.get("web_sessions", 0.0),
            })
        return series

    def recap(self, first_day, last_day, limit=5):
        """A period in figures: listening time, songs, days, the best day, the
        most played songs, and the song that most often opened the day."""
        if not self.enabled:
            return None
        start, end = first_day.isoformat(), last_day.isoformat()
        months = (first_day.strftime("%Y-%m"), last_day.strftime("%Y-%m"))
        try:
            days = self._rows(
                "SELECT day, key, value FROM daily WHERE day <> ? AND day BETWEEN ? AND ?",
                (PENDING_DAY, start, end))
            tracks = self._rows(
                "SELECT name, SUM(count) AS n, SUM(seconds) AS s FROM monthly WHERE kind = 'track'"
                " AND month BETWEEN ? AND ? GROUP BY name ORDER BY n DESC, s DESC", months)
            first = self._rows(
                "SELECT name, SUM(count) AS n FROM monthly WHERE kind = 'first'"
                " AND month BETWEEN ? AND ? GROUP BY name ORDER BY n DESC LIMIT 1", months)
        except Exception:  # noqa: BLE001
            log.exception("Could not read the recap")
            return None
        music = {}
        totals = {}
        for row in days:
            totals[row["key"]] = totals.get(row["key"], 0.0) + row["value"]
            if row["key"] == "seconds_music":
                music[row["day"]] = row["value"]
        best = max(music.items(), key=lambda kv: kv[1]) if music else None
        return {
            "first_day": start,
            "last_day": end,
            "seconds_music": totals.get("seconds_music", 0.0),
            "tracks_played": totals.get("tracks_played", 0.0),
            "days": sum(1 for v in music.values() if v > 0),
            "best_day": {"day": best[0], "seconds": best[1]} if best and best[1] > 0 else None,
            "tracks": [{"name": r["name"], "count": r["n"], "seconds": r["s"]} for r in tracks],
            "top_limit": int(limit),
            "morning": {"name": first[0]["name"], "count": first[0]["n"]} if first else None,
        }

    def today_summary(self, limit=3):
        """Today in a few figures, for everyone's Home."""
        if not self.enabled:
            return None
        today = date.today()
        start = time.mktime(today.timetuple())
        try:
            values = {r["key"]: r["value"] for r in self._rows(
                "SELECT key, value FROM daily WHERE day = ?", (today.isoformat(),))}
            sounds = self._rows(
                "SELECT COUNT(*) AS n FROM events WHERE ts >= ? AND type IN ('meme_played', 'announce_played')",
                (start,))[0]["n"]
            top = self._rows(
                "SELECT label, COUNT(*) AS n FROM events WHERE ts >= ? AND type = 'track_played'"
                " AND label IS NOT NULL GROUP BY label ORDER BY n DESC, MAX(ts) DESC LIMIT ?",
                (start, int(limit)))
        except Exception:  # noqa: BLE001
            log.exception("Could not read today's summary")
            return None
        return {
            "day": today.isoformat(),
            "seconds_music": values.get("seconds_music", 0.0),
            "tracks_played": values.get("tracks_played", 0.0),
            "sounds_played": sounds,
            "clicks": sum(v for k, v in values.items() if k.startswith("clicks_")),
            "top": [{"name": r["label"], "count": r["n"]} for r in top],
        }

    def export(self):
        """Everything, as a dict, for the download button."""
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "exported_at": time.time(),
            "summary": self.summary(top_limit=100, recent_sessions=200),
            "daily": self.daily_series(days=365),
            "events": self.events(limit=1000),
        }

    def close(self):
        if not self.enabled or self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass


def _cli(argv):
    if len(argv) < 2 or argv[0] != "record":
        print("usage: stats.py record <event_type> [label] [detail-json]",
              file=sys.stderr)
        return 2
    event_type = argv[1]
    label = argv[2] if len(argv) > 2 else None
    detail = None
    if len(argv) > 3 and argv[3]:
        try:
            detail = json.loads(argv[3])
        except ValueError:
            detail = {"raw": argv[3]}

    db_path = os.environ.get("RUKEBOX_STATS_DB") or os.path.join(
        os.environ.get("RUKEBOX_STATE_DIR", "/var/lib/rukebox"), "stats.db")
    recorder = StatsRecorder(db_path)
    try:
        recorder.attach_current_session()
        recorder.record(event_type, label=label, detail=detail)
    finally:
        recorder.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv[1:]))
    except Exception:  # noqa: BLE001
        sys.exit(0)

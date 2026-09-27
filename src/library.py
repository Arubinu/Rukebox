"""Music library catalogue: tags read once with ffprobe, kept in SQLite."""
import json
import os
import re
import sqlite3
import subprocess
import threading
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    key TEXT,
    title TEXT,
    artist TEXT,
    album TEXT,
    genre TEXT,
    year TEXT,
    track INTEGER,
    duration REAL,
    search TEXT,
    probed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tracks_key ON tracks(key);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist);
"""

PROBE_TIMEOUT_SEC = 20
_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")


def fold(text):
    """Lower case, no accents, punctuation as spaces: the form searches
    compare."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def _from_path(path, music_dir):
    """(title, artist, album) guessed from the path, until the tags are read."""
    rel = os.path.relpath(path, music_dir) if music_dir else os.path.basename(path)
    parts = rel.replace("\\", "/").split("/")
    title = _LEADING_NUMBER.sub("", os.path.splitext(parts[-1])[0]) or parts[-1]
    artist = parts[0] if len(parts) >= 2 else ""
    album = parts[-2] if len(parts) >= 3 else ""
    return title, artist, album


def _first(tagsets, *names):
    wanted = {n.lower() for n in names}
    for tags in tagsets:
        for key, value in tags.items():
            if key.lower() in wanted and str(value).strip():
                return str(value).strip()
    return None


def read_tags(path):
    """The file's own tags and duration."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags:stream_tags",
           "-of", "json", path]
    if os.name == "posix":
        cmd = ["nice", "-n", "19"] + cmd
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_SEC).stdout
        info = json.loads(out.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    fmt = info.get("format") or {}
    tagsets = [fmt.get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams") or []]
    year = _first(tagsets, "date", "year", "originaldate")
    track = _first(tagsets, "track", "tracknumber")
    try:
        track = int(str(track).split("/")[0]) if track else None
    except ValueError:
        track = None
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except ValueError:
        duration = None
    return {
        "title": _first(tagsets, "title"),
        "artist": _first(tagsets, "artist", "album_artist", "album artist", "albumartist"),
        "album": _first(tagsets, "album"),
        "genre": _first(tagsets, "genre"),
        "year": (re.match(r"\d{4}", year).group(0) if year and re.match(r"\d{4}", year) else None),
        "track": track,
        "duration": duration,
    }


class Library:
    def __init__(self, db_path, key_fn):
        """key_fn(path) gives the opaque key the web interface uses for a
        track."""
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._key_fn = key_fn

    def sync(self, paths, music_dir):
        """Brings the rows in step with the music list: new files added."""
        wanted = {}
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            wanted[path] = (st.st_size, st.st_mtime)
        with self._lock:
            rows = {r["path"]: (r["size"], r["mtime"]) for r in
                    self._db.execute("SELECT path, size, mtime FROM tracks")}
            removed = [p for p in rows if p not in wanted]
            for path in removed:
                self._db.execute("DELETE FROM tracks WHERE path = ?", (path,))
            added = 0
            for path, (size, mtime) in wanted.items():
                if rows.get(path) == (size, mtime):
                    continue
                title, artist, album = _from_path(path, music_dir)
                self._db.execute(
                    "INSERT OR REPLACE INTO tracks (path, size, mtime, key, title, artist, album,"
                    " genre, year, track, duration, search, probed)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, 0)",
                    (path, size, mtime, self._key_fn(path), title, artist, album,
                     fold(" ".join([title, artist, album, os.path.basename(path)]))))
                added += 1
            self._db.commit()
        return added, len(removed)

    def unread(self, limit=50):
        with self._lock:
            return [r["path"] for r in self._db.execute(
                "SELECT path FROM tracks WHERE probed = 0 ORDER BY path LIMIT ?", (limit,))]

    def store(self, path, tags, music_dir):
        """Records what read_tags() found; None keeps the names taken from the
        path."""
        title, artist, album = _from_path(path, music_dir)
        tags = tags or {}
        title = tags.get("title") or title
        artist = tags.get("artist") or artist
        album = tags.get("album") or album
        with self._lock:
            self._db.execute(
                "UPDATE tracks SET title = ?, artist = ?, album = ?, genre = ?, year = ?, track = ?,"
                " duration = ?, search = ?, probed = 1 WHERE path = ?",
                (title, artist, album, tags.get("genre"), tags.get("year"), tags.get("track"),
                 tags.get("duration"),
                 fold(" ".join(filter(None, [title, artist, album, tags.get("genre"),
                                             os.path.basename(path)]))), path))
            self._db.commit()

    def status(self):
        with self._lock:
            total, read = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(probed), 0) FROM tracks").fetchone()
        return {"total": total, "read": read}

    @staticmethod
    def _item(row):
        return {"key": row["key"], "title": row["title"], "artist": row["artist"], "album": row["album"],
                "genre": row["genre"], "year": row["year"], "duration": row["duration"]}

    def search(self, words="", artist=None, album=None, genre=None, offset=0, limit=30):
        clauses, args = [], []
        for token in fold(words).split():
            clauses.append("search LIKE ?")
            args.append("%" + token + "%")
        for column, value in (("artist", artist), ("album", album), ("genre", genre)):
            if value:
                clauses.append(column + " = ?")
                args.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM tracks" + where, args).fetchone()[0]
            rows = self._db.execute(
                "SELECT * FROM tracks" + where +
                " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, track, title COLLATE NOCASE"
                " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
        return {"items": [self._item(r) for r in rows], "total": total}

    def facets(self, artist=None):
        """Artists and genres with their counts."""
        with self._lock:
            artists = [{"name": r[0], "count": r[1]} for r in self._db.execute(
                "SELECT artist, COUNT(*) FROM tracks WHERE artist <> '' GROUP BY artist"
                " ORDER BY artist COLLATE NOCASE")]
            genres = [{"name": r[0], "count": r[1]} for r in self._db.execute(
                "SELECT genre, COUNT(*) FROM tracks WHERE genre IS NOT NULL AND genre <> ''"
                " GROUP BY genre ORDER BY genre COLLATE NOCASE")]
            album_sql = ("SELECT album, COUNT(*) FROM tracks WHERE album <> ''" +
                         (" AND artist = ?" if artist else "") +
                         " GROUP BY album ORDER BY album COLLATE NOCASE")
            albums = [{"name": r[0], "count": r[1]} for r in
                      self._db.execute(album_sql, (artist,) if artist else ())]
        return {"artists": artists, "genres": genres, "albums": albums}

    def path_for_key(self, key):
        with self._lock:
            row = self._db.execute("SELECT path FROM tracks WHERE key = ?", (str(key),)).fetchone()
        return row["path"] if row else None

    def item_for_path(self, path):
        with self._lock:
            row = self._db.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return self._item(row) if row else None

    def item_for_basename(self, name):
        """The track whose file is called `name`."""
        with self._lock:
            pattern = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            row = self._db.execute(
                "SELECT * FROM tracks WHERE path = ? OR path LIKE ? ESCAPE '\\'"
                " OR path LIKE ? ESCAPE '\\' LIMIT 1",
                (name, "%/" + pattern, "%\\\\" + pattern)).fetchone()
        return self._item(row) if row else None

    def match(self, text):
        """The library track a suggestion most likely names, or None."""
        raw = str(text or "")
        tokens = fold(raw).split()
        if not tokens:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tracks WHERE " + " AND ".join("search LIKE ?" for _ in tokens),
                ["%" + tok + "%" for tok in tokens]).fetchall()
        if not rows:
            return None
        if " - " in raw:
            left, right = raw.split(" - ", 1)
            for artist_part, title_part in ((left, right), (right, left)):
                a_tokens, t_tokens = fold(artist_part).split(), fold(title_part).split()
                for row in rows:
                    title, artist = fold(row["title"]), fold(row["artist"])
                    if t_tokens and all(t in title for t in t_tokens) and all(a in artist for a in a_tokens):
                        return self._item(row)
        return self._item(rows[0]) if len(tokens) >= 2 else None

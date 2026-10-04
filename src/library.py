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
LOUDNESS_TIMEOUT_SEC = 300
_LOUDNESS_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")
# A genre tag can hold several genres at once.
_GENRE_SEPARATORS = re.compile(r"[;,/|]")


def fold(text):
    """Lower case, no accents, punctuation as spaces: the form searches
    compare."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def split_genres(value):
    """Every genre a tag holds, in the order they are written: "Rock; Pop"
    is two genres, "Rock" stays one. Empty parts are dropped and a genre
    written twice counts once."""
    out, seen = [], set()
    for part in _GENRE_SEPARATORS.split(str(value or "")):
        name = " ".join(part.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


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


def read_loudness(path):
    """The file's integrated loudness (LUFS, EBU R128), or None."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner", "-i", path, "-map", "0:a:0",
           "-af", "ebur128", "-f", "null", "-"]
    if os.name == "posix":
        cmd = ["nice", "-n", "19"] + cmd
    try:
        err = subprocess.run(cmd, capture_output=True, timeout=LOUDNESS_TIMEOUT_SEC).stderr
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = _LOUDNESS_RE.findall(err.decode("utf-8", errors="replace"))
    # The summary comes last; -70 is what silence measures.
    value = float(found[-1]) if found else None
    return value if value is not None and value > -70 else None


class Library:
    def __init__(self, db_path, key_fn):
        """key_fn(path) gives the opaque key the web interface uses for a
        track."""
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        known = {row[1] for row in self._db.execute("PRAGMA table_info(tracks)")}
        if "loudness" not in known:
            self._db.execute("ALTER TABLE tracks ADD COLUMN loudness REAL")
        if "measured" not in known:
            self._db.execute("ALTER TABLE tracks ADD COLUMN measured INTEGER NOT NULL DEFAULT 0")
        self._db.commit()
        self._lock = threading.Lock()
        self._key_fn = key_fn
        self._genre_cache = None

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
        self._genre_cache = None
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
        self._genre_cache = None

    def unmeasured(self, limit=20):
        with self._lock:
            return [r["path"] for r in self._db.execute(
                "SELECT path FROM tracks WHERE measured = 0 ORDER BY path LIMIT ?", (limit,))]

    def store_loudness(self, path, value):
        """Records a measurement; None too, so an unreadable file is not tried again."""
        with self._lock:
            self._db.execute("UPDATE tracks SET loudness = ?, measured = 1 WHERE path = ?", (value, path))
            self._db.commit()

    def loudness_for(self, paths):
        """{path: LUFS} for the paths already measured."""
        wanted = list(paths)
        found = {}
        with self._lock:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start:start + 500]
                rows = self._db.execute(
                    "SELECT path, loudness FROM tracks WHERE loudness IS NOT NULL AND path IN (%s)"
                    % ",".join("?" * len(chunk)), chunk)
                found.update((r["path"], r["loudness"]) for r in rows)
        return found

    def quiz_tracks(self):
        """The rows a blind test can ask about: read, with a title and an artist."""
        with self._lock:
            return [{"path": r["path"], "title": r["title"], "artist": r["artist"], "duration": r["duration"]}
                    for r in self._db.execute(
                        "SELECT path, title, artist, duration FROM tracks"
                        " WHERE probed = 1 AND title IS NOT NULL AND artist IS NOT NULL")]

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
        for column, value in (("artist", artist), ("album", album)):
            if value:
                clauses.append(column + " = ?")
                args.append(value)
        if genre:
            raw = self._raw_genres(genre)
            if not raw:
                return {"items": [], "total": 0}
            clauses.append("genre IN (%s)" % ",".join("?" * len(raw)))
            args.extend(raw)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM tracks" + where, args).fetchone()[0]
            rows = self._db.execute(
                "SELECT * FROM tracks" + where +
                " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, track, title COLLATE NOCASE"
                " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
        return {"items": [self._item(r) for r in rows], "total": total}

    def _genre_index(self):
        """Every genre the library holds, once each: a tag carrying several
        ("Rock; Pop") counts for each of them, and each entry remembers the
        raw tag values it comes from, so a search can find them back. The
        spelling shown is the one the most files use."""
        if self._genre_cache is not None:
            return self._genre_cache
        index = {}
        with self._lock:
            rows = self._db.execute(
                "SELECT genre, COUNT(*) FROM tracks WHERE genre IS NOT NULL AND genre <> ''"
                " GROUP BY genre ORDER BY COUNT(*) DESC, genre").fetchall()
        for raw, count in rows:
            for name in split_genres(raw):
                entry = index.setdefault(name.casefold(), {"name": name, "count": 0, "raw": []})
                entry["count"] += count
                entry["raw"].append(raw)
        self._genre_cache = index
        return index

    def _raw_genres(self, genre):
        """The tag values, as stored, that hold `genre` among others."""
        return list(self._genre_index().get(fold(genre), {}).get("raw") or [])

    def facets(self, artist=None):
        """Artists, genres and albums with their counts, genres one by one."""
        with self._lock:
            artists = [{"name": r[0], "count": r[1]} for r in self._db.execute(
                "SELECT artist, COUNT(*) FROM tracks WHERE artist <> '' GROUP BY artist"
                " ORDER BY artist COLLATE NOCASE")]
            album_sql = ("SELECT album, COUNT(*) FROM tracks WHERE album <> ''" +
                         (" AND artist = ?" if artist else "") +
                         " GROUP BY album ORDER BY album COLLATE NOCASE")
            albums = [{"name": r[0], "count": r[1]} for r in
                      self._db.execute(album_sql, (artist,) if artist else ())]
        genres = [{"name": entry["name"], "count": entry["count"]}
                  for entry in sorted(self._genre_index().values(),
                                      key=lambda e: fold(e["name"]))]
        return {"artists": artists, "genres": genres, "albums": albums}

    def paths_for_genres(self, genres):
        """The paths of every track tagged with one of `genres`, compared
        without case or accents - so a list built from the web interface's
        "Jazz" also finds files tagged "jazz", and a file tagged
        "Jazz; Vocal" belongs to both."""
        wanted = {fold(g) for g in (genres or []) if str(g).strip()}
        if not wanted:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT path, genre FROM tracks WHERE genre IS NOT NULL AND genre <> ''"
                " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, track,"
                " title COLLATE NOCASE").fetchall()
        return [r["path"] for r in rows
                if wanted.intersection(fold(g) for g in split_genres(r["genre"]))]

    def path_for_key(self, key):
        with self._lock:
            row = self._db.execute("SELECT path FROM tracks WHERE key = ?", (str(key),)).fetchone()
        return row["path"] if row else None

    def item_for_path(self, path):
        with self._lock:
            row = self._db.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return self._item(row) if row else None

    def items_for_paths(self, paths):
        """The catalogue rows for these paths, in the order given; a path the
        catalogue does not know is left out."""
        wanted = [p for p in paths if p]
        rows = {}
        with self._lock:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for row in self._db.execute(
                        "SELECT * FROM tracks WHERE path IN (%s)" % marks, chunk):
                    item = self._item(row)
                    item["path"] = row["path"]
                    rows[row["path"]] = item
        return [rows[p] for p in wanted if p in rows]

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

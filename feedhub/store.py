"""Local persistence for FeedHub: subscriptions, folders and cached articles.

A thin, well-tested wrapper over a single SQLite database.  Two tables:

* ``feeds``    -- one row per subscription (url is unique), with an optional
  ``folder`` label and a cached ``title``.
* ``articles`` -- cached entries, unique per ``(feed_id, guid)`` so refreshing
  the same feed twice never duplicates rows; carries ``read`` and ``starred``
  flags plus the display fields.

The database path is overridable (tests pass ``":memory:"`` or a tmp file); by
default it lives next to the GUI config at the per-user config dir.  Every
failure is surfaced as :class:`FeedHubError` -- callers catch one type.
"""

from __future__ import annotations

import os
import sqlite3
import time

from .errors import FeedHubError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL DEFAULT '',
    folder     TEXT NOT NULL DEFAULT '',
    added_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id    INTEGER NOT NULL,
    guid       TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    link       TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    published  TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    read       INTEGER NOT NULL DEFAULT 0,
    starred    INTEGER NOT NULL DEFAULT 0,
    fetched_at REAL NOT NULL,
    UNIQUE (feed_id, guid),
    FOREIGN KEY (feed_id) REFERENCES feeds (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles (feed_id);
"""

_ARTICLE_COLS = ("id", "feed_id", "guid", "title", "link", "author",
                 "published", "summary", "content", "read", "starred",
                 "fetched_at")
_FEED_COLS = ("id", "url", "title", "folder", "added_at")


class Store:
    """SQLite-backed subscriptions + article cache.

    Use as a context manager or call :meth:`close` when done.  ``path=None``
    (the default) resolves to the per-user config location; pass an explicit
    path (or ``":memory:"``) in tests.
    """

    def __init__(self, path=None):
        if path is None:
            from . import guiconfig
            path = guiconfig.store_path()
        self.path = path
        try:
            if path != ":memory:":
                parent = os.path.dirname(os.path.abspath(path))
                if parent:
                    os.makedirs(parent, exist_ok=True)
            self._db = sqlite3.connect(path)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not open feed store: {exc}") from exc

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        try:
            self._db.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- feeds -------------------------------------------------------------
    def add_feed(self, url, title="", folder=""):
        """Insert a subscription and return its row (dict).

        Raises :class:`FeedHubError` if *url* is blank or already subscribed.
        """
        url = (url or "").strip()
        if not url:
            raise FeedHubError("feed URL must not be empty")
        try:
            cur = self._db.execute(
                "INSERT INTO feeds (url, title, folder, added_at) "
                "VALUES (?, ?, ?, ?)",
                (url, title or "", folder or "", time.time()))
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            raise FeedHubError(f"already subscribed to {url}") from exc
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not add feed: {exc}") from exc
        return self.get_feed(cur.lastrowid)

    def remove_feed(self, feed):
        """Delete a feed (and its cached articles). *feed* is a url or id."""
        row = self.get_feed(feed)
        try:
            self._db.execute("DELETE FROM articles WHERE feed_id = ?", (row["id"],))
            self._db.execute("DELETE FROM feeds WHERE id = ?", (row["id"],))
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not remove feed: {exc}") from exc
        return True

    def list_feeds(self):
        """All subscriptions ordered by folder then title/url (list of dicts)."""
        try:
            rows = self._db.execute(
                "SELECT * FROM feeds ORDER BY folder COLLATE NOCASE, "
                "title COLLATE NOCASE, url COLLATE NOCASE").fetchall()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not list feeds: {exc}") from exc
        return [dict(r) for r in rows]

    def get_feed(self, feed):
        """Resolve *feed* (a row id, url, or title) to a feed dict.

        Raises :class:`FeedHubError` if no feed matches.
        """
        if isinstance(feed, dict):
            feed = feed.get("id", feed.get("url"))
        row = None
        try:
            if isinstance(feed, int) or (isinstance(feed, str) and feed.isdigit()):
                row = self._db.execute(
                    "SELECT * FROM feeds WHERE id = ?", (int(feed),)).fetchone()
            if row is None and isinstance(feed, str):
                row = self._db.execute(
                    "SELECT * FROM feeds WHERE url = ?", (feed.strip(),)).fetchone()
            if row is None and isinstance(feed, str):
                row = self._db.execute(
                    "SELECT * FROM feeds WHERE title = ? COLLATE NOCASE",
                    (feed.strip(),)).fetchone()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not look up feed: {exc}") from exc
        if row is None:
            raise FeedHubError(f"no such feed: {feed}")
        return dict(row)

    def find_feed(self, feed):
        """Like :meth:`get_feed` but returns ``None`` instead of raising."""
        try:
            return self.get_feed(feed)
        except FeedHubError:
            return None

    def set_feed_title(self, feed, title):
        row = self.get_feed(feed)
        try:
            self._db.execute("UPDATE feeds SET title = ? WHERE id = ?",
                             (title or "", row["id"]))
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not update feed: {exc}") from exc

    def set_feed_folder(self, feed, folder):
        row = self.get_feed(feed)
        try:
            self._db.execute("UPDATE feeds SET folder = ? WHERE id = ?",
                             (folder or "", row["id"]))
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not update feed: {exc}") from exc

    def list_folders(self):
        """Distinct non-empty folder labels, sorted."""
        try:
            rows = self._db.execute(
                "SELECT DISTINCT folder FROM feeds WHERE folder != '' "
                "ORDER BY folder COLLATE NOCASE").fetchall()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not list folders: {exc}") from exc
        return [r["folder"] for r in rows]

    # -- articles ----------------------------------------------------------
    def upsert_article(self, feed, article):
        """Insert *article* if new for this feed; return True if it was new.

        Dedupe key is ``(feed_id, guid)`` where guid is the entry id (falling
        back to its link).  Existing rows keep their read/starred flags and are
        refreshed with the latest title/summary/content.
        """
        row = self.get_feed(feed)
        guid = (article.get("id") or article.get("guid")
                or article.get("link") or "").strip()
        if not guid:
            # No stable identity: synthesise one from title+published so the
            # same entry still dedupes across refreshes.
            guid = "hash:" + str(hash((article.get("title", ""),
                                       article.get("published", ""))))
        now = time.time()
        try:
            existing = self._db.execute(
                "SELECT id FROM articles WHERE feed_id = ? AND guid = ?",
                (row["id"], guid)).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO articles (feed_id, guid, title, link, author, "
                    "published, summary, content, read, starred, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
                    (row["id"], guid, article.get("title", ""),
                     article.get("link", ""), article.get("author", ""),
                     article.get("published", ""), article.get("summary", ""),
                     article.get("content", ""), now))
                self._db.commit()
                return True
            # Refresh mutable display fields; leave read/starred alone.
            self._db.execute(
                "UPDATE articles SET title = ?, link = ?, author = ?, "
                "published = ?, summary = ?, content = ? WHERE id = ?",
                (article.get("title", ""), article.get("link", ""),
                 article.get("author", ""), article.get("published", ""),
                 article.get("summary", ""), article.get("content", ""),
                 existing["id"]))
            self._db.commit()
            return False
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not cache article: {exc}") from exc

    def list_articles(self, feed, unread_only=False, starred_only=False,
                      search=None):
        """Cached articles for *feed*, newest-cached first (list of dicts)."""
        row = self.get_feed(feed)
        sql = "SELECT * FROM articles WHERE feed_id = ?"
        params = [row["id"]]
        if unread_only:
            sql += " AND read = 0"
        if starred_only:
            sql += " AND starred = 1"
        if search:
            sql += " AND (title LIKE ? OR summary LIKE ? OR content LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like]
        sql += " ORDER BY fetched_at DESC, id DESC"
        try:
            rows = self._db.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not list articles: {exc}") from exc
        return [dict(r) for r in rows]

    def search_articles(self, text, unread_only=False):
        """Search across ALL feeds; returns article dicts with feed_id."""
        like = f"%{text}%"
        sql = ("SELECT * FROM articles WHERE "
               "(title LIKE ? OR summary LIKE ? OR content LIKE ?)")
        params = [like, like, like]
        if unread_only:
            sql += " AND read = 0"
        sql += " ORDER BY fetched_at DESC, id DESC"
        try:
            rows = self._db.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not search articles: {exc}") from exc
        return [dict(r) for r in rows]

    def get_article(self, article_id):
        try:
            row = self._db.execute(
                "SELECT * FROM articles WHERE id = ?",
                (int(article_id),)).fetchone()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise FeedHubError(f"could not read article: {exc}") from exc
        if row is None:
            raise FeedHubError(f"no such article: {article_id}")
        return dict(row)

    def mark_read(self, article_id, read=True):
        return self._set_flag(article_id, "read", read)

    def mark_starred(self, article_id, starred=True):
        return self._set_flag(article_id, "starred", starred)

    def _set_flag(self, article_id, column, value):
        art = self.get_article(article_id)  # validates existence
        try:
            self._db.execute(
                f"UPDATE articles SET {column} = ? WHERE id = ?",
                (1 if value else 0, art["id"]))
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not update article: {exc}") from exc
        return True

    def mark_all_read(self, feed, read=True):
        """Mark every cached article in *feed* read (or unread). Returns count."""
        row = self.get_feed(feed)
        try:
            cur = self._db.execute(
                "UPDATE articles SET read = ? WHERE feed_id = ?",
                (1 if read else 0, row["id"]))
            self._db.commit()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not update articles: {exc}") from exc
        return cur.rowcount

    def unread_count(self, feed=None):
        """Unread article count for *feed*, or across all feeds if ``None``."""
        try:
            if feed is None:
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM articles WHERE read = 0").fetchone()
            else:
                fr = self.get_feed(feed)
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM articles "
                    "WHERE feed_id = ? AND read = 0", (fr["id"],)).fetchone()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not count unread: {exc}") from exc
        return int(row["n"])

    def unread_counts(self):
        """Map of ``feed_id -> unread count`` for every feed (0 included)."""
        counts = {f["id"]: 0 for f in self.list_feeds()}
        try:
            rows = self._db.execute(
                "SELECT feed_id, COUNT(*) AS n FROM articles "
                "WHERE read = 0 GROUP BY feed_id").fetchall()
        except sqlite3.Error as exc:
            raise FeedHubError(f"could not count unread: {exc}") from exc
        for r in rows:
            counts[r["feed_id"]] = int(r["n"])
        return counts

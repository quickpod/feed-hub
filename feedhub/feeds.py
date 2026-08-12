"""Parsing and fetching of RSS/Atom feeds.

Built on :mod:`feedparser` for the parsing (which works on a plain string with
no network) and stdlib :mod:`urllib` for fetching (so there is no ``requests``
dependency).  Three public functions:

* :func:`parse` -- turn feed text/bytes into a normalised dict with a
  ``feed_title`` and a list of ``entries`` (each with a stable ``id``, plus
  ``title``, ``link``, ``author``, ``published``, ``summary`` and ``content``).
* :func:`fetch` -- download a URL's raw bytes over HTTP(S); network errors are
  wrapped as :class:`FeedHubError`.  Only ``http``/``https`` are allowed.
* :func:`refresh` -- fetch + parse a subscribed feed and upsert its entries
  into a :class:`~feedhub.store.Store`, deduped by entry id/link.

Parsing never touches the network, which keeps it fully unit-testable offline.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from .errors import FeedHubError

USER_AGENT = "FeedHub/1.0 (+https://quickopen.ai/projects/feed-hub)"
DEFAULT_TIMEOUT = 20


def _entry_content(entry):
    """Best HTML/text body for an entry: content[].value, else summary."""
    content = entry.get("content")
    if content:
        try:
            # feedparser gives a list of {'value': ...}; join non-empty values.
            values = [c.get("value", "") for c in content if c.get("value")]
            if values:
                return "\n".join(values)
        except Exception:
            pass
    return entry.get("summary", "") or ""


def _entry_id(entry):
    """A stable identity for dedupe: id/guid, then link, then title."""
    return (entry.get("id") or entry.get("guid") or entry.get("link")
            or entry.get("title") or "")


def parse(text_or_bytes):
    """Parse RSS/Atom *text_or_bytes* into a normalised dict.

    Returns ``{"feed_title": str, "entries": [ {id,title,link,author,
    published,summary,content}, ... ]}``.  Works on a string with no network.
    Raises :class:`FeedHubError` only when the input is unusable (empty, or not
    a feed at all).
    """
    if text_or_bytes is None:
        raise FeedHubError("nothing to parse (empty feed)")
    try:
        import feedparser
    except Exception as exc:  # pragma: no cover - dependency is declared
        raise FeedHubError(
            "feedparser is required to read feeds but is not installed") from exc

    try:
        parsed = feedparser.parse(text_or_bytes)
    except Exception as exc:
        raise FeedHubError(f"could not parse feed: {exc}") from exc

    # feedparser is very forgiving: a total non-feed yields bozo + no entries
    # and no feed title.  Treat that as an error so callers get a clean message.
    feed_meta = getattr(parsed, "feed", {}) or {}
    entries_raw = getattr(parsed, "entries", []) or []
    feed_title = (feed_meta.get("title") or "").strip()
    if not feed_title and not entries_raw:
        detail = ""
        if getattr(parsed, "bozo", 0) and getattr(parsed, "bozo_exception", None):
            detail = f": {parsed.bozo_exception}"
        raise FeedHubError("input does not look like an RSS or Atom feed" + detail)

    entries = []
    for entry in entries_raw:
        author = entry.get("author", "")
        if not author:
            detail = entry.get("author_detail") or {}
            author = detail.get("name", "") if isinstance(detail, dict) else ""
        entries.append({
            "id": _entry_id(entry),
            "title": (entry.get("title", "") or "").strip(),
            "link": entry.get("link", "") or "",
            "author": author or "",
            "published": (entry.get("published") or entry.get("updated")
                          or entry.get("created") or ""),
            "summary": entry.get("summary", "") or "",
            "content": _entry_content(entry),
        })
    return {"feed_title": feed_title, "entries": entries}


def fetch(url, timeout=DEFAULT_TIMEOUT, opener=None):
    """Download *url* and return its raw bytes.

    Only ``http``/``https`` URLs are accepted.  Any network/HTTP failure is
    wrapped as :class:`FeedHubError` so callers never see a raw urllib error.
    ``opener`` is injectable for tests.
    """
    url = (url or "").strip()
    if not url:
        raise FeedHubError("feed URL must not be empty")
    if not url.lower().startswith(("http://", "https://")):
        raise FeedHubError(f"unsupported URL scheme (need http/https): {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        _open = opener if opener is not None else urllib.request.urlopen
        with _open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise FeedHubError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise FeedHubError(f"could not reach {url}: {exc.reason}") from exc
    except Exception as exc:  # timeouts, socket errors, malformed responses
        raise FeedHubError(f"could not fetch {url}: {exc}") from exc


def fetch_and_parse(url, timeout=DEFAULT_TIMEOUT, fetcher=None):
    """Convenience: :func:`fetch` a URL then :func:`parse` the result."""
    fetcher = fetcher or fetch
    return parse(fetcher(url, timeout=timeout))


def refresh(store, feed, fetcher=None, timeout=DEFAULT_TIMEOUT):
    """Fetch a subscribed *feed*, parse it, and upsert entries into *store*.

    *feed* is anything :meth:`Store.get_feed` accepts (id, url or title).
    Returns ``{"feed": <feed dict>, "new": int, "total": int}`` where ``new`` is
    the count of entries not previously cached (dedupe by id/link).  ``fetcher``
    is injectable for tests (defaults to :func:`fetch`).  Network/parse failures
    raise :class:`FeedHubError`.
    """
    feed_row = store.get_feed(feed)
    fetcher = fetcher or fetch
    raw = fetcher(feed_row["url"], timeout=timeout)
    parsed = parse(raw)

    # Learn the feed's real title on first successful refresh.
    if parsed["feed_title"] and not feed_row.get("title"):
        store.set_feed_title(feed_row["id"], parsed["feed_title"])
        feed_row = store.get_feed(feed_row["id"])

    new = 0
    for entry in parsed["entries"]:
        if store.upsert_article(feed_row["id"], entry):
            new += 1
    return {"feed": feed_row, "new": new, "total": len(parsed["entries"])}

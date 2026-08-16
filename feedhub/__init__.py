"""feedhub -- a permissively-licensed, offline RSS/Atom reader library.

Public API, all raising :class:`FeedHubError` on failure::

    from feedhub import Store, parse, fetch, refresh
    data = parse(rss_text)                 # no network -- works on a string
    with Store(":memory:") as store:
        feed = store.add_feed("https://example.com/feed.xml", folder="News")
        refresh(store, feed, fetcher=my_fetcher)
        for art in store.list_articles(feed, unread_only=True):
            print(art["title"])

Parsing (:func:`parse`) never touches the network; fetching is a separate,
guarded step.  The tkinter GUI (:mod:`feedhub.gui`) and the CLI
(:mod:`feedhub.__main__`) are thin layers over this module.
"""

from __future__ import annotations

from .errors import FeedHubError
from .store import Store
from .feeds import parse, fetch, fetch_and_parse, refresh
from .opml import parse_opml, import_opml, export_opml
from .reader import clean_html, to_text

__version__ = "1.1.0"

__all__ = [
    "FeedHubError",
    "Store",
    "parse",
    "fetch",
    "fetch_and_parse",
    "refresh",
    "parse_opml",
    "import_opml",
    "export_opml",
    "clean_html",
    "to_text",
    "__version__",
]

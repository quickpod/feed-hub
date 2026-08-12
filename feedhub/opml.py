"""OPML import/export for FeedHub subscriptions.

OPML is the portable format every feed reader speaks.  We support the common
shape: ``<outline>`` elements carrying ``xmlUrl`` are feeds; an ``<outline>``
*without* ``xmlUrl`` that contains feed outlines is a folder, and its ``text``/
``title`` becomes the folder label (one level of nesting, which covers virtually
every real-world OPML).

* :func:`parse_opml` -- text -> ``[{title, url, folder}, ...]`` (no store).
* :func:`import_opml` -- parse then add the feeds to a :class:`Store`,
  skipping URLs already subscribed.  Returns the list of newly-added feeds.
* :func:`export_opml` -- a :class:`Store` -> OPML text, folders as groups.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .errors import FeedHubError


def _outline_title(el):
    return (el.get("text") or el.get("title") or "").strip()


def _walk(el, folder, out):
    """Recurse the outline tree, collecting feeds and tracking the folder."""
    for child in el.findall("outline"):
        url = (child.get("xmlUrl") or child.get("xmlurl") or "").strip()
        if url:
            out.append({
                "title": _outline_title(child),
                "url": url,
                "folder": folder,
            })
        else:
            # A container outline: its label becomes the folder for its children.
            label = _outline_title(child) or folder
            _walk(child, label, out)


def parse_opml(text):
    """Parse OPML *text* into a list of ``{title, url, folder}`` dicts.

    Raises :class:`FeedHubError` on malformed XML or a missing ``<body>``.
    """
    if text is None:
        raise FeedHubError("nothing to import (empty OPML)")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedHubError(f"invalid OPML XML: {exc}") from exc
    body = root.find("body")
    if body is None:
        raise FeedHubError("OPML has no <body> element")
    feeds = []
    _walk(body, "", feeds)
    return feeds


def import_opml(store, text):
    """Import feeds from OPML *text* into *store*.

    Feeds whose URL is already subscribed are skipped.  Returns the list of
    newly-added feed dicts.  Raises :class:`FeedHubError` on malformed input.
    """
    added = []
    for item in parse_opml(text):
        if store.find_feed(item["url"]) is not None:
            continue
        try:
            row = store.add_feed(item["url"], title=item["title"],
                                 folder=item["folder"])
        except FeedHubError:
            continue  # racey duplicate / bad row -> just skip it
        added.append(row)
    return added


def export_opml(store, title="FeedHub subscriptions"):
    """Serialise *store*'s subscriptions to OPML text (folders as groups)."""
    feeds = store.list_feeds()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<opml version="2.0">',
             '  <head>',
             f'    <title>{escape(title)}</title>',
             '  </head>',
             '  <body>']

    def feed_line(feed, indent):
        text = feed.get("title") or feed.get("url")
        pad = " " * indent
        return (f'{pad}<outline type="rss" text={quoteattr(text)} '
                f'title={quoteattr(text)} xmlUrl={quoteattr(feed["url"])}/>')

    # Group by folder, preserving list_feeds() ordering.
    by_folder = {}
    order = []
    for feed in feeds:
        folder = feed.get("folder") or ""
        if folder not in by_folder:
            by_folder[folder] = []
            order.append(folder)

        by_folder[folder].append(feed)

    for folder in order:
        if folder:
            lines.append(f'    <outline text={quoteattr(folder)} '
                         f'title={quoteattr(folder)}>')
            for feed in by_folder[folder]:
                lines.append(feed_line(feed, 6))
            lines.append('    </outline>')
        else:
            for feed in by_folder[folder]:
                lines.append(feed_line(feed, 4))

    lines += ['  </body>', '</opml>', '']
    return "\n".join(lines)

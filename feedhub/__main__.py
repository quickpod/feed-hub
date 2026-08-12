"""Command-line interface: ``python -m feedhub <command> ...``.

Manages subscriptions and reads cached articles from a local store.  Fetching
is the only command that touches the network; everything else works offline on
whatever has already been cached.  Every failure exits cleanly (status 1, a
one-line ``error:`` message) via the single :class:`FeedHubError` type -- never
a traceback.

Commands::

    add <url> [--folder F] [--title T]   subscribe to a feed
    list                                 show subscriptions (+ unread counts)
    remove <url>                         unsubscribe (drops cached articles)
    refresh [<feed> | --all]             fetch + cache new articles
    articles <feed> [--unread] [--starred] [--search Q]
    read <article_id> [--unread]         mark an article read (or unread)
    star <article_id> [--unstar]         star an article (or remove the star)
    opml import <file>                   import subscriptions from OPML
    opml export <file>                   export subscriptions to OPML

Global: ``--store PATH`` overrides the database location (default: the per-user
config dir).
"""

from __future__ import annotations

import argparse
import sys

from .errors import FeedHubError
from .store import Store
from . import feeds as feeds_mod
from . import opml as opml_mod
from .reader import to_text


def _open_store(args):
    return Store(getattr(args, "store", None))


def _feed_label(feed):
    name = feed.get("title") or feed.get("url")
    folder = feed.get("folder")
    return f"[{folder}] {name}" if folder else name


# --- command handlers -------------------------------------------------------

def cmd_add(a):
    with _open_store(a) as store:
        feed = store.add_feed(a.url, title=a.title or "", folder=a.folder or "")
        print(f"Subscribed to {_feed_label(feed)}  (id {feed['id']})")


def cmd_list(a):
    with _open_store(a) as store:
        feeds = store.list_feeds()
        if not feeds:
            print("No subscriptions yet. Add one with: feedhub add <url>")
            return
        counts = store.unread_counts()
        for feed in feeds:
            unread = counts.get(feed["id"], 0)
            badge = f"({unread} unread)" if unread else ""
            print(f"  {feed['id']:>3}  {_feed_label(feed):<50} {badge}")
            print(f"       {feed['url']}")


def cmd_remove(a):
    with _open_store(a) as store:
        feed = store.get_feed(a.url)
        store.remove_feed(feed["id"])
        print(f"Removed {_feed_label(feed)}")


def cmd_refresh(a):
    with _open_store(a) as store:
        if a.all or not a.feed:
            targets = store.list_feeds()
            if not targets:
                print("No subscriptions to refresh.")
                return
        else:
            targets = [store.get_feed(a.feed)]
        total_new = 0
        errors = 0
        for feed in targets:
            try:
                result = feeds_mod.refresh(store, feed["id"])
            except FeedHubError as exc:
                errors += 1
                print(f"  ! {_feed_label(feed)}: {exc}", file=sys.stderr)
                continue
            total_new += result["new"]
            print(f"  {_feed_label(result['feed'])}: "
                  f"{result['new']} new / {result['total']} entries")
        print(f"Done. {total_new} new article(s) across {len(targets)} feed(s).")
        if errors and errors == len(targets):
            # Nothing succeeded -- surface a non-zero exit.
            raise FeedHubError("all feeds failed to refresh")


def cmd_articles(a):
    with _open_store(a) as store:
        feed = store.get_feed(a.feed)
        arts = store.list_articles(feed["id"], unread_only=a.unread,
                                   starred_only=a.starred, search=a.search)
        if not arts:
            print("(no matching cached articles -- try 'refresh')")
            return
        print(f"{_feed_label(feed)} -- {len(arts)} article(s):")
        for art in arts:
            flags = ("*" if art["starred"] else " ") + \
                    (" " if art["read"] else "•")
            when = f"  {art['published']}" if art["published"] else ""
            print(f"  {flags} {art['id']:>4}  {art['title'] or '(untitled)'}{when}")


def cmd_read(a):
    with _open_store(a) as store:
        art = store.get_article(a.article_id)
        if a.mark_only or a.unread:
            store.mark_read(art["id"], read=not a.unread)
            state = "unread" if a.unread else "read"
            print(f"Marked article {art['id']} as {state}.")
            return
        # Print the article and mark it read.
        store.mark_read(art["id"], read=True)
        print(art["title"] or "(untitled)")
        meta = " · ".join(x for x in (art["author"], art["published"]) if x)
        if meta:
            print(meta)
        if art["link"]:
            print(art["link"])
        print("-" * 60)
        body = to_text(art["content"] or art["summary"])
        print(body or "(no content)")


def cmd_star(a):
    with _open_store(a) as store:
        art = store.get_article(a.article_id)
        store.mark_starred(art["id"], starred=not a.unstar)
        state = "un-starred" if a.unstar else "starred"
        print(f"Article {art['id']} {state}.")


def cmd_opml(a):
    with _open_store(a) as store:
        if a.opml_cmd == "import":
            try:
                with open(a.file, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                raise FeedHubError(f"could not read {a.file}: {exc}") from exc
            added = opml_mod.import_opml(store, text)
            print(f"Imported {len(added)} new feed(s) from {a.file}.")
            for feed in added:
                print(f"  + {_feed_label(feed)}")
        else:  # export
            text = opml_mod.export_opml(store)
            try:
                with open(a.file, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError as exc:
                raise FeedHubError(f"could not write {a.file}: {exc}") from exc
            print(f"Exported {len(store.list_feeds())} feed(s) to {a.file}.")


# --- parser -----------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="feedhub",
        description="FeedHub -- an offline RSS/Atom reader (CLI).")
    p.add_argument("--store", metavar="PATH",
                   help="database path (default: per-user config dir)")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    def add(name, help_text, func):
        s = sub.add_parser(name, help=help_text)
        s.set_defaults(func=func)
        return s

    s = add("add", "Subscribe to a feed URL", cmd_add)
    s.add_argument("url")
    s.add_argument("--folder", help="folder/category label")
    s.add_argument("--title", help="override the feed's display title")

    add("list", "List subscriptions with unread counts", cmd_list)

    s = add("remove", "Unsubscribe from a feed", cmd_remove)
    s.add_argument("url", help="feed URL, id, or title")

    s = add("refresh", "Fetch new articles (network)", cmd_refresh)
    s.add_argument("feed", nargs="?", help="feed URL/id/title (omit with --all)")
    s.add_argument("--all", action="store_true", help="refresh every feed")

    s = add("articles", "List cached articles for a feed", cmd_articles)
    s.add_argument("feed", help="feed URL, id, or title")
    s.add_argument("--unread", action="store_true", help="only unread")
    s.add_argument("--starred", action="store_true", help="only starred")
    s.add_argument("--search", metavar="Q", help="filter by text")

    s = add("read", "Show an article (and mark it read)", cmd_read)
    s.add_argument("article_id")
    s.add_argument("--mark-only", action="store_true",
                   help="just mark read, don't print")
    s.add_argument("--unread", action="store_true", help="mark UNread instead")

    s = add("star", "Star an article for later", cmd_star)
    s.add_argument("article_id")
    s.add_argument("--unstar", action="store_true", help="remove the star")

    s = add("opml", "Import/export subscriptions as OPML", cmd_opml)
    s.add_argument("opml_cmd", choices=["import", "export"])
    s.add_argument("file")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except FeedHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

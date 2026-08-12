"""Store CRUD, mark read/starred, unread counts, refresh dedupe."""

import pytest

from feedhub import feeds as feeds_mod
from feedhub.errors import FeedHubError


def test_add_list_remove_feed(store):
    feed = store.add_feed("https://example.com/rss.xml", folder="News")
    assert feed["url"] == "https://example.com/rss.xml"
    assert feed["folder"] == "News"
    assert len(store.list_feeds()) == 1
    store.remove_feed(feed["id"])
    assert store.list_feeds() == []


def test_add_duplicate_feed_raises(store):
    store.add_feed("https://example.com/rss.xml")
    with pytest.raises(FeedHubError):
        store.add_feed("https://example.com/rss.xml")


def test_add_empty_url_raises(store):
    with pytest.raises(FeedHubError):
        store.add_feed("   ")


def test_get_feed_by_url_id_title(store):
    feed = store.add_feed("https://example.com/rss.xml", title="My Feed")
    assert store.get_feed(feed["id"])["url"] == feed["url"]
    assert store.get_feed("https://example.com/rss.xml")["id"] == feed["id"]
    assert store.get_feed("My Feed")["id"] == feed["id"]
    with pytest.raises(FeedHubError):
        store.get_feed("https://nope.example/x")


def test_folders(store):
    store.add_feed("https://a.example/feed", folder="News")
    store.add_feed("https://b.example/feed", folder="Tech")
    store.add_feed("https://c.example/feed")
    assert store.list_folders() == ["News", "Tech"]


def test_upsert_and_mark_read_starred(store):
    feed = store.add_feed("https://example.com/rss.xml")
    art = {"id": "e1", "title": "Hello", "link": "https://example.com/1",
           "summary": "hi", "content": "<p>hi</p>"}
    assert store.upsert_article(feed["id"], art) is True
    # duplicate guid -> not new
    assert store.upsert_article(feed["id"], art) is False
    arts = store.list_articles(feed["id"])
    assert len(arts) == 1
    aid = arts[0]["id"]
    assert arts[0]["read"] == 0 and arts[0]["starred"] == 0

    store.mark_read(aid, True)
    store.mark_starred(aid, True)
    got = store.get_article(aid)
    assert got["read"] == 1 and got["starred"] == 1

    store.mark_read(aid, False)
    assert store.get_article(aid)["read"] == 0


def test_unread_count_and_mark_all(store):
    feed = store.add_feed("https://example.com/rss.xml")
    for i in range(3):
        store.upsert_article(feed["id"], {"id": f"e{i}", "title": f"t{i}"})
    assert store.unread_count(feed["id"]) == 3
    assert store.unread_count() == 3
    # mark one read
    aid = store.list_articles(feed["id"])[0]["id"]
    store.mark_read(aid, True)
    assert store.unread_count(feed["id"]) == 2
    counts = store.unread_counts()
    assert counts[feed["id"]] == 2
    store.mark_all_read(feed["id"], True)
    assert store.unread_count(feed["id"]) == 0


def test_list_articles_filters(store):
    feed = store.add_feed("https://example.com/rss.xml")
    store.upsert_article(feed["id"], {"id": "a", "title": "Apple pie"})
    store.upsert_article(feed["id"], {"id": "b", "title": "Banana bread"})
    aid = store.list_articles(feed["id"])[0]["id"]
    store.mark_starred(aid, True)
    assert len(store.list_articles(feed["id"], starred_only=True)) == 1
    assert len(store.list_articles(feed["id"], search="Banana")) == 1
    assert len(store.list_articles(feed["id"], unread_only=True)) == 2


def test_refresh_dedupes(store, rss_sample):
    feed = store.add_feed("https://example.com/rss.xml")
    fetcher = lambda url, timeout=20: rss_sample  # noqa: E731 - local stub

    r1 = feeds_mod.refresh(store, feed["id"], fetcher=fetcher)
    assert r1["new"] == 2
    assert r1["total"] == 2
    # second refresh of identical content -> zero new, no duplicates
    r2 = feeds_mod.refresh(store, feed["id"], fetcher=fetcher)
    assert r2["new"] == 0
    assert len(store.list_articles(feed["id"])) == 2
    # feed title learned from the parsed feed
    assert store.get_feed(feed["id"])["title"] == "Example RSS Feed"


def test_refresh_atom_dedupes(store, atom_sample):
    feed = store.add_feed("https://atom.example.com/atom.xml")
    fetcher = lambda url, timeout=20: atom_sample  # noqa: E731
    feeds_mod.refresh(store, feed["id"], fetcher=fetcher)
    feeds_mod.refresh(store, feed["id"], fetcher=fetcher)
    assert len(store.list_articles(feed["id"])) == 2

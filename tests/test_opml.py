"""OPML import parses feeds + folders; export round-trips."""

import pytest

from feedhub import opml
from feedhub.errors import FeedHubError


def test_parse_opml_feeds_and_folders(opml_sample):
    items = opml.parse_opml(opml_sample)
    urls = {i["url"]: i for i in items}
    assert len(items) == 3
    assert urls["https://example.com/rss.xml"]["folder"] == "News"
    assert urls["https://atom.example.com/atom.xml"]["folder"] == "News"
    assert urls["https://toplevel.example.com/feed"]["folder"] == ""


def test_import_opml_into_store(store, opml_sample):
    added = opml.import_opml(store, opml_sample)
    assert len(added) == 3
    assert len(store.list_feeds()) == 3
    assert "News" in store.list_folders()
    # re-import is idempotent (URLs already present are skipped)
    again = opml.import_opml(store, opml_sample)
    assert again == []
    assert len(store.list_feeds()) == 3


def test_export_round_trips(store, opml_sample):
    opml.import_opml(store, opml_sample)
    exported = opml.export_opml(store)
    assert exported.strip().startswith("<?xml")

    # Import the export into a second, fresh store and compare feed sets.
    from feedhub.store import Store
    other = Store(":memory:")
    try:
        opml.import_opml(other, exported)
        urls_a = sorted(f["url"] for f in store.list_feeds())
        urls_b = sorted(f["url"] for f in other.list_feeds())
        assert urls_a == urls_b
        folders_a = sorted(store.list_folders())
        folders_b = sorted(other.list_folders())
        assert folders_a == folders_b
    finally:
        other.close()


def test_parse_bad_opml_raises():
    with pytest.raises(FeedHubError):
        opml.parse_opml("<opml><not-closed>")

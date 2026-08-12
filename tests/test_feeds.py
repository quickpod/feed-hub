"""Parsing of RSS and Atom sample strings (no network)."""

import pytest

from feedhub import parse
from feedhub.errors import FeedHubError


def test_parse_rss_title_and_entries(rss_sample):
    data = parse(rss_sample)
    assert data["feed_title"] == "Example RSS Feed"
    assert len(data["entries"]) == 2
    first = data["entries"][0]
    assert first["title"] == "First RSS Post"
    assert first["link"] == "https://example.com/posts/1"
    assert first["id"] == "https://example.com/posts/1"
    assert first["published"]  # a non-empty date string
    assert "Alice" in first["author"]


def test_parse_atom_title_and_entries(atom_sample):
    data = parse(atom_sample)
    assert data["feed_title"] == "Example Atom Feed"
    assert len(data["entries"]) == 2
    first = data["entries"][0]
    assert first["title"] == "First Atom Entry"
    assert first["link"] == "https://atom.example.com/entries/1"
    assert first["id"] == "urn:uuid:atom-1"
    assert first["published"].startswith("2025-08-06")
    assert first["author"] == "Bob"
    # content came from <content>, not <summary>
    assert "Body" in first["content"]


def test_parse_accepts_bytes(rss_sample):
    data = parse(rss_sample.encode("utf-8"))
    assert data["feed_title"] == "Example RSS Feed"


def test_parse_rejects_non_feed():
    with pytest.raises(FeedHubError):
        parse("this is not a feed at all, just text")


def test_parse_rejects_none():
    with pytest.raises(FeedHubError):
        parse(None)

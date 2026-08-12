"""fetch() over a real localhost http.server, plus add+refresh via localhost."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from feedhub import feeds as feeds_mod
from feedhub.errors import FeedHubError
from feedhub.store import Store
from .conftest import RSS_SAMPLE


class _FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/feed.xml":
            body = RSS_SAMPLE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # keep test output quiet


@pytest.fixture
def feed_server():
    server = HTTPServer(("127.0.0.1", 0), _FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_from_localhost(feed_server):
    raw = feeds_mod.fetch(feed_server + "/feed.xml")
    assert b"Example RSS Feed" in raw
    data = feeds_mod.parse(raw)
    assert data["feed_title"] == "Example RSS Feed"


def test_fetch_404_raises(feed_server):
    with pytest.raises(FeedHubError):
        feeds_mod.fetch(feed_server + "/missing")


def test_fetch_rejects_bad_scheme():
    with pytest.raises(FeedHubError):
        feeds_mod.fetch("ftp://example.com/feed")


def test_add_and_refresh_over_localhost(feed_server):
    store = Store(":memory:")
    try:
        url = feed_server + "/feed.xml"
        feed = store.add_feed(url)
        result = feeds_mod.refresh(store, feed["id"])
        assert result["new"] == 2
        titles = [a["title"] for a in store.list_articles(feed["id"])]
        assert "First RSS Post" in titles
    finally:
        store.close()

"""CLI smoke tests: add/list/articles/read/star/opml + clean error exit."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from feedhub import __main__ as cli
from .conftest import RSS_SAMPLE


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = RSS_SAMPLE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}/feed.xml"
    finally:
        srv.shutdown()
        srv.server_close()


def run(dbpath, *args):
    return cli.main(["--store", str(dbpath), *args])


def test_cli_full_flow(tmp_path, server, capsys):
    db = tmp_path / "cli.db"

    assert run(db, "add", server, "--folder", "News") == 0
    assert "Subscribed" in capsys.readouterr().out

    assert run(db, "refresh", "--all") == 0
    assert "new" in capsys.readouterr().out

    assert run(db, "list") == 0
    out = capsys.readouterr().out
    assert "News" in out

    assert run(db, "articles", server) == 0
    out = capsys.readouterr().out
    assert "First RSS Post" in out

    # read article id 1 (first inserted)
    assert run(db, "read", "1") == 0
    out = capsys.readouterr().out
    assert "First RSS Post" in out

    assert run(db, "star", "1") == 0
    assert "starred" in capsys.readouterr().out.lower()

    # unread filter should now show one fewer than total (id 1 was read)
    assert run(db, "articles", server, "--unread") == 0
    out = capsys.readouterr().out
    assert "First RSS Post" not in out  # already read
    assert "Second RSS Post" in out


def test_cli_opml_export_import(tmp_path, server, capsys):
    db = tmp_path / "cli.db"
    run(db, "add", server, "--folder", "News")
    capsys.readouterr()

    opml_file = tmp_path / "subs.opml"
    assert run(db, "opml", "export", str(opml_file)) == 0
    assert opml_file.exists()

    db2 = tmp_path / "cli2.db"
    assert run(db2, "opml", "import", str(opml_file)) == 0
    out = capsys.readouterr().out
    assert "Imported 1" in out


def test_cli_error_exit_is_clean(tmp_path, capsys):
    db = tmp_path / "cli.db"
    # removing a feed that doesn't exist -> FeedHubError -> exit 1, no traceback
    rc = run(db, "remove", "https://nope.example/feed")
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")

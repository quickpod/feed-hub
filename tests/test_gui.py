"""GUI tests for the 1.1.0 Aura layout-language rework.

Pure helpers run anywhere; the App tests need a display (run the suite under
``xvfb-run -a python3 -m pytest``) and are skipped headless, mirroring the
house pattern.  Everything is hermetic: FEEDHUB_HOME + the store live in the
test's tmp dir; no network is touched (articles are seeded via the store).
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedhub import gui, guiconfig  # noqa: E402
from feedhub.store import Store  # noqa: E402


# ---------------------------------------------------------------------------
# pure helpers (no display needed)
# ---------------------------------------------------------------------------
def test_rel_date_buckets():
    now = time.time()
    assert gui.rel_date(None) == ""
    assert gui.rel_date(now - 10, now) == "now"
    assert gui.rel_date(now - 300, now) == "5m"
    assert gui.rel_date(now - 400 * 86400, now)      # a year form renders


def test_parse_when_formats():
    now = time.time()
    assert gui.parse_when({"published": "Mon, 04 Aug 2025 09:00:00 GMT",
                           "fetched_at": now})
    iso = gui.parse_when({"published": "2025-08-06T12:00:00Z",
                          "fetched_at": now})
    assert iso and abs(iso - 1754481600) < 86400
    assert gui.parse_when({"published": "not a date",
                           "fetched_at": now}) == now
    assert gui.parse_when({"published": ""}) is None


def test_theme_defaults_to_system(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDHUB_HOME", str(tmp_path))
    assert guiconfig.get_theme() == "system"
    guiconfig.set_theme("dark")
    assert guiconfig.get_theme() == "dark"
    guiconfig.set_theme("bogus")            # rejected
    assert guiconfig.get_theme() == "dark"
    guiconfig.set_theme("system")
    assert guiconfig.get_theme() == "system"


# ---------------------------------------------------------------------------
# the real window (Xvfb)
# ---------------------------------------------------------------------------
needs_display = pytest.mark.skipif(
    sys.platform == "win32" or not os.environ.get("DISPLAY"),
    reason="needs a display (run under xvfb-run)")


def _pump(a, seconds=0.5):
    end = time.time() + seconds
    while time.time() < end:
        a.update()
        time.sleep(0.02)


def _seed(store):
    """Two feeds (one foldered), three articles, no network."""
    f1 = store.add_feed("https://example.com/rss.xml", title="Example",
                        folder="News")
    f2 = store.add_feed("https://blog.example.com/atom.xml", title="Blog")
    now = time.time()
    store.upsert_article(f1["id"], {
        "id": "a1", "title": "Hello world", "link": "https://example.com/1",
        "summary": "<p>First body</p>",
        "published": "Mon, 04 Aug 2025 09:00:00 GMT"})
    store.upsert_article(f1["id"], {
        "id": "a2", "title": "Second post", "link": "https://example.com/2",
        "summary": "More text", "published": ""})
    store.upsert_article(f2["id"], {
        "id": "b1", "title": "Blog entry", "link": "",
        "content": "<p>Blog body</p>", "published": ""})
    return f1, f2


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    """ONE real window per module — tk/CTk do not survive multiple roots."""
    home = tmp_path_factory.mktemp("fh-home")
    old = os.environ.get("FEEDHUB_HOME")
    os.environ["FEEDHUB_HOME"] = str(home)
    App = gui.build_app()
    a = App(store_path=str(home / "boot.db"))
    _pump(a, 0.8)
    yield a
    try:
        a.destroy()
    except Exception:
        pass
    if old is None:
        os.environ.pop("FEEDHUB_HOME", None)
    else:
        os.environ["FEEDHUB_HOME"] = old


@pytest.fixture()
def app(win, tmp_path):
    """The shared window pointed at a FRESH seeded store for each test."""
    try:
        win.store.close()
    except Exception:
        pass
    win.store = Store(str(tmp_path / "test.db"))
    win.test_feeds = _seed(win.store)
    win._scope = ("all",)
    win._current_article = None
    win._unread_only = False
    win.filter_seg.set(gui.FILTER_ALL)
    win.search.set("")
    win._refresh_library()
    win._reload_articles()
    _pump(win, 0.3)
    return win


@needs_display
def test_shell_and_library(app):
    assert app.sidebar_visible
    # library rows: All, Starred, folder News, feed Example, feed Blog
    texts = [b.cget("text") for b in app._lib_rows]
    assert any("All articles" in t for t in texts)
    assert any("Starred" in t for t in texts)
    assert any("News" in t for t in texts)
    assert any("Example" in t for t in texts)
    assert any("Blog" in t for t in texts)
    # unread counts surface next to rows (3 unread total)
    assert any("3" in t for t in texts if "All articles" in t)
    assert len(app.alist.get_children()) == 3
    app.toggle_sidebar()
    assert not app.sidebar_visible
    app.toggle_sidebar()
    assert app.sidebar_visible


@needs_display
def test_scope_and_folder(app):
    f1, f2 = app.test_feeds
    app._set_scope(("feed", f2["id"]))
    assert len(app.alist.get_children()) == 1
    app._set_scope(("folder", "News"))
    assert len(app.alist.get_children()) == 2
    app._set_scope(("all",))
    assert len(app.alist.get_children()) == 3


@needs_display
def test_read_on_open_and_unread_filter(app):
    iids = app.alist.get_children()
    app.alist.selection_set(iids[0])
    _pump(app, 0.2)
    assert app._current_article is not None
    assert app._current_article["read"] == 1
    assert app.store.unread_count() == 2
    app._set_filter(gui.FILTER_UNREAD)
    assert len(app.alist.get_children()) == 2
    app._set_filter(gui.FILTER_ALL)
    assert len(app.alist.get_children()) == 3


@needs_display
def test_star_and_starred_scope(app):
    iids = app.alist.get_children()
    app.alist.selection_set(iids[0])
    _pump(app, 0.2)
    app._toggle_star()
    assert app._current_article["starred"] == 1
    app._set_scope(("starred",))
    assert len(app.alist.get_children()) == 1
    app._toggle_star()          # unstar from within Starred reloads the list
    _pump(app, 0.2)
    assert len(app.alist.get_children()) == 0


@needs_display
def test_search_and_mark_all_read(app):
    app.search.set("Blog")
    app._reload_articles()
    assert len(app.alist.get_children()) == 1
    app.search.set("")
    app._reload_articles()
    app._mark_all_read()
    assert app.store.unread_count() == 0


@needs_display
def test_mark_unread_again(app):
    iids = app.alist.get_children()
    app.alist.selection_set(iids[0])
    _pump(app, 0.2)
    assert app._current_article["read"] == 1
    app._mark_unread()
    assert app._current_article["read"] == 0
    assert app.store.unread_count() == 3   # 2 untouched + 1 re-marked


@needs_display
def test_both_themes_no_crash(app):
    for theme in ("light", "dark"):
        app.set_theme(theme)
        app.update_idletasks()
        app.update()
        assert app.theme == theme
    # welcome empty state on a store with no feeds at all
    app.store.close()
    import tempfile
    d = tempfile.mkdtemp()
    app.store = Store(os.path.join(d, "empty.db"))
    app._scope = ("all",)
    app._refresh_library()
    app._reload_articles()
    app.update_idletasks()
    assert not app.alist.get_children()

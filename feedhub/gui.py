#!/usr/bin/env python3
r"""FeedHub -- an Aura (QuickOpen design system) GUI on top of the ``feedhub``
library.

Layout per branding/aura-design-system/APP-LAYOUT-LANGUAGE.md, benchmarked
against Feedly / Reeder (the commercial three-column reader convention):

  * **Sidebar** (AuraApp) -- Articles / About nav, plus the subscription
    library in ``sidebar_body``: All articles, Starred, then folders and
    feeds with live unread counts.  Right-click a feed for Refresh / Set
    folder / Mark read / Unsubscribe.  Collapsible with Ctrl+\.
  * **Toolbar** -- "+ Add feed" (primary), Refresh, Mark all read, the
    All | Unread filter, and the debounced search field (Ctrl+F).
  * **Content** -- a two-pane splitter: the article list (unread dot / star,
    title, feed, compact date) and a clean reader (title, meta, sanitised
    text, Star / Mark unread / Open in browser).  Empty states show an Aura
    illustration instead of blank panes.
  * **Status bar** -- unread / total counts; errors surface here.

Daily-use features from the benchmark: unread-first reading with mark-on-open,
star for later, folders, full-text search, OPML import/export, timed
auto-refresh, keyboard flow (Space stars, Enter opens the original, F5
refreshes), a Ctrl+, Settings dialog (System/Light/Dark theme, auto-refresh
interval).  Fresh installs follow the OS Aura theme live.  The pro tail
(accounts, sync services, sharing integrations) is deliberately out.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``feedhub/aura.py`` design system (CustomTkinter).
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.
  * Every refresh runs on a background thread and is marshalled back with
    ``self.after``; failures show the ``FeedHubError`` message in the Aura
    status bar, never a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

# tkinter/customtkinter are imported lazily inside main()/build_app so merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "FeedHub"
APP_VERSION = "1.1.0"
WINDOW_TITLE = "FeedHub — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai/projects/feed-hub"
ACCENT = "#5b86f7"      # Aura brand accent (the old per-app orange was a
                        # legacy scaffold accent)

FILTER_ALL = "All"
FILTER_UNREAD = "Unread"


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_url(url):
    """Open *url* in the default browser, guarded on every platform."""
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def parse_when(art):
    """Best-effort timestamp for an article dict (published, else fetched)."""
    pub = (art.get("published") or "").strip()
    if pub:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub).timestamp()
        except Exception:
            pass
        try:
            from datetime import datetime
            return datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return art.get("fetched_at") or None


def rel_date(ts, now=None):
    """A compact human stamp: 'now', '5m', '2h', 'Yesterday', '12 Aug'."""
    if not ts:
        return ""
    now = now if now is not None else time.time()
    diff = max(0, now - ts)
    if diff < 90:
        return "now"
    if diff < 3600:
        return "%dm" % (diff // 60)
    if diff < 86400 and time.localtime(ts).tm_mday == time.localtime(now).tm_mday:
        return "%dh" % (diff // 3600)
    if diff < 2 * 86400:
        return "Yesterday"
    st, sn = time.localtime(ts), time.localtime(now)
    if st.tm_year == sn.tm_year:
        return time.strftime("%d %b", st)
    return time.strftime("%b %Y", st)


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import customtkinter as ctk

    from . import aura, guiconfig
    from .errors import FeedHubError
    from .store import Store
    from . import feeds as feeds_mod
    from . import opml as opml_mod
    from .reader import to_text

    UI_FAMILY = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
    pair = aura._pair

    SCOPE_ALL = ("all",)
    SCOPE_STARRED = ("starred",)

    class App(aura.AuraApp):
        AUTO_REFRESH_CHOICES = (("Off", 0), ("Every 5 min", 5),
                                ("Every 15 min", 15), ("Every 30 min", 30),
                                ("Every hour", 60), ("Every 2 hours", 120))

        def __init__(self, store_path=None):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("feed-hub.png"), version=APP_VERSION,
                tagline="RSS reader",
                on_theme_change=guiconfig.set_theme,
                size=(1240, 760), min_size=(960, 560))

            cfg = guiconfig.load()
            self._unread_only = bool(cfg["unread_only"])
            self._refresh_minutes = cfg["refresh_minutes"]
            self._busy = False
            self._img_refs_gui = []
            self._scope = SCOPE_ALL         # ("all")/("starred")/("folder",f)/("feed",id)
            self._articles = {}             # list iid -> article dict
            self._current_article = None
            self._auto_after = None
            self._lib_rows = []

            try:
                self.store = Store(store_path)
            except FeedHubError as exc:
                messagebox.showerror(
                    APP_NAME, f"Could not open the feed store:\n{exc}")
                raise

            self._set_icon()
            self._build_menu()
            self.add_section("articles", "Articles", "▤", self._build_articles)
            self.add_section("about", "About", "ℹ", self._build_about)
            self._build_library_sidebar()
            self.show("articles")
            self.protocol("WM_DELETE_WINDOW", self._on_close)

            self.after(50, self._startup)
            self._schedule_auto_refresh()

        # ---- assets / icon --------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("feed-hub.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("feed-hub.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu + keyboard baseline (APP-LAYOUT-LANGUAGE.md §7/§9) --------
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Add feed…", accelerator="Ctrl+N",
                              command=self._add_feed)
            filem.add_separator()
            filem.add_command(label="Import OPML…", command=self._import_opml)
            filem.add_command(label="Export OPML…", command=self._export_opml)
            filem.add_separator()
            filem.add_command(label="Refresh all", accelerator="F5",
                              command=lambda: self._refresh(scope="all"))
            filem.add_command(label="Mark all read", command=self._mark_all_read)
            filem.add_separator()
            filem.add_command(label="Settings…", accelerator="Ctrl+,",
                              command=self._open_settings)
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle sidebar", accelerator="Ctrl+\\",
                              command=self.toggle_sidebar)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_url(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

            self.bind_all("<Control-n>", lambda e: (self._add_feed(), "break")[1])
            self.bind_all("<Control-f>", lambda e: (self._focus_search(), "break")[1])
            self.bind_all("<Control-r>",
                          lambda e: (self._refresh(scope="all"), "break")[1])
            self.bind_all("<F5>", lambda e: self._refresh(scope="all"))
            self.bind_all("<Control-comma>",
                          lambda e: (self._open_settings(), "break")[1])

        # =================================================================
        # Sidebar library (sidebar_body): All / Starred / folders / feeds
        # =================================================================
        def _build_library_sidebar(self):
            aura.SectionLabel(self.sidebar_body, "Library").pack(
                anchor="w", padx=6, pady=(0, 4))
            self._lib_scroll = ctk.CTkScrollableFrame(
                self.sidebar_body, fg_color="transparent")
            self._lib_scroll.pack(fill="both", expand=True)
            self._feed_menu = tk.Menu(self, tearoff=0)
            aura.track(self._feed_menu, "menu")

        def _lib_row(self, label, count, scope, indent=0, feed=None):
            active = (scope == self._scope)
            text = label if not count else "%s   ·  %d" % (label, count)
            btn = ctk.CTkButton(
                self._lib_scroll, text=text, anchor="w", height=30,
                corner_radius=aura.TOKENS["geometry"]["radius_button"],
                fg_color=pair("accent_soft") if active else "transparent",
                hover_color=(aura._pal["light"]["surface2"],
                             aura._pal["dark"]["surface2"]),
                text_color=pair("text") if active else pair("muted"),
                font=aura.font(role="body"),
                command=lambda: self._set_scope(scope))
            btn.pack(fill="x", pady=1, padx=(12 * indent, 0))
            if feed is not None:
                btn.bind("<Button-3>",
                         lambda e, f=feed: self._show_feed_menu(e, f))
            self._lib_rows.append(btn)
            return btn

        def _refresh_library(self):
            for w in list(self._lib_scroll.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
            self._lib_rows = []
            try:
                feeds = self.store.list_feeds()
                counts = self.store.unread_counts()
                total = sum(counts.values())
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            self._feeds = feeds
            self._lib_row("▤  All articles", total, SCOPE_ALL)
            self._lib_row("★  Starred", 0, SCOPE_STARRED)
            folders = {}
            loose = []
            for f in feeds:
                if f.get("folder"):
                    folders.setdefault(f["folder"], []).append(f)
                else:
                    loose.append(f)
            for name in sorted(folders, key=str.lower):
                members = folders[name]
                fcount = sum(counts.get(f["id"], 0) for f in members)
                self._lib_row("◈  " + name, fcount, ("folder", name))
                for f in members:
                    self._lib_row(f.get("title") or f.get("url"),
                                  counts.get(f["id"], 0), ("feed", f["id"]),
                                  indent=1, feed=f)
            for f in loose:
                self._lib_row(f.get("title") or f.get("url"),
                              counts.get(f["id"], 0), ("feed", f["id"]),
                              feed=f)
            # a stale scope (feed/folder gone) falls back to All
            ids = {f["id"] for f in feeds}
            if (self._scope[0] == "feed" and self._scope[1] not in ids) or \
               (self._scope[0] == "folder" and self._scope[1] not in folders):
                self._scope = SCOPE_ALL

        def _set_scope(self, scope):
            self._scope = scope
            self._refresh_library()
            self._reload_articles()

        def _scope_feed(self):
            """The feed dict for a single-feed scope, else None."""
            if self._scope[0] == "feed":
                for f in getattr(self, "_feeds", []):
                    if f["id"] == self._scope[1]:
                        return f
            return None

        def _show_feed_menu(self, event, feed):
            m = self._feed_menu
            m.delete(0, "end")
            m.add_command(label="Refresh this feed",
                          command=lambda: self._refresh(scope="feed", feed=feed))
            m.add_command(label="Set folder…",
                          command=lambda: self._set_folder(feed))
            m.add_command(label="Mark all read",
                          command=lambda: self._mark_all_read(feed))
            m.add_separator()
            m.add_command(label="Unsubscribe…",
                          command=lambda: self._remove_feed(feed))
            aura.style_menu(m)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    m.grab_release()
                except Exception:
                    pass

        # =================================================================
        # Articles section — toolbar + splitter (list | reader)
        # =================================================================
        def _build_articles(self, frame):
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(1, weight=1)

            # ---- toolbar (primary action left; filter + search right)
            tb = aura.Toolbar(frame)
            tb.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            tb.add_button("＋ Add feed", self._add_feed, kind="primary")
            self._refresh_btn = tb.add_button("⟳ Refresh",
                                              lambda: self._refresh())
            tb.add_button("Mark all read", self._mark_all_read, kind="ghost")
            self.search = tb.add_search("Search articles…  (Ctrl+F)",
                                        on_change=lambda _t: self._reload_articles(),
                                        width=240)
            self.filter_seg = aura.SegmentedControl(
                tb, values=[FILTER_ALL, FILTER_UNREAD], width=150,
                command=self._set_filter)
            self.filter_seg.set(FILTER_UNREAD if self._unread_only else FILTER_ALL)
            tb.add_right(self.filter_seg)

            # ---- the workspace splitter
            self.panes = ttk.Panedwindow(frame, orient="horizontal")
            self.panes.grid(row=1, column=0, sticky="nsew")

            # ---- left: article list
            listwrap = ctk.CTkFrame(self.panes, fg_color=pair("surface"),
                                    corner_radius=10, border_width=1,
                                    border_color=pair("border"))
            self.panes.add(listwrap, weight=2)
            cols = ("state", "title", "feed", "date")
            self.alist = ttk.Treeview(listwrap, columns=cols, show="headings",
                                      selectmode="browse")
            self.alist.heading("state", text="")
            self.alist.heading("title", text="Article")
            self.alist.heading("feed", text="Feed")
            self.alist.heading("date", text="When")
            self.alist.column("state", width=36, minwidth=32, anchor="center",
                              stretch=False)
            self.alist.column("title", width=210, minwidth=120, stretch=True)
            self.alist.column("feed", width=100, minwidth=70, stretch=False)
            self.alist.column("date", width=72, minwidth=60, stretch=False,
                              anchor="e")
            asb = aura.AuraScrollbar(listwrap, command=self.alist.yview)
            self.alist.configure(yscrollcommand=asb.set)
            asb.pack(side="right", fill="y", padx=(0, 4), pady=6)
            self.alist.pack(side="left", fill="both", expand=True,
                            padx=(6, 0), pady=6)
            self.alist.bind("<<TreeviewSelect>>", self._on_article_select)
            self.alist.bind("<space>", lambda e: (self._toggle_star(), "break")[1])
            self.alist.bind("<Return>", lambda e: self._open_current())
            self.alist.bind("<Button-3>", self._show_article_menu)
            self._article_menu = tk.Menu(self, tearoff=0)
            aura.track(self._article_menu, "menu")
            self._style_list_tags()

            # ---- right: reader
            right = ctk.CTkFrame(self.panes, fg_color=pair("bg"),
                                 corner_radius=0)
            self.panes.add(right, weight=3)
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(1, weight=1)

            head = ctk.CTkFrame(right, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=(10, 0), pady=(0, 6))
            head.grid_columnconfigure(0, weight=1)
            self._open_btn = aura.AuraButton(
                head, "Open in browser ↗", kind="secondary", height=30,
                command=self._open_current)
            self._open_btn.pack(side="right")
            self._unread_btn = aura.AuraButton(
                head, "Mark unread", kind="ghost", height=30,
                command=self._mark_unread)
            self._unread_btn.pack(side="right", padx=(0, 8))
            self._star_btn = aura.AuraButton(
                head, "☆ Star", kind="ghost", height=30,
                command=self._toggle_star)
            self._star_btn.pack(side="right", padx=(0, 8))

            self.rd_wrap = ctk.CTkFrame(right, fg_color=pair("field"),
                                        corner_radius=10, border_width=1,
                                        border_color=pair("border"))
            self.rd_wrap.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
            self.reader = tk.Text(self.rd_wrap, wrap="word", padx=16, pady=14,
                                  relief="flat", cursor="arrow", bd=0,
                                  width=30, height=8, state="disabled")
            rsb = aura.AuraScrollbar(self.rd_wrap, command=self.reader.yview)
            self.reader.configure(yscrollcommand=rsb.set)
            rsb.pack(side="right", fill="y", padx=(0, 4), pady=4)
            self.reader.pack(side="left", fill="both", expand=True,
                             padx=(4, 0), pady=4)
            aura.track(self.reader, "text")
            self._style_reader_tags()

            # ---- empty states
            self.empty_feeds = aura.EmptyState(
                frame, title="Welcome to FeedHub",
                caption="Follow your favourite sites in one quiet place. "
                        "Add an RSS or Atom feed to get started — or import "
                        "your subscriptions from OPML (File menu).",
                action_text="＋ Add feed", action=self._add_feed,
                image=(asset_path("assets/feeds-empty-light.png"),
                       asset_path("assets/feeds-empty-dark.png")))
            self.empty_articles = aura.EmptyState(
                listwrap, glyph="▤", title="No articles here",
                caption="Refresh to fetch new articles, or switch the filter "
                        "back to All.")
            self.empty_reader = aura.EmptyState(
                right, glyph="☕", title="Nothing selected",
                caption="Choose an article on the left. Space stars it, "
                        "Enter opens the original in your browser.")
            self._reader_btns(False)
            self.after(250, self._init_sashes)

        def _init_sashes(self):
            try:
                if self.panes.winfo_width() > 700:
                    self.panes.sashpos(0, 480)
            except Exception:
                pass

        def _reader_btns(self, enabled):
            for b in (self._open_btn, self._star_btn, self._unread_btn):
                try:
                    b.configure(state="normal" if enabled else "disabled")
                except Exception:
                    pass

        # ---- reader tag colours (track() flips bg/fg but not tag colours) ---
        def _style_reader_tags(self):
            if not hasattr(self, "reader"):
                return
            p = aura.P()
            try:
                self.reader.tag_configure("title", font=(UI_FAMILY, 17, "bold"),
                                          foreground=p["text"], spacing3=6)
                self.reader.tag_configure("meta", foreground=p["muted"],
                                          font=(UI_FAMILY, 10), spacing3=12)
                self.reader.tag_configure("body", font=(UI_FAMILY, 11),
                                          spacing1=2, spacing3=4)
            except Exception:
                pass

        # ---- theme override: keep raw-tk surfaces + library rows in sync ----
        def set_theme(self, theme, _system=False):
            super().set_theme(theme, _system=_system)
            try:
                self._style_reader_tags()
                self._style_list_tags()
                self._render_article()
                self._refresh_library()
            except Exception:
                pass

        # ---- startup --------------------------------------------------------
        def _startup(self):
            self._refresh_library()
            self._reload_articles()

        def _focus_search(self):
            try:
                self.show("articles")
                self.search.focus_set()
            except Exception:
                pass

        # ---- background runner ----------------------------------------------
        def _bg(self, work, on_ok, busy="Working…"):
            """Run ``work()`` off the UI thread; ``on_ok(result)`` back on it."""
            if self._busy:
                self.set_error("Please wait — a refresh is already running.")
                return
            self._busy = True
            try:
                self._refresh_btn.configure(state="disabled")
            except Exception:
                pass
            self.set_status(busy, kind="working")

            def run():
                try:
                    res, err = work(), None
                except FeedHubError as ex:
                    res, err = None, str(ex)
                except Exception as ex:  # never leak a traceback
                    res, err = None, f"Unexpected error: {ex}"
                try:
                    self.after(0, lambda: finish(res, err))
                except Exception:
                    self._busy = False

            def finish(res, err):
                self._busy = False
                try:
                    self._refresh_btn.configure(state="normal")
                except Exception:
                    pass
                if err is not None:
                    self.set_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self.set_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- article list ---------------------------------------------------
        def _collect_articles(self):
            """Articles for the current scope (+ unread filter + search)."""
            search = (self.search.get().strip() or None) \
                if hasattr(self, "search") else None
            unread = self._unread_only
            feeds = self.store.list_feeds()
            if self._scope[0] == "feed":
                feeds = [f for f in feeds if f["id"] == self._scope[1]]
            elif self._scope[0] == "folder":
                feeds = [f for f in feeds
                         if (f.get("folder") or "") == self._scope[1]]
            starred = self._scope == SCOPE_STARRED
            arts = []
            for f in feeds:
                # starred is "read later": ignore the unread filter there
                arts += self.store.list_articles(
                    f["id"], unread_only=(unread and not starred),
                    starred_only=starred, search=search)
            names = {f["id"]: (f.get("title") or f.get("url")) for f in feeds}
            for a in arts:
                a["_feed"] = names.get(a["feed_id"], "")
                a["_when"] = parse_when(a)
            arts.sort(key=lambda a: (a.get("_when") or 0), reverse=True)
            return arts

        def _reload_articles(self):
            try:
                arts = self._collect_articles()
                have_feeds = bool(self.store.list_feeds())
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            keep = self._current_article["id"] if self._current_article else None
            self.alist.delete(*self.alist.get_children())
            self._articles = {}
            now = time.time()
            single = self._scope[0] == "feed"
            self.alist.configure(displaycolumns=(
                ("state", "title", "date") if single
                else ("state", "title", "feed", "date")))
            for art in arts:
                iid = "art:%d" % art["id"]
                state = ("★" if art["starred"] else "") + \
                        ("" if art["read"] else "●")
                self.alist.insert(
                    "", "end", iid=iid,
                    values=(state, art["title"] or "(untitled)",
                            art["_feed"], rel_date(art["_when"], now)),
                    tags=() if art["read"] else ("unread",))
                self._articles[iid] = art
            # ---- empty states + status
            if have_feeds:
                self.empty_feeds.place_forget()
                self.panes.grid()
            else:
                self.panes.grid_remove()
                self.empty_feeds.place(relx=0, rely=0.06, relwidth=1,
                                       relheight=0.94)
            if arts or not have_feeds:
                self.empty_articles.place_forget()
            else:
                self.empty_articles.place(relx=0, rely=0, relwidth=1,
                                          relheight=1)
                self.empty_articles.lift()
            kiid = "art:%d" % keep if keep is not None else None
            if kiid and self.alist.exists(kiid):
                self.alist.selection_set(kiid)
                self.alist.see(kiid)
            else:
                self._current_article = None
                self._render_article()
            self._update_counts()

        def _style_list_tags(self):
            try:
                self.alist.tag_configure(
                    "unread", font=(UI_FAMILY, 10, "bold"))
            except Exception:
                pass

        def _update_counts(self):
            try:
                total_unread = self.store.unread_count()
            except FeedHubError:
                total_unread = 0
            n = len(self.alist.get_children()) if hasattr(self, "alist") else 0
            self.set_status(f"{n} article(s) shown   ·   "
                            f"{total_unread} unread in your library")

        def _set_filter(self, value):
            self._unread_only = (value == FILTER_UNREAD)
            guiconfig.set_unread_only(self._unread_only)
            self._reload_articles()

        def _on_article_select(self, _event=None):
            sel = self.alist.selection()
            if not sel:
                return
            art = self._articles.get(sel[0])
            if not art:
                return
            self._current_article = art
            if not art["read"]:                       # mark read on open
                try:
                    self.store.mark_read(art["id"], True)
                    art["read"] = 1
                    self.alist.set(sel[0], "state",
                                   "★" if art["starred"] else "")
                    self.alist.item(sel[0], tags=())
                    self._refresh_library()
                    self._update_counts()
                except FeedHubError as exc:
                    self.set_error(str(exc))
            self._render_article()

        def _show_article_menu(self, event):
            iid = self.alist.identify_row(event.y)
            if not iid:
                return
            self.alist.selection_set(iid)
            art = self._articles.get(iid)
            if not art:
                return
            m = self._article_menu
            m.delete(0, "end")
            m.add_command(label="Open in browser",
                          command=self._open_current)
            m.add_command(label=("Unstar" if art["starred"] else "Star")
                          + "  (Space)", command=self._toggle_star)
            m.add_command(label="Mark unread" if art["read"] else "Mark read",
                          command=self._toggle_read)
            aura.style_menu(m)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    m.grab_release()
                except Exception:
                    pass

        # ---- reader ---------------------------------------------------------
        def _render_article(self):
            if not hasattr(self, "reader"):
                return
            art = self._current_article
            self.reader.configure(state="normal")
            self.reader.delete("1.0", "end")
            if art is None:
                self.reader.configure(state="disabled")
                self._reader_btns(False)
                self.empty_reader.place(x=0, y=0, relwidth=1, relheight=1)
                self.empty_reader.lift()
                return
            self.empty_reader.place_forget()
            self.reader.insert("end", (art["title"] or "(untitled)") + "\n",
                               "title")
            meta = "  ·  ".join(x for x in (
                art.get("_feed"), art.get("author"),
                rel_date(art.get("_when") or parse_when(art))) if x)
            if meta:
                self.reader.insert("end", meta + "\n", "meta")
            body = to_text(art.get("content") or art.get("summary") or "")
            self.reader.insert("end", (body or "(no content)") + "\n", "body")
            self.reader.configure(state="disabled")
            self._reader_btns(True)
            if not art.get("link"):
                self._open_btn.configure(state="disabled")
            self._star_btn.configure(
                text="★ Unstar" if art["starred"] else "☆ Star")
            self._unread_btn.configure(
                text="Mark unread" if art["read"] else "Mark read")

        def _open_current(self):
            if self._current_article and self._current_article.get("link"):
                open_url(self._current_article["link"])

        def _toggle_star(self):
            art = self._current_article
            if not art:
                return
            new = 0 if art["starred"] else 1
            try:
                self.store.mark_starred(art["id"], bool(new))
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            art["starred"] = new
            iid = "art:%d" % art["id"]
            if self.alist.exists(iid):
                self.alist.set(
                    iid, "state",
                    ("★" if new else "") + ("" if art["read"] else "●"))
            self._star_btn.configure(text="★ Unstar" if new else "☆ Star")
            if self._scope == SCOPE_STARRED and not new:
                self._reload_articles()

        def _toggle_read(self):
            art = self._current_article
            if not art:
                return
            new = 0 if art["read"] else 1
            self._set_read(art, new)

        def _mark_unread(self):
            art = self._current_article
            if art:
                self._set_read(art, 0 if art["read"] else 1)

        def _set_read(self, art, flag):
            try:
                self.store.mark_read(art["id"], bool(flag))
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            art["read"] = flag
            iid = "art:%d" % art["id"]
            if self.alist.exists(iid):
                self.alist.set(iid, "state",
                               ("★" if art["starred"] else "")
                               + ("" if flag else "●"))
                self.alist.item(iid, tags=() if flag else ("unread",))
            self._unread_btn.configure(
                text="Mark unread" if flag else "Mark read")
            self._refresh_library()
            self._update_counts()

        # ---- dialogs --------------------------------------------------------
        def _add_feed(self):
            dlg = aura.Dialog(self, title="Add feed", size=(480, 250))
            aura.Caption(dlg.body, "Feed URL (RSS or Atom):").pack(anchor="w")
            url_e = aura.AuraEntry(dlg.body,
                                   placeholder="https://example.com/feed.xml")
            url_e.pack(fill="x", pady=(6, 12))
            aura.Caption(dlg.body, "Folder (optional):").pack(anchor="w")
            try:
                folders = self.store.list_folders()
            except FeedHubError:
                folders = []
            fol_e = aura.AuraCombo(dlg.body, values=[""] + folders)
            fol_e.set("")
            fol_e.pack(fill="x", pady=(6, 0))

            def ok(_e=None):
                url = url_e.get().strip()
                if not url:
                    return
                folder = fol_e.get().strip()
                dlg.close()
                try:
                    feed = self.store.add_feed(url, folder=folder)
                except FeedHubError as exc:
                    self.set_error(str(exc))
                    return
                self._scope = ("feed", feed["id"])
                self._refresh_library()
                self._reload_articles()
                self.set_status(f"Added {url} — fetching…", kind="working")
                self._refresh(scope="feed", feed=feed)

            dlg.add_button("Add feed", ok)
            dlg.add_button("Cancel", dlg.close, kind="secondary")
            url_e.bind("<Return>", ok)
            self.after(120, url_e.focus_set)

        def _remove_feed(self, feed):
            name = feed.get("title") or feed.get("url")
            if not messagebox.askyesno(
                    "Unsubscribe", f"Unsubscribe from “{name}”?\n"
                    "Cached articles for this feed are removed too.",
                    parent=self):
                return
            try:
                self.store.remove_feed(feed["id"])
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            if self._scope == ("feed", feed["id"]):
                self._scope = SCOPE_ALL
            self._refresh_library()
            self._reload_articles()
            self.set_success(f"Unsubscribed from {name}.")

        def _set_folder(self, feed):
            dlg = aura.Dialog(self, title="Set folder", size=(440, 200))
            aura.Caption(dlg.body,
                         "Folder for “%s” (blank for none):"
                         % (feed.get("title") or feed.get("url"))).pack(anchor="w")
            try:
                folders = self.store.list_folders()
            except FeedHubError:
                folders = []
            fol_e = aura.AuraCombo(dlg.body, values=[""] + folders)
            fol_e.set(feed.get("folder") or "")
            fol_e.pack(fill="x", pady=(6, 0))

            def ok(_e=None):
                folder = fol_e.get().strip()
                dlg.close()
                try:
                    self.store.set_feed_folder(feed["id"], folder)
                except FeedHubError as exc:
                    self.set_error(str(exc))
                    return
                self._refresh_library()

            dlg.add_button("Save", ok)
            dlg.add_button("Cancel", dlg.close, kind="secondary")
            self.after(120, fol_e.focus_set)

        def _mark_all_read(self, feed=None):
            """Mark the current scope (or *feed*) read."""
            try:
                if feed is not None:
                    self.store.mark_all_read(feed["id"], True)
                elif self._scope[0] == "feed":
                    self.store.mark_all_read(self._scope[1], True)
                else:
                    feeds = self.store.list_feeds()
                    if self._scope[0] == "folder":
                        feeds = [f for f in feeds
                                 if (f.get("folder") or "") == self._scope[1]]
                    for f in feeds:
                        self.store.mark_all_read(f["id"], True)
            except FeedHubError as exc:
                self.set_error(str(exc))
                return
            self._refresh_library()
            self._reload_articles()
            self.set_success("Marked read.")

        # ---- refresh --------------------------------------------------------
        def _refresh(self, scope=None, feed=None):
            """Threaded refresh of one feed or the current scope."""
            if scope is None:
                scope = "feed" if self._scope[0] == "feed" else "all"
            if scope == "feed":
                target = feed or self._scope_feed()
                targets = [target] if target else []
            else:
                try:
                    targets = self.store.list_feeds()
                except FeedHubError as exc:
                    self.set_error(str(exc))
                    return
            if not targets:
                self.set_status("No feeds to refresh — add one first.")
                return

            def work():
                total_new = 0
                failures = []
                for f in targets:
                    try:
                        res = feeds_mod.refresh(self.store, f["id"])
                        total_new += res["new"]
                    except FeedHubError as ex:
                        failures.append((f, str(ex)))
                return total_new, failures, len(targets)

            def done(result):
                total_new, failures, n = result
                self._refresh_library()
                self._reload_articles()
                if failures and len(failures) == n:
                    self.set_error(f"Refresh failed: {failures[0][1]}")
                elif failures:
                    self.set_status(
                        f"{total_new} new; {len(failures)} feed(s) failed.",
                        kind="ok")
                else:
                    self.set_success(f"Refreshed — {total_new} new article(s).")

            self._bg(work, done, busy=f"Refreshing {len(targets)} feed(s)…")

        # ---- OPML -----------------------------------------------------------
        def _import_opml(self):
            path = filedialog.askopenfilename(
                title="Import OPML",
                filetypes=[("OPML files", "*.opml *.xml"), ("All files", "*.*")])
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    added = opml_mod.import_opml(self.store, fh.read())
            except (OSError, FeedHubError) as exc:
                self.set_error(str(exc))
                return
            self._refresh_library()
            self._reload_articles()
            self.set_success(f"Imported {len(added)} new feed(s).")

        def _export_opml(self):
            path = filedialog.asksaveasfilename(
                title="Export OPML", defaultextension=".opml",
                filetypes=[("OPML files", "*.opml"), ("All files", "*.*")])
            if not path:
                return
            try:
                text = opml_mod.export_opml(self.store)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except (OSError, FeedHubError) as exc:
                self.set_error(str(exc))
                return
            self.set_success(f"Exported to {path}.")

        # ---- auto-refresh ---------------------------------------------------
        def _schedule_auto_refresh(self):
            if self._auto_after is not None:
                try:
                    self.after_cancel(self._auto_after)
                except Exception:
                    pass
                self._auto_after = None
            minutes = self._refresh_minutes
            if minutes and minutes > 0:
                self._auto_after = self.after(
                    int(minutes * 60_000), self._auto_refresh_tick)

        def _auto_refresh_tick(self):
            if not self._busy:
                self._refresh(scope="all")
            self._schedule_auto_refresh()

        # ---- settings (Ctrl+,) ----------------------------------------------
        def _open_settings(self):
            dlg = aura.Dialog(self, title="Settings", size=(520, 420))

            aura.SectionLabel(dlg.body, "Refreshing").pack(anchor="w",
                                                           pady=(0, 2))
            rrow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            rrow.pack(anchor="w", pady=(4, 14))
            aura.Caption(rrow, "Auto-refresh").pack(side="left", padx=(0, 10))
            labels = [lbl for lbl, _m in self.AUTO_REFRESH_CHOICES]
            cur_lbl = labels[0]
            for lbl, m in self.AUTO_REFRESH_CHOICES:
                if m == self._refresh_minutes:
                    cur_lbl = lbl
            ar = aura.AuraOption(rrow, values=labels, width=140, height=30,
                                 command=self._set_interval)
            ar.set(cur_lbl)
            ar.pack(side="left")

            aura.SectionLabel(dlg.body, "Appearance").pack(anchor="w",
                                                           pady=(0, 2))
            trow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            trow.pack(anchor="w", pady=(4, 2))
            aura.Caption(trow, "Theme").pack(side="left", padx=(0, 10))
            cur = guiconfig.get_theme()
            th = aura.AuraOption(trow, values=["System", "Light", "Dark"],
                                 width=110, height=30,
                                 command=self._set_theme_pref)
            th.set(cur.capitalize() if cur in ("light", "dark") else "System")
            th.pack(side="left")
            aura.Caption(dlg.body,
                         "System follows the OS Aura Dark/Light live.").pack(
                anchor="w", pady=(0, 14))

            aura.SectionLabel(dlg.body, "Data").pack(anchor="w", pady=(0, 2))
            aura.Caption(dlg.body, guiconfig.store_path()).pack(anchor="w")
            drow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            drow.pack(anchor="w", pady=(6, 0))
            aura.AuraButton(drow, "Open data folder", kind="ghost", height=30,
                            command=lambda: open_url(
                                "file://" + guiconfig.config_dir())).pack(
                side="left")

            dlg.add_button("Close")

        def _set_interval(self, label):
            for lbl, m in self.AUTO_REFRESH_CHOICES:
                if lbl == label:
                    self._refresh_minutes = m
                    guiconfig.set_refresh_minutes(m)
                    self._schedule_auto_refresh()
                    self.set_status(
                        "Auto-refresh off." if not m
                        else f"Auto-refresh every {m} min.")
                    return

        def _set_theme_pref(self, choice):
            pref = str(choice).lower()
            if pref == "system":
                guiconfig.set_theme("system")
                self._follow_system = True
                if self._sys_listener is None:
                    self._start_system_listener()
                self.set_theme(aura._system_theme(), _system=True)
            elif pref in ("light", "dark"):
                self.set_theme(pref)     # persists via on_theme_change

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About FeedHub")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=560,
                text="A fast, offline, 100% open-source RSS & Atom reader. "
                     "Organize feeds into folders, read a clean article view, "
                     "mark read/unread and star for later, search across "
                     "everything, and import/export your subscriptions as "
                     "OPML.\n\n"
                     "Feeds refresh on a schedule; everything is stored "
                     "locally. Nothing is ever uploaded anywhere.").pack(
                anchor="w")
            aura.Caption(card.body,
                         "Shortcuts: Ctrl+N add feed · Ctrl+F search · F5 "
                         "refresh · Space star · Enter open in browser · "
                         "Ctrl+, settings · Ctrl+\\ sidebar").pack(
                anchor="w", pady=(10, 0))
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on feedparser (BSD) "
                         "and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_url(PROJECT_URL)).pack(
                anchor="w", pady=(6, 0))

        # ---- lifecycle ------------------------------------------------------
        def _on_close(self):
            try:
                guiconfig.set_unread_only(self._unread_only)
            except Exception:
                pass
            try:
                self.store.close()
            except Exception:
                pass
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
    except tk.TclError as exc:
        # Typically "no display name and no $DISPLAY environment variable".
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

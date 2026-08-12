#!/usr/bin/env python3
r"""FeedHub -- an Aura (QuickOpen design system) GUI on top of the ``feedhub``
library.

A single Aura window whose main **Feeds** section is a classic three-pane
reader:

  * left    -- a tree of folders and feeds, each with an unread count;
  * middle  -- the article list for the selected scope (star / read dot,
    title and date);
  * right   -- a clean reader for the selected article (title, meta, rendered
    text) with an "Open in browser" button.

The header carries Add feed / Refresh (threaded) / Mark all read and a search
box; a second **About** section describes the app.  Feeds auto-refresh on a
configurable timer.  Every network refresh runs on a background thread and is
marshalled back with ``self.after`` so the UI never freezes; failures show the
``FeedHubError`` message in the Aura status bar, never a traceback.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``feedhub/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

# tkinter/customtkinter are imported lazily inside main()/build_app so merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "FeedHub"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "FeedHub — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai/projects/feed-hub"
ACCENT = "#b0700a"      # publish/specs/feed-hub.json "accent": [176, 112, 10]


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


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox, filedialog
    import customtkinter as ctk

    from . import aura, guiconfig
    from .errors import FeedHubError
    from .store import Store
    from . import feeds as feeds_mod
    from . import opml as opml_mod
    from .reader import to_text

    ALL_ID = "all"           # tree iid for the "All feeds" pseudo-node
    STARRED_ID = "starred"   # tree iid for the "Starred" pseudo-node

    class App(aura.AuraApp):
        def __init__(self, store_path=None):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("feed-hub.png"), version=APP_VERSION,
                tagline="RSS reader",
                on_theme_change=guiconfig.set_theme,
                size=(1180, 720), min_size=(940, 560))

            cfg = guiconfig.load()
            self._unread_only = cfg["unread_only"]
            self._refresh_minutes = cfg["refresh_minutes"]
            self._busy = False
            self._font_family = aura._family()
            self._img_refs_gui = []       # keep PhotoImage refs alive (own list)
            self._feed_iids = {}          # tree iid -> feed dict
            self._article_iids = {}       # list iid -> article dict
            self._current_feed = None     # selected feed dict, or None (=All)
            self._current_scope = ALL_ID
            self._current_article = None
            self._search_text = ""
            self._auto_after = None

            try:
                self.store = Store(store_path)
            except FeedHubError as exc:
                messagebox.showerror(
                    APP_NAME, f"Could not open the feed store:\n{exc}")
                raise

            self._set_icon()
            self._build_menu()
            self.add_section("feeds", "Feeds", "▤", self._build_feeds)
            self.add_section("about", "About", "ℹ", self._build_about)
            self.show("feeds")
            self.protocol("WM_DELETE_WINDOW", self._on_close)

            self.after(50, self._reload_feeds)
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

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Add feed…", accelerator="Ctrl+N",
                              command=self._add_feed)
            filem.add_separator()
            filem.add_command(label="Import OPML…", command=self._import_opml)
            filem.add_command(label="Export OPML…", command=self._export_opml)
            filem.add_separator()
            filem.add_command(label="Refresh all", accelerator="Ctrl+R",
                              command=lambda: self._refresh(scope="all"))
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            viewm.add_command(label="Auto-refresh interval…",
                              command=self._set_interval)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_url(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-n>", lambda e: self._add_feed())
            self.bind_all("<Control-r>", lambda e: self._refresh(scope="all"))

        # =================================================================
        # Feeds section — the three-pane reader
        # =================================================================
        def _build_feeds(self, frame):
            # -- toolbar
            tb = ctk.CTkFrame(frame, fg_color="transparent")
            tb.pack(side="top", fill="x", pady=(0, 12))
            aura.AuraButton(tb, "Add feed", kind="primary",
                            command=self._add_feed).pack(side="left")
            self._refresh_btn = aura.AuraButton(
                tb, "⟳ Refresh", kind="secondary",
                command=lambda: self._refresh())
            self._refresh_btn.pack(side="left", padx=(8, 0))
            aura.AuraButton(tb, "Mark all read", kind="secondary",
                            command=self._mark_all_read).pack(
                side="left", padx=(8, 0))
            # search box (no textvariable: CTkEntry placeholders need it absent)
            self._search_entry = aura.AuraEntry(
                tb, placeholder="Search articles…", width=240)
            self._search_entry.pack(side="right")
            self._search_entry.bind("<KeyRelease>", lambda _e: self._on_search())

            # -- three-pane body
            panes = ttk.Panedwindow(frame, orient="horizontal")
            panes.pack(side="top", fill="both", expand=True)

            # left: feed tree
            left = ctk.CTkFrame(panes, fg_color=aura._pair("surface"),
                                corner_radius=0)
            self.feed_tree = ttk.Treeview(left, show="tree", selectmode="browse")
            fsb = ttk.Scrollbar(left, orient="vertical",
                                command=self.feed_tree.yview)
            self.feed_tree.configure(yscrollcommand=fsb.set)
            fsb.pack(side="right", fill="y")
            self.feed_tree.pack(side="left", fill="both", expand=True)
            self.feed_tree.bind("<<TreeviewSelect>>", self._on_feed_select)
            self.feed_tree.bind("<Delete>", lambda e: self._remove_feed())
            self.feed_tree.bind("<Button-3>", self._feed_context_menu)
            panes.add(left, weight=1)

            # middle: article list
            mid = ctk.CTkFrame(panes, fg_color=aura._pair("surface"),
                               corner_radius=0)
            cols = ("state", "title", "date")
            self.article_list = ttk.Treeview(mid, columns=cols, show="headings",
                                             selectmode="browse")
            self.article_list.heading("state", text="", anchor="w")
            self.article_list.heading("title", text=aura.spaced("Article"),
                                      anchor="w")
            self.article_list.heading("date", text=aura.spaced("Date"),
                                      anchor="w")
            self.article_list.column("state", width=34, anchor="center",
                                     stretch=False)
            self.article_list.column("title", width=220, anchor="w",
                                     stretch=True)
            self.article_list.column("date", width=120, anchor="w", stretch=False)
            asb = ttk.Scrollbar(mid, orient="vertical",
                                command=self.article_list.yview)
            self.article_list.configure(yscrollcommand=asb.set)
            asb.pack(side="right", fill="y")
            self.article_list.pack(side="left", fill="both", expand=True)
            self.article_list.bind("<<TreeviewSelect>>", self._on_article_select)
            self.article_list.bind("<space>", lambda e: self._toggle_star())
            panes.add(mid, weight=2)

            # right: reader
            right = ctk.CTkFrame(panes, fg_color=aura._pair("surface"),
                                 corner_radius=0)
            rtop = ctk.CTkFrame(right, fg_color="transparent")
            rtop.pack(side="top", fill="x", padx=10, pady=(10, 4))
            self._open_btn = aura.AuraButton(
                rtop, "Open in browser", kind="secondary", height=30,
                command=self._open_current)
            self._open_btn.pack(side="right")
            self._star_btn = aura.AuraButton(
                rtop, "Star", kind="ghost", height=30,
                command=self._toggle_star)
            self._star_btn.pack(side="right", padx=(0, 8))
            # width/height are kept small: tk.Text sizes its request in PIXELS
            # by font metrics, and the default 80x24 over-requests badly and
            # crushes the sibling panes — it fills via expand regardless.
            self.reader = tk.Text(right, wrap="word", padx=16, pady=14,
                                  relief="flat", cursor="arrow", bd=0,
                                  width=30, height=8, state="disabled")
            self.reader.pack(side="left", fill="both", expand=True)
            aura.track(self.reader, "text")
            self._style_reader_tags()
            panes.add(right, weight=3)

            self._open_btn.state(["disabled"])
            self._star_btn.state(["disabled"])
            self._panes = panes
            self.after(120, self._init_sashes)

        def _init_sashes(self):
            """Give the three panes sensible initial widths (feed / list / read)."""
            try:
                total = self._panes.winfo_width()
                if total <= 1:
                    self.after(120, self._init_sashes)
                    return
                self._panes.sashpos(0, int(total * 0.22))
                self._panes.sashpos(1, int(total * 0.22) + int(total * 0.34))
            except Exception:
                pass

        # ---- reader tag colours (track() flips bg/fg but not tag colours) ---
        def _style_reader_tags(self):
            if not hasattr(self, "reader"):
                return
            p = aura.P()
            fam = self._font_family
            try:
                self.reader.tag_configure("title", font=(fam, 16, "bold"),
                                          foreground=p["text"], spacing3=6)
                self.reader.tag_configure("meta", foreground=p["muted"],
                                          font=(fam, 10), spacing3=10)
                self.reader.tag_configure("body", font=(fam, 11), spacing1=2,
                                          spacing3=4)
            except Exception:
                pass

        # ---- theme override: re-tint the raw tk reader on dark<->light ------
        def set_theme(self, theme):
            super().set_theme(theme)
            self._style_reader_tags()
            try:
                self._render_article()   # re-render with the new tag colours
            except Exception:
                pass

        # ---- background runner ---------------------------------------------
        def _bg(self, work, on_ok, busy="Working…"):
            """Run ``work()`` off the UI thread; ``on_ok(result)`` back on it.

            Errors are shown inline (FeedHubError message, or a generic note),
            never as a traceback.  Refuses to start a second op while busy.
            """
            if self._busy:
                self.set_error("Please wait — a refresh is already running.")
                return
            self._busy = True
            try:
                self._refresh_btn.state(["disabled"])
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
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                try:
                    self._refresh_btn.state(["!disabled"])
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

        # ---- feed tree ------------------------------------------------------
        def _reload_feeds(self):
            """Rebuild the left tree from the store, keeping the selection."""
            try:
                feeds = self.store.list_feeds()
                counts = self.store.unread_counts()
                total_unread = self.store.unread_count()
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self.feed_tree.delete(*self.feed_tree.get_children())
            self._feed_iids = {}

            def label(text, unread):
                return f"{text}  ({unread})" if unread else text

            self.feed_tree.insert("", "end", iid=ALL_ID,
                                  text=label("▤ All feeds", total_unread),
                                  open=True)
            self.feed_tree.insert("", "end", iid=STARRED_ID, text="★ Starred")

            folders = {}
            for feed in feeds:
                folder = feed.get("folder") or ""
                parent = ""
                if folder:
                    fiid = "folder:" + folder
                    if fiid not in folders:
                        self.feed_tree.insert("", "end", iid=fiid,
                                              text="◈ " + folder, open=True)
                        folders[fiid] = 0
                    parent = fiid
                iid = "feed:%d" % feed["id"]
                unread = counts.get(feed["id"], 0)
                name = feed.get("title") or feed.get("url")
                self.feed_tree.insert(parent, "end", iid=iid,
                                      text=label(name, unread))
                self._feed_iids[iid] = feed
                if parent:
                    folders[parent] += unread
            # annotate folder unread totals
            for fiid, total in folders.items():
                base = self.feed_tree.item(fiid, "text")
                if total:
                    self.feed_tree.item(fiid, text=f"{base}  ({total})")

            # restore / default selection
            want = self._current_scope
            if self._current_feed:
                want = "feed:%d" % self._current_feed["id"]
            if not self.feed_tree.exists(want):
                want = ALL_ID
            self.feed_tree.selection_set(want)
            self.feed_tree.focus(want)

        def _on_feed_select(self, _event=None):
            sel = self.feed_tree.selection()
            if not sel:
                return
            iid = sel[0]
            if iid in self._feed_iids:
                self._current_feed = self._feed_iids[iid]
                self._current_scope = iid
            elif iid == STARRED_ID:
                self._current_feed = None
                self._current_scope = STARRED_ID
            elif iid.startswith("folder:"):
                self._current_feed = None
                self._current_scope = iid
            else:
                self._current_feed = None
                self._current_scope = ALL_ID
            self._reload_articles()

        def _feed_context_menu(self, event):
            iid = self.feed_tree.identify_row(event.y)
            if not iid or iid not in self._feed_iids:
                return
            self.feed_tree.selection_set(iid)
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Refresh this feed",
                             command=lambda: self._refresh(scope="feed"))
            menu.add_command(label="Set folder…", command=self._set_folder)
            menu.add_command(label="Mark all read", command=self._mark_all_read)
            menu.add_separator()
            menu.add_command(label="Remove feed", command=self._remove_feed)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        # ---- article list ---------------------------------------------------
        def _collect_articles(self):
            """Articles for the current scope (feed/folder/all/starred/search)."""
            search = self._search_text or None
            if self._current_scope == STARRED_ID:
                arts = []
                for feed in self.store.list_feeds():
                    arts += self.store.list_articles(
                        feed["id"], unread_only=self._unread_only,
                        starred_only=True, search=search)
                return arts
            if self._current_feed is not None:
                return self.store.list_articles(
                    self._current_feed["id"], unread_only=self._unread_only,
                    search=search)
            # all feeds or a folder
            feeds = self.store.list_feeds()
            if self._current_scope.startswith("folder:"):
                folder = self._current_scope.split(":", 1)[1]
                feeds = [f for f in feeds if (f.get("folder") or "") == folder]
            arts = []
            for feed in feeds:
                arts += self.store.list_articles(
                    feed["id"], unread_only=self._unread_only, search=search)
            arts.sort(key=lambda a: (a.get("fetched_at") or 0), reverse=True)
            return arts

        def _reload_articles(self):
            try:
                arts = self._collect_articles()
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self.article_list.delete(*self.article_list.get_children())
            self._article_iids = {}
            for art in arts:
                iid = "art:%d" % art["id"]
                state = ("★" if art["starred"] else "") + \
                        ("" if art["read"] else "●")
                title = art["title"] or "(untitled)"
                date = (art.get("published") or "")[:24]
                self.article_list.insert("", "end", iid=iid,
                                         values=(state, title, date))
                self._article_iids[iid] = art
            self._current_article = None
            self._clear_reader()
            if not arts:
                self.set_status("No articles here yet — try Refresh.")
            else:
                self.set_status(f"{len(arts)} article(s).")

        def _on_article_select(self, _event=None):
            sel = self.article_list.selection()
            if not sel:
                return
            art = self._article_iids.get(sel[0])
            if not art:
                return
            self._current_article = art
            # mark read on open
            if not art["read"]:
                try:
                    self.store.mark_read(art["id"], True)
                    art["read"] = 1
                    self.article_list.set(sel[0], "state",
                                          "★" if art["starred"] else "")
                except FeedHubError as exc:
                    self.set_error(exc)
                self._reload_feeds()
            self._render_article()

        # ---- reader ---------------------------------------------------------
        def _clear_reader(self):
            self.reader.configure(state="normal")
            self.reader.delete("1.0", "end")
            self.reader.configure(state="disabled")
            self._open_btn.state(["disabled"])
            self._star_btn.state(["disabled"])
            self._star_btn.configure(text="Star")

        def _render_article(self):
            if not hasattr(self, "reader"):
                return
            art = self._current_article
            self.reader.configure(state="normal")
            self.reader.delete("1.0", "end")
            if art is None:
                self.reader.configure(state="disabled")
                return
            self.reader.insert("end", (art["title"] or "(untitled)") + "\n",
                               "title")
            meta = " · ".join(x for x in (art.get("author"),
                                          art.get("published")) if x)
            if meta:
                self.reader.insert("end", meta + "\n", "meta")
            body = to_text(art.get("content") or art.get("summary") or "")
            self.reader.insert("end", (body or "(no content)") + "\n", "body")
            self.reader.configure(state="disabled")
            self._open_btn.state(["!disabled"] if art.get("link") else ["disabled"])
            self._star_btn.state(["!disabled"])
            self._star_btn.configure(text="Unstar" if art["starred"] else "Star")

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
                self.set_error(exc)
                return
            art["starred"] = new
            iid = "art:%d" % art["id"]
            if self.article_list.exists(iid):
                self.article_list.set(
                    iid, "state",
                    ("★" if new else "") + ("" if art["read"] else "●"))
            self._star_btn.configure(text="Unstar" if new else "Star")
            if self._current_scope == STARRED_ID:
                self._reload_articles()

        # ---- actions --------------------------------------------------------
        def _add_feed(self):
            url = simpledialog.askstring(
                "Add feed", "Feed URL (RSS or Atom):", parent=self)
            if not url:
                return
            url = url.strip()
            folder = simpledialog.askstring(
                "Add feed", "Folder (optional):", parent=self) or ""
            try:
                feed = self.store.add_feed(url, folder=folder.strip())
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self._current_feed = feed
            self._current_scope = "feed:%d" % feed["id"]
            self._reload_feeds()
            self.set_status(f"Added {url} — refreshing…")
            self._refresh(scope="feed", feed=feed)

        def _remove_feed(self):
            feed = self._current_feed
            if not feed:
                self.set_error("Select a feed to remove.")
                return
            name = feed.get("title") or feed.get("url")
            if not messagebox.askyesno("Remove feed",
                                       f"Unsubscribe from “{name}”?", parent=self):
                return
            try:
                self.store.remove_feed(feed["id"])
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self._current_feed = None
            self._current_scope = ALL_ID
            self._reload_feeds()
            self._reload_articles()
            self.set_status(f"Removed {name}.")

        def _set_folder(self):
            feed = self._current_feed
            if not feed:
                return
            folder = simpledialog.askstring(
                "Set folder", "Folder name (blank for none):",
                initialvalue=feed.get("folder") or "", parent=self)
            if folder is None:
                return
            try:
                self.store.set_feed_folder(feed["id"], folder.strip())
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self._reload_feeds()

        def _mark_all_read(self):
            try:
                if self._current_feed is not None:
                    self.store.mark_all_read(self._current_feed["id"], True)
                else:
                    for feed in self.store.list_feeds():
                        self.store.mark_all_read(feed["id"], True)
            except FeedHubError as exc:
                self.set_error(exc)
                return
            self._reload_feeds()
            self._reload_articles()

        def _refresh(self, scope=None, feed=None):
            """Threaded refresh of one feed, the current feed, or everything."""
            if scope is None:
                scope = "feed" if self._current_feed else "all"
            if scope == "feed":
                target = feed or self._current_feed
                if not target:
                    self._refresh(scope="all")
                    return
                targets = [target]
            else:
                targets = self.store.list_feeds()
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
                self._reload_feeds()
                self._reload_articles()
                if failures and len(failures) == n:
                    self.set_error(f"Refresh failed: {failures[0][1]}")
                elif failures:
                    self.set_status(
                        f"{total_new} new; {len(failures)} feed(s) failed.",
                        kind="ok")
                else:
                    self.set_status(f"Refreshed — {total_new} new article(s).",
                                    kind="ok")

            self._bg(work, done, busy=f"Refreshing {len(targets)} feed(s)…")

        def _on_search(self):
            self._search_text = self._search_entry.get().strip()
            self._reload_articles()

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
                self.set_error(exc)
                return
            self._reload_feeds()
            self.set_status(f"Imported {len(added)} new feed(s).", kind="ok")

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
                self.set_error(exc)
                return
            self.set_status(f"Exported to {path}.", kind="ok")

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

        def _set_interval(self):
            minutes = simpledialog.askinteger(
                "Auto-refresh", "Minutes between refreshes (0 = off):",
                initialvalue=self._refresh_minutes, minvalue=0, maxvalue=1440,
                parent=self)
            if minutes is None:
                return
            self._refresh_minutes = minutes
            guiconfig.set_refresh_minutes(minutes)
            self._schedule_auto_refresh()
            self.set_status(
                "Auto-refresh off." if not minutes
                else f"Auto-refresh every {minutes} min.")

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
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

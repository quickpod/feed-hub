#!/usr/bin/env python3
r"""FeedHub -- a pure-stdlib tkinter GUI on top of the ``feedhub`` library.

A single main window laid out as three panes:

  * left    -- a tree of folders and feeds, each with an unread count;
  * middle  -- the article list for the selected feed (star, read-state,
    title and date);
  * right   -- a clean reader for the selected article (title, meta, rendered
    text) with an "Open in browser" button.

A toolbar offers Add feed, Refresh (threaded), Mark all read and a search box.
Feeds auto-refresh on a configurable timer.  Every network refresh runs on a
background thread and is marshalled back with ``self.after`` so the UI never
freezes; failures show the ``FeedHubError`` message in an inline bar, never a
traceback.

Design goals (mirrored from the QuickOpen house style):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

# NOTE: tkinter is imported lazily inside main()/build_app so that merely
# importing this module (e.g. during packaging or on a headless CI box) is safe.

APP_NAME = "FeedHub"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "FeedHub — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai/projects/feed-hub"

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}


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
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox

    from . import guiconfig
    from .errors import FeedHubError
    from .store import Store
    from . import feeds as feeds_mod
    from . import opml as opml_mod
    from .reader import to_text

    FONT = "Segoe UI"

    ALL_ID = "all"           # tree iid for the "All feeds" pseudo-node
    STARRED_ID = "starred"   # tree iid for the "Starred" pseudo-node

    class App(tk.Tk):
        def __init__(self, store_path=None):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1180x720")
            self.minsize(940, 560)

            cfg = guiconfig.load()
            self.theme = cfg["theme"]
            self._unread_only = cfg["unread_only"]
            self._refresh_minutes = cfg["refresh_minutes"]
            self._busy = False
            self._tracked = []          # (tk_widget, role) for manual re-theming
            self._img_refs = []         # keep PhotoImage refs alive
            self._feed_iids = {}        # tree iid -> feed dict
            self._article_iids = {}     # list iid -> article dict
            self._current_feed = None   # selected feed dict, or None (=All)
            self._current_scope = ALL_ID
            self._current_article = None
            self._search_text = ""
            self._auto_after = None

            try:
                self.store = Store(store_path)
            except FeedHubError as exc:
                messagebox.showerror(APP_NAME, f"Could not open the feed store:\n{exc}")
                raise

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self._on_close)

            self.after(50, self._reload_feeds)
            self._schedule_auto_refresh()

        # ---- assets / icon --------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("feed-hub.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("feed-hub.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming --------------------------------------------------------
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Surface.TFrame", background=p["surface"])
            style.configure("Toolbar.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Surface.TLabel", background=p["surface"],
                            foreground=p["text"])
            style.configure("Header.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 15, "bold"))
            style.configure("Meta.TLabel", background=p["surface"],
                            foreground=p["muted"], font=(FONT, 10))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("TEntry", fieldbackground=p["entry"], foreground=p["text"],
                            insertcolor=p["text"], bordercolor=p["border"])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=26)
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("Treeview.Heading", background=p["surface"],
                            foreground=p["muted"], font=(FONT, 9, "bold"))
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TPanedwindow", background=p["bg"])
            style.configure("TSeparator", background=p["border"])

            # manually re-colour raw tk widgets (Text)
            for widget, role in list(self._tracked):
                try:
                    if role == "text":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                        widget.tag_configure("title", font=(FONT, 16, "bold"),
                                             foreground=p["text"],
                                             spacing3=6)
                        widget.tag_configure("meta", foreground=p["muted"],
                                             font=(FONT, 10), spacing3=10)
                        widget.tag_configure("body", font=(FONT, 11), spacing1=2,
                                             spacing3=4)
                except Exception:
                    pass
            self._restyle_status()

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light" if self.theme == "dark" else "🌙 Dark")
            self._render_article()  # refresh Text tag colours

        # ---- menu -----------------------------------------------------------
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
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            viewm.add_command(label="Auto-refresh interval…",
                              command=self._set_interval)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_url(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-n>", lambda e: self._add_feed())
            self.bind_all("<Control-r>", lambda e: self._refresh(scope="all"))

        # ---- layout ---------------------------------------------------------
        def _build_layout(self):
            # Toolbar
            tb = ttk.Frame(self, style="Toolbar.TFrame", padding=(10, 8))
            tb.pack(side="top", fill="x")
            ttk.Button(tb, text="＋ Add feed", style="Accent.TButton",
                       command=self._add_feed).pack(side="left")
            self._refresh_btn = ttk.Button(tb, text="⟳ Refresh",
                                           command=lambda: self._refresh())
            self._refresh_btn.pack(side="left", padx=(6, 0))
            ttk.Button(tb, text="✓ Mark all read",
                       command=self._mark_all_read).pack(side="left", padx=(6, 0))

            self._theme_btn = ttk.Button(
                tb, text="☀ Light" if self.theme == "dark" else "🌙 Dark",
                command=self.toggle_theme)
            self._theme_btn.pack(side="right")

            self._search_var = tk.StringVar()
            search_entry = ttk.Entry(tb, textvariable=self._search_var, width=26)
            search_entry.pack(side="right", padx=(0, 8))
            search_entry.insert(0, "")
            self._search_var.trace_add("write", lambda *_: self._on_search())
            ttk.Label(tb, text="Search:", style="Surface.TLabel").pack(
                side="right", padx=(0, 4))

            # Three-pane body
            panes = ttk.Panedwindow(self, orient="horizontal")
            panes.pack(side="top", fill="both", expand=True, padx=8, pady=(6, 0))

            # -- left: feed tree
            left = ttk.Frame(panes, style="Surface.TFrame")
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

            # -- middle: article list
            mid = ttk.Frame(panes, style="Surface.TFrame")
            cols = ("state", "title", "date")
            self.article_list = ttk.Treeview(mid, columns=cols, show="headings",
                                             selectmode="browse")
            self.article_list.heading("state", text="")
            self.article_list.heading("title", text="Article")
            self.article_list.heading("date", text="Date")
            self.article_list.column("state", width=34, anchor="center", stretch=False)
            self.article_list.column("title", width=340, anchor="w")
            self.article_list.column("date", width=130, anchor="w", stretch=False)
            asb = ttk.Scrollbar(mid, orient="vertical",
                                command=self.article_list.yview)
            self.article_list.configure(yscrollcommand=asb.set)
            asb.pack(side="right", fill="y")
            self.article_list.pack(side="left", fill="both", expand=True)
            self.article_list.bind("<<TreeviewSelect>>", self._on_article_select)
            self.article_list.bind("<space>", lambda e: self._toggle_star())
            panes.add(mid, weight=2)

            # -- right: reader
            right = ttk.Frame(panes, style="Surface.TFrame", padding=0)
            rtop = ttk.Frame(right, style="Surface.TFrame", padding=(10, 8))
            rtop.pack(side="top", fill="x")
            self._open_btn = ttk.Button(rtop, text="Open in browser",
                                        command=self._open_current, state="disabled")
            self._open_btn.pack(side="right")
            self._star_btn = ttk.Button(rtop, text="☆ Star",
                                        command=self._toggle_star, state="disabled")
            self._star_btn.pack(side="right", padx=(0, 6))
            self.reader = tk.Text(right, wrap="word", padx=16, pady=14,
                                  relief="flat", cursor="arrow", state="disabled")
            self.reader.pack(side="left", fill="both", expand=True)
            self.track(self.reader, "text")
            panes.add(right, weight=3)

            # Status / error bar
            self._status_bar = tk.Frame(self, height=26)
            self._status_bar.pack(side="bottom", fill="x")
            self.status_lbl = tk.Label(self._status_bar, anchor="w", padx=10)
            self.status_lbl.pack(side="left", fill="x", expand=True)
            self._set_status("Ready")

        def _restyle_status(self):
            p = self._pal()
            try:
                self._status_bar.configure(bg=p["surface"],
                                           highlightthickness=1,
                                           highlightbackground=p["border"])
                self.status_lbl.configure(bg=p["surface"], fg=p["muted"])
            except Exception:
                pass

        # ---- status / error bar --------------------------------------------
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            try:
                self.status_lbl.configure(text=text, fg=color)
            except Exception:
                pass

        def _show_error(self, message):
            self._set_status("✕ " + str(message), kind="err")

        # ---- background runner ---------------------------------------------
        def _bg(self, work, on_ok, busy="Working…"):
            """Run ``work()`` off the UI thread; ``on_ok(result)`` back on it.

            Errors are shown inline (FeedHubError message, or a generic note),
            never as a traceback.  Refuses to start a second op while busy.
            """
            if self._busy:
                self._show_error("Please wait — a refresh is already running.")
                return
            self._busy = True
            try:
                self._refresh_btn.state(["disabled"])
            except Exception:
                pass
            self._set_status(busy, kind="working")

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
                    self._show_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- feed tree ------------------------------------------------------
        def _reload_feeds(self):
            """Rebuild the left tree from the store, keeping the selection."""
            try:
                feeds = self.store.list_feeds()
                counts = self.store.unread_counts()
                total_unread = self.store.unread_count()
            except FeedHubError as exc:
                self._show_error(exc)
                return
            self.feed_tree.delete(*self.feed_tree.get_children())
            self._feed_iids = {}

            def label(text, unread):
                return f"{text}  ({unread})" if unread else text

            self.feed_tree.insert("", "end", iid=ALL_ID,
                                  text=label("📚 All feeds", total_unread),
                                  open=True)
            self.feed_tree.insert("", "end", iid=STARRED_ID, text="⭐ Starred")

            folders = {}
            for feed in feeds:
                folder = feed.get("folder") or ""
                parent = ""
                if folder:
                    fiid = "folder:" + folder
                    if fiid not in folders:
                        self.feed_tree.insert("", "end", iid=fiid,
                                              text="📁 " + folder, open=True)
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
                self._show_error(exc)
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
                self._set_status("No articles here yet — try Refresh.")
            else:
                self._set_status(f"{len(arts)} article(s).")

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
                    self._show_error(exc)
                self._reload_feeds()
            self._render_article()

        # ---- reader ---------------------------------------------------------
        def _clear_reader(self):
            self.reader.configure(state="normal")
            self.reader.delete("1.0", "end")
            self.reader.configure(state="disabled")
            self._open_btn.state(["disabled"])
            self._star_btn.state(["disabled"])
            self._star_btn.configure(text="☆ Star")

        def _render_article(self):
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
            self._star_btn.configure(text="★ Unstar" if art["starred"] else "☆ Star")

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
                self._show_error(exc)
                return
            art["starred"] = new
            iid = "art:%d" % art["id"]
            if self.article_list.exists(iid):
                self.article_list.set(
                    iid, "state",
                    ("★" if new else "") + ("" if art["read"] else "●"))
            self._star_btn.configure(text="★ Unstar" if new else "☆ Star")
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
                self._show_error(exc)
                return
            self._current_feed = feed
            self._current_scope = "feed:%d" % feed["id"]
            self._reload_feeds()
            self._set_status(f"Added {url} — refreshing…")
            self._refresh(scope="feed", feed=feed)

        def _remove_feed(self):
            feed = self._current_feed
            if not feed:
                self._show_error("Select a feed to remove.")
                return
            name = feed.get("title") or feed.get("url")
            if not messagebox.askyesno("Remove feed",
                                       f"Unsubscribe from “{name}”?", parent=self):
                return
            try:
                self.store.remove_feed(feed["id"])
            except FeedHubError as exc:
                self._show_error(exc)
                return
            self._current_feed = None
            self._current_scope = ALL_ID
            self._reload_feeds()
            self._reload_articles()
            self._set_status(f"Removed {name}.")

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
                self._show_error(exc)
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
                self._show_error(exc)
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
                self._set_status("No feeds to refresh — add one first.")
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
                    self._show_error(f"Refresh failed: {failures[0][1]}")
                elif failures:
                    self._set_status(
                        f"{total_new} new; {len(failures)} feed(s) failed.",
                        kind="ok")
                else:
                    self._set_status(f"Refreshed — {total_new} new article(s).",
                                     kind="ok")

            self._bg(work, done, busy=f"Refreshing {len(targets)} feed(s)…")

        def _on_search(self):
            self._search_text = self._search_var.get().strip()
            self._reload_articles()

        # ---- OPML -----------------------------------------------------------
        def _import_opml(self):
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title="Import OPML",
                filetypes=[("OPML files", "*.opml *.xml"), ("All files", "*.*")])
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    added = opml_mod.import_opml(self.store, fh.read())
            except (OSError, FeedHubError) as exc:
                self._show_error(exc)
                return
            self._reload_feeds()
            self._set_status(f"Imported {len(added)} new feed(s).", kind="ok")

        def _export_opml(self):
            from tkinter import filedialog
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
                self._show_error(exc)
                return
            self._set_status(f"Exported to {path}.", kind="ok")

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
            self._set_status(
                "Auto-refresh off." if not minutes
                else f"Auto-refresh every {minutes} min.")

        # ---- misc -----------------------------------------------------------
        def _about(self):
            messagebox.showinfo(
                "About " + APP_NAME,
                f"{APP_NAME} {APP_VERSION}\n\n"
                "A fast, offline, 100% open-source RSS & Atom reader.\n"
                "Built by AI, published on QuickOpen.\n\n" + PROJECT_URL)

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
    With no display (e.g. a server), it prints a friendly note and returns 0
    instead of raising.
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

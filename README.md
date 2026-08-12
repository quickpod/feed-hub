# FeedHub

A fast, **offline**, **100% open-source** RSS & Atom feed reader for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/feed-hub).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Follow websites, blogs and podcasts via RSS/Atom: organize feeds into folders, read a clean article view, mark read/unread and star for later, search across everything, and import/export your subscriptions as OPML. Refreshes on a schedule; everything is stored locally.

## Install

Download **`FeedHub-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/feed-hub) or the [GitHub release](https://github.com/quickpod/feed-hub/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python feed_hub_app.py          # GUI
python -m feedhub --help    # CLI
```

## Features

- **Three-pane reader** — folders/feeds on the left with live unread counts, the article list in the middle, and a clean, readable article view on the right.
- **RSS 2.0 and Atom 1.0** — parsed with `feedparser`; feed titles, entry titles, links, authors and dates all handled.
- **Folders & organization** — group subscriptions into folders; a "Starred" view collects read-later items across every feed.
- **Read/unread & starring** — articles mark read on open; star anything for later; "Mark all read" per feed or globally.
- **Search** — filter articles by text across the current feed, folder, or all feeds at once.
- **Clean reader** — article HTML is sanitized (scripts, event handlers and `javascript:` URLs stripped) and rendered as tidy text, with one-click "Open in browser".
- **Auto-refresh** — feeds refresh on a configurable timer; manual refresh runs on a background thread so the UI never freezes.
- **OPML import/export** — move your subscriptions (and folders) in and out, round-trip safe.
- **Offline & local** — everything lives in a per-user SQLite store; nothing is uploaded anywhere.
- **Dark mode** — light/dark theme toggle in the QuickOpen palette; your choice is remembered.

## CLI examples

Every feature is scriptable via `python -m feedhub` (the store lives in your per-user config dir; override it with `--store PATH`).

```sh
# Subscribe to a feed, optionally in a folder
python -m feedhub add https://example.com/feed.xml --folder News

# List subscriptions with unread counts
python -m feedhub list

# Fetch new articles (one feed, or everything)
python -m feedhub refresh --all
python -m feedhub refresh https://example.com/feed.xml

# Browse cached articles (filter to unread / starred / a search term)
python -m feedhub articles https://example.com/feed.xml --unread
python -m feedhub articles "Example Feed" --search release

# Read an article by id (marks it read and prints a clean text view)
python -m feedhub read 1

# Star an article for later (or --unstar it)
python -m feedhub star 1

# Move subscriptions in and out as OPML
python -m feedhub opml export subscriptions.opml
python -m feedhub opml import subscriptions.opml

# Unsubscribe (also drops that feed's cached articles)
python -m feedhub remove https://example.com/feed.xml
```

Feeds can be referenced by URL, numeric id, or title. On any error the CLI prints a single `error:` line and exits non-zero — no traceback.


## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.

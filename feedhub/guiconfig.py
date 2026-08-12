r"""Tiny JSON-backed config for the FeedHub GUI.

Stores just the presentation preferences and never raises: the chosen theme
("light"/"dark"), the auto-refresh interval (minutes; 0 disables) and whether
the article list hides already-read items.  Subscriptions themselves live in
the SQLite store (see :mod:`feedhub.store`), not here.

On Windows the file lives at ``%LOCALAPPDATA%\FeedHub\config.json``; elsewhere
it falls back to ``~/.feedhub/config.json``.  Every function is defensive -- a
corrupt or unreadable config must never stop the app from starting.
"""

from __future__ import annotations

import json
import os

APP_DIRNAME = "FeedHub"
CONFIG_NAME = "config.json"
STORE_NAME = "feedhub.db"
VALID_THEMES = ("light", "dark")
DEFAULT_REFRESH_MINUTES = 30
MAX_REFRESH_MINUTES = 24 * 60


def config_dir():
    r"""Directory that holds the config + store (created on demand).

    ``%LOCALAPPDATA%\FeedHub`` on Windows, ``~/.feedhub`` otherwise.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        return os.path.join(local, APP_DIRNAME)
    return os.path.join(os.path.expanduser("~"), "." + APP_DIRNAME.lower())


def config_path():
    return os.path.join(config_dir(), CONFIG_NAME)


def store_path():
    """Default on-disk location of the subscriptions/articles database."""
    return os.path.join(config_dir(), STORE_NAME)


def _defaults():
    return {"theme": "light", "refresh_minutes": DEFAULT_REFRESH_MINUTES,
            "unread_only": False}


def _clean_refresh(value):
    try:
        n = int(value)
    except Exception:
        return DEFAULT_REFRESH_MINUTES
    if n < 0:
        return 0
    return min(n, MAX_REFRESH_MINUTES)


def load():
    """Return the config dict, always with all known keys populated."""
    cfg = _defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            if data.get("theme") in VALID_THEMES:
                cfg["theme"] = data["theme"]
            if "refresh_minutes" in data:
                cfg["refresh_minutes"] = _clean_refresh(data["refresh_minutes"])
            cfg["unread_only"] = bool(data.get("unread_only", False))
    except Exception:
        pass  # missing/corrupt -> defaults; never fatal
    return cfg


def save(cfg):
    """Persist *cfg* (best-effort; failures are swallowed)."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        clean = {
            "theme": cfg.get("theme") if cfg.get("theme") in VALID_THEMES else "light",
            "refresh_minutes": _clean_refresh(cfg.get("refresh_minutes")),
            "unread_only": bool(cfg.get("unread_only", False)),
        }
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        os.replace(tmp, config_path())
    except Exception:
        pass


def get_theme():
    return load().get("theme", "light")


def set_theme(theme):
    if theme not in VALID_THEMES:
        return
    cfg = load()
    cfg["theme"] = theme
    save(cfg)


def get_refresh_minutes():
    return load().get("refresh_minutes", DEFAULT_REFRESH_MINUTES)


def set_refresh_minutes(minutes):
    cfg = load()
    cfg["refresh_minutes"] = _clean_refresh(minutes)
    save(cfg)


def get_unread_only():
    return load().get("unread_only", False)


def set_unread_only(flag):
    cfg = load()
    cfg["unread_only"] = bool(flag)
    save(cfg)

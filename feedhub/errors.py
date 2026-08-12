"""Error types for feedhub."""


class FeedHubError(Exception):
    """Raised for any recoverable failure in a feedhub operation.

    All public functions raise this (and only this) on failure so callers
    -- including the CLI and the GUI -- have a single exception to catch and
    can report a clean message instead of a raw traceback.
    """

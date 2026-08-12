"""Turn feed HTML into something safe and pleasant to display.

Two public helpers, both pure-stdlib (``html.parser`` + ``html``):

* :func:`clean_html` -- strip ``<script>``/``<style>`` and other dangerous
  markup (event handlers, ``javascript:`` URLs) while keeping the basic
  formatting tags a reader cares about (paragraphs, lists, links, emphasis).
* :func:`to_text` -- flatten HTML to readable plain text, collapsing runs of
  whitespace and turning block elements into line breaks.

Neither function raises on malformed input; the worst case is that some markup
is dropped.  ``feedparser`` already sanitises most feeds, but content that is
rendered verbatim (or handled outside feedparser) still needs this pass.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# Tags we keep in :func:`clean_html`.  Everything else is unwrapped (its text
# survives, the tag itself is dropped).  ``script``/``style`` are dropped whole.
_ALLOWED_TAGS = {
    "p", "br", "hr", "a", "b", "strong", "i", "em", "u", "s", "code", "pre",
    "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "td", "th", "span", "div", "img",
}
_VOID_TAGS = {"br", "hr", "img"}
_DROP_CONTENT_TAGS = {"script", "style", "head", "title", "noscript", "iframe"}
# Attributes worth keeping per tag (anything else, e.g. ``onclick``, is dropped).
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}
# Block-level tags that become line breaks in :func:`to_text`.
_BLOCK_TAGS = {
    "p", "div", "br", "hr", "li", "tr", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
}


def _safe_url(value):
    """Return *value* if it is a benign URL, else an empty string."""
    if not value:
        return ""
    stripped = value.strip()
    lowered = stripped.lower()
    # Block javascript:, vbscript: and data: (except plain images) URLs.
    if lowered.startswith(("javascript:", "vbscript:")):
        return ""
    if lowered.startswith("data:") and not lowered.startswith("data:image/"):
        return ""
    return stripped


class _Cleaner(HTMLParser):
    """Rebuild a sanitised subset of the input HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip_depth = 0  # >0 while inside a drop-content element

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS:
            return
        kept = []
        allowed = _ALLOWED_ATTRS.get(tag, set())
        for name, value in attrs:
            name = (name or "").lower()
            if name not in allowed:
                continue
            if name in ("href", "src"):
                value = _safe_url(value)
                if not value:
                    continue
            kept.append((name, value))
        attr_str = "".join(
            f' {n}="{html.escape(v or "", quote=True)}"' for n, v in kept)
        if tag in _VOID_TAGS:
            self.out.append(f"<{tag}{attr_str}>")
        else:
            self.out.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.out.append(html.escape(data, quote=False))

    def result(self):
        return "".join(self.out)


class _Textifier(HTMLParser):
    """Flatten HTML to plain text with sensible line breaks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if tag == "li":
            self.parts.append("\n• ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.parts.append(data)

    def result(self):
        return "".join(self.parts)


def clean_html(source):
    """Return a sanitised HTML fragment safe to render in a viewer.

    Drops ``<script>``/``<style>`` (content and all), event-handler attributes
    and ``javascript:`` URLs, and unwraps unknown tags while keeping their text.
    Never raises -- malformed input just yields best-effort output.
    """
    if source is None:
        return ""
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    parser = _Cleaner()
    try:
        parser.feed(str(source))
        parser.close()
    except Exception:
        pass
    return parser.result()


def to_text(source):
    """Flatten HTML *source* to readable plain text.

    Block elements become newlines, list items get a bullet, runs of blank
    lines and horizontal whitespace are collapsed.  Plain (non-HTML) text is
    returned essentially unchanged.  Never raises.
    """
    if source is None:
        return ""
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    parser = _Textifier()
    try:
        parser.feed(str(source))
        parser.close()
    except Exception:
        pass
    text = parser.result()
    # Collapse horizontal whitespace, then squeeze blank-line runs.
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

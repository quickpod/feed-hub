"""reader.to_text / clean_html strip HTML safely."""

from feedhub.reader import to_text, clean_html


def test_to_text_strips_tags():
    html = "<p>Hello <b>world</b></p><p>Second line</p>"
    text = to_text(html)
    assert "Hello world" in text
    assert "Second line" in text
    assert "<" not in text and ">" not in text


def test_to_text_drops_script():
    html = "<p>Visible</p><script>alert('x')</script>"
    text = to_text(html)
    assert "Visible" in text
    assert "alert" not in text


def test_to_text_lists_become_bullets():
    html = "<ul><li>one</li><li>two</li></ul>"
    text = to_text(html)
    assert "one" in text and "two" in text


def test_to_text_handles_entities():
    assert "AT&T" in to_text("<p>AT&amp;T</p>")


def test_clean_html_removes_script_and_handlers():
    dirty = '<p onclick="evil()">Hi</p><script>bad()</script>'
    clean = clean_html(dirty)
    assert "script" not in clean.lower()
    assert "onclick" not in clean.lower()
    assert "Hi" in clean


def test_clean_html_strips_javascript_url():
    clean = clean_html('<a href="javascript:alert(1)">link</a>')
    assert "javascript:" not in clean.lower()
    assert "link" in clean


def test_clean_html_keeps_safe_link():
    clean = clean_html('<a href="https://example.com">go</a>')
    assert 'href="https://example.com"' in clean


def test_empty_inputs():
    assert to_text(None) == ""
    assert clean_html(None) == ""
    assert to_text("") == ""

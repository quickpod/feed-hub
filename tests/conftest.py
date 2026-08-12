"""Shared fixtures: local RSS/Atom sample strings (no network needed)."""

import pytest

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example RSS Feed</title>
    <link>https://example.com/</link>
    <description>A sample RSS 2.0 feed for testing.</description>
    <item>
      <title>First RSS Post</title>
      <link>https://example.com/posts/1</link>
      <guid>https://example.com/posts/1</guid>
      <author>alice@example.com (Alice)</author>
      <pubDate>Mon, 04 Aug 2025 09:00:00 GMT</pubDate>
      <description>Summary of the &lt;b&gt;first&lt;/b&gt; post.</description>
    </item>
    <item>
      <title>Second RSS Post</title>
      <link>https://example.com/posts/2</link>
      <guid>https://example.com/posts/2</guid>
      <pubDate>Tue, 05 Aug 2025 10:30:00 GMT</pubDate>
      <description>The second summary.</description>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <link href="https://atom.example.com/"/>
  <updated>2025-08-06T12:00:00Z</updated>
  <id>urn:uuid:atom-feed</id>
  <entry>
    <title>First Atom Entry</title>
    <link href="https://atom.example.com/entries/1"/>
    <id>urn:uuid:atom-1</id>
    <updated>2025-08-06T12:00:00Z</updated>
    <published>2025-08-06T12:00:00Z</published>
    <author><name>Bob</name></author>
    <summary>Atom summary one.</summary>
    <content type="html">&lt;p&gt;Body &lt;i&gt;one&lt;/i&gt;.&lt;/p&gt;</content>
  </entry>
  <entry>
    <title>Second Atom Entry</title>
    <link href="https://atom.example.com/entries/2"/>
    <id>urn:uuid:atom-2</id>
    <updated>2025-08-07T08:15:00Z</updated>
    <published>2025-08-07T08:15:00Z</published>
    <author><name>Carol</name></author>
    <summary>Atom summary two.</summary>
  </entry>
</feed>
"""

OPML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My subscriptions</title></head>
  <body>
    <outline text="News">
      <outline type="rss" text="Example RSS" title="Example RSS"
               xmlUrl="https://example.com/rss.xml"/>
      <outline type="rss" text="Atom Example" title="Atom Example"
               xmlUrl="https://atom.example.com/atom.xml"/>
    </outline>
    <outline type="rss" text="Top level feed" title="Top level feed"
             xmlUrl="https://toplevel.example.com/feed"/>
  </body>
</opml>
"""


@pytest.fixture
def rss_sample():
    return RSS_SAMPLE


@pytest.fixture
def atom_sample():
    return ATOM_SAMPLE


@pytest.fixture
def opml_sample():
    return OPML_SAMPLE


@pytest.fixture
def store(tmp_path):
    """A fresh on-disk store in a temp dir (closed on teardown)."""
    from feedhub.store import Store
    s = Store(str(tmp_path / "test.db"))
    yield s
    s.close()

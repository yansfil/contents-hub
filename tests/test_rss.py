"""Tests for RSS/Atom feed parser."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from llm_wiki.collectors.rss import FeedItem, FeedResult, fetch_feed, _parse_feed

# ---------------------------------------------------------------------------
# Fixtures: sample XML feeds
# ---------------------------------------------------------------------------

RSS2_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>My Blog</title>
    <link>https://example.com</link>
    <item>
      <title>First Post</title>
      <link>https://example.com/first-post</link>
      <description>A short summary.</description>
      <dc:creator>Alice</dc:creator>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
      <category>tech</category>
      <category>python</category>
      <content:encoded><![CDATA[<p>Full content here</p>]]></content:encoded>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/second-post/</link>
      <description>Another summary.</description>
      <pubDate>Tue, 02 Jan 2024 08:30:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

RSS2_GUID_PERMALINK_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>GUID Blog</title>
    <item>
      <title>GUID Post</title>
      <guid isPermaLink="true">https://example.com/guid-post</guid>
      <description>Summary via guid.</description>
    </item>
  </channel>
</rss>
"""

RSS2_PODCAST_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>My Podcast</title>
    <item>
      <title>Episode 1</title>
      <link>https://example.com/ep1</link>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="12345678"/>
    </item>
  </channel>
</rss>
"""

ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <entry>
    <title>Atom Entry</title>
    <link rel="alternate" href="https://example.com/atom-entry"/>
    <summary>Atom summary</summary>
    <author><name>Bob</name></author>
    <published>2024-03-15T10:00:00Z</published>
    <category term="science"/>
    <content type="html">&lt;p&gt;Atom content&lt;/p&gt;</content>
  </entry>
</feed>
"""

ATOM_PODCAST_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Podcast</title>
  <entry>
    <title>Audio Entry</title>
    <link rel="alternate" href="https://example.com/audio-entry"/>
    <link rel="enclosure" href="https://cdn.example.com/audio.mp3" type="audio/mpeg" length="9999"/>
    <summary>Has audio</summary>
  </entry>
</feed>
"""

RDF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>RDF Feed</title>
  </channel>
  <item rdf:about="https://example.com/rdf-item">
    <title>RDF Item</title>
    <description>RDF summary</description>
    <dc:creator>Charlie</dc:creator>
    <dc:date>2024-06-01T00:00:00Z</dc:date>
  </item>
</rdf:RDF>
"""


# ---------------------------------------------------------------------------
# Tests: _parse_feed (pure, no I/O)
# ---------------------------------------------------------------------------


class TestParseRss2:
    def test_basic_parse(self):
        result = _parse_feed(RSS2_XML, feed_url="https://example.com/feed", max_items=100)
        assert result.ok is True
        assert result.feed_title == "My Blog"
        assert len(result.items) == 2

    def test_first_item_fields(self):
        result = _parse_feed(RSS2_XML, feed_url="https://example.com/feed", max_items=100)
        item = result.items[0]
        assert item.url == "https://example.com/first-post"
        assert item.title == "First Post"
        assert item.summary == "A short summary."
        assert item.author == "Alice"
        assert item.published_at == datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert item.tags == ["tech", "python"]
        assert "<p>Full content here</p>" in item.content_html

    def test_trailing_slash_normalized(self):
        result = _parse_feed(RSS2_XML, feed_url="https://example.com/feed", max_items=100)
        # second-post/ should become second-post
        assert result.items[1].url == "https://example.com/second-post"

    def test_guid_as_permalink(self):
        result = _parse_feed(
            RSS2_GUID_PERMALINK_XML, feed_url="https://example.com/feed", max_items=100
        )
        assert result.ok is True
        assert result.items[0].url == "https://example.com/guid-post"

    def test_podcast_detection(self):
        result = _parse_feed(
            RSS2_PODCAST_XML, feed_url="https://example.com/feed", max_items=100
        )
        assert result.ok is True
        assert result.is_podcast is True
        item = result.items[0]
        assert item.enclosure_url == "https://cdn.example.com/ep1.mp3"
        assert item.enclosure_type == "audio/mpeg"
        assert item.enclosure_length == 12345678

    def test_max_items_limit(self):
        result = _parse_feed(RSS2_XML, feed_url="https://example.com/feed", max_items=1)
        assert len(result.items) == 1


class TestParseAtom:
    def test_basic_parse(self):
        result = _parse_feed(ATOM_XML, feed_url="https://example.com/feed", max_items=100)
        assert result.ok is True
        assert result.feed_title == "Atom Blog"
        assert len(result.items) == 1

    def test_entry_fields(self):
        result = _parse_feed(ATOM_XML, feed_url="https://example.com/feed", max_items=100)
        item = result.items[0]
        assert item.url == "https://example.com/atom-entry"
        assert item.title == "Atom Entry"
        assert item.summary == "Atom summary"
        assert item.author == "Bob"
        assert item.published_at == datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc)
        assert item.tags == ["science"]

    def test_atom_podcast(self):
        result = _parse_feed(
            ATOM_PODCAST_XML, feed_url="https://example.com/feed", max_items=100
        )
        assert result.is_podcast is True
        assert result.items[0].enclosure_url == "https://cdn.example.com/audio.mp3"


class TestParseRss1:
    def test_basic_parse(self):
        result = _parse_feed(RDF_XML, feed_url="https://example.com/feed", max_items=100)
        assert result.ok is True
        assert result.feed_title == "RDF Feed"
        assert len(result.items) == 1

    def test_item_fields(self):
        result = _parse_feed(RDF_XML, feed_url="https://example.com/feed", max_items=100)
        item = result.items[0]
        assert item.url == "https://example.com/rdf-item"
        assert item.title == "RDF Item"
        assert item.author == "Charlie"


class TestParseErrors:
    def test_invalid_xml(self):
        result = _parse_feed("<not valid xml", feed_url="https://x.com/feed", max_items=100)
        assert result.ok is False
        assert "XML parse error" in result.error

    def test_unknown_format(self):
        result = _parse_feed(
            '<?xml version="1.0"?><html></html>',
            feed_url="https://x.com/feed",
            max_items=100,
        )
        assert result.ok is False
        assert "Unknown feed format" in result.error

    def test_empty_channel(self):
        xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
        result = _parse_feed(xml, feed_url="https://x.com/feed", max_items=100)
        assert result.ok is False
        assert "No valid items" in result.error


# ---------------------------------------------------------------------------
# Tests: fetch_feed (async, with mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchFeed:
    @respx.mock
    async def test_success(self):
        respx.get("https://example.com/feed.xml").respond(
            200,
            text=RSS2_XML,
            headers={"content-type": "application/rss+xml"},
        )
        result = await fetch_feed("https://example.com/feed.xml")
        assert result.ok is True
        assert len(result.items) == 2
        assert result.feed_title == "My Blog"

    @respx.mock
    async def test_404(self):
        respx.get("https://example.com/feed.xml").respond(404)
        result = await fetch_feed("https://example.com/feed.xml")
        assert result.ok is False
        assert "404" in result.error

    @respx.mock
    async def test_429_rate_limit(self):
        respx.get("https://example.com/feed.xml").respond(429)
        result = await fetch_feed("https://example.com/feed.xml")
        assert result.ok is False
        assert "429" in result.error

    @respx.mock
    async def test_timeout(self):
        respx.get("https://example.com/feed.xml").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        result = await fetch_feed("https://example.com/feed.xml")
        assert result.ok is False
        assert "timed out" in result.error.lower()

    @respx.mock
    async def test_custom_client(self):
        """Verify that a provided httpx client is used."""
        respx.get("https://example.com/feed.xml").respond(200, text=ATOM_XML)
        async with httpx.AsyncClient() as client:
            result = await fetch_feed("https://example.com/feed.xml", client=client)
        assert result.ok is True
        assert result.feed_title == "Atom Blog"

    @respx.mock
    async def test_invalid_xml_response(self):
        respx.get("https://example.com/feed.xml").respond(200, text="<broken>")
        result = await fetch_feed("https://example.com/feed.xml")
        assert result.ok is False


# ---------------------------------------------------------------------------
# Tests: FeedItem immutability
# ---------------------------------------------------------------------------


class TestFeedItemDataclass:
    def test_frozen(self):
        item = FeedItem(url="https://example.com", title="Test")
        with pytest.raises(AttributeError):
            item.url = "https://other.com"  # type: ignore[misc]

    def test_defaults(self):
        item = FeedItem(url="https://example.com", title="Test")
        assert item.summary == ""
        assert item.tags == []
        assert item.published_at is None
        assert item.enclosure_url == ""

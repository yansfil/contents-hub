"""Tests for YouTube RSS feed parser."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from llm_wiki.collectors.youtube import (
    YouTubeFeedResult,
    YouTubeVideo,
    fetch_youtube_feed,
    parse_youtube_feed,
    resolve_feed_url,
    resolve_handle,
    _extract_channel_id_from_html,
)

# ---------------------------------------------------------------------------
# Fixtures: sample YouTube Atom feed XML
# ---------------------------------------------------------------------------

YOUTUBE_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <link rel="self" href="https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"/>
  <link rel="alternate" href="https://www.youtube.com/channel/UCxyz123456789012345678"/>
  <id>yt:channel:UCxyz123456789012345678</id>
  <yt:channelId>UCxyz123456789012345678</yt:channelId>
  <title>Test Channel</title>
  <author>
    <name>Test Channel</name>
    <uri>https://www.youtube.com/channel/UCxyz123456789012345678</uri>
  </author>
  <published>2020-01-01T00:00:00+00:00</published>
  <entry>
    <id>yt:video:dQw4w9WgXcQ</id>
    <yt:videoId>dQw4w9WgXcQ</yt:videoId>
    <yt:channelId>UCxyz123456789012345678</yt:channelId>
    <title>First Video Title</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=dQw4w9WgXcQ"/>
    <author>
      <name>Test Channel</name>
      <uri>https://www.youtube.com/channel/UCxyz123456789012345678</uri>
    </author>
    <published>2024-01-15T10:00:00+00:00</published>
    <updated>2024-01-16T12:00:00+00:00</updated>
    <media:group>
      <media:title>First Video Title</media:title>
      <media:content url="https://www.youtube.com/v/dQw4w9WgXcQ?version=3" type="application/x-shockwave-flash" width="640" height="390"/>
      <media:thumbnail url="https://i1.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg" width="480" height="360"/>
      <media:description>This is the video description.</media:description>
      <media:community>
        <media:starRating count="123456" average="4.85" min="1" max="5"/>
        <media:statistics views="9876543"/>
      </media:community>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:abc123def456</id>
    <yt:videoId>abc123def456</yt:videoId>
    <yt:channelId>UCxyz123456789012345678</yt:channelId>
    <title>Second Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123def456"/>
    <author>
      <name>Test Channel</name>
    </author>
    <published>2024-02-01T08:30:00+00:00</published>
    <updated>2024-02-01T08:30:00+00:00</updated>
    <media:group>
      <media:title>Second Video</media:title>
      <media:thumbnail url="https://i1.ytimg.com/vi/abc123def456/hqdefault.jpg" width="480" height="360"/>
      <media:description>Second video description.</media:description>
      <media:community>
        <media:starRating count="100" average="3.50" min="1" max="5"/>
        <media:statistics views="5000"/>
      </media:community>
    </media:group>
  </entry>
</feed>
"""

YOUTUBE_FEED_MINIMAL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Minimal Channel</title>
  <yt:channelId>UCminimal1234567890123</yt:channelId>
  <entry>
    <yt:videoId>min123</yt:videoId>
    <title>Minimal Video</title>
    <published>2024-06-01T00:00:00+00:00</published>
  </entry>
</feed>
"""

YOUTUBE_FEED_EMPTY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Empty Channel</title>
  <yt:channelId>UCempty12345678901234567</yt:channelId>
</feed>
"""


# ---------------------------------------------------------------------------
# Tests: resolve_feed_url (pure, no I/O)
# ---------------------------------------------------------------------------


class TestResolveFeedUrl:
    def test_channel_url(self):
        url = "https://www.youtube.com/channel/UCxyz123456789012345678"
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert feed_url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"

    def test_channel_url_with_trailing_path(self):
        url = "https://www.youtube.com/channel/UCxyz123456789012345678/videos"
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert "channel_id=UCxyz123456789012345678" in feed_url

    def test_playlist_url(self):
        url = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert feed_url == "https://www.youtube.com/feeds/videos.xml?playlist_id=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

    def test_direct_feed_url_passthrough(self):
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert feed_url == url

    def test_handle_url_requires_scrape(self):
        url = "https://www.youtube.com/@testchannel"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "@testchannel" in err

    def test_custom_url_requires_scrape(self):
        url = "https://www.youtube.com/c/TestChannel"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "/c/TestChannel" in err

    def test_user_url_requires_scrape(self):
        url = "https://www.youtube.com/user/TestUser"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "/user/TestUser" in err

    def test_non_youtube_url(self):
        url = "https://example.com/some-page"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "Not a YouTube URL" in err

    def test_playlist_missing_list_param(self):
        url = "https://www.youtube.com/playlist"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "missing" in err.lower()

    def test_unrecognized_youtube_path(self):
        url = "https://www.youtube.com/results?search_query=test"
        feed_url, err = resolve_feed_url(url)
        assert feed_url == ""
        assert "Unrecognized" in err

    def test_mobile_youtube_url(self):
        url = "https://m.youtube.com/channel/UCxyz123456789012345678"
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert "channel_id=UCxyz123456789012345678" in feed_url

    def test_whitespace_trimmed(self):
        url = "  https://www.youtube.com/channel/UCxyz123456789012345678  "
        feed_url, err = resolve_feed_url(url)
        assert err == ""
        assert "channel_id=UCxyz123456789012345678" in feed_url


# ---------------------------------------------------------------------------
# Tests: _extract_channel_id_from_html
# ---------------------------------------------------------------------------


class TestExtractChannelId:
    def test_canonical_link(self):
        html = '<link rel="canonical" href="https://www.youtube.com/channel/UCxyz123456789012345678">'
        assert _extract_channel_id_from_html(html) == "UCxyz123456789012345678"

    def test_json_channel_id(self):
        html = '{"channelId":"UCxyz123456789012345678","other":"data"}'
        assert _extract_channel_id_from_html(html) == "UCxyz123456789012345678"

    def test_json_external_id(self):
        html = '{"externalId":"UCxyz123456789012345678"}'
        assert _extract_channel_id_from_html(html) == "UCxyz123456789012345678"

    def test_no_match(self):
        html = "<html><body>No channel here</body></html>"
        assert _extract_channel_id_from_html(html) is None


# ---------------------------------------------------------------------------
# Tests: parse_youtube_feed (pure, no I/O)
# ---------------------------------------------------------------------------


class TestParseYoutubeFeed:
    def test_basic_parse(self):
        result = parse_youtube_feed(YOUTUBE_FEED_XML, feed_url="https://yt.com/feed")
        assert result.ok is True
        assert result.channel_title == "Test Channel"
        assert result.channel_id == "UCxyz123456789012345678"
        assert len(result.videos) == 2

    def test_first_video_fields(self):
        result = parse_youtube_feed(YOUTUBE_FEED_XML, feed_url="https://yt.com/feed")
        v = result.videos[0]
        assert v.video_id == "dQw4w9WgXcQ"
        assert v.title == "First Video Title"
        assert v.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert v.author == "Test Channel"
        assert v.published_at == datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        assert v.updated_at == datetime(2024, 1, 16, 12, 0, tzinfo=timezone.utc)
        assert v.thumbnail_url == "https://i1.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        assert v.description == "This is the video description."
        assert v.views == 9876543
        assert v.star_rating == pytest.approx(4.85)

    def test_second_video(self):
        result = parse_youtube_feed(YOUTUBE_FEED_XML, feed_url="https://yt.com/feed")
        v = result.videos[1]
        assert v.video_id == "abc123def456"
        assert v.views == 5000
        assert v.star_rating == pytest.approx(3.50)

    def test_channel_url_extracted(self):
        result = parse_youtube_feed(YOUTUBE_FEED_XML, feed_url="https://yt.com/feed")
        assert result.channel_url == "https://www.youtube.com/channel/UCxyz123456789012345678"

    def test_max_items_limit(self):
        result = parse_youtube_feed(YOUTUBE_FEED_XML, feed_url="https://yt.com/feed", max_items=1)
        assert len(result.videos) == 1
        assert result.videos[0].video_id == "dQw4w9WgXcQ"

    def test_minimal_feed(self):
        result = parse_youtube_feed(YOUTUBE_FEED_MINIMAL_XML, feed_url="https://yt.com/feed")
        assert result.ok is True
        assert result.channel_title == "Minimal Channel"
        assert len(result.videos) == 1
        v = result.videos[0]
        assert v.video_id == "min123"
        assert v.title == "Minimal Video"
        # Fallback thumbnail
        assert v.thumbnail_url == "https://i.ytimg.com/vi/min123/hqdefault.jpg"
        assert v.description == ""
        assert v.views is None

    def test_empty_feed(self):
        result = parse_youtube_feed(YOUTUBE_FEED_EMPTY_XML, feed_url="https://yt.com/feed")
        assert result.ok is False
        assert "No video entries" in result.error

    def test_invalid_xml(self):
        result = parse_youtube_feed("<broken", feed_url="https://yt.com/feed")
        assert result.ok is False
        assert "XML parse error" in result.error

    def test_feed_url_in_result(self):
        result = parse_youtube_feed(
            YOUTUBE_FEED_XML, feed_url="https://yt.com/feeds/videos.xml?channel_id=UC123"
        )
        assert result.feed_url == "https://yt.com/feeds/videos.xml?channel_id=UC123"


# ---------------------------------------------------------------------------
# Tests: YouTubeVideo dataclass
# ---------------------------------------------------------------------------


class TestYouTubeVideoDataclass:
    def test_frozen(self):
        v = YouTubeVideo(video_id="abc", title="Test")
        with pytest.raises(AttributeError):
            v.video_id = "xyz"  # type: ignore[misc]

    def test_defaults(self):
        v = YouTubeVideo(video_id="abc", title="Test")
        assert v.published_at is None
        assert v.thumbnail_url == ""
        assert v.views is None
        assert v.star_rating is None
        assert v.url == ""

    def test_url_fallback_generated(self):
        """When no link element, parse_youtube_feed generates a URL from video_id."""
        result = parse_youtube_feed(YOUTUBE_FEED_MINIMAL_XML, feed_url="")
        v = result.videos[0]
        assert v.url == "https://www.youtube.com/watch?v=min123"


# ---------------------------------------------------------------------------
# Tests: fetch_youtube_feed (async, with mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchYoutubeFeed:
    @respx.mock
    async def test_success_with_channel_url(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)
        result = await fetch_youtube_feed(
            "https://www.youtube.com/channel/UCxyz123456789012345678"
        )
        assert result.ok is True
        assert len(result.videos) == 2
        assert result.channel_title == "Test Channel"

    @respx.mock
    async def test_success_with_direct_feed_url(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)
        result = await fetch_youtube_feed(feed_url)
        assert result.ok is True

    @respx.mock
    async def test_404(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        respx.get(feed_url).respond(404)
        result = await fetch_youtube_feed(
            "https://www.youtube.com/channel/UCxyz123456789012345678"
        )
        assert result.ok is False
        assert "404" in result.error

    @respx.mock
    async def test_429_rate_limit(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        respx.get(feed_url).respond(429)
        result = await fetch_youtube_feed(
            "https://www.youtube.com/channel/UCxyz123456789012345678"
        )
        assert result.ok is False
        assert "429" in result.error

    @respx.mock
    async def test_timeout(self):
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123456789012345678"
        respx.get(feed_url).mock(side_effect=httpx.ReadTimeout("timed out"))
        result = await fetch_youtube_feed(
            "https://www.youtube.com/channel/UCxyz123456789012345678"
        )
        assert result.ok is False
        assert "timed out" in result.error.lower()

    async def test_unresolvable_url(self):
        """Handle URL should fail gracefully without network call."""
        result = await fetch_youtube_feed("https://www.youtube.com/@testhandle")
        assert result.ok is False
        assert "@testhandle" in result.error

    async def test_non_youtube_url(self):
        result = await fetch_youtube_feed("https://example.com/page")
        assert result.ok is False
        assert "Not a YouTube URL" in result.error


# ---------------------------------------------------------------------------
# Tests: resolve_handle (async, with mocked HTTP)
# ---------------------------------------------------------------------------


class TestResolveHandle:
    @respx.mock
    async def test_handle_resolution(self):
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCxyz123456789012345678">'
        respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)
        feed_url, err = await resolve_handle("https://www.youtube.com/@testchannel")
        assert err == ""
        assert "channel_id=UCxyz123456789012345678" in feed_url

    @respx.mock
    async def test_handle_no_channel_id_found(self):
        respx.get("https://www.youtube.com/@unknown").respond(
            200, text="<html><body>No channel ID</body></html>"
        )
        feed_url, err = await resolve_handle("https://www.youtube.com/@unknown")
        assert feed_url == ""
        assert "Could not find channel_id" in err

    @respx.mock
    async def test_handle_http_error(self):
        respx.get("https://www.youtube.com/@gone").respond(404)
        feed_url, err = await resolve_handle("https://www.youtube.com/@gone")
        assert feed_url == ""
        assert "404" in err

    async def test_channel_url_resolves_directly(self):
        """Channel URLs with ID don't need scraping."""
        feed_url, err = await resolve_handle(
            "https://www.youtube.com/channel/UCxyz123456789012345678"
        )
        assert err == ""
        assert "channel_id=UCxyz123456789012345678" in feed_url

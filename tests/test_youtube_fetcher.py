"""Tests for YouTubeFetcher class."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import httpx
import pytest
import respx

from llm_wiki.fetchers.base import BaseFetcher, FetchResult, FetchedItem
from llm_wiki.fetchers.youtube import (
    YouTubeFetcher,
    _filter_videos_since,
    _video_to_fetched_item,
)
from llm_wiki.collectors.youtube import YouTubeVideo

# ---------------------------------------------------------------------------
# Fixtures: sample YouTube Atom feed XML
# ---------------------------------------------------------------------------

YOUTUBE_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <link rel="self" href="https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"/>
  <link rel="alternate" href="https://www.youtube.com/channel/UCxyz123"/>
  <yt:channelId>UCxyz123</yt:channelId>
  <title>Test Channel</title>
  <entry>
    <yt:videoId>vid001</yt:videoId>
    <title>Oldest Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid001"/>
    <author><name>Test Channel</name></author>
    <published>2024-01-01T00:00:00+00:00</published>
    <updated>2024-01-01T00:00:00+00:00</updated>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/vid001/hqdefault.jpg" width="480" height="360"/>
      <media:description>First video ever.</media:description>
      <media:community>
        <media:starRating count="100" average="4.50" min="1" max="5"/>
        <media:statistics views="10000"/>
      </media:community>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>vid002</yt:videoId>
    <title>Middle Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid002"/>
    <author><name>Test Channel</name></author>
    <published>2024-06-15T12:00:00+00:00</published>
    <updated>2024-06-15T12:00:00+00:00</updated>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/vid002/hqdefault.jpg" width="480" height="360"/>
      <media:description>Mid-year video.</media:description>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>vid003</yt:videoId>
    <title>Newest Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid003"/>
    <author><name>Test Channel</name></author>
    <published>2024-12-01T08:00:00+00:00</published>
    <updated>2024-12-01T08:00:00+00:00</updated>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/vid003/hqdefault.jpg" width="480" height="360"/>
      <media:description>Latest upload.</media:description>
    </media:group>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# Tests: YouTubeFetcher inherits BaseFetcher
# ---------------------------------------------------------------------------


class TestYouTubeFetcherInheritance:
    def test_is_base_fetcher(self):
        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        assert isinstance(fetcher, BaseFetcher)

    def test_source_type(self):
        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        assert fetcher.source_type == "youtube"

    def test_url_property(self):
        url = "https://www.youtube.com/channel/UCxyz123"
        fetcher = YouTubeFetcher(url)
        assert fetcher.url == url


# ---------------------------------------------------------------------------
# Tests: poll() with channel URL (no handle resolution needed)
# ---------------------------------------------------------------------------


class TestYouTubeFetcherPoll:
    @respx.mock
    async def test_poll_all_videos(self):
        """poll() without since returns all videos."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll()

        assert result.ok is True
        assert len(result.items) == 3
        assert result.source_title == "Test Channel"
        assert result.total_available == 3

    @respx.mock
    async def test_poll_filters_by_since(self):
        """poll(since=...) only returns videos after the cutoff."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        # Only videos after June 2024
        since = datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc)
        result = await fetcher.poll(since=since)

        assert result.ok is True
        assert len(result.items) == 2  # vid002 (Jun 15) and vid003 (Dec 1)
        assert result.total_available == 3
        titles = [item.title for item in result.items]
        assert "Middle Video" in titles
        assert "Newest Video" in titles
        assert "Oldest Video" not in titles

    @respx.mock
    async def test_poll_since_after_all_videos(self):
        """poll(since=future) returns no items."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        since = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        result = await fetcher.poll(since=since)

        assert result.ok is True
        assert len(result.items) == 0
        assert result.total_available == 3

    @respx.mock
    async def test_poll_since_naive_datetime(self):
        """poll() handles naive datetime by assuming UTC."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        # Naive datetime (no tzinfo) — should be treated as UTC
        since = datetime(2024, 11, 1, 0, 0)
        result = await fetcher.poll(since=since)

        assert result.ok is True
        assert len(result.items) == 1  # Only vid003 (Dec 1)
        assert result.items[0].title == "Newest Video"

    @respx.mock
    async def test_poll_max_items(self):
        """poll(max_items=N) limits results."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll(max_items=1)

        assert result.ok is True
        assert result.total_available == 1  # max_items limits feed parse too


# ---------------------------------------------------------------------------
# Tests: poll() error cases
# ---------------------------------------------------------------------------


class TestYouTubeFetcherErrors:
    @respx.mock
    async def test_poll_network_error(self):
        """poll() returns FetchResult(ok=False) on HTTP error."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(500)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll()

        assert result.ok is False
        assert result.error != ""
        assert result.source_url == "https://www.youtube.com/channel/UCxyz123"

    @respx.mock
    async def test_poll_timeout(self):
        """poll() handles timeout gracefully."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).mock(side_effect=httpx.ReadTimeout("timed out"))

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll()

        assert result.ok is False
        assert "timed out" in result.error.lower()

    async def test_poll_non_youtube_url(self):
        """poll() fails for non-YouTube URLs."""
        fetcher = YouTubeFetcher("https://example.com/page")
        result = await fetcher.poll()

        assert result.ok is False
        assert "Could not resolve" in result.error or "Not a YouTube URL" in result.error


# ---------------------------------------------------------------------------
# Tests: poll() with @handle URL (requires page scrape)
# ---------------------------------------------------------------------------


class TestYouTubeFetcherHandleResolution:
    @respx.mock
    async def test_handle_url_resolved_and_polled(self):
        """@handle URLs are resolved via page scrape, then feed is polled."""
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCxyz123">'
        respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)

        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/@testchannel")
        result = await fetcher.poll()

        assert result.ok is True
        assert len(result.items) == 3
        assert result.source_title == "Test Channel"

    @respx.mock
    async def test_handle_resolution_cached(self):
        """Feed URL resolution is cached across poll() calls."""
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCxyz123">'
        page_route = respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)

        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/@testchannel")

        # First poll — triggers handle resolution
        result1 = await fetcher.poll()
        assert result1.ok is True
        assert page_route.call_count == 1

        # Second poll — uses cached feed URL, no second page fetch
        result2 = await fetcher.poll()
        assert result2.ok is True
        assert page_route.call_count == 1  # Still 1, not 2


# ---------------------------------------------------------------------------
# Tests: FetchedItem structure
# ---------------------------------------------------------------------------


class TestFetchedItemConversion:
    @respx.mock
    async def test_fetched_item_fields(self):
        """FetchedItem has correct fields from YouTube video."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll()

        item = result.items[0]
        assert isinstance(item, FetchedItem)
        assert item.url == "https://www.youtube.com/watch?v=vid001"
        assert item.title == "Oldest Video"
        assert item.author == "Test Channel"
        assert item.source_type == "youtube"
        assert item.summary == "First video ever."
        assert item.published_at == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    @respx.mock
    async def test_fetched_item_extra_metadata(self):
        """FetchedItem.extra contains YouTube-specific fields."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCxyz123"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        fetcher = YouTubeFetcher("https://www.youtube.com/channel/UCxyz123")
        result = await fetcher.poll()

        item = result.items[0]
        assert item.extra["video_id"] == "vid001"
        assert item.extra["thumbnail_url"] == "https://i.ytimg.com/vi/vid001/hqdefault.jpg"
        assert item.extra["views"] == 10000
        assert item.extra["star_rating"] == pytest.approx(4.50)


# ---------------------------------------------------------------------------
# Tests: _filter_videos_since (pure function)
# ---------------------------------------------------------------------------


class TestFilterVideosSince:
    def test_filters_old_videos(self):
        videos = [
            YouTubeVideo(video_id="a", title="Old", published_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            YouTubeVideo(video_id="b", title="New", published_at=datetime(2024, 7, 1, tzinfo=timezone.utc)),
        ]
        since = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = _filter_videos_since(videos, since)
        assert len(result) == 1
        assert result[0].video_id == "b"

    def test_excludes_exact_boundary(self):
        """Videos with published_at == since are excluded (strictly after)."""
        videos = [
            YouTubeVideo(video_id="a", title="Exact", published_at=datetime(2024, 6, 1, tzinfo=timezone.utc)),
        ]
        since = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = _filter_videos_since(videos, since)
        assert len(result) == 0

    def test_includes_videos_without_date(self):
        """Videos with no published_at are included (conservative)."""
        videos = [
            YouTubeVideo(video_id="a", title="No Date", published_at=None),
            YouTubeVideo(video_id="b", title="Old", published_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
        ]
        since = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = _filter_videos_since(videos, since)
        assert len(result) == 1
        assert result[0].video_id == "a"

    def test_empty_list(self):
        result = _filter_videos_since([], datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _video_to_fetched_item (pure function)
# ---------------------------------------------------------------------------


class TestVideoToFetchedItem:
    def test_basic_conversion(self):
        video = YouTubeVideo(
            video_id="abc123",
            title="Test Video",
            published_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
            author="Author Name",
            url="https://www.youtube.com/watch?v=abc123",
            thumbnail_url="https://i.ytimg.com/vi/abc123/hqdefault.jpg",
            description="Video description.",
            views=5000,
            star_rating=4.2,
            updated_at=datetime(2024, 6, 16, tzinfo=timezone.utc),
        )
        item = _video_to_fetched_item(video, "Channel Name")

        assert item.url == "https://www.youtube.com/watch?v=abc123"
        assert item.title == "Test Video"
        assert item.summary == "Video description."
        assert item.author == "Author Name"
        assert item.source_type == "youtube"
        assert item.extra["video_id"] == "abc123"
        assert item.extra["views"] == 5000
        assert item.extra["updated_at"] == "2024-06-16T00:00:00+00:00"

    def test_fallback_author_to_channel(self):
        """If video has no author, channel_title is used."""
        video = YouTubeVideo(video_id="x", title="T", author="")
        item = _video_to_fetched_item(video, "Channel Fallback")
        assert item.author == "Channel Fallback"

    def test_none_updated_at(self):
        video = YouTubeVideo(video_id="x", title="T", updated_at=None)
        item = _video_to_fetched_item(video, "Ch")
        assert item.extra["updated_at"] is None

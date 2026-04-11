"""Tests for YouTube subscription registration and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.subscriptions import SubscriptionStore, SubscriptionStatus
from llm_wiki.youtube_subscription import (
    validate_youtube_url,
    validate_and_probe_youtube_url,
    add_youtube_subscription,
    is_youtube_url,
    YouTubeValidationResult,
    YouTubeSubscriptionResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

YOUTUBE_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <link rel="self" href="https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"/>
  <link rel="alternate" href="https://www.youtube.com/channel/UCtest123456789012345678"/>
  <yt:channelId>UCtest123456789012345678</yt:channelId>
  <title>Test Channel</title>
  <entry>
    <yt:videoId>vid001</yt:videoId>
    <title>Video One</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid001"/>
    <author><name>Test Channel</name></author>
    <published>2024-06-15T12:00:00+00:00</published>
    <updated>2024-06-15T12:00:00+00:00</updated>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/vid001/hqdefault.jpg" width="480" height="360"/>
      <media:description>First video.</media:description>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>vid002</yt:videoId>
    <title>Video Two</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid002"/>
    <author><name>Test Channel</name></author>
    <published>2024-07-01T08:00:00+00:00</published>
    <updated>2024-07-01T08:00:00+00:00</updated>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/vid002/hqdefault.jpg" width="480" height="360"/>
      <media:description>Second video.</media:description>
    </media:group>
  </entry>
</feed>
"""

PLAYLIST_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>My Playlist</title>
  <entry>
    <yt:videoId>plvid001</yt:videoId>
    <title>Playlist Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=plvid001"/>
    <author><name>Some Author</name></author>
    <published>2024-03-01T00:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/plvid001/hqdefault.jpg" width="480" height="360"/>
      <media:description>A playlist video.</media:description>
    </media:group>
  </entry>
</feed>
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    v = tmp_path / "vault"
    v.mkdir(parents=True)
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    return SubscriptionStore(config)


# ---------------------------------------------------------------------------
# Tests: validate_youtube_url (pure, no I/O)
# ---------------------------------------------------------------------------


class TestValidateYouTubeUrl:
    def test_channel_url(self):
        result = validate_youtube_url("https://www.youtube.com/channel/UCtest123456789012345678")
        assert result.valid is True
        assert result.url_type == "channel"
        assert "channel_id=UCtest123456789012345678" in result.feed_url
        assert result.channel_id == "UCtest123456789012345678"

    def test_channel_url_with_videos_path(self):
        result = validate_youtube_url("https://www.youtube.com/channel/UCtest123456789012345678/videos")
        assert result.valid is True
        assert result.url_type == "channel"

    def test_handle_url(self):
        result = validate_youtube_url("https://www.youtube.com/@testchannel")
        assert result.valid is True
        assert result.url_type == "handle"
        # feed_url is empty (needs async resolution)
        assert result.feed_url == ""

    def test_custom_url(self):
        result = validate_youtube_url("https://www.youtube.com/c/TestChannel")
        assert result.valid is True
        assert result.url_type == "custom"
        assert result.feed_url == ""

    def test_user_url(self):
        result = validate_youtube_url("https://www.youtube.com/user/TestUser")
        assert result.valid is True
        assert result.url_type == "user"
        assert result.feed_url == ""

    def test_playlist_url(self):
        result = validate_youtube_url(
            "https://www.youtube.com/playlist?list=PLtest1234567890"
        )
        assert result.valid is True
        assert result.url_type == "playlist"
        assert "playlist_id=PLtest1234567890" in result.feed_url

    def test_direct_feed_url(self):
        result = validate_youtube_url(
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        )
        assert result.valid is True
        assert result.url_type == "feed"

    def test_mobile_url(self):
        result = validate_youtube_url("https://m.youtube.com/channel/UCtest123456789012345678")
        assert result.valid is True

    def test_empty_url(self):
        result = validate_youtube_url("")
        assert result.valid is False
        assert "empty" in result.error.lower()

    def test_non_youtube_url(self):
        result = validate_youtube_url("https://example.com/feed.xml")
        assert result.valid is False
        assert "Not a YouTube URL" in result.error

    def test_watch_url_rejected(self):
        result = validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.valid is False
        assert "/watch" in result.error

    def test_shorts_url_rejected(self):
        result = validate_youtube_url("https://www.youtube.com/shorts/abc123")
        assert result.valid is False
        assert "/shorts" in result.error

    def test_search_results_rejected(self):
        result = validate_youtube_url("https://www.youtube.com/results?search_query=test")
        assert result.valid is False
        assert "/results" in result.error

    def test_embed_url_rejected(self):
        result = validate_youtube_url("https://www.youtube.com/embed/abc123")
        assert result.valid is False

    def test_whitespace_trimmed(self):
        result = validate_youtube_url("  https://www.youtube.com/channel/UCtest123456789012345678  ")
        assert result.valid is True
        assert result.url.strip() == result.url

    def test_unrecognized_path(self):
        result = validate_youtube_url("https://www.youtube.com/premium")
        assert result.valid is False
        assert "Unrecognized" in result.error


# ---------------------------------------------------------------------------
# Tests: validate_and_probe_youtube_url (async, mocked HTTP)
# ---------------------------------------------------------------------------


class TestValidateAndProbe:
    @respx.mock
    async def test_channel_url_probed(self):
        """Channel URL is resolved and feed is probed."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/channel/UCtest123456789012345678"
        )
        assert result.valid is True
        assert result.channel_title == "Test Channel"
        assert result.channel_id == "UCtest123456789012345678"
        assert result.video_count == 2
        assert result.feed_url == feed_url

    @respx.mock
    async def test_handle_url_resolved_and_probed(self):
        """@handle URL is resolved via page scrape, then feed probed."""
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCtest123456789012345678">'
        respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)

        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/@testchannel"
        )
        assert result.valid is True
        assert result.channel_title == "Test Channel"
        assert result.video_count == 2

    @respx.mock
    async def test_handle_resolution_fails(self):
        """@handle that can't be resolved returns error."""
        respx.get("https://www.youtube.com/@unknown").respond(
            200, text="<html>No channel ID here</html>"
        )

        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/@unknown"
        )
        assert result.valid is False
        assert "Could not resolve" in result.error

    @respx.mock
    async def test_feed_probe_fails(self):
        """Valid URL but feed returns error."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(404)

        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/channel/UCtest123456789012345678"
        )
        assert result.valid is False
        assert "Feed probe failed" in result.error

    @respx.mock
    async def test_playlist_url_probed(self):
        """Playlist URL is resolved and probed."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?playlist_id=PLtest1234567890"
        respx.get(feed_url).respond(200, text=PLAYLIST_FEED_XML)

        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/playlist?list=PLtest1234567890"
        )
        assert result.valid is True
        assert result.channel_title == "My Playlist"
        assert result.video_count == 1

    async def test_non_youtube_url_fails_fast(self):
        """Non-YouTube URL fails without network calls."""
        result = await validate_and_probe_youtube_url("https://example.com/page")
        assert result.valid is False
        assert "Not a YouTube URL" in result.error

    async def test_watch_url_fails_fast(self):
        """Watch URL fails without network calls."""
        result = await validate_and_probe_youtube_url(
            "https://www.youtube.com/watch?v=abc"
        )
        assert result.valid is False


# ---------------------------------------------------------------------------
# Tests: add_youtube_subscription (full flow)
# ---------------------------------------------------------------------------


class TestAddYouTubeSubscription:
    @respx.mock
    async def test_full_flow_channel_url(self, store: SubscriptionStore, config: WikiConfig):
        """Full registration: validate → probe → subscribe → dispatch."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
            lenses=["tech", "ai"],
        )

        assert result.ok is True
        assert result.channel_title == "Test Channel"
        assert result.channel_id == "UCtest123456789012345678"
        assert result.video_count == 2
        assert result.subscription is not None
        assert result.subscription.title == "Test Channel"
        assert result.subscription.lenses == ["tech", "ai"]

        # Verify subscription persisted
        assert store.count == 1
        stored = store.get(feed_url)
        assert stored is not None
        assert stored.title == "Test Channel"

    @respx.mock
    async def test_custom_title_overrides_detected(self, store: SubscriptionStore, config: WikiConfig):
        """User-provided title overrides auto-detected title."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
            title="My Custom Title",
        )

        assert result.ok is True
        assert result.channel_title == "My Custom Title"
        assert result.subscription.title == "My Custom Title"

    @respx.mock
    async def test_duplicate_channel_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Cannot subscribe to the same channel twice."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        # First subscription succeeds
        result1 = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
        )
        assert result1.ok is True

        # Second subscription (same channel) fails
        result2 = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
        )
        assert result2.ok is False
        assert "Already subscribed" in result2.error

    @respx.mock
    async def test_duplicate_via_different_url_format(self, store: SubscriptionStore, config: WikiConfig):
        """Same channel via different URL format (handle vs channel) is detected."""
        # First: subscribe via channel URL
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result1 = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
        )
        assert result1.ok is True

        # Second: try via @handle (resolves to same channel)
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCtest123456789012345678">'
        respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)

        result2 = await add_youtube_subscription(
            "https://www.youtube.com/@testchannel",
            store,
            config,
        )
        assert result2.ok is False
        assert "Already subscribed" in result2.error

    @respx.mock
    async def test_handle_url_registration(self, store: SubscriptionStore, config: WikiConfig):
        """@handle URL is resolved and registered."""
        page_html = '<link rel="canonical" href="https://www.youtube.com/channel/UCtest123456789012345678">'
        respx.get("https://www.youtube.com/@testchannel").respond(200, text=page_html)

        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/@testchannel",
            store,
            config,
        )

        assert result.ok is True
        assert result.feed_url == feed_url
        # Stored by feed URL, not the handle URL
        assert store.get(feed_url) is not None

    async def test_invalid_url_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Non-YouTube URLs are rejected without network calls."""
        result = await add_youtube_subscription(
            "https://example.com/page",
            store,
            config,
        )
        assert result.ok is False
        assert store.count == 0

    async def test_watch_url_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Watch URLs are rejected."""
        result = await add_youtube_subscription(
            "https://www.youtube.com/watch?v=abc",
            store,
            config,
        )
        assert result.ok is False

    @respx.mock
    async def test_playlist_registration(self, store: SubscriptionStore, config: WikiConfig):
        """Playlist URL subscription works."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?playlist_id=PLtest1234567890"
        respx.get(feed_url).respond(200, text=PLAYLIST_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/playlist?list=PLtest1234567890",
            store,
            config,
        )

        assert result.ok is True
        assert result.channel_title == "My Playlist"
        assert store.count == 1

    async def test_skip_probe_with_channel_url(self, store: SubscriptionStore, config: WikiConfig):
        """skip_probe mode works for channel URLs (no network)."""
        result = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
            title="Offline Channel",
            skip_probe=True,
        )

        assert result.ok is True
        assert result.subscription.title == "Offline Channel"
        assert store.count == 1

    async def test_skip_probe_with_handle_url_fails(self, store: SubscriptionStore, config: WikiConfig):
        """skip_probe mode fails for @handle URLs (can't resolve without network)."""
        result = await add_youtube_subscription(
            "https://www.youtube.com/@testchannel",
            store,
            config,
            skip_probe=True,
        )

        assert result.ok is False
        assert "Cannot resolve feed URL without network" in result.error
        assert store.count == 0


# ---------------------------------------------------------------------------
# Tests: Schedule dispatch integration
# ---------------------------------------------------------------------------


class TestScheduleDispatch:
    @respx.mock
    async def test_schedule_created_on_subscribe(self, store: SubscriptionStore, config: WikiConfig):
        """Subscribing creates a schedule entry in SQLite."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
        )
        assert result.ok is True

        # Verify schedule exists in DB
        from llm_wiki.dispatch import get_schedule
        schedule = get_schedule(feed_url, config)
        assert schedule is not None
        assert schedule.source_type == "youtube"
        assert schedule.enabled is True
        assert schedule.interval_minutes == 60  # YouTube default

    @respx.mock
    async def test_custom_interval(self, store: SubscriptionStore, config: WikiConfig):
        """Custom interval_minutes is passed to scheduler."""
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest123456789012345678"
        respx.get(feed_url).respond(200, text=YOUTUBE_FEED_XML)

        result = await add_youtube_subscription(
            "https://www.youtube.com/channel/UCtest123456789012345678",
            store,
            config,
            interval_minutes=120,
        )
        assert result.ok is True

        from llm_wiki.dispatch import get_schedule
        schedule = get_schedule(feed_url, config)
        assert schedule.interval_minutes == 120


# ---------------------------------------------------------------------------
# Tests: Fetcher registry integration
# ---------------------------------------------------------------------------


class TestFetcherRegistry:
    def test_youtube_fetcher_registered(self):
        """YouTube fetcher is auto-registered in the registry."""
        from llm_wiki.fetchers.registry import is_registered, get_fetcher
        assert is_registered("youtube") is True

        fetcher = get_fetcher("youtube", "https://www.youtube.com/channel/UCtest123")
        assert fetcher is not None
        assert fetcher.source_type == "youtube"

    def test_get_fetcher_for_url(self):
        """get_fetcher_for_url auto-detects YouTube URLs."""
        from llm_wiki.fetchers.registry import get_fetcher_for_url

        fetcher = get_fetcher_for_url("https://www.youtube.com/channel/UCtest123")
        assert fetcher is not None
        assert fetcher.source_type == "youtube"

    def test_unregistered_type_returns_none(self):
        """Unknown source types return None."""
        from llm_wiki.fetchers.registry import get_fetcher
        assert get_fetcher("unknown_type", "https://example.com") is None

    def test_registered_types(self):
        """registered_types includes youtube."""
        from llm_wiki.fetchers.registry import registered_types
        assert "youtube" in registered_types()


# ---------------------------------------------------------------------------
# Tests: is_youtube_url helper
# ---------------------------------------------------------------------------


class TestIsYouTubeUrl:
    def test_youtube_urls(self):
        assert is_youtube_url("https://www.youtube.com/channel/UC123") is True
        assert is_youtube_url("https://youtube.com/@test") is True
        assert is_youtube_url("https://m.youtube.com/watch?v=abc") is True
        assert is_youtube_url("https://youtu.be/abc") is True

    def test_non_youtube_urls(self):
        assert is_youtube_url("https://example.com") is False
        assert is_youtube_url("https://vimeo.com/123") is False
        assert is_youtube_url("") is False
        assert is_youtube_url("not-a-url") is False

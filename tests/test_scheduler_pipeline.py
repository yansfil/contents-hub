"""Tests for the scheduler auto-execution pipeline (AC 1030203).

Verifies that due subscriptions trigger fetchers through the registry,
save source files, and update both SQLite schedules and subscription YAML.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.dispatch import (
    dispatch_subscription,
    get_schedule,
    record_run_result,
)
from llm_wiki.fetchers.base import BaseFetcher, FetchedItem, FetchResult
from llm_wiki.fetchers.registry import register_fetcher, _registry
from llm_wiki.scheduler_engine import (
    CollectionOutcome,
    DueSubscription,
    SchedulerEngine,
)
from llm_wiki.source_writer import save_source_file
from llm_wiki.subscriptions import SubscriptionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    return SubscriptionStore(config)


@pytest.fixture
def engine(config: WikiConfig) -> SchedulerEngine:
    return SchedulerEngine(config)


def _add_and_dispatch(
    store: SubscriptionStore,
    config: WikiConfig,
    url: str,
    title: str = "",
    lenses: list[str] | None = None,
    **dispatch_kwargs,
):
    sub = store.add(url, title=title or url, lenses=lenses or [])
    dispatch_subscription(sub, config, **dispatch_kwargs)
    return sub


# ---------------------------------------------------------------------------
# Source writer tests
# ---------------------------------------------------------------------------


class TestSourceWriter:
    def test_saves_rss_item(self, config: WikiConfig):
        item = FetchedItem(
            url="https://blog.com/post-1",
            title="Test Post",
            summary="A test post summary",
            author="Author Name",
            published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            source_type="rss",
            extra={"feed_title": "Example Blog"},
        )

        path = save_source_file(item, config, lenses=["tech", "ai"])
        assert path.exists()

        content = path.read_text()
        assert "type: rss" in content
        assert "title: Test Post" in content
        assert "author: Author Name" in content
        assert "status: pending" in content
        assert "tech" in content
        assert "ai" in content
        assert path.parent == config.sources_path

    def test_saves_youtube_item(self, config: WikiConfig):
        item = FetchedItem(
            url="https://youtube.com/watch?v=test123",
            title="Test Video",
            summary="A video description",
            author="Channel Name",
            source_type="youtube",
            extra={"video_id": "test123", "thumbnail_url": "https://img.youtube.com/test.jpg"},
        )

        path = save_source_file(item, config)
        assert path.exists()

        content = path.read_text()
        assert "type: youtube" in content
        assert "video_id: test123" in content
        assert "thumbnail" in content

    def test_saves_twitter_item(self, config: WikiConfig):
        item = FetchedItem(
            url="https://x.com/user/status/12345",
            title="A tweet",
            summary="Full tweet text here",
            author="@user",
            source_type="twitter",
            extra={"tweet_id": "12345", "like_count": 42, "retweet_count": 5},
        )

        path = save_source_file(item, config)
        assert path.exists()

        content = path.read_text()
        assert "type: twitter" in content
        assert "tweet_id: 12345" in content

    def test_immutable_no_overwrite(self, config: WikiConfig):
        item = FetchedItem(
            url="https://blog.com/post-1",
            title="Same Post",
            source_type="rss",
        )

        p1 = save_source_file(item, config)
        p2 = save_source_file(item, config)

        assert p1 != p2
        assert p1.exists()
        assert p2.exists()


# ---------------------------------------------------------------------------
# Pipeline: fetcher registry → source writer → state update
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    """Test the full pipeline: tick_and_run with fetcher registry."""

    @pytest.mark.asyncio
    async def test_rss_auto_collected_via_registry(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """RSS subscriptions are collected via the registered RSSFetcher."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog", lenses=["tech"])

        # Mock the RSSFetcher.poll to avoid network calls
        mock_result = FetchResult(
            ok=True,
            items=[
                FetchedItem(
                    url="https://blog.test/post-1",
                    title="New Post",
                    summary="Summary",
                    author="Author",
                    source_type="rss",
                    extra={"feed_title": "Blog"},
                ),
            ],
            source_title="Blog",
            source_url="https://blog.test/feed.xml",
            total_available=1,
        )

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()
            mock_fetcher.poll.return_value = mock_result
            mock_get.return_value = mock_fetcher

            result = await engine.tick_and_run()

        assert result.tick.due_count == 1
        assert len(result.outcomes) == 1
        assert result.outcomes[0].ok is True
        assert result.outcomes[0].new_items == 1
        assert result.outcomes[0].source_type == "rss"

    @pytest.mark.asyncio
    async def test_youtube_auto_collected_via_registry(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """YouTube subscriptions are collected via the registered YouTubeFetcher."""
        _add_and_dispatch(
            store, config,
            "https://youtube.com/feeds/videos.xml?channel_id=UC1",
            "YouTube Channel",
            lenses=["tech"],
        )

        mock_result = FetchResult(
            ok=True,
            items=[
                FetchedItem(
                    url="https://youtube.com/watch?v=abc",
                    title="New Video",
                    summary="Video desc",
                    author="Channel",
                    source_type="youtube",
                    extra={"video_id": "abc"},
                ),
            ],
            source_title="YouTube Channel",
            source_url="https://youtube.com/feeds/videos.xml?channel_id=UC1",
            total_available=1,
        )

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()
            mock_fetcher.poll.return_value = mock_result
            mock_get.return_value = mock_fetcher

            result = await engine.tick_and_run()

        assert result.outcomes[0].ok is True
        assert result.outcomes[0].new_items == 1
        assert result.outcomes[0].source_type == "youtube"

    @pytest.mark.asyncio
    async def test_twitter_auto_collected_via_registry(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Twitter subscriptions are collected via the registered TwitterFetcher."""
        _add_and_dispatch(
            store, config,
            "https://x.com/testuser",
            "Twitter User",
        )

        mock_result = FetchResult(
            ok=True,
            items=[
                FetchedItem(
                    url="https://x.com/testuser/status/999",
                    title="A tweet",
                    summary="Full text",
                    author="@testuser",
                    source_type="twitter",
                    extra={"tweet_id": "999"},
                ),
            ],
            source_title="@testuser",
            source_url="https://x.com/testuser",
            total_available=1,
        )

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()
            mock_fetcher.poll.return_value = mock_result
            mock_get.return_value = mock_fetcher

            result = await engine.tick_and_run()

        assert result.outcomes[0].ok is True
        assert result.outcomes[0].new_items == 1
        assert result.outcomes[0].source_type == "twitter"


# ---------------------------------------------------------------------------
# State updates: lastFetchedAt + schedule advancement
# ---------------------------------------------------------------------------


class TestStateUpdates:
    """Verify that both SQLite schedule and YAML subscription are updated."""

    @pytest.mark.asyncio
    async def test_schedule_advanced_after_success(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """SQLite schedule.next_run_at is advanced after successful collection."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        original = get_schedule("https://blog.test/feed.xml", config)

        async def mock_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(url=ds.url, source_type="rss", ok=True, new_items=3)

        await engine.tick_and_run(collector=mock_collector)

        updated = get_schedule("https://blog.test/feed.xml", config)
        assert updated.last_run_ok is True
        assert updated.consecutive_errors == 0
        assert updated.next_run_at > original.next_run_at

    @pytest.mark.asyncio
    async def test_subscription_last_fetched_at_updated(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Subscription YAML last_fetched_at is updated after collection."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        # Verify initially None
        sub_before = store.get("https://blog.test/feed.xml")
        assert sub_before.last_fetched_at is None

        async def mock_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(url=ds.url, source_type="rss", ok=True, new_items=5)

        await engine.tick_and_run(collector=mock_collector)

        # Reload store to pick up the save
        store.reload()
        sub_after = store.get("https://blog.test/feed.xml")
        assert sub_after.last_fetched_at is not None
        assert sub_after.last_fetched_count == 5

    @pytest.mark.asyncio
    async def test_subscription_error_status_on_failure(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Subscription status set to 'error' on failed collection."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        async def failing_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(
                url=ds.url, source_type="rss", ok=False, error="HTTP 500",
            )

        await engine.tick_and_run(collector=failing_collector)

        store.reload()
        sub = store.get("https://blog.test/feed.xml")
        assert sub.status.value == "error"
        assert sub.error_message == "HTTP 500"

    @pytest.mark.asyncio
    async def test_second_tick_idle_after_successful_run(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """After collection, next immediate tick is idle (next_run is in the future)."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        async def mock_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(url=ds.url, source_type="rss", ok=True, new_items=1)

        await engine.tick_and_run(collector=mock_collector)

        # Immediate second tick should be idle
        tick = engine.tick()
        assert tick.is_idle is True

    @pytest.mark.asyncio
    async def test_backoff_on_consecutive_errors(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Schedule backoff increases on consecutive errors."""
        _add_and_dispatch(store, config, "https://bad.test/feed.xml", "Bad Feed")

        async def failing_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(
                url=ds.url, source_type="rss", ok=False, error="timeout",
            )

        # First failure
        await engine.tick_and_run(collector=failing_collector)
        s1 = get_schedule("https://bad.test/feed.xml", config)
        assert s1.consecutive_errors == 1

        # Second failure (need to be in the future past first backoff)
        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        await engine.tick_and_run(collector=failing_collector, now=far_future)
        s2 = get_schedule("https://bad.test/feed.xml", config)
        assert s2.consecutive_errors == 2


# ---------------------------------------------------------------------------
# Source file creation during pipeline
# ---------------------------------------------------------------------------


class TestSourceFileCreation:
    """Verify that source files are created in vault/sources/ during pipeline."""

    @pytest.mark.asyncio
    async def test_source_files_created_on_collection(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Source files are written to vault/sources/ when items are fetched."""
        _add_and_dispatch(
            store, config,
            "https://blog.test/feed.xml", "Test Blog",
            lenses=["tech"],
        )

        mock_items = [
            FetchedItem(
                url="https://blog.test/post-1",
                title="Post One",
                summary="Summary one",
                source_type="rss",
                extra={"feed_title": "Test Blog"},
            ),
            FetchedItem(
                url="https://blog.test/post-2",
                title="Post Two",
                summary="Summary two",
                source_type="rss",
                extra={"feed_title": "Test Blog"},
            ),
        ]

        mock_result = FetchResult(
            ok=True,
            items=mock_items,
            source_title="Test Blog",
            source_url="https://blog.test/feed.xml",
            total_available=2,
        )

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()
            mock_fetcher.poll.return_value = mock_result
            mock_get.return_value = mock_fetcher

            result = await engine.tick_and_run()

        assert result.outcomes[0].new_items == 2

        # Check source files exist
        sources = list(config.sources_path.glob("*.md"))
        assert len(sources) == 2

        # Check content has correct frontmatter
        content = sources[0].read_text()
        assert "type: rss" in content
        assert "status: pending" in content

    @pytest.mark.asyncio
    async def test_no_source_files_on_empty_fetch(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """No source files created when fetch returns 0 items."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        mock_result = FetchResult(
            ok=True,
            items=[],
            source_title="Blog",
            source_url="https://blog.test/feed.xml",
        )

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()
            mock_fetcher.poll.return_value = mock_result
            mock_get.return_value = mock_fetcher

            result = await engine.tick_and_run()

        assert result.outcomes[0].new_items == 0
        sources = list(config.sources_path.glob("*.md")) if config.sources_path.exists() else []
        assert len(sources) == 0


# ---------------------------------------------------------------------------
# Multi-type concurrent collection
# ---------------------------------------------------------------------------


class TestMultiTypeConcurrent:
    """Test concurrent collection across multiple source types."""

    @pytest.mark.asyncio
    async def test_mixed_types_collected_concurrently(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """RSS, YouTube, and Twitter subscriptions are all collected in one tick."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        _add_and_dispatch(
            store, config,
            "https://youtube.com/feeds/videos.xml?channel_id=UC1",
            "YT Channel",
        )
        _add_and_dispatch(store, config, "https://x.com/testuser", "Twitter")

        collected_types: list[str] = []

        async def tracking_collector(ds: DueSubscription) -> CollectionOutcome:
            collected_types.append(ds.source_type)
            return CollectionOutcome(
                url=ds.url, source_type=ds.source_type, ok=True, new_items=1,
            )

        result = await engine.tick_and_run(collector=tracking_collector)

        assert result.tick.due_count == 3
        assert len(result.outcomes) == 3
        assert sorted(collected_types) == ["rss", "twitter", "youtube"]
        assert result.total_new_items == 3

        # All subscriptions should have updated last_fetched_at
        store.reload()
        for url in [
            "https://blog.test/feed.xml",
            "https://youtube.com/feeds/videos.xml?channel_id=UC1",
            "https://x.com/testuser",
        ]:
            sub = store.get(url)
            assert sub.last_fetched_at is not None, f"last_fetched_at not set for {url}"


# ---------------------------------------------------------------------------
# Fetcher passes since=last_fetched_at
# ---------------------------------------------------------------------------


class TestIncrementalFetch:
    """Verify the fetcher receives since=last_fetched_at for incremental polling."""

    @pytest.mark.asyncio
    async def test_fetcher_receives_since_from_subscription(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """The fetcher's poll() is called with since=last_fetched_at."""
        sub = _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        # Set a known last_fetched_at
        known_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        store.record_fetch("https://blog.test/feed.xml", 3)
        store.reload()
        # Directly update last_fetched_at in SQLite for test precision
        from llm_wiki.db import get_db
        with get_db(config) as conn:
            conn.execute(
                "UPDATE subscriptions SET last_fetched_at = ? WHERE url = ?",
                (known_time.isoformat(), "https://blog.test/feed.xml"),
            )

        mock_result = FetchResult(ok=True, items=[], source_title="Blog")
        captured_since = []

        with patch("llm_wiki.scheduler_engine.get_fetcher") as mock_get:
            mock_fetcher = AsyncMock()

            async def capture_poll(*, since=None, max_items=50):
                captured_since.append(since)
                return mock_result

            mock_fetcher.poll = capture_poll
            mock_get.return_value = mock_fetcher

            await engine.tick_and_run()

        assert len(captured_since) == 1
        assert captured_since[0] == known_time

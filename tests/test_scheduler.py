"""Tests for the RSS auto-collection scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from llm_wiki.collectors.rss import FeedItem, FeedResult
from llm_wiki.config import WikiConfig
from llm_wiki.scheduler import (
    SeenURLTracker,
    SchedulerRunResult,
    backoff_seconds,
    classify_error,
    collect_feed,
    run_once,
    should_retry,
    _save_feed_item_as_source,
)
from llm_wiki.subscriptions import Subscription, SubscriptionStatus, SubscriptionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    vault.mkdir(parents=True)
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    return SubscriptionStore(config)


@pytest.fixture
def seen(config: WikiConfig) -> SeenURLTracker:
    return SeenURLTracker(config)


def _make_feed_item(url: str = "https://example.com/post-1", title: str = "Post 1") -> FeedItem:
    return FeedItem(
        url=url,
        title=title,
        summary="A summary",
        author="Author",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_feed_result(items: list[FeedItem] | None = None) -> FeedResult:
    items = items or [_make_feed_item()]
    return FeedResult(
        ok=True,
        items=items,
        feed_title="Example Blog",
        feed_url="https://example.com/feed.xml",
    )


# ---------------------------------------------------------------------------
# Error classification & backoff
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_permanent_404(self):
        assert classify_error("Feed not found (404)") == "permanent"

    def test_permanent_403(self):
        assert classify_error("HTTP error: 403 Forbidden") == "permanent"

    def test_transient_timeout(self):
        assert classify_error("Request timed out after 30s") == "transient"

    def test_transient_network(self):
        assert classify_error("network error: connection refused") == "transient"

    def test_transient_429(self):
        assert classify_error("Rate limited (429)") == "transient"

    def test_ambiguous_unknown(self):
        assert classify_error("some weird error") == "ambiguous"


class TestShouldRetry:
    def test_transient_first_error(self):
        assert should_retry("timeout", 0) is True

    def test_transient_max_errors(self):
        assert should_retry("timeout", 3) is False

    def test_permanent_never_retry(self):
        assert should_retry("404 not found", 0) is False

    def test_ambiguous_retry_once(self):
        assert should_retry("some weird error", 0) is True
        assert should_retry("some weird error", 1) is False


class TestBackoff:
    def test_first_error_5min(self):
        assert backoff_seconds(0) == 5 * 60

    def test_second_error_30min(self):
        assert backoff_seconds(1) == 30 * 60

    def test_third_error_2h(self):
        assert backoff_seconds(2) == 120 * 60

    def test_beyond_max_stays_at_2h(self):
        assert backoff_seconds(10) == 120 * 60


# ---------------------------------------------------------------------------
# SeenURLTracker
# ---------------------------------------------------------------------------


class TestSeenURLTracker:
    def test_empty_on_init(self, seen: SeenURLTracker):
        assert seen.count == 0
        assert seen.is_seen("https://example.com") is False

    def test_mark_and_check(self, seen: SeenURLTracker):
        seen.mark_seen(["https://example.com/1", "https://example.com/2"])
        assert seen.count == 2
        assert seen.is_seen("https://example.com/1") is True
        assert seen.is_seen("https://example.com/3") is False

    def test_persistence(self, config: WikiConfig):
        seen1 = SeenURLTracker(config)
        seen1.mark_seen(["https://example.com/a"])

        # New instance reads from disk
        seen2 = SeenURLTracker(config)
        assert seen2.is_seen("https://example.com/a") is True

    def test_no_duplicates_on_remark(self, seen: SeenURLTracker, config: WikiConfig):
        seen.mark_seen(["https://example.com/1"])
        seen.mark_seen(["https://example.com/1", "https://example.com/2"])
        assert seen.count == 2

        # Check file has no duplicates
        text = (config.meta_path / "seen_urls.txt").read_text()
        lines = [l for l in text.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_reload(self, seen: SeenURLTracker, config: WikiConfig):
        seen.mark_seen(["https://example.com/1"])

        # Simulate external edit
        path = config.meta_path / "seen_urls.txt"
        with path.open("a") as f:
            f.write("https://example.com/external\n")

        seen.reload()
        assert seen.is_seen("https://example.com/external") is True


# ---------------------------------------------------------------------------
# Source file creation
# ---------------------------------------------------------------------------


class TestSaveFeedItem:
    def test_creates_source_file(self, config: WikiConfig):
        item = _make_feed_item()
        sub = Subscription(url="https://example.com/feed.xml", lenses=["tech"])

        path = _save_feed_item_as_source(item, "Example Blog", sub, config)

        assert path.exists()
        content = path.read_text()
        assert "---" in content
        assert "type: rss" in content
        assert "status: pending" in content
        assert "tech" in content
        assert "Post 1" in content

    def test_immutable_no_overwrite(self, config: WikiConfig):
        item = _make_feed_item()
        sub = Subscription(url="https://example.com/feed.xml")

        path1 = _save_feed_item_as_source(item, "Blog", sub, config)
        path2 = _save_feed_item_as_source(item, "Blog", sub, config)

        assert path1 != path2
        assert path1.exists()
        assert path2.exists()

    def test_frontmatter_has_lenses(self, config: WikiConfig):
        item = _make_feed_item()
        sub = Subscription(
            url="https://example.com/feed.xml",
            lenses=["tech", "ai"],
        )

        path = _save_feed_item_as_source(item, "Blog", sub, config)
        content = path.read_text()
        assert "lenses:" in content
        assert "  - tech" in content
        assert "  - ai" in content


# ---------------------------------------------------------------------------
# collect_feed
# ---------------------------------------------------------------------------


class TestCollectFeed:
    @pytest.mark.asyncio
    async def test_collects_new_items(
        self, config: WikiConfig, seen: SeenURLTracker
    ):
        sub = Subscription(url="https://example.com/feed.xml", title="Blog")
        feed_result = _make_feed_result([
            _make_feed_item("https://example.com/1", "Post 1"),
            _make_feed_item("https://example.com/2", "Post 2"),
        ])

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = feed_result
            result = await collect_feed(sub, seen, config)

        assert result.new_items == 2
        assert result.skipped_items == 0
        assert result.error == ""
        assert len(result.source_files) == 2

    @pytest.mark.asyncio
    async def test_skips_seen_items(
        self, config: WikiConfig, seen: SeenURLTracker
    ):
        seen.mark_seen(["https://example.com/1"])

        sub = Subscription(url="https://example.com/feed.xml", title="Blog")
        feed_result = _make_feed_result([
            _make_feed_item("https://example.com/1", "Old Post"),
            _make_feed_item("https://example.com/2", "New Post"),
        ])

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = feed_result
            result = await collect_feed(sub, seen, config)

        assert result.new_items == 1
        assert result.skipped_items == 1

    @pytest.mark.asyncio
    async def test_handles_feed_error(
        self, config: WikiConfig, seen: SeenURLTracker
    ):
        sub = Subscription(url="https://example.com/feed.xml", title="Blog")
        error_result = FeedResult(
            ok=False,
            feed_url="https://example.com/feed.xml",
            error="Feed not found (404)",
        )

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = error_result
            result = await collect_feed(sub, seen, config)

        assert result.error == "Feed not found (404)"
        assert result.new_items == 0


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_no_feeds(self, config: WikiConfig):
        result = await run_once(config)
        assert result.feeds_checked == 0
        assert result.new_items_total == 0

    @pytest.mark.asyncio
    async def test_with_active_feeds(self, config: WikiConfig, store: SubscriptionStore):
        store.add("https://blog1.com/feed.xml", title="Blog 1")
        store.add("https://blog2.com/feed.xml", title="Blog 2")

        feed_result = _make_feed_result([
            _make_feed_item("https://blog1.com/post-1", "Post"),
        ])

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = feed_result
            result = await run_once(config, store=store)

        assert result.feeds_checked == 2
        assert result.feeds_ok == 2
        assert result.new_items_total > 0

    @pytest.mark.asyncio
    async def test_records_errors_on_store(self, config: WikiConfig, store: SubscriptionStore):
        store.add("https://broken.com/feed.xml", title="Broken")

        error_result = FeedResult(
            ok=False,
            feed_url="https://broken.com/feed.xml",
            error="HTTP error: 500",
        )

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = error_result
            result = await run_once(config, store=store)

        assert result.feeds_error == 1

        # Check subscription was marked as error
        sub = store.get("https://broken.com/feed.xml")
        assert sub.status == SubscriptionStatus.ERROR

    @pytest.mark.asyncio
    async def test_skips_paused_feeds(self, config: WikiConfig, store: SubscriptionStore):
        store.add("https://active.com/feed.xml", title="Active")
        store.add("https://paused.com/feed.xml", title="Paused")
        store.set_status("https://paused.com/feed.xml", SubscriptionStatus.PAUSED)

        feed_result = _make_feed_result()

        with patch("llm_wiki.scheduler.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = feed_result
            result = await run_once(config, store=store)

        # Only the active feed should be checked
        assert result.feeds_checked == 1
        assert mock.call_count == 1

"""Tests for the fetch-all-subscriptions module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.fetch_all import (
    FetchAllResult,
    FetchOutcome,
    fetch_all_subscriptions,
    fetch_subscription,
)
from llm_wiki.fetchers.base import FetchedItem, FetchResult
from llm_wiki.subscriptions import (
    CollectionSchedule,
    ScheduleConfig,
    Subscription,
    SubscriptionStatus,
    SubscriptionStore,
)


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


# ---------------------------------------------------------------------------
# fetch_all_subscriptions: empty state
# ---------------------------------------------------------------------------


class TestFetchAllEmpty:
    def test_no_subscriptions(self, config: WikiConfig):
        result = asyncio.run(fetch_all_subscriptions(config))
        assert result.total_subscriptions == 0
        assert result.fetched == 0
        assert result.new_items_total == 0
        assert result.outcomes == []

    def test_all_paused(self, config: WikiConfig, store: SubscriptionStore):
        sub = store.add("https://example.com/feed.xml", title="Test Feed")
        store.set_status(sub.url, SubscriptionStatus.PAUSED)
        result = asyncio.run(fetch_all_subscriptions(config))
        assert result.fetched == 0
        assert result.total_subscriptions == 1


class TestFetchAllDryRun:
    def test_dry_run_lists_without_fetching(
        self, config: WikiConfig, store: SubscriptionStore
    ):
        store.add("https://example.com/feed.xml", title="Feed 1")
        store.add("https://example.com/feed2.xml", title="Feed 2")

        result = asyncio.run(fetch_all_subscriptions(config, dry_run=True))
        assert result.fetched == 0
        assert result.succeeded == 0
        assert len(result.outcomes) == 2
        # All outcomes should list the subscriptions
        titles = {o.title for o in result.outcomes}
        assert "Feed 1" in titles
        assert "Feed 2" in titles


class TestFetchAllFiltering:
    def test_source_type_filter(
        self, config: WikiConfig, store: SubscriptionStore
    ):
        store.add("https://example.com/feed.xml", title="RSS Feed")
        store.add(
            "https://www.youtube.com/channel/UC123",
            title="YouTube Channel",
            source_type="youtube",
        )

        result = asyncio.run(
            fetch_all_subscriptions(config, source_type_filter="rss", dry_run=True)
        )
        assert len(result.outcomes) == 1
        assert result.outcomes[0].source_type == "rss"

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_manual_subscriptions_skipped(
        self, mock_get_fetcher, config: WikiConfig, store: SubscriptionStore
    ):
        store.add(
            "https://example.com/feed.xml",
            title="Auto Feed",
            schedule="daily",
        )
        store.add(
            "https://example.com/manual.html",
            title="Manual Page",
            source_type="webpage",
            schedule="manual",
        )

        # Mock fetcher for the non-manual subscription
        mock_fetcher = MagicMock()
        mock_fetcher.poll = AsyncMock(
            return_value=FetchResult(ok=True, items=[], source_title="Auto Feed")
        )
        mock_get_fetcher.return_value = mock_fetcher

        result = asyncio.run(fetch_all_subscriptions(config))
        # Manual subscription should be skipped, auto feed should be fetched
        assert result.skipped == 1
        assert result.fetched == 1
        assert result.succeeded == 1


# ---------------------------------------------------------------------------
# fetch_subscription: unit tests with mocked fetcher
# ---------------------------------------------------------------------------


class TestFetchSubscription:
    def test_no_fetcher_registered(self, config: WikiConfig):
        sub = Subscription(
            url="https://example.com/unknown",
            title="Unknown",
            source_type="unknown_type",
        )
        result = asyncio.run(fetch_subscription(sub, config))
        assert result.ok is False
        assert "No fetcher" in result.error

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_successful_fetch(self, mock_get_fetcher, config: WikiConfig):
        # Setup mock fetcher
        mock_fetcher = MagicMock()
        mock_fetcher.poll = AsyncMock(
            return_value=FetchResult(
                ok=True,
                items=[
                    FetchedItem(
                        url="https://example.com/post-1",
                        title="Post 1",
                        summary="Summary 1",
                        source_type="rss",
                    ),
                ],
                source_title="Test Blog",
            )
        )
        mock_get_fetcher.return_value = mock_fetcher

        sub = Subscription(
            url="https://example.com/feed.xml",
            title="Test Feed",
            source_type="rss",
        )
        result = asyncio.run(fetch_subscription(sub, config))
        assert result.ok is True
        assert result.new_items == 1
        assert len(result.source_files) == 1
        mock_fetcher.poll.assert_awaited_once()

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_fetch_error(self, mock_get_fetcher, config: WikiConfig):
        mock_fetcher = MagicMock()
        mock_fetcher.poll = AsyncMock(
            return_value=FetchResult(
                ok=False,
                error="Connection timeout",
            )
        )
        mock_get_fetcher.return_value = mock_fetcher

        sub = Subscription(
            url="https://example.com/feed.xml",
            title="Test Feed",
            source_type="rss",
        )
        result = asyncio.run(fetch_subscription(sub, config))
        assert result.ok is False
        assert result.error == "Connection timeout"

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_fetch_exception(self, mock_get_fetcher, config: WikiConfig):
        mock_fetcher = MagicMock()
        mock_fetcher.poll = AsyncMock(side_effect=RuntimeError("Network error"))
        mock_get_fetcher.return_value = mock_fetcher

        sub = Subscription(
            url="https://example.com/feed.xml",
            title="Test Feed",
            source_type="rss",
        )
        result = asyncio.run(fetch_subscription(sub, config))
        assert result.ok is False
        assert "Network error" in result.error

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_incremental_fetch_uses_since(self, mock_get_fetcher, config: WikiConfig):
        """Verify that last_fetched_at is passed as 'since' parameter."""
        mock_fetcher = MagicMock()
        mock_fetcher.poll = AsyncMock(
            return_value=FetchResult(ok=True, items=[])
        )
        mock_get_fetcher.return_value = mock_fetcher

        last_fetch = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        sub = Subscription(
            url="https://example.com/feed.xml",
            title="Test Feed",
            source_type="rss",
            last_fetched_at=last_fetch,
        )
        asyncio.run(fetch_subscription(sub, config))
        mock_fetcher.poll.assert_awaited_once_with(since=last_fetch)


# ---------------------------------------------------------------------------
# FetchAllResult serialization
# ---------------------------------------------------------------------------


class TestFetchAllResultSerialization:
    def test_to_dict(self):
        result = FetchAllResult(
            total_subscriptions=5,
            fetched=3,
            succeeded=2,
            failed=1,
            skipped=2,
            new_items_total=10,
            duration_seconds=5.5,
            outcomes=[
                FetchOutcome(
                    url="https://example.com/feed.xml",
                    title="Test",
                    source_type="rss",
                    ok=True,
                    new_items=10,
                ),
            ],
        )
        d = result.to_dict()
        assert d["status"] == "complete"
        assert d["total_subscriptions"] == 5
        assert d["fetched"] == 3
        assert d["new_items_total"] == 10
        assert len(d["per_subscription"]) == 1
        assert d["per_subscription"][0]["ok"] is True


# ---------------------------------------------------------------------------
# Integration: fetch_all_subscriptions with mocked fetcher
# ---------------------------------------------------------------------------


class TestFetchAllIntegration:
    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_end_to_end(self, mock_get_fetcher, config: WikiConfig, store: SubscriptionStore):
        """Test the full pipeline: store → fetch → save → record."""
        # Setup: two RSS subscriptions
        store.add("https://blog1.com/feed.xml", title="Blog 1", lenses=["tech"])
        store.add("https://blog2.com/feed.xml", title="Blog 2", lenses=["ai"])

        # Mock fetcher returns different items per URL
        def _create_fetcher(source_type, url, **kwargs):
            fetcher = MagicMock()
            if "blog1" in url:
                fetcher.poll = AsyncMock(
                    return_value=FetchResult(
                        ok=True,
                        items=[
                            FetchedItem(
                                url="https://blog1.com/post-1",
                                title="Blog1 Post",
                                source_type="rss",
                            ),
                        ],
                        source_title="Blog 1",
                    )
                )
            else:
                fetcher.poll = AsyncMock(
                    return_value=FetchResult(
                        ok=True,
                        items=[
                            FetchedItem(
                                url="https://blog2.com/article-1",
                                title="Blog2 Article",
                                source_type="rss",
                            ),
                            FetchedItem(
                                url="https://blog2.com/article-2",
                                title="Blog2 Article 2",
                                source_type="rss",
                            ),
                        ],
                        source_title="Blog 2",
                    )
                )
            return fetcher

        mock_get_fetcher.side_effect = _create_fetcher

        result = asyncio.run(fetch_all_subscriptions(config))

        assert result.fetched == 2
        assert result.succeeded == 2
        assert result.failed == 0
        assert result.new_items_total == 3

        # Verify source files were created
        sources_dir = config.sources_path
        assert sources_dir.exists()
        source_files = list(sources_dir.glob("*.md"))
        assert len(source_files) == 3

        # Verify subscription YAML was updated
        store_reloaded = SubscriptionStore(config)
        blog1 = store_reloaded.get("https://blog1.com/feed.xml")
        assert blog1 is not None
        assert blog1.last_fetched_at is not None
        assert blog1.last_fetched_count == 1
        assert blog1.status == SubscriptionStatus.ACTIVE

    @patch("llm_wiki.fetch_all.get_fetcher")
    def test_mixed_success_and_failure(
        self, mock_get_fetcher, config: WikiConfig, store: SubscriptionStore
    ):
        """One feed succeeds, one fails — both should be recorded."""
        store.add("https://ok.com/feed.xml", title="OK Feed")
        store.add("https://bad.com/feed.xml", title="Bad Feed")

        def _create_fetcher(source_type, url, **kwargs):
            fetcher = MagicMock()
            if "ok.com" in url:
                fetcher.poll = AsyncMock(
                    return_value=FetchResult(ok=True, items=[], source_title="OK")
                )
            else:
                fetcher.poll = AsyncMock(
                    return_value=FetchResult(ok=False, error="404 Not Found")
                )
            return fetcher

        mock_get_fetcher.side_effect = _create_fetcher

        result = asyncio.run(fetch_all_subscriptions(config))

        assert result.succeeded == 1
        assert result.failed == 1
        assert result.fetched == 2

        # Verify error was recorded in subscription store
        store_reloaded = SubscriptionStore(config)
        bad = store_reloaded.get("https://bad.com/feed.xml")
        assert bad is not None
        assert bad.status == SubscriptionStatus.ERROR
        assert bad.error_message == "404 Not Found"

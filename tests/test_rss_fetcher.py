"""Tests for the RSS fetcher (BaseFetcher interface wrapper)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from llm_wiki.collectors.rss import FeedItem, FeedResult
from llm_wiki.fetchers.rss import RSSFetcher


class TestRSSFetcher:
    def test_source_type(self):
        f = RSSFetcher("https://example.com/feed.xml")
        assert f.source_type == "rss"
        assert f.url == "https://example.com/feed.xml"

    @pytest.mark.asyncio
    async def test_poll_success(self):
        mock_feed_result = FeedResult(
            ok=True,
            items=[
                FeedItem(
                    url="https://example.com/post-1",
                    title="Post One",
                    summary="Summary",
                    author="Author",
                    published_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
                ),
            ],
            feed_title="Example Blog",
            feed_url="https://example.com/feed.xml",
        )

        with patch("llm_wiki.fetchers.rss.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = mock_feed_result

            fetcher = RSSFetcher("https://example.com/feed.xml")
            result = await fetcher.poll()

        assert result.ok is True
        assert len(result.items) == 1
        assert result.items[0].url == "https://example.com/post-1"
        assert result.items[0].source_type == "rss"
        assert result.items[0].extra["feed_title"] == "Example Blog"
        assert result.source_title == "Example Blog"

    @pytest.mark.asyncio
    async def test_poll_error(self):
        mock_feed_result = FeedResult(
            ok=False,
            feed_url="https://example.com/feed.xml",
            error="HTTP 404",
        )

        with patch("llm_wiki.fetchers.rss.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = mock_feed_result

            fetcher = RSSFetcher("https://example.com/feed.xml")
            result = await fetcher.poll()

        assert result.ok is False
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_poll_filters_by_since(self):
        old = datetime(2024, 1, 1, tzinfo=timezone.utc)
        new = datetime(2024, 6, 15, tzinfo=timezone.utc)

        mock_feed_result = FeedResult(
            ok=True,
            items=[
                FeedItem(url="https://example.com/old", title="Old", published_at=old),
                FeedItem(url="https://example.com/new", title="New", published_at=new),
            ],
            feed_title="Blog",
            feed_url="https://example.com/feed.xml",
        )

        with patch("llm_wiki.fetchers.rss.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = mock_feed_result

            fetcher = RSSFetcher("https://example.com/feed.xml")
            since = datetime(2024, 3, 1, tzinfo=timezone.utc)
            result = await fetcher.poll(since=since)

        assert result.ok is True
        assert len(result.items) == 1
        assert result.items[0].url == "https://example.com/new"
        assert result.total_available == 2  # both were in the feed

    @pytest.mark.asyncio
    async def test_poll_includes_items_without_date(self):
        """Items without published_at are included (conservative)."""
        mock_feed_result = FeedResult(
            ok=True,
            items=[
                FeedItem(url="https://example.com/no-date", title="No Date", published_at=None),
            ],
            feed_title="Blog",
            feed_url="https://example.com/feed.xml",
        )

        with patch("llm_wiki.fetchers.rss.fetch_feed", new_callable=AsyncMock) as mock:
            mock.return_value = mock_feed_result

            fetcher = RSSFetcher("https://example.com/feed.xml")
            since = datetime(2024, 3, 1, tzinfo=timezone.utc)
            result = await fetcher.poll(since=since)

        assert len(result.items) == 1  # included despite no date

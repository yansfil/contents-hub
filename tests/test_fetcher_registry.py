"""Tests for the fetcher registry."""

from __future__ import annotations

import pytest

from llm_wiki.fetchers.base import BaseFetcher, FetchResult
from llm_wiki.fetchers.registry import (
    register_fetcher,
    get_fetcher,
    get_fetcher_for_url,
    registered_types,
    is_registered,
    _registry,
)
from llm_wiki.fetchers.rss import RSSFetcher
from llm_wiki.fetchers.youtube import YouTubeFetcher


class TestRegistry:
    def test_youtube_auto_registered(self):
        """YouTube fetcher is registered at import time."""
        assert is_registered("youtube") is True
        assert "youtube" in registered_types()

    def test_rss_auto_registered(self):
        """RSS fetcher is registered at import time."""
        assert is_registered("rss") is True
        assert "rss" in registered_types()

    def test_get_fetcher_youtube(self):
        url = "https://www.youtube.com/channel/UCtest123"
        fetcher = get_fetcher("youtube", url)
        assert fetcher is not None
        assert isinstance(fetcher, YouTubeFetcher)
        assert fetcher.url == url

    def test_get_fetcher_rss(self):
        url = "https://example.com/feed.xml"
        fetcher = get_fetcher("rss", url)
        assert fetcher is not None
        assert isinstance(fetcher, RSSFetcher)
        assert fetcher.url == url

    def test_get_fetcher_unknown_type(self):
        assert get_fetcher("nonexistent", "https://example.com") is None

    def test_get_fetcher_for_url_youtube(self):
        fetcher = get_fetcher_for_url("https://www.youtube.com/@test")
        assert fetcher is not None
        assert isinstance(fetcher, YouTubeFetcher)

    def test_get_fetcher_for_url_rss(self):
        """RSS fetcher is registered — auto-detected from generic HTTP URL."""
        fetcher = get_fetcher_for_url("https://example.com/feed.xml")
        assert fetcher is not None
        assert isinstance(fetcher, RSSFetcher)

    def test_register_custom_fetcher(self):
        """Can register a custom fetcher factory."""

        class DummyFetcher(BaseFetcher):
            @property
            def source_type(self) -> str:
                return "dummy"

            async def poll(self, *, since=None, max_items=50) -> FetchResult:
                return FetchResult(ok=True)

        register_fetcher("dummy", DummyFetcher)
        try:
            assert is_registered("dummy") is True
            fetcher = get_fetcher("dummy", "https://example.com/dummy")
            assert fetcher is not None
            assert fetcher.source_type == "dummy"
        finally:
            # Clean up
            _registry.pop("dummy", None)

    def test_registered_types_returns_list(self):
        types = registered_types()
        assert isinstance(types, list)
        assert len(types) >= 1  # At least youtube

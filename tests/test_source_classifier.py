"""Tests for llm_wiki.source_router.classify()."""
from __future__ import annotations

import pytest

from llm_wiki.source_router import FEATURED_SOURCE_TYPES, classify


def test_classify_youtube_handle() -> None:
    info = classify("https://youtube.com/@karpathy")
    assert info["source_type"] == "youtube"
    assert info["recipe_base"] == "youtube"
    assert info["suggested_title"] == "@karpathy"


def test_classify_youtube_short() -> None:
    info = classify("https://youtu.be/abc123")
    assert info["source_type"] == "youtube"


def test_classify_twitter_x_domain() -> None:
    info = classify("https://x.com/sama")
    assert info["source_type"] == "twitter"
    assert info["recipe_base"] == "twitter"
    assert info["suggested_title"] == "@sama"


def test_classify_twitter_legacy_domain() -> None:
    info = classify("https://twitter.com/elonmusk")
    assert info["source_type"] == "twitter"


def test_classify_linkedin() -> None:
    info = classify("https://www.linkedin.com/in/someone")
    assert info["source_type"] == "linkedin"
    assert info["recipe_base"] == "linkedin"


def test_classify_substack() -> None:
    info = classify("https://foo.substack.com")
    assert info["source_type"] == "substack"
    assert info["recipe_base"] == "substack"


def test_classify_medium() -> None:
    info = classify("https://medium.com/@author")
    assert info["source_type"] == "medium"


def test_classify_reddit_subreddit() -> None:
    info = classify("https://www.reddit.com/r/MachineLearning/")
    assert info["source_type"] == "reddit"
    assert "r/MachineLearning" in info["suggested_title"]


def test_classify_rss_feed_pattern() -> None:
    info = classify("https://example.com/feed.xml")
    assert info["source_type"] == "rss"
    assert info["has_rss_hint"] is True


def test_classify_rss_path() -> None:
    info = classify("https://example.com/rss/")
    assert info["source_type"] == "rss"
    assert info["has_rss_hint"] is True


def test_classify_webpage_fallback() -> None:
    info = classify("https://unknown-site.example.io/blog/post")
    assert info["source_type"] == "webpage"
    assert info["recipe_base"] is None
    assert info["has_rss_hint"] is False


def test_classify_returns_all_keys() -> None:
    info = classify("https://example.com")
    assert set(info.keys()) == {"source_type", "recipe_base", "has_rss_hint", "suggested_title"}


@pytest.mark.parametrize("source_type", sorted(FEATURED_SOURCE_TYPES))
def test_featured_source_types_constant(source_type: str) -> None:
    # Ensure the 7 featured types are all in the registry constant.
    assert source_type in FEATURED_SOURCE_TYPES


def test_featured_source_type_count() -> None:
    assert len(FEATURED_SOURCE_TYPES) == 7

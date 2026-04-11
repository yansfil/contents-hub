"""Tests for markdown body template rendering module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm_wiki.body_template import (
    render_body,
    render_body_for_type,
    available_types,
    _heading,
    _summary_block,
    _metadata_section,
    _source_footer,
    _assemble_body,
    _strip_html,
)
from llm_wiki.fetchers.base import FetchedItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TIME = datetime(2024, 3, 15, 14, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def rss_item() -> FetchedItem:
    return FetchedItem(
        url="https://blog.example.com/post-1",
        title="Understanding Transformers",
        summary="An introduction to transformer architecture.",
        author="Alice",
        published_at=datetime(2024, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
        tags=["ai", "ml"],
        content_html="<p>Full article content about transformers.</p>",
        source_type="rss",
        extra={
            "feed_title": "AI Blog",
            "subscription_title": "AI Blog RSS",
        },
    )


@pytest.fixture
def youtube_item() -> FetchedItem:
    return FetchedItem(
        url="https://www.youtube.com/watch?v=abc123",
        title="GPT-5 Explained",
        summary="A deep dive into GPT-5 architecture.",
        author="TechChannel",
        published_at=datetime(2024, 3, 12, 16, 0, 0, tzinfo=timezone.utc),
        source_type="youtube",
        extra={
            "video_id": "abc123",
            "thumbnail_url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
            "views": 1500000,
            "channel_title": "TechChannel Official",
        },
    )


@pytest.fixture
def twitter_item() -> FetchedItem:
    return FetchedItem(
        url="https://x.com/elonmusk/status/123456",
        title="@elonmusk tweet",
        summary="Just shipped a new feature!",
        author="@elonmusk",
        published_at=datetime(2024, 3, 14, 20, 0, 0, tzinfo=timezone.utc),
        source_type="twitter",
        extra={
            "tweet_id": "123456",
            "like_count": 50000,
            "retweet_count": 10000,
            "reply_count": 5000,
            "view_count": 2000000,
        },
    )


@pytest.fixture
def browser_item() -> FetchedItem:
    return FetchedItem(
        url="https://docs.example.com/guide",
        title="Complete Guide to RAG",
        summary="A comprehensive guide to retrieval-augmented generation.",
        author="",
        source_type="browser",
        content_html="<div>Full guide content about RAG patterns.</div>",
        extra={
            "domain": "docs.example.com",
            "word_count": 3500,
            "query": "RAG best practices 2024",
        },
    )


@pytest.fixture
def minimal_item() -> FetchedItem:
    return FetchedItem(
        url="https://example.com/page",
        title="",
        source_type="unknown",
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


class TestHeading:
    def test_with_title(self):
        assert _heading("My Title", "https://example.com") == "# My Title"

    def test_empty_title_uses_url(self):
        assert _heading("", "https://example.com") == "# https://example.com"


class TestSummaryBlock:
    def test_single_line(self):
        assert _summary_block("Hello world") == "> Hello world"

    def test_multi_line(self):
        result = _summary_block("Line one\nLine two")
        assert result == "> Line one\n> Line two"

    def test_empty_returns_empty(self):
        assert _summary_block("") == ""

    def test_whitespace_stripped(self):
        result = _summary_block("  Hello  ")
        assert result == "> Hello"


class TestMetadataSection:
    def test_full_metadata(self):
        result = _metadata_section(
            author="Alice",
            published_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            feed="My Feed",
            subscription="My Sub",
        )
        assert "**Author**: Alice" in result
        assert "**Published**: 2024-01-01T00:00:00+00:00" in result
        assert "**Feed**: My Feed" in result
        assert "**Subscription**: My Sub" in result

    def test_empty_fields_omitted(self):
        result = _metadata_section(author="Alice")
        assert "**Author**: Alice" in result
        assert "Published" not in result
        assert "Feed" not in result

    def test_extra_fields(self):
        result = _metadata_section(
            extra_fields=[("Views", "1,500"), ("Domain", "example.com")],
        )
        assert "**Views**: 1,500" in result
        assert "**Domain**: example.com" in result

    def test_empty_extra_field_value_omitted(self):
        result = _metadata_section(
            extra_fields=[("Key", ""), ("Valid", "yes")],
        )
        assert "Key" not in result
        assert "**Valid**: yes" in result

    def test_all_empty_returns_empty(self):
        result = _metadata_section()
        assert result == ""


class TestSourceFooter:
    def test_footer_format(self):
        dt = datetime(2024, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = _source_footer("https://example.com/post", dt)
        assert "---" in result
        assert "Source: https://example.com/post" in result
        assert "Collected: 2024-03-15 14:30 UTC" in result


class TestAssembleBody:
    def test_joins_sections(self):
        result = _assemble_body("# Title", "> Summary", "**Key**: value")
        assert "# Title\n\n> Summary\n\n**Key**: value\n" == result

    def test_empty_sections_skipped(self):
        result = _assemble_body("# Title", "", "Footer")
        assert "# Title\n\nFooter\n" == result

    def test_trailing_newline(self):
        result = _assemble_body("content")
        assert result.endswith("\n")

    def test_all_empty(self):
        result = _assemble_body("", "", "")
        assert result == "\n"


# ---------------------------------------------------------------------------
# RSS renderer
# ---------------------------------------------------------------------------


class TestRSSRenderer:
    def test_basic_structure(self, rss_item: FetchedItem):
        body = render_body(rss_item, collected_at=FIXED_TIME)

        assert body.startswith("# Understanding Transformers\n")
        assert "> An introduction to transformer architecture." in body
        assert "**Author**: Alice" in body
        assert "**Published**: 2024-03-10T08:00:00+00:00" in body
        assert "**Feed**: AI Blog" in body
        assert "Source: https://blog.example.com/post-1" in body
        assert "Collected: 2024-03-15 14:30 UTC" in body

    def test_html_content_stripped(self, rss_item: FetchedItem):
        body = render_body(rss_item, collected_at=FIXED_TIME)
        assert "## Content" in body
        assert "Full article content about transformers." in body
        assert "<p>" not in body

    def test_content_section_skipped_when_same_as_summary(self):
        item = FetchedItem(
            url="https://example.com/p",
            title="Post",
            summary="Same text",
            content_html="Same text",
            source_type="rss",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "## Content" not in body

    def test_podcast_enclosure(self):
        item = FetchedItem(
            url="https://example.com/ep1",
            title="Episode 1",
            source_type="rss",
            extra={
                "enclosure_url": "https://example.com/ep1.mp3",
                "enclosure_type": "audio/mpeg",
            },
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "## Podcast" in body
        assert "[Listen](https://example.com/ep1.mp3)" in body

    def test_no_podcast_for_non_audio(self):
        item = FetchedItem(
            url="https://example.com/ep1",
            title="Episode 1",
            source_type="rss",
            extra={
                "enclosure_url": "https://example.com/img.jpg",
                "enclosure_type": "image/jpeg",
            },
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "## Podcast" not in body

    def test_minimal_rss(self):
        item = FetchedItem(
            url="https://example.com/x",
            title="X",
            source_type="rss",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert body.startswith("# X\n")
        assert "Source: https://example.com/x" in body
        assert "Author" not in body
        assert "Published" not in body


# ---------------------------------------------------------------------------
# YouTube renderer
# ---------------------------------------------------------------------------


class TestYouTubeRenderer:
    def test_basic_structure(self, youtube_item: FetchedItem):
        body = render_body(youtube_item, collected_at=FIXED_TIME)

        assert body.startswith("# GPT-5 Explained\n")
        assert "![thumbnail](https://i.ytimg.com/vi/abc123/hqdefault.jpg)" in body
        assert "> A deep dive into GPT-5 architecture." in body
        assert "**Author**: TechChannel" in body
        assert "**Views**: 1,500,000" in body
        assert "**Video ID**: abc123" in body
        assert "Source: https://www.youtube.com/watch?v=abc123" in body

    def test_channel_title_as_feed(self, youtube_item: FetchedItem):
        body = render_body(youtube_item, collected_at=FIXED_TIME)
        assert "**Feed**: TechChannel Official" in body

    def test_no_thumbnail(self):
        item = FetchedItem(
            url="https://www.youtube.com/watch?v=xyz",
            title="No Thumb Video",
            source_type="youtube",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "![thumbnail]" not in body

    def test_no_views(self):
        item = FetchedItem(
            url="https://www.youtube.com/watch?v=xyz",
            title="Video",
            source_type="youtube",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "Views" not in body


# ---------------------------------------------------------------------------
# Twitter renderer
# ---------------------------------------------------------------------------


class TestTwitterRenderer:
    def test_basic_structure(self, twitter_item: FetchedItem):
        body = render_body(twitter_item, collected_at=FIXED_TIME)

        assert "# @elonmusk tweet" in body
        assert "> Just shipped a new feature!" in body
        assert "**Author**: @elonmusk" in body

    def test_engagement_stats(self, twitter_item: FetchedItem):
        body = render_body(twitter_item, collected_at=FIXED_TIME)

        assert "## Engagement" in body
        assert "50000 likes" in body
        assert "10000 retweets" in body
        assert "5000 replies" in body
        assert "2000000 views" in body

    def test_no_engagement_when_zero(self):
        item = FetchedItem(
            url="https://x.com/user/status/1",
            title="Tweet",
            source_type="twitter",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "## Engagement" not in body

    def test_media_urls(self):
        item = FetchedItem(
            url="https://x.com/user/status/1",
            title="Tweet with media",
            source_type="twitter",
            extra={
                "media_urls": [
                    "https://pbs.twimg.com/media/img1.jpg",
                    "https://pbs.twimg.com/media/img2.jpg",
                ],
            },
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "## Media" in body
        assert "![](https://pbs.twimg.com/media/img1.jpg)" in body
        assert "![](https://pbs.twimg.com/media/img2.jpg)" in body

    def test_partial_engagement(self):
        item = FetchedItem(
            url="https://x.com/user/status/1",
            title="Tweet",
            source_type="twitter",
            extra={"like_count": 42},
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "42 likes" in body
        assert "retweets" not in body


# ---------------------------------------------------------------------------
# Browser renderer
# ---------------------------------------------------------------------------


class TestBrowserRenderer:
    def test_basic_structure(self, browser_item: FetchedItem):
        body = render_body(browser_item, collected_at=FIXED_TIME)

        assert body.startswith("# Complete Guide to RAG\n")
        assert "> A comprehensive guide" in body
        assert "**Domain**: docs.example.com" in body
        assert "**Word Count**: 3,500" in body
        assert "**Query**: RAG best practices 2024" in body
        assert "Source: https://docs.example.com/guide" in body

    def test_content_section(self, browser_item: FetchedItem):
        body = render_body(browser_item, collected_at=FIXED_TIME)
        assert "## Content" in body
        assert "Full guide content about RAG patterns." in body
        assert "<div>" not in body

    def test_no_domain_no_wordcount(self):
        item = FetchedItem(
            url="https://example.com/page",
            title="Simple Page",
            source_type="browser",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "Domain" not in body
        assert "Word Count" not in body


# ---------------------------------------------------------------------------
# Generic renderer (fallback)
# ---------------------------------------------------------------------------


class TestGenericRenderer:
    def test_unknown_type_uses_generic(self, minimal_item: FetchedItem):
        body = render_body(minimal_item, collected_at=FIXED_TIME)
        assert "# https://example.com/page" in body
        assert "Source: https://example.com/page" in body
        assert "Collected: 2024-03-15 14:30 UTC" in body

    def test_custom_type_uses_generic(self):
        item = FetchedItem(
            url="https://example.com",
            title="Custom Item",
            source_type="mastodon",
            author="user@instance",
        )
        body = render_body(item, collected_at=FIXED_TIME)
        assert "# Custom Item" in body
        assert "**Author**: user@instance" in body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_render_body_for_type_override(self, rss_item: FetchedItem):
        """render_body_for_type uses explicit type regardless of item.source_type."""
        body = render_body_for_type("youtube", rss_item, collected_at=FIXED_TIME)
        # YouTube template includes Video ID field logic, won't have ## Content
        assert "# Understanding Transformers" in body

    def test_available_types(self):
        types = available_types()
        assert "rss" in types
        assert "youtube" in types
        assert "twitter" in types
        assert "browser" in types

    def test_default_collected_at(self, rss_item: FetchedItem):
        """When collected_at is not provided, uses current UTC time."""
        body = render_body(rss_item)
        assert "Collected:" in body

    def test_trailing_newline(self, rss_item: FetchedItem):
        body = render_body(rss_item, collected_at=FIXED_TIME)
        assert body.endswith("\n")


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_basic_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_entities(self):
        assert _strip_html("A &amp; B") == "A & B"

    def test_nested(self):
        assert _strip_html("<div><b>Bold</b></div>") == "Bold"

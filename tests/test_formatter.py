"""Tests for Obsidian vault markdown formatter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.collectors.rss import FeedItem
from llm_wiki.collectors.youtube import YouTubeVideo
from llm_wiki.collectors.browser import ExtractedPage
from llm_wiki.formatter import (
    format_feed_item,
    format_youtube_video,
    format_extracted_page,
    source_filename,
    source_path,
    _slugify,
    _strip_html,
    _assemble,
    _yaml_scalar,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TIME = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def rss_item() -> FeedItem:
    return FeedItem(
        url="https://example.com/first-post",
        title="First Post",
        summary="A short summary.",
        author="Alice",
        published_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=["tech", "python"],
        content_html="<p>Full content here</p>",
    )


@pytest.fixture
def podcast_item() -> FeedItem:
    return FeedItem(
        url="https://example.com/episode-1",
        title="Episode 1: Hello World",
        summary="First episode of the podcast.",
        author="Bob",
        published_at=datetime(2024, 2, 10, 8, 0, 0, tzinfo=timezone.utc),
        enclosure_url="https://example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=12345678,
    )


@pytest.fixture
def youtube_video() -> YouTubeVideo:
    return YouTubeVideo(
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        published_at=datetime(2009, 10, 25, 6, 57, 33, tzinfo=timezone.utc),
        author="Rick Astley",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        description="The official video for Rick Astley's classic hit.",
        views=1500000000,
    )


@pytest.fixture
def extracted_page() -> ExtractedPage:
    return ExtractedPage.from_raw(
        url="https://blog.example.com/great-article",
        title="A Great Article About AI",
        content="This is the full extracted content of the article about artificial intelligence.",
    )


# ---------------------------------------------------------------------------
# format_feed_item
# ---------------------------------------------------------------------------


class TestFormatFeedItem:
    def test_basic_output(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)

        # Frontmatter structure
        assert result.startswith("---\n")
        assert "source_type: rss" in result
        assert "url: https://example.com/first-post" in result
        assert "title: First Post" in result
        assert "author: Alice" in result
        assert "published: 2024-01-15T12:00:00+00:00" in result
        assert f"collected: {FIXED_TIME.isoformat()}" in result

    def test_tags_in_frontmatter(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert "tags:" in result
        assert "  - tech" in result
        assert "  - python" in result

    def test_lenses_in_frontmatter(self, rss_item: FeedItem) -> None:
        result = format_feed_item(
            rss_item, lenses=["ai", "programming"], collected_at=FIXED_TIME
        )
        assert "lenses:" in result
        assert "  - ai" in result
        assert "  - programming" in result

    def test_feed_title_in_frontmatter(self, rss_item: FeedItem) -> None:
        result = format_feed_item(
            rss_item, feed_title="My Blog", collected_at=FIXED_TIME
        )
        assert "feed: My Blog" in result

    def test_body_contains_title_heading(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert "# First Post" in result

    def test_body_contains_summary(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert "A short summary." in result

    def test_body_contains_stripped_html_content(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert "## Content" in result
        assert "Full content here" in result
        assert "<p>" not in result

    def test_body_contains_source_link(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert "Source: https://example.com/first-post" in result

    def test_trailing_newline(self, rss_item: FeedItem) -> None:
        result = format_feed_item(rss_item, collected_at=FIXED_TIME)
        assert result.endswith("\n")

    def test_podcast_enclosure(self, podcast_item: FeedItem) -> None:
        result = format_feed_item(podcast_item, collected_at=FIXED_TIME)
        assert "enclosure_url: https://example.com/ep1.mp3" in result
        assert "enclosure_type: audio/mpeg" in result

    def test_minimal_item(self) -> None:
        """FeedItem with only required fields."""
        item = FeedItem(url="https://example.com/x", title="X")
        result = format_feed_item(item, collected_at=FIXED_TIME)
        assert "source_type: rss" in result
        assert "url: https://example.com/x" in result
        assert "title: X" in result
        # No author, no published, no tags, no content section
        assert "author:" not in result
        assert "published:" not in result


# ---------------------------------------------------------------------------
# format_youtube_video
# ---------------------------------------------------------------------------


class TestFormatYouTubeVideo:
    def test_basic_output(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(youtube_video, collected_at=FIXED_TIME)

        assert "source_type: youtube" in result
        assert "video_id: dQw4w9WgXcQ" in result
        assert "title: Never Gonna Give You Up" in result
        assert "author: Rick Astley" in result
        assert "views: 1500000000" in result

    def test_youtube_tag_always_present(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(youtube_video, collected_at=FIXED_TIME)
        assert "  - youtube" in result

    def test_thumbnail_in_body(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(youtube_video, collected_at=FIXED_TIME)
        assert "![thumbnail](https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg)" in result

    def test_description_in_body(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(youtube_video, collected_at=FIXED_TIME)
        assert "The official video for Rick Astley's classic hit." in result

    def test_views_in_body(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(youtube_video, collected_at=FIXED_TIME)
        assert "Views: 1,500,000,000" in result

    def test_channel_title(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(
            youtube_video, channel_title="Rick Astley Official", collected_at=FIXED_TIME
        )
        assert "feed: Rick Astley Official" in result

    def test_lenses(self, youtube_video: YouTubeVideo) -> None:
        result = format_youtube_video(
            youtube_video, lenses=["music"], collected_at=FIXED_TIME
        )
        assert "lenses:" in result
        assert "  - music" in result


# ---------------------------------------------------------------------------
# format_extracted_page
# ---------------------------------------------------------------------------


class TestFormatExtractedPage:
    def test_basic_output(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(extracted_page, collected_at=FIXED_TIME)

        assert "source_type: browser" in result
        assert "url: https://blog.example.com/great-article" in result
        assert "title: A Great Article About AI" in result
        assert "domain: blog.example.com" in result

    def test_word_count_in_frontmatter(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(extracted_page, collected_at=FIXED_TIME)
        assert "word_count:" in result

    def test_query_in_frontmatter(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(
            extracted_page, query="AI trends 2024", collected_at=FIXED_TIME
        )
        assert "query: AI trends 2024" in result

    def test_tags_in_frontmatter(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(
            extracted_page, tags=["ai", "trends"], collected_at=FIXED_TIME
        )
        assert "tags:" in result
        assert "  - ai" in result
        assert "  - trends" in result

    def test_content_in_body(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(extracted_page, collected_at=FIXED_TIME)
        assert "artificial intelligence" in result

    def test_source_link(self, extracted_page: ExtractedPage) -> None:
        result = format_extracted_page(extracted_page, collected_at=FIXED_TIME)
        assert "Source: https://blog.example.com/great-article" in result


# ---------------------------------------------------------------------------
# source_filename / source_path
# ---------------------------------------------------------------------------


class TestSourceFilename:
    def test_basic_filename(self) -> None:
        result = source_filename("rss", "First Post", published_at=FIXED_TIME)
        assert result == "2024-01-15-first-post.md"

    def test_special_characters_removed(self) -> None:
        result = source_filename(
            "rss", "Hello: World! (Part 1)", published_at=FIXED_TIME
        )
        assert result == "2024-01-15-hello-world-part-1.md"

    def test_long_title_truncated(self) -> None:
        long_title = "a" * 200
        result = source_filename("rss", long_title, published_at=FIXED_TIME)
        # 10 (date) + 1 (dash) + MAX_SLUG_LENGTH + 3 (.md) = reasonable length
        assert len(result) <= 10 + 1 + 80 + 3 + 5  # generous bound

    def test_empty_title_fallback(self) -> None:
        result = source_filename("rss", "", published_at=FIXED_TIME)
        assert result == "2024-01-15-untitled.md"

    def test_collected_at_fallback(self) -> None:
        collected = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = source_filename("youtube", "My Video", collected_at=collected)
        assert result.startswith("2024-06-01-")

    def test_published_at_takes_priority(self) -> None:
        published = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        collected = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = source_filename(
            "rss", "Test", published_at=published, collected_at=collected
        )
        assert result.startswith("2024-01-01-")


class TestSourcePath:
    def test_rss_path(self) -> None:
        result = source_path("rss", "First Post", published_at=FIXED_TIME)
        assert result == Path("sources/rss/2024-01-15-first-post.md")

    def test_youtube_path(self) -> None:
        result = source_path("youtube", "My Video", published_at=FIXED_TIME)
        assert result == Path("sources/youtube/2024-01-15-my-video.md")

    def test_browser_path(self) -> None:
        result = source_path("browser", "Search Result", published_at=FIXED_TIME)
        assert result == Path("sources/browser/2024-01-15-search-result.md")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self) -> None:
        assert _slugify("Hello: World! (2024)") == "hello-world-2024"

    def test_multiple_spaces(self) -> None:
        assert _slugify("hello   world") == "hello-world"

    def test_unicode(self) -> None:
        # Korean characters should be preserved
        result = _slugify("AI 기술 동향")
        assert "ai" in result
        assert "기술" in result

    def test_empty(self) -> None:
        assert _slugify("") == ""

    def test_leading_trailing_hyphens(self) -> None:
        assert _slugify("---hello---") == "hello"


class TestStripHtml:
    def test_basic_tags(self) -> None:
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_nested_tags(self) -> None:
        assert _strip_html("<div><p><b>Bold</b> text</p></div>") == "Bold text"

    def test_entities(self) -> None:
        assert _strip_html("A &amp; B") == "A & B"
        assert _strip_html("&lt;script&gt;") == "<script>"

    def test_whitespace_normalization(self) -> None:
        assert _strip_html("<p>Hello</p>  \n  <p>World</p>") == "Hello World"


class TestYamlScalar:
    def test_simple_string(self) -> None:
        assert _yaml_scalar("hello") == "hello"

    def test_string_with_colon_space(self) -> None:
        assert _yaml_scalar("key: value") == '"key: value"'

    def test_boolean_like(self) -> None:
        assert _yaml_scalar("true") == '"true"'
        assert _yaml_scalar("false") == '"false"'

    def test_integer(self) -> None:
        assert _yaml_scalar(42) == "42"

    def test_none(self) -> None:
        assert _yaml_scalar(None) == "null"

    def test_bool(self) -> None:
        assert _yaml_scalar(True) == "true"
        assert _yaml_scalar(False) == "false"

    def test_empty_string_quoted(self) -> None:
        assert _yaml_scalar("") == '""'


class TestAssemble:
    def test_basic_assembly(self) -> None:
        fm = {"title": "Hello", "tags": ["a", "b"]}
        body = "# Hello\n\nContent here."
        result = _assemble(fm, body)

        assert result.startswith("---\n")
        assert "title: Hello" in result
        assert "tags:\n  - a\n  - b" in result
        assert result.endswith("Content here.\n")

    def test_double_fence(self) -> None:
        result = _assemble({"key": "val"}, "body")
        # Count "---" occurrences (should be exactly 2)
        fences = [line for line in result.split("\n") if line == "---"]
        assert len(fences) == 2

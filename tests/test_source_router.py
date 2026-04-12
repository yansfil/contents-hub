"""Tests for the source type routing module (source_router.py).

Covers:
- URL-based content/collector type detection
- Config-based routing overrides
- Custom patterns
- Source type hints
- Batch routing
- Fallback behavior
- SourceRoute properties and fetcher integration
"""

from __future__ import annotations

import pytest

from llm_wiki.source_router import (
    COLLECTOR_TYPES,
    CONTENT_TO_COLLECTOR,
    CustomRoutePattern,
    RoutingOverrides,
    SourceRoute,
    detect_content_type,
    detect_source_type,
    resolve_batch,
    resolve_source,
)


# ---------------------------------------------------------------------------
# URL-based detection: YouTube
# ---------------------------------------------------------------------------


class TestYouTubeDetection:
    def test_youtube_channel(self):
        route = resolve_source("https://www.youtube.com/channel/UCtest123")
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"
        assert route.matched_by == "builtin"

    def test_youtube_handle(self):
        route = resolve_source("https://www.youtube.com/@fireship")
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"

    def test_youtube_short_url(self):
        route = resolve_source("https://youtu.be/abc123")
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"

    def test_youtube_feed_url(self):
        route = resolve_source(
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
        )
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"

    def test_youtube_case_insensitive(self):
        route = resolve_source("https://WWW.YOUTUBE.COM/@Test")
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"


# ---------------------------------------------------------------------------
# URL-based detection: Twitter/X
# ---------------------------------------------------------------------------


class TestTwitterDetection:
    def test_twitter_profile(self):
        route = resolve_source("https://twitter.com/elonmusk")
        assert route.content_type == "twitter"
        assert route.collector_type == "twitter"

    def test_x_dot_com(self):
        route = resolve_source("https://x.com/OpenAI")
        assert route.content_type == "twitter"
        assert route.collector_type == "twitter"

    def test_twitter_status(self):
        route = resolve_source("https://x.com/user/status/123456789")
        assert route.content_type == "twitter"
        assert route.collector_type == "twitter"

    def test_nitter_mirror(self):
        route = resolve_source("https://nitter.net/user/rss")
        assert route.content_type == "twitter"
        assert route.collector_type == "twitter"


# ---------------------------------------------------------------------------
# URL-based detection: RSS-mapped types
# ---------------------------------------------------------------------------


class TestRSSMappedTypes:
    def test_substack(self):
        route = resolve_source("https://example.substack.com/feed")
        assert route.content_type == "substack"
        assert route.collector_type == "rss"

    def test_medium(self):
        route = resolve_source("https://medium.com/@user/article-slug")
        assert route.content_type == "medium"
        assert route.collector_type == "rss"

    def test_generic_rss_url(self):
        route = resolve_source("https://blog.example.com/feed.xml")
        assert route.content_type == "rss"
        assert route.collector_type == "rss"
        assert route.matched_by == "fallback_feed_pattern"


# ---------------------------------------------------------------------------
# URL-based detection: Browser-mapped types
# ---------------------------------------------------------------------------


class TestBrowserMappedTypes:
    def test_reddit(self):
        route = resolve_source("https://www.reddit.com/r/MachineLearning")
        assert route.content_type == "reddit"
        assert route.collector_type == "browser"

    def test_github(self):
        route = resolve_source("https://github.com/anthropics/claude-code")
        assert route.content_type == "github"
        assert route.collector_type == "browser"

    def test_arxiv(self):
        route = resolve_source("https://arxiv.org/abs/2401.00001")
        assert route.content_type == "arxiv"
        assert route.collector_type == "browser"

    def test_hackernews(self):
        route = resolve_source("https://news.ycombinator.com/item?id=123")
        assert route.content_type == "hackernews"
        assert route.collector_type == "browser"

    def test_linkedin(self):
        route = resolve_source("https://www.linkedin.com/in/johndoe")
        assert route.content_type == "linkedin"
        assert route.collector_type == "browser"


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestFallback:
    def test_unknown_http_url_defaults_to_browser(self):
        route = resolve_source("https://unknown-blog.example.com/posts")
        assert route.content_type == "webpage"
        assert route.collector_type == "browser"
        assert route.matched_by == "fallback"

    def test_non_http_defaults_to_browser(self):
        route = resolve_source("file:///path/to/local/page.html")
        assert route.content_type == "webpage"
        assert route.collector_type == "browser"
        assert route.matched_by == "fallback"

    def test_empty_string(self):
        route = resolve_source("")
        assert route.collector_type == "browser"
        assert route.matched_by == "fallback"


# ---------------------------------------------------------------------------
# Config overrides
# ---------------------------------------------------------------------------


class TestConfigOverrides:
    def test_domain_override_to_rss(self):
        overrides = RoutingOverrides(
            overrides={"newsletter.example.com": "rss"}
        )
        route = resolve_source(
            "https://newsletter.example.com/latest",
            overrides=overrides,
        )
        assert route.collector_type == "rss"
        assert route.matched_by == "config-override"

    def test_domain_override_to_browser(self):
        overrides = RoutingOverrides(
            overrides={"medium.com": "browser"}
        )
        # Medium would normally be RSS, but override changes it
        route = resolve_source(
            "https://medium.com/@user/article",
            overrides=overrides,
        )
        assert route.collector_type == "browser"
        assert route.matched_by == "config-override"

    def test_config_override_takes_priority_over_builtin(self):
        """Config overrides beat built-in rules."""
        overrides = RoutingOverrides(
            overrides={"youtube.com": "rss"}
        )
        route = resolve_source(
            "https://www.youtube.com/@test",
            overrides=overrides,
        )
        assert route.collector_type == "rss"
        assert route.matched_by == "config-override"


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestCustomPatterns:
    def test_custom_pattern_match(self):
        overrides = RoutingOverrides(
            custom_patterns=[
                CustomRoutePattern(
                    pattern="feeds.internal.corp",
                    collector="rss",
                    content_type="internal-feed",
                )
            ]
        )
        route = resolve_source(
            "https://feeds.internal.corp/engineering",
            overrides=overrides,
        )
        assert route.content_type == "internal-feed"
        assert route.collector_type == "rss"
        assert route.matched_by == "config-pattern"

    def test_custom_pattern_default_content_type(self):
        """When content_type is omitted, defaults to collector type."""
        overrides = RoutingOverrides(
            custom_patterns=[
                CustomRoutePattern(
                    pattern="special.example.com",
                    collector="youtube",
                )
            ]
        )
        route = resolve_source(
            "https://special.example.com/video",
            overrides=overrides,
        )
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"

    def test_override_before_custom_pattern(self):
        """Domain overrides take priority over custom patterns."""
        overrides = RoutingOverrides(
            overrides={"special.example.com": "twitter"},
            custom_patterns=[
                CustomRoutePattern(
                    pattern="special.example.com",
                    collector="rss",
                )
            ],
        )
        route = resolve_source(
            "https://special.example.com/feed",
            overrides=overrides,
        )
        # Override wins over custom pattern
        assert route.collector_type == "twitter"
        assert route.matched_by == "config-override"


# ---------------------------------------------------------------------------
# Source type hints
# ---------------------------------------------------------------------------


class TestSourceTypeHint:
    def test_collector_type_hint(self):
        route = resolve_source(
            "https://anything.example.com",
            source_type_hint="youtube",
        )
        assert route.content_type == "youtube"
        assert route.collector_type == "youtube"
        assert route.matched_by == "hint"

    def test_content_type_hint(self):
        route = resolve_source(
            "https://anything.example.com",
            source_type_hint="substack",
        )
        assert route.content_type == "substack"
        assert route.collector_type == "rss"
        assert route.matched_by == "hint"

    def test_hint_takes_highest_priority(self):
        """Hint beats both config overrides and built-in rules."""
        overrides = RoutingOverrides(
            overrides={"youtube.com": "rss"}
        )
        route = resolve_source(
            "https://www.youtube.com/@test",
            overrides=overrides,
            source_type_hint="twitter",
        )
        assert route.collector_type == "twitter"
        assert route.matched_by == "hint"

    def test_unknown_hint_fallback(self):
        route = resolve_source(
            "https://example.com",
            source_type_hint="exotic-type",
        )
        assert route.content_type == "exotic-type"
        assert route.collector_type == "browser"
        assert route.matched_by == "hint-fallback"


# ---------------------------------------------------------------------------
# SourceRoute properties
# ---------------------------------------------------------------------------


class TestSourceRouteProperties:
    def test_is_manual_for_browser(self):
        route = resolve_source("https://github.com/user/repo")
        assert route.is_manual is True

    def test_is_not_manual_for_rss(self):
        route = resolve_source("https://blog.example.com/feed")
        assert route.is_manual is False

    def test_is_feed_based(self):
        route = resolve_source("https://example.substack.com/feed")
        assert route.is_feed_based is True

    def test_is_not_feed_based_for_youtube(self):
        route = resolve_source("https://youtube.com/@test")
        assert route.is_feed_based is False

    def test_default_intervals(self):
        rss_route = resolve_source("https://example.substack.com/feed")
        assert rss_route.default_interval_minutes == 30

        yt_route = resolve_source("https://youtube.com/@test")
        assert yt_route.default_interval_minutes == 60

        tw_route = resolve_source("https://x.com/user")
        assert tw_route.default_interval_minutes == 15

        br_route = resolve_source("https://github.com/user/repo")
        assert br_route.default_interval_minutes == 0


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_detect_source_type_returns_collector(self):
        """detect_source_type returns collector_type, not content_type."""
        assert detect_source_type("https://example.substack.com/feed") == "rss"
        assert detect_source_type("https://youtube.com/@test") == "youtube"
        assert detect_source_type("https://x.com/user") == "twitter"
        assert detect_source_type("https://github.com/user/repo") == "browser"
        # Unknown HTTP URLs now default to browser (auto-detects RSS via BrowserFetcher)
        assert detect_source_type("https://unknown.example.com") == "browser"
        assert detect_source_type("https://example.com/article") == "browser"
        # Feed-like URLs still route to RSS
        assert detect_source_type("https://example.com/feed.xml") == "rss"

    def test_detect_content_type_returns_content(self):
        """detect_content_type returns the granular content_type."""
        assert detect_content_type("https://example.substack.com/feed") == "substack"
        assert detect_content_type("https://youtube.com/@test") == "youtube"
        assert detect_content_type("https://arxiv.org/abs/2401.00001") == "arxiv"
        assert detect_content_type("https://github.com/user/repo") == "github"
        assert detect_content_type("https://example.com/article") == "webpage"


# ---------------------------------------------------------------------------
# Batch routing
# ---------------------------------------------------------------------------


class TestBatchRouting:
    def test_groups_by_collector_type(self):
        urls = [
            "https://blog1.example.com/feed.xml",
            "https://blog2.example.com/feed.xml",
            "https://www.youtube.com/@channel1",
            "https://x.com/user1",
            "https://github.com/user/repo",
        ]
        groups = resolve_batch(urls)

        assert "rss" in groups
        assert len(groups["rss"]) == 2

        assert "youtube" in groups
        assert len(groups["youtube"]) == 1

        assert "twitter" in groups
        assert len(groups["twitter"]) == 1

        assert "browser" in groups
        assert len(groups["browser"]) == 1

    def test_batch_with_overrides(self):
        overrides = RoutingOverrides(
            overrides={"blog2.example.com": "browser"}
        )
        urls = [
            "https://blog1.example.com/feed.xml",
            "https://blog2.example.com/feed.xml",
        ]
        groups = resolve_batch(urls, overrides=overrides)

        assert len(groups["rss"]) == 1
        assert len(groups["browser"]) == 1

    def test_batch_empty_list(self):
        groups = resolve_batch([])
        assert groups == {}


# ---------------------------------------------------------------------------
# RoutingOverrides.from_dict
# ---------------------------------------------------------------------------


class TestRoutingOverridesFromDict:
    def test_valid_overrides(self):
        data = {
            "overrides": {
                "blog.example.com": "rss",
                "video.example.com": "youtube",
            }
        }
        result = RoutingOverrides.from_dict(data)
        assert result.overrides == {
            "blog.example.com": "rss",
            "video.example.com": "youtube",
        }

    def test_invalid_collector_ignored(self):
        data = {
            "overrides": {
                "blog.example.com": "invalid_collector",
            }
        }
        result = RoutingOverrides.from_dict(data)
        assert result.overrides == {}

    def test_custom_patterns(self):
        data = {
            "custom_patterns": [
                {
                    "pattern": "feeds.corp.com",
                    "collector": "rss",
                    "content_type": "internal",
                },
            ]
        }
        result = RoutingOverrides.from_dict(data)
        assert len(result.custom_patterns) == 1
        assert result.custom_patterns[0].pattern == "feeds.corp.com"
        assert result.custom_patterns[0].collector == "rss"
        assert result.custom_patterns[0].content_type == "internal"

    def test_empty_dict(self):
        result = RoutingOverrides.from_dict({})
        assert result.overrides == {}
        assert result.custom_patterns == []


# ---------------------------------------------------------------------------
# Backward compatibility with dispatch.detect_source_type
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure the new router produces the same results as the old
    dispatch.detect_source_type for all tested cases in test_dispatch.py."""

    def test_rss_default(self):
        assert detect_source_type("https://example.com/feed.xml") == "rss"

    def test_youtube(self):
        assert detect_source_type(
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
        ) == "youtube"

    def test_youtube_short(self):
        assert detect_source_type("https://youtu.be/abc123") == "youtube"

    def test_twitter(self):
        assert detect_source_type("https://twitter.com/user/status/123") == "twitter"

    def test_x_dot_com(self):
        assert detect_source_type("https://x.com/user") == "twitter"

    def test_nitter(self):
        assert detect_source_type("https://nitter.net/user/rss") == "twitter"

    def test_substack_is_rss(self):
        """Substack should map to RSS collector (same as old behavior)."""
        assert detect_source_type("https://example.substack.com/feed") == "rss"

    def test_non_http_is_browser(self):
        assert detect_source_type("file:///local/page.html") == "browser"


# ---------------------------------------------------------------------------
# Content type mapping table
# ---------------------------------------------------------------------------


class TestContentToCollectorMapping:
    def test_all_content_types_have_valid_collectors(self):
        """Every content type must map to a valid collector type."""
        for content_type, collector_type in CONTENT_TO_COLLECTOR.items():
            assert collector_type in COLLECTOR_TYPES, (
                f"Content type '{content_type}' maps to invalid "
                f"collector '{collector_type}'"
            )

    def test_collector_types_are_subset(self):
        """Collector types should be a small, stable set."""
        assert COLLECTOR_TYPES == {"rss", "youtube", "twitter", "browser"}

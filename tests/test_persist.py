"""Tests for persist.py — persistence orchestrator."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.agent_results import (
    AgentReport,
    AgentStatus,
    CollectedItem,
    ItemStatus,
    SourceType,
)
from llm_wiki.config import WikiConfig
from llm_wiki.persist import (
    _format_item,
    _format_generic_item,
    persist_agent_reports,
    persist_item,
    persist_report,
)
from llm_wiki.scheduler import SeenURLTracker
from llm_wiki.writer import WriteAction, ensure_vault_structure


@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """Create a temporary vault directory with standard structure."""
    config = WikiConfig(vault_path=tmp_path)
    ensure_vault_structure(config)
    return config


@pytest.fixture
def sample_rss_item() -> CollectedItem:
    return CollectedItem(
        url="https://example.com/post-1",
        title="First Post",
        source_type=SourceType.RSS,
        content="This is the first blog post content.",
        author="Alice",
        published_at=datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
        collected_at=datetime(2024, 6, 16, 8, 0, tzinfo=timezone.utc),
        tags=["tech", "python"],
        lenses=["programming"],
        metadata={"feed_title": "Alice's Blog"},
    )


@pytest.fixture
def sample_youtube_item() -> CollectedItem:
    return CollectedItem(
        url="https://youtube.com/watch?v=abc123",
        title="Deep Learning Tutorial",
        source_type=SourceType.YOUTUBE,
        content="In this video we explore deep learning...",
        author="TechChannel",
        published_at=datetime(2024, 6, 10, tzinfo=timezone.utc),
        tags=["ai", "tutorial"],
        lenses=["machine-learning"],
        metadata={
            "video_id": "abc123",
            "channel_title": "TechChannel",
            "thumbnail_url": "https://img.youtube.com/vi/abc123/hqdefault.jpg",
            "views": 50000,
        },
    )


@pytest.fixture
def sample_browser_item() -> CollectedItem:
    return CollectedItem(
        url="https://docs.python.org/3/tutorial/",
        title="Python Tutorial",
        source_type=SourceType.WEBPAGE,
        content="An informal introduction to Python...",
        tags=["python", "tutorial"],
        lenses=["programming"],
        metadata={
            "domain": "docs.python.org",
            "word_count": 2500,
            "query": "python tutorial",
        },
    )


# ---------------------------------------------------------------------------
# Formatter routing tests
# ---------------------------------------------------------------------------


class TestFormatItem:
    def test_rss_format(self, sample_rss_item: CollectedItem):
        md = _format_item(sample_rss_item)
        assert "---" in md
        assert "source_type: rss" in md
        assert "First Post" in md
        assert sample_rss_item.url in md

    def test_youtube_format(self, sample_youtube_item: CollectedItem):
        md = _format_item(sample_youtube_item)
        assert "source_type: youtube" in md
        assert "Deep Learning Tutorial" in md
        assert "abc123" in md  # video_id

    def test_browser_format(self, sample_browser_item: CollectedItem):
        md = _format_item(sample_browser_item)
        # format_extracted_page writes the legacy 'browser' literal in the
        # frontmatter regardless of whether the enum is WEBPAGE or BROWSER.
        assert "source_type: browser" in md
        assert "Python Tutorial" in md

    def test_twitter_uses_generic(self):
        item = CollectedItem(
            url="https://twitter.com/user/status/123",
            title="A tweet",
            source_type=SourceType.TWITTER,
            content="Hello twitter!",
        )
        md = _format_item(item)
        assert "source_type: twitter" in md
        assert "A tweet" in md

    def test_generic_format(self):
        item = CollectedItem(
            url="https://example.com",
            title="Generic Page",
            source_type=SourceType.WEBPAGE,
            content="Some content",
            tags=["test"],
            lenses=["research"],
        )
        md = _format_generic_item(item)
        assert "---" in md
        assert "source_type: webpage" in md
        assert "Generic Page" in md
        assert "test" in md


# ---------------------------------------------------------------------------
# persist_item tests
# ---------------------------------------------------------------------------


class TestPersistItem:
    def test_persist_creates_file(
        self, vault: WikiConfig, sample_rss_item: CollectedItem
    ):
        result = persist_item(vault, sample_rss_item)
        assert result is not None
        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert result.bytes_written > 0

        # Verify content
        content = result.path.read_text()
        assert "source_type: rss" in content
        assert "First Post" in content

    def test_persist_skips_non_ok(self, vault: WikiConfig):
        item = CollectedItem(
            url="https://example.com/fail",
            title="Failed",
            source_type=SourceType.RSS,
            status=ItemStatus.ERROR,
        )
        result = persist_item(vault, item)
        assert result is None

    def test_persist_dedup_with_seen_tracker(
        self, vault: WikiConfig, sample_rss_item: CollectedItem
    ):
        seen = SeenURLTracker(vault)

        # First persist — should create
        r1 = persist_item(vault, sample_rss_item, seen=seen)
        assert r1 is not None
        assert r1.action == WriteAction.CREATED

        # Second persist — should be deduped by seen tracker
        r2 = persist_item(vault, sample_rss_item, seen=seen)
        assert r2 is None  # already seen

    def test_persist_youtube(
        self, vault: WikiConfig, sample_youtube_item: CollectedItem
    ):
        result = persist_item(vault, sample_youtube_item)
        assert result is not None
        assert result.action == WriteAction.CREATED
        content = result.path.read_text()
        assert "youtube" in content

    def test_persist_browser(
        self, vault: WikiConfig, sample_browser_item: CollectedItem
    ):
        result = persist_item(vault, sample_browser_item)
        assert result is not None
        assert result.action == WriteAction.CREATED


# ---------------------------------------------------------------------------
# persist_report tests
# ---------------------------------------------------------------------------


class TestPersistReport:
    def test_persist_report_writes_files(
        self, vault: WikiConfig, sample_rss_item: CollectedItem
    ):
        # We need the schedules table — init db
        from llm_wiki.db import init_db
        from llm_wiki.dispatch import dispatch_subscription
        from llm_wiki.subscriptions import Subscription

        init_db(vault)
        sub = Subscription(
            url=sample_rss_item.url,  # use any url; dispatch key
            title="Test Feed",
        )
        # We need to register a schedule so record_run_result works
        # Actually record_run_result just warns if no schedule, so skip

        report = AgentReport(
            source_type=SourceType.RSS,
            subscription_url="https://example.com/feed.xml",
            items=[sample_rss_item],
            feed_title="Test Feed",
        )

        results = persist_report(vault, report)
        assert len(results) == 1
        assert results[0].action == WriteAction.CREATED

    def test_persist_failed_report(self, vault: WikiConfig):
        from llm_wiki.db import init_db

        init_db(vault)

        report = AgentReport.failure(
            SourceType.RSS,
            "https://example.com/broken",
            "Connection refused",
        )
        results = persist_report(vault, report)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# persist_agent_reports (integration)
# ---------------------------------------------------------------------------


class TestPersistAgentReports:
    def test_full_flow(self, vault: WikiConfig):
        from llm_wiki.db import init_db

        init_db(vault)

        reports = [
            AgentReport(
                source_type=SourceType.RSS,
                subscription_url="https://blog.example.com/feed",
                items=[
                    CollectedItem(
                        url="https://blog.example.com/1",
                        title="Blog Post 1",
                        source_type=SourceType.RSS,
                        content="Content 1",
                    ),
                    CollectedItem(
                        url="https://blog.example.com/2",
                        title="Blog Post 2",
                        source_type=SourceType.RSS,
                        content="Content 2",
                    ),
                ],
                feed_title="Example Blog",
            ),
            AgentReport(
                source_type=SourceType.YOUTUBE,
                subscription_url="https://youtube.com/channel/test",
                items=[
                    CollectedItem(
                        url="https://youtube.com/watch?v=xyz",
                        title="Video 1",
                        source_type=SourceType.YOUTUBE,
                        content="Video description",
                        metadata={"video_id": "xyz"},
                    ),
                ],
                feed_title="Test Channel",
            ),
        ]

        tick_result = persist_agent_reports(vault, reports)

        assert tick_result.total_new == 3
        assert tick_result.total_errors == 0
        assert len(tick_result.source_files_created) == 3
        assert tick_result.is_ok

        # Verify files exist on disk
        for rel_path in tick_result.source_files_created:
            abs_path = vault.vault_path / rel_path
            assert abs_path.exists(), f"Missing: {rel_path}"

    def test_mixed_success_and_failure(self, vault: WikiConfig):
        from llm_wiki.db import init_db

        init_db(vault)

        reports = [
            AgentReport(
                source_type=SourceType.RSS,
                subscription_url="https://ok.example.com/feed",
                items=[
                    CollectedItem(
                        url="https://ok.example.com/1",
                        title="OK Post",
                        source_type=SourceType.RSS,
                    ),
                ],
            ),
            AgentReport.failure(
                SourceType.TWITTER,
                "https://twitter.com/fail",
                "Rate limited",
            ),
        ]

        tick = persist_agent_reports(vault, reports)
        assert tick.total_new == 1
        assert tick.is_ok  # partial success
        assert len(tick.reports) == 2

    def test_summary_table(self, vault: WikiConfig):
        from llm_wiki.db import init_db

        init_db(vault)

        reports = [
            AgentReport(
                source_type=SourceType.RSS,
                subscription_url="https://example.com/feed",
                items=[
                    CollectedItem(
                        url=f"https://example.com/{i}",
                        title=f"Post {i}",
                        source_type=SourceType.RSS,
                    )
                    for i in range(5)
                ],
            ),
        ]
        tick = persist_agent_reports(vault, reports)
        table = tick.summary_table()
        assert "rss" in table
        assert "**5**" in table  # total new

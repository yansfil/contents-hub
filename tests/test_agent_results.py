"""Tests for agent_results.py — typed result models."""

import json
from datetime import datetime, timezone

import pytest

from llm_wiki.agent_results import (
    AgentReport,
    AgentStatus,
    CollectedItem,
    ItemStatus,
    SourceType,
    TickResult,
)


# ---------------------------------------------------------------------------
# CollectedItem
# ---------------------------------------------------------------------------


class TestCollectedItem:
    def test_basic_creation(self):
        item = CollectedItem(
            url="https://example.com/post",
            title="Test Post",
            source_type=SourceType.RSS,
            content="Hello world",
        )
        assert item.url == "https://example.com/post"
        assert item.title == "Test Post"
        assert item.source_type == SourceType.RSS
        assert item.is_ok
        assert item.collected_at is not None

    def test_default_collected_at(self):
        item = CollectedItem(
            url="https://example.com",
            title="Test",
            source_type=SourceType.RSS,
        )
        assert item.collected_at is not None
        # Should be approximately now
        delta = (datetime.now(timezone.utc) - item.collected_at).total_seconds()
        assert delta < 5

    def test_error_item(self):
        item = CollectedItem(
            url="https://example.com/fail",
            title="Failed",
            source_type=SourceType.RSS,
            status=ItemStatus.ERROR,
            error="404 Not Found",
        )
        assert not item.is_ok
        assert item.error == "404 Not Found"

    def test_round_trip_dict(self):
        original = CollectedItem(
            url="https://example.com/post",
            title="Test Post",
            source_type=SourceType.YOUTUBE,
            content="Video content",
            author="Author",
            published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            collected_at=datetime(2024, 1, 16, tzinfo=timezone.utc),
            tags=["tech", "ai"],
            lenses=["machine-learning"],
            metadata={"video_id": "abc123", "views": 1000},
        )
        d = original.to_dict()
        restored = CollectedItem.from_dict(d)

        assert restored.url == original.url
        assert restored.title == original.title
        assert restored.source_type == original.source_type
        assert restored.author == original.author
        assert restored.tags == original.tags
        assert restored.lenses == original.lenses
        assert restored.metadata == original.metadata

    def test_round_trip_json(self):
        item = CollectedItem(
            url="https://example.com",
            title="Test",
            source_type=SourceType.WEBPAGE,
            tags=["search"],
        )
        json_str = json.dumps(item.to_dict())
        restored = CollectedItem.from_dict(json.loads(json_str))
        assert restored.url == item.url
        assert restored.source_type == SourceType.WEBPAGE


# ---------------------------------------------------------------------------
# AgentReport
# ---------------------------------------------------------------------------


class TestAgentReport:
    def _make_items(self, count: int = 3, errors: int = 0) -> list[CollectedItem]:
        items = []
        for i in range(count):
            status = ItemStatus.ERROR if i < errors else ItemStatus.OK
            items.append(
                CollectedItem(
                    url=f"https://example.com/item-{i}",
                    title=f"Item {i}",
                    source_type=SourceType.RSS,
                    status=status,
                    error="fail" if status == ItemStatus.ERROR else "",
                )
            )
        return items

    def test_auto_counts(self):
        items = self._make_items(5, errors=2)
        report = AgentReport(
            source_type=SourceType.RSS,
            subscription_url="https://example.com/feed.xml",
            items=items,
        )
        assert report.new_count == 3
        assert report.error_count == 2
        assert report.status == AgentStatus.PARTIAL

    def test_all_ok(self):
        items = self._make_items(3, errors=0)
        report = AgentReport(
            source_type=SourceType.RSS,
            subscription_url="https://example.com/feed.xml",
            items=items,
        )
        assert report.status == AgentStatus.SUCCESS
        assert report.is_ok
        assert len(report.ok_items) == 3

    def test_all_errors(self):
        items = self._make_items(3, errors=3)
        report = AgentReport(
            source_type=SourceType.RSS,
            subscription_url="https://example.com/feed.xml",
            items=items,
        )
        assert report.status == AgentStatus.FAILURE
        assert not report.is_ok
        assert len(report.ok_items) == 0

    def test_failure_factory(self):
        report = AgentReport.failure(
            SourceType.YOUTUBE,
            "https://youtube.com/channel/123",
            "Connection timeout",
        )
        assert report.status == AgentStatus.FAILURE
        assert report.error == "Connection timeout"
        assert not report.is_ok

    def test_round_trip_dict(self):
        items = self._make_items(2)
        original = AgentReport(
            source_type=SourceType.YOUTUBE,
            subscription_url="https://youtube.com/feed",
            items=items,
            feed_title="Tech Channel",
            duration_seconds=1.5,
        )
        d = original.to_dict()
        restored = AgentReport.from_dict(d)

        assert restored.source_type == original.source_type
        assert restored.subscription_url == original.subscription_url
        assert restored.feed_title == original.feed_title
        assert len(restored.items) == 2

    def test_from_json(self):
        data = {
            "source_type": "rss",
            "subscription_url": "https://example.com/feed",
            "status": "success",
            "items": [
                {
                    "url": "https://example.com/1",
                    "title": "Post 1",
                    "source_type": "rss",
                    "content": "Hello",
                    "status": "ok",
                }
            ],
            "new_count": 1,
            "feed_title": "Example Blog",
        }
        report = AgentReport.from_json(json.dumps(data))
        assert report.source_type == SourceType.RSS
        assert len(report.items) == 1
        assert report.items[0].title == "Post 1"


# ---------------------------------------------------------------------------
# TickResult
# ---------------------------------------------------------------------------


class TestTickResult:
    def test_add_report(self):
        tick = TickResult()
        report = AgentReport(
            source_type=SourceType.RSS,
            subscription_url="https://example.com/feed",
            items=[
                CollectedItem(
                    url="https://example.com/1",
                    title="Post",
                    source_type=SourceType.RSS,
                )
            ],
        )
        tick.add_report(report)

        assert tick.total_new == 1
        assert len(tick.reports) == 1

    def test_summary_table(self):
        tick = TickResult()
        tick.add_report(
            AgentReport(
                source_type=SourceType.RSS,
                subscription_url="https://example.com/feed",
                items=[
                    CollectedItem(
                        url=f"https://example.com/{i}",
                        title=f"Post {i}",
                        source_type=SourceType.RSS,
                    )
                    for i in range(3)
                ],
            )
        )
        table = tick.summary_table()
        assert "rss" in table
        assert "Total" in table

    def test_empty_tick(self):
        tick = TickResult()
        assert not tick.is_ok
        assert tick.total_new == 0
        assert tick.source_types_collected == []

    def test_source_types_collected(self):
        tick = TickResult()
        tick.add_report(
            AgentReport(
                source_type=SourceType.RSS,
                subscription_url="url1",
                items=[
                    CollectedItem(url="u1", title="t", source_type=SourceType.RSS)
                ],
            )
        )
        tick.add_report(
            AgentReport.failure(SourceType.YOUTUBE, "url2", "timeout")
        )
        assert tick.source_types_collected == ["rss"]

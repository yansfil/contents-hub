"""Tests for the dispatch module (subscription → schedule registration)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_wiki.config import ScheduleConfig, WikiConfig
from llm_wiki.db import init_db
from llm_wiki.dispatch import (
    Schedule,
    detect_source_type,
    default_interval,
    dispatch_subscription,
    get_due_schedules,
    get_schedule,
    list_schedules,
    pause_schedule,
    record_run_result,
    resume_schedule,
    undispatch_subscription,
    update_schedule,
)
from llm_wiki.subscriptions import Subscription, SubscriptionStore


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
# Source type detection
# ---------------------------------------------------------------------------


class TestDetectSourceType:
    def test_rss_default(self):
        assert detect_source_type("https://example.com/feed.xml") == "rss"

    def test_youtube(self):
        assert detect_source_type("https://www.youtube.com/feeds/videos.xml?channel_id=UC123") == "youtube"

    def test_youtube_short(self):
        assert detect_source_type("https://youtu.be/abc123") == "youtube"

    def test_twitter(self):
        assert detect_source_type("https://twitter.com/user/status/123") == "twitter"

    def test_x_dot_com(self):
        assert detect_source_type("https://x.com/user") == "twitter"

    def test_nitter(self):
        assert detect_source_type("https://nitter.net/user/rss") == "twitter"

    def test_substack_is_rss(self):
        assert detect_source_type("https://example.substack.com/feed") == "rss"


class TestDefaultInterval:
    def test_rss(self):
        assert default_interval("rss") == 30

    def test_youtube(self):
        assert default_interval("youtube") == 60

    def test_twitter(self):
        assert default_interval("twitter") == 15

    def test_browser_manual(self):
        assert default_interval("browser") == 0

    def test_unknown_falls_back(self):
        assert default_interval("unknown") == 30


# ---------------------------------------------------------------------------
# Core dispatch
# ---------------------------------------------------------------------------


class TestDispatchSubscription:
    def test_creates_schedule_for_rss(self, config: WikiConfig):
        sub = Subscription(url="https://blog.example.com/feed.xml", title="Blog")
        schedule = dispatch_subscription(sub, config)

        assert schedule.subscription_url == sub.url
        assert schedule.source_type == "rss"
        assert schedule.interval_minutes == 30
        assert schedule.enabled is True
        assert schedule.next_run_at is not None
        assert schedule.id > 0

    def test_creates_schedule_for_youtube(self, config: WikiConfig):
        sub = Subscription(url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
        schedule = dispatch_subscription(sub, config)

        assert schedule.source_type == "youtube"
        assert schedule.interval_minutes == 60

    def test_creates_schedule_for_twitter(self, config: WikiConfig):
        sub = Subscription(url="https://x.com/elonmusk")
        schedule = dispatch_subscription(sub, config)

        assert schedule.source_type == "twitter"
        assert schedule.interval_minutes == 15

    def test_custom_interval_override(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config, interval_minutes=120)

        assert schedule.interval_minutes == 120

    def test_cron_expression(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config, cron_expr="*/15 * * * *")

        assert schedule.cron_expr == "*/15 * * * *"

    def test_idempotent_update(self, config: WikiConfig):
        """Dispatching the same URL twice updates rather than duplicates."""
        sub = Subscription(url="https://example.com/feed.xml")
        s1 = dispatch_subscription(sub, config, interval_minutes=30)
        s2 = dispatch_subscription(sub, config, interval_minutes=60)

        assert s1.id == s2.id
        assert s2.interval_minutes == 60

    def test_next_run_is_immediate_for_new(self, config: WikiConfig):
        """New subscriptions should be collected immediately."""
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config)

        assert schedule.next_run_at is not None
        assert schedule.next_run_at >= before

    def test_browser_source_disabled_by_default(self, config: WikiConfig):
        """Browser sources (manual) should have enabled=False."""
        sub = Subscription(url="file:///local/page.html")
        schedule = dispatch_subscription(sub, config)

        assert schedule.source_type == "browser"
        assert schedule.interval_minutes == 0
        assert schedule.enabled is False


# ---------------------------------------------------------------------------
# Undispatch
# ---------------------------------------------------------------------------


class TestUndispatchSubscription:
    def test_removes_schedule(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        removed = undispatch_subscription(sub.url, config)
        assert removed is True

        assert get_schedule(sub.url, config) is None

    def test_returns_false_for_missing(self, config: WikiConfig):
        removed = undispatch_subscription("https://nonexistent.com/feed", config)
        assert removed is False


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_pause(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        paused = pause_schedule(sub.url, config)
        assert paused is True

        schedule = get_schedule(sub.url, config)
        assert schedule is not None
        assert schedule.enabled is False

    def test_resume(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)
        pause_schedule(sub.url, config)

        resumed = resume_schedule(sub.url, config)
        assert resumed is True

        schedule = get_schedule(sub.url, config)
        assert schedule is not None
        assert schedule.enabled is True


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestGetDueSchedules:
    def test_returns_due_schedules(self, config: WikiConfig):
        s1 = Subscription(url="https://blog1.com/feed.xml")
        s2 = Subscription(url="https://blog2.com/feed.xml")
        dispatch_subscription(s1, config)
        dispatch_subscription(s2, config)

        # Both should be due immediately
        due = get_due_schedules(config)
        assert len(due) == 2

    def test_excludes_paused(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)
        pause_schedule(sub.url, config)

        due = get_due_schedules(config)
        assert len(due) == 0

    def test_excludes_future(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        # Check with a time in the past
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        due = get_due_schedules(config, now=past)
        assert len(due) == 0


class TestListSchedules:
    def test_lists_all(self, config: WikiConfig):
        for i in range(3):
            sub = Subscription(url=f"https://blog{i}.com/feed.xml")
            dispatch_subscription(sub, config)

        schedules = list_schedules(config)
        assert len(schedules) == 3


# ---------------------------------------------------------------------------
# Run result recording
# ---------------------------------------------------------------------------


class TestRecordRunResult:
    def test_success_advances_next_run(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config)
        original_next = schedule.next_run_at

        record_run_result(sub.url, config, ok=True, new_items=5)

        updated = get_schedule(sub.url, config)
        assert updated is not None
        assert updated.last_run_ok is True
        assert updated.consecutive_errors == 0
        # next_run should be ~30 min from now (not from original)
        assert updated.next_run_at > original_next

    def test_error_increments_consecutive(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        record_run_result(sub.url, config, ok=False, error="timeout")

        updated = get_schedule(sub.url, config)
        assert updated is not None
        assert updated.last_run_ok is False
        assert updated.consecutive_errors == 1
        assert updated.last_error == "timeout"

    def test_backoff_increases_on_consecutive_errors(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        # First error: +5 min backoff
        record_run_result(sub.url, config, ok=False, error="err1")
        s1 = get_schedule(sub.url, config)

        # Second error: +30 min backoff
        record_run_result(sub.url, config, ok=False, error="err2")
        s2 = get_schedule(sub.url, config)

        assert s2.consecutive_errors == 2
        # Second backoff should push next_run further out
        assert s2.next_run_at > s1.next_run_at

    def test_success_resets_errors(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        record_run_result(sub.url, config, ok=False, error="err")
        record_run_result(sub.url, config, ok=True, new_items=3)

        updated = get_schedule(sub.url, config)
        assert updated.consecutive_errors == 0
        assert updated.last_error == ""


# ---------------------------------------------------------------------------
# Update schedule (interval / cron)
# ---------------------------------------------------------------------------


class TestUpdateSchedule:
    def test_update_interval(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        updated = update_schedule(sub.url, config, interval_minutes=120)
        assert updated is not None
        assert updated.interval_minutes == 120

    def test_update_cron(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        updated = update_schedule(sub.url, config, cron_expr="*/10 * * * *")
        assert updated is not None
        assert updated.cron_expr == "*/10 * * * *"

    def test_clear_cron(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config, cron_expr="*/15 * * * *")

        updated = update_schedule(sub.url, config, cron_expr=None)
        assert updated is not None
        assert updated.cron_expr is None

    def test_returns_none_for_missing(self, config: WikiConfig):
        result = update_schedule("https://nonexistent.com/feed", config, interval_minutes=60)
        assert result is None

    def test_zero_interval_disables(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)

        updated = update_schedule(sub.url, config, interval_minutes=0)
        assert updated is not None
        assert updated.enabled is False

    def test_positive_interval_enables(self, config: WikiConfig):
        sub = Subscription(url="https://example.com/feed.xml")
        dispatch_subscription(sub, config)
        pause_schedule(sub.url, config)

        updated = update_schedule(sub.url, config, interval_minutes=45)
        assert updated is not None
        assert updated.enabled is True
        assert updated.interval_minutes == 45


# ---------------------------------------------------------------------------
# Config-aware defaults
# ---------------------------------------------------------------------------


class TestConfigAwareDefaults:
    def test_builtin_defaults_without_config(self):
        assert default_interval("rss") == 30
        assert default_interval("youtube") == 60

    def test_config_override_per_type(self, vault: Path):
        schedule_cfg = ScheduleConfig(defaults={"rss": 10, "youtube": 120, "twitter": 15, "browser": 0})
        config = WikiConfig(vault_path=vault, schedule=schedule_cfg)

        assert default_interval("rss", config) == 10
        assert default_interval("youtube", config) == 120

    def test_global_interval_overrides_all(self, vault: Path):
        schedule_cfg = ScheduleConfig(global_interval=5)
        config = WikiConfig(vault_path=vault, schedule=schedule_cfg)

        assert default_interval("rss", config) == 5
        assert default_interval("youtube", config) == 5
        assert default_interval("twitter", config) == 5

    def test_dispatch_uses_config_defaults(self, vault: Path):
        schedule_cfg = ScheduleConfig(defaults={"rss": 10, "youtube": 60, "twitter": 15, "browser": 0})
        config = WikiConfig(vault_path=vault, schedule=schedule_cfg)

        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config)

        assert schedule.interval_minutes == 10

    def test_dispatch_uses_global_cron(self, vault: Path):
        schedule_cfg = ScheduleConfig(global_cron="0 * * * *")
        config = WikiConfig(vault_path=vault, schedule=schedule_cfg)

        sub = Subscription(url="https://example.com/feed.xml")
        schedule = dispatch_subscription(sub, config)

        assert schedule.cron_expr == "0 * * * *"

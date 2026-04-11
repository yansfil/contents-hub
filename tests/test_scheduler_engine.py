"""Tests for the cron scheduler engine (tick logic)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_wiki.config import ScheduleConfig, WikiConfig
from llm_wiki.dispatch import (
    dispatch_subscription,
    get_schedule,
    pause_schedule,
    record_run_result,
)
from llm_wiki.scheduler_engine import (
    BACKOFF_MINUTES,
    MAX_CONSECUTIVE_ERRORS,
    CollectionOutcome,
    DueSubscription,
    SchedulerEngine,
    TickResult,
    TickRunResult,
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


@pytest.fixture
def engine(config: WikiConfig) -> SchedulerEngine:
    return SchedulerEngine(config)


def _add_and_dispatch(
    store: SubscriptionStore,
    config: WikiConfig,
    url: str,
    title: str = "",
    **dispatch_kwargs,
) -> Subscription:
    """Helper: add a subscription and dispatch it."""
    sub = store.add(url, title=title or url)
    dispatch_subscription(sub, config, **dispatch_kwargs)
    return sub


# ---------------------------------------------------------------------------
# Tick: empty state
# ---------------------------------------------------------------------------


class TestTickEmpty:
    def test_no_schedules(self, engine: SchedulerEngine):
        result = engine.tick()
        assert result.is_idle is True
        assert result.due_count == 0
        assert result.total_schedules == 0

    def test_dispatch_plan_empty(self, engine: SchedulerEngine):
        result = engine.tick()
        plan = result.to_dispatch_plan()
        assert plan["status"] == "idle"
        assert plan["due_count"] == 0
        assert plan["dispatch"] == {}


# ---------------------------------------------------------------------------
# Tick: due evaluation
# ---------------------------------------------------------------------------


class TestTickDue:
    def test_new_subscription_is_immediately_due(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """Newly dispatched subscriptions have next_run_at = now → immediately due."""
        _add_and_dispatch(store, config, "https://blog.example.com/feed.xml", "Blog")

        result = engine.tick()
        assert result.is_idle is False
        assert result.due_count == 1
        assert "rss" in result.due_by_type
        assert result.due_by_type["rss"][0].url == "https://blog.example.com/feed.xml"

    def test_multiple_source_types(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        _add_and_dispatch(store, config, "https://youtube.com/feeds/videos.xml?channel_id=UC1", "YT")
        _add_and_dispatch(store, config, "https://x.com/user", "Twitter")

        result = engine.tick()
        assert result.due_count == 3
        assert "rss" in result.due_by_type
        assert "youtube" in result.due_by_type
        assert "twitter" in result.due_by_type

    def test_enriched_with_title_and_lenses(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        store.add(
            "https://blog.com/feed.xml",
            title="My Blog",
            lenses=["tech", "ai"],
        )
        sub = store.get("https://blog.com/feed.xml")
        dispatch_subscription(sub, config)

        result = engine.tick()
        ds = result.due_by_type["rss"][0]
        assert ds.title == "My Blog"
        assert ds.lenses == ["tech", "ai"]

    def test_not_due_if_future(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        # Tick with a time in the past → nothing due
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = engine.tick(now=past)
        assert result.is_idle is True

    def test_paused_schedules_excluded(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        pause_schedule("https://blog.com/feed.xml", config)

        result = engine.tick()
        assert result.due_count == 0
        assert result.skipped_disabled == 1


# ---------------------------------------------------------------------------
# Tick: max errors skip
# ---------------------------------------------------------------------------


class TestTickMaxErrors:
    def test_skips_max_consecutive_errors(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://bad.com/feed.xml", "Bad")

        # Simulate MAX_CONSECUTIVE_ERRORS failures
        for _ in range(MAX_CONSECUTIVE_ERRORS):
            record_run_result(
                "https://bad.com/feed.xml", config,
                ok=False, error="timeout",
            )

        # Tick far in the future so next_run_at has passed despite backoff
        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = engine.tick(now=far_future)
        assert result.skipped_max_errors == 1
        assert result.due_count == 0


# ---------------------------------------------------------------------------
# Dispatch plan serialization
# ---------------------------------------------------------------------------


class TestDispatchPlan:
    def test_plan_structure(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        _add_and_dispatch(store, config, "https://x.com/user", "Twitter User")

        result = engine.tick()
        plan = result.to_dispatch_plan()

        assert plan["status"] == "dispatch"
        assert plan["due_count"] == 2
        assert "rss" in plan["dispatch"]
        assert "twitter" in plan["dispatch"]

        rss_entry = plan["dispatch"]["rss"][0]
        assert rss_entry["url"] == "https://blog.com/feed.xml"
        assert "interval_minutes" in rss_entry
        assert "schedule_id" in rss_entry


# ---------------------------------------------------------------------------
# Record outcome
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    def test_success_advances_next_run(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        original = get_schedule("https://blog.com/feed.xml", config)

        outcome = CollectionOutcome(
            url="https://blog.com/feed.xml",
            source_type="rss",
            ok=True,
            new_items=5,
        )
        engine.record_outcome(outcome)

        updated = get_schedule("https://blog.com/feed.xml", config)
        assert updated.last_run_ok is True
        assert updated.consecutive_errors == 0
        assert updated.next_run_at > original.next_run_at

    def test_error_sets_backoff(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        outcome = CollectionOutcome(
            url="https://blog.com/feed.xml",
            source_type="rss",
            ok=False,
            error="HTTP 500",
        )
        engine.record_outcome(outcome)

        updated = get_schedule("https://blog.com/feed.xml", config)
        assert updated.last_run_ok is False
        assert updated.consecutive_errors == 1
        assert updated.last_error == "HTTP 500"


# ---------------------------------------------------------------------------
# Compute next run
# ---------------------------------------------------------------------------


class TestComputeNextRun:
    def test_interval_based_success(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        schedule = get_schedule("https://blog.com/feed.xml", config)
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        nxt = engine.compute_next_run(schedule, ok=True, now=now)
        expected = now + timedelta(minutes=30)  # RSS default = 30min
        assert nxt == expected

    def test_interval_based_error_backoff(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        schedule = get_schedule("https://blog.com/feed.xml", config)
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # First error: 5 min backoff
        nxt = engine.compute_next_run(schedule, ok=False, now=now)
        expected = now + timedelta(minutes=BACKOFF_MINUTES[0])
        assert nxt == expected

    def test_cron_based(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(
            store, config, "https://blog.com/feed.xml", "Blog",
            cron_expr="0 */6 * * *",  # every 6 hours
        )
        schedule = get_schedule("https://blog.com/feed.xml", config)
        now = datetime(2024, 1, 1, 7, 0, 0, tzinfo=timezone.utc)

        nxt = engine.compute_next_run(schedule, ok=True, now=now)
        assert nxt == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_cron_overrides_interval_on_error(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """With cron, next run is always determined by cron, even after error."""
        _add_and_dispatch(
            store, config, "https://blog.com/feed.xml", "Blog",
            cron_expr="0 */6 * * *",
        )
        schedule = get_schedule("https://blog.com/feed.xml", config)
        now = datetime(2024, 1, 1, 7, 0, 0, tzinfo=timezone.utc)

        nxt = engine.compute_next_run(schedule, ok=False, now=now)
        # Cron determines next run, not backoff
        assert nxt == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# tick_and_run integration
# ---------------------------------------------------------------------------


class TestTickAndRun:
    @pytest.mark.asyncio
    async def test_idle_when_no_due(self, engine: SchedulerEngine):
        result = await engine.tick_and_run()
        assert result.tick.is_idle is True
        assert result.outcomes == []

    @pytest.mark.asyncio
    async def test_with_custom_collector(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        async def mock_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(
                url=ds.url,
                source_type=ds.source_type,
                ok=True,
                new_items=3,
            )

        result = await engine.tick_and_run(collector=mock_collector)
        assert result.tick.due_count == 1
        assert len(result.outcomes) == 1
        assert result.outcomes[0].ok is True
        assert result.total_new_items == 3

    @pytest.mark.asyncio
    async def test_collector_error_is_recorded(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        async def failing_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(
                url=ds.url,
                source_type=ds.source_type,
                ok=False,
                error="network timeout",
            )

        result = await engine.tick_and_run(collector=failing_collector)
        assert len(result.errors) == 1
        assert result.errors[0].error == "network timeout"

        # Schedule should have recorded the error
        schedule = get_schedule("https://blog.com/feed.xml", config)
        assert schedule.consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_exception_in_collector_handled(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        async def crashing_collector(ds: DueSubscription) -> CollectionOutcome:
            raise RuntimeError("collector crashed")

        result = await engine.tick_and_run(collector=crashing_collector)
        assert len(result.outcomes) == 1
        assert result.outcomes[0].ok is False
        assert "collector crashed" in result.outcomes[0].error

    @pytest.mark.asyncio
    async def test_multiple_due_collected_concurrently(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog1.com/feed.xml", "Blog1")
        _add_and_dispatch(store, config, "https://blog2.com/feed.xml", "Blog2")
        _add_and_dispatch(store, config, "https://x.com/user1", "Twitter1")

        collected_urls: list[str] = []

        async def tracking_collector(ds: DueSubscription) -> CollectionOutcome:
            collected_urls.append(ds.url)
            return CollectionOutcome(
                url=ds.url, source_type=ds.source_type, ok=True, new_items=1,
            )

        result = await engine.tick_and_run(collector=tracking_collector)
        assert result.tick.due_count == 3
        assert len(result.outcomes) == 3
        assert len(collected_urls) == 3
        assert result.total_new_items == 3

    @pytest.mark.asyncio
    async def test_second_tick_idle_after_run(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        """After collecting, next tick should be idle until next_run_at."""
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")

        async def mock_collector(ds: DueSubscription) -> CollectionOutcome:
            return CollectionOutcome(
                url=ds.url, source_type=ds.source_type, ok=True, new_items=1,
            )

        # First tick: collect
        await engine.tick_and_run(collector=mock_collector)

        # Second tick immediately: should be idle (next_run is 30min out)
        result = engine.tick()
        assert result.is_idle is True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_empty_status(self, engine: SchedulerEngine):
        status = engine.status()
        assert status["total_schedules"] == 0
        assert status["due_now"] == 0

    def test_status_with_schedules(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        _add_and_dispatch(store, config, "https://blog.com/feed.xml", "Blog")
        _add_and_dispatch(store, config, "https://x.com/user", "Twitter")
        pause_schedule("https://x.com/user", config)

        status = engine.status()
        assert status["total_schedules"] == 2
        assert status["enabled"] == 1
        assert status["disabled"] == 1
        assert status["by_source_type"] == {"rss": 1, "twitter": 1}


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_loop_respects_max_ticks(
        self, engine: SchedulerEngine, store: SubscriptionStore, config: WikiConfig,
    ):
        tick_count = 0

        def on_tick(result: TickRunResult) -> None:
            nonlocal tick_count
            tick_count += 1

        await engine.run_loop(
            interval_minutes=0.001,  # 60ms
            max_ticks=3,
            on_tick=on_tick,
        )

        assert tick_count == 3

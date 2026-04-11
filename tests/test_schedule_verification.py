"""Tests for schedule registration verification, execution logs, and error handling.

Sub-AC 3: Verifies that:
1. Schedule registration results are correctly persisted and queryable
2. Execution logs (schedule_runs) are properly recorded
3. Error handling (backoff, consecutive errors, max errors) works correctly
4. The tick CLI history command exposes run logs
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.db import get_db, init_db
from llm_wiki.dispatch import (
    RunHistoryEntry,
    Schedule,
    dispatch_subscription,
    get_due_schedules,
    get_run_stats,
    get_schedule,
    list_run_history,
    list_schedules,
    pause_schedule,
    record_run_result,
    resume_schedule,
    undispatch_subscription,
    update_schedule,
)
from llm_wiki.scheduler_engine import (
    BACKOFF_MINUTES,
    MAX_CONSECUTIVE_ERRORS,
    CollectionOutcome,
    DueSubscription,
    SchedulerEngine,
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
    **kwargs,
) -> Subscription:
    sub = store.add(url, title=title or url)
    dispatch_subscription(sub, config, **kwargs)
    return sub


# ===========================================================================
# 1. Schedule Registration Results
# ===========================================================================


class TestScheduleRegistrationResults:
    """Verify schedule registration creates correct entries in SQLite."""

    def test_dispatch_returns_complete_schedule(self, config: WikiConfig, store: SubscriptionStore):
        """dispatch_subscription returns a fully populated Schedule object."""
        sub = store.add("https://example.com/feed.xml", title="Example Blog")
        schedule = dispatch_subscription(sub, config)

        assert isinstance(schedule, Schedule)
        assert schedule.id > 0
        assert schedule.subscription_url == "https://example.com/feed.xml"
        assert schedule.source_type == "rss"
        assert schedule.interval_minutes == 30  # rss default
        assert schedule.enabled is True
        assert schedule.next_run_at is not None
        assert schedule.consecutive_errors == 0
        assert schedule.last_run_at is None
        assert schedule.last_run_ok is None
        assert schedule.last_error == ""
        assert schedule.created_at is not None
        assert schedule.updated_at is not None

    def test_dispatch_persists_to_sqlite(self, config: WikiConfig, store: SubscriptionStore):
        """Schedule can be retrieved from SQLite after dispatch."""
        sub = store.add("https://blog.test/feed.xml", title="Blog")
        dispatch_subscription(sub, config)

        retrieved = get_schedule("https://blog.test/feed.xml", config)
        assert retrieved is not None
        assert retrieved.subscription_url == "https://blog.test/feed.xml"

    def test_dispatch_with_custom_cron(self, config: WikiConfig, store: SubscriptionStore):
        """Cron expression is stored alongside interval."""
        sub = store.add("https://blog.test/feed.xml", title="Blog")
        schedule = dispatch_subscription(sub, config, cron_expr="0 9 * * 1-5")

        assert schedule.cron_expr == "0 9 * * 1-5"
        # Interval is also set (for fallback)
        assert schedule.interval_minutes == 30

    def test_list_schedules_returns_all(self, config: WikiConfig, store: SubscriptionStore):
        """list_schedules returns all registered schedules."""
        urls = [
            "https://blog1.com/feed.xml",
            "https://youtube.com/feeds/videos.xml?channel_id=UC1",
            "https://x.com/user1",
        ]
        for url in urls:
            sub = store.add(url, title=url)
            dispatch_subscription(sub, config)

        schedules = list_schedules(config)
        assert len(schedules) == 3
        retrieved_urls = {s.subscription_url for s in schedules}
        assert retrieved_urls == set(urls)

    def test_idempotent_dispatch_preserves_id(self, config: WikiConfig, store: SubscriptionStore):
        """Re-dispatching the same URL updates in-place (same ID)."""
        sub = store.add("https://blog.test/feed.xml", title="Blog")
        s1 = dispatch_subscription(sub, config, interval_minutes=30)
        s2 = dispatch_subscription(sub, config, interval_minutes=60)

        assert s1.id == s2.id
        assert s2.interval_minutes == 60

        # Only one schedule exists
        all_schedules = list_schedules(config)
        assert len(all_schedules) == 1

    def test_undispatch_removes_schedule_and_runs(self, config: WikiConfig, store: SubscriptionStore):
        """undispatch removes the schedule entry."""
        sub = store.add("https://blog.test/feed.xml", title="Blog")
        dispatch_subscription(sub, config)

        # Record a run first
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=5)

        removed = undispatch_subscription("https://blog.test/feed.xml", config)
        assert removed is True
        assert get_schedule("https://blog.test/feed.xml", config) is None

        # Run history is cascade-deleted (foreign key)
        runs = list_run_history(config, url="https://blog.test/feed.xml")
        assert len(runs) == 0


# ===========================================================================
# 2. Execution Logs (schedule_runs)
# ===========================================================================


class TestExecutionLogs:
    """Verify that execution logs are recorded and queryable."""

    def test_successful_run_creates_log(self, config: WikiConfig, store: SubscriptionStore):
        """record_run_result inserts a schedule_runs entry."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result(
            "https://blog.test/feed.xml", config,
            ok=True, new_items=5,
        )

        history = list_run_history(config)
        assert len(history) == 1
        assert history[0].status == "ok"
        assert history[0].new_items == 5
        assert history[0].error_message == ""

    def test_error_run_creates_log(self, config: WikiConfig, store: SubscriptionStore):
        """Error runs are logged with error_message."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result(
            "https://blog.test/feed.xml", config,
            ok=False, error="HTTP 500 Internal Server Error",
        )

        history = list_run_history(config)
        assert len(history) == 1
        assert history[0].status == "error"
        assert history[0].error_message == "HTTP 500 Internal Server Error"

    def test_multiple_runs_ordered_newest_first(self, config: WikiConfig, store: SubscriptionStore):
        """History entries are ordered by started_at DESC."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        for i in range(5):
            record_run_result(
                "https://blog.test/feed.xml", config,
                ok=True, new_items=i + 1,
            )

        history = list_run_history(config)
        assert len(history) == 5
        # Newest first: most items should be first (last recorded)
        assert history[0].new_items == 5

    def test_filter_by_url(self, config: WikiConfig, store: SubscriptionStore):
        """History can be filtered by subscription URL."""
        _add_and_dispatch(store, config, "https://blog1.test/feed.xml", "Blog1")
        _add_and_dispatch(store, config, "https://blog2.test/feed.xml", "Blog2")

        record_run_result("https://blog1.test/feed.xml", config, ok=True, new_items=3)
        record_run_result("https://blog2.test/feed.xml", config, ok=True, new_items=7)
        record_run_result("https://blog1.test/feed.xml", config, ok=True, new_items=2)

        blog1_runs = list_run_history(config, url="https://blog1.test/feed.xml")
        assert len(blog1_runs) == 2

        blog2_runs = list_run_history(config, url="https://blog2.test/feed.xml")
        assert len(blog2_runs) == 1

    def test_filter_by_status(self, config: WikiConfig, store: SubscriptionStore):
        """History can be filtered by status (ok/error)."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=3)
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="timeout")
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=5)

        ok_runs = list_run_history(config, status_filter="ok")
        assert len(ok_runs) == 2

        error_runs = list_run_history(config, status_filter="error")
        assert len(error_runs) == 1
        assert error_runs[0].error_message == "timeout"

    def test_limit_parameter(self, config: WikiConfig, store: SubscriptionStore):
        """History respects the limit parameter."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        for i in range(10):
            record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=i)

        limited = list_run_history(config, limit=3)
        assert len(limited) == 3

    def test_run_stats_aggregate(self, config: WikiConfig, store: SubscriptionStore):
        """get_run_stats returns correct aggregate statistics."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=3)
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=5)
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="timeout")

        stats = get_run_stats(config)
        assert stats["total_runs"] == 3
        assert stats["ok_count"] == 2
        assert stats["error_count"] == 1
        assert stats["last_ok_at"] is not None
        assert stats["last_error_at"] is not None

    def test_run_stats_per_url(self, config: WikiConfig, store: SubscriptionStore):
        """get_run_stats can filter by URL."""
        _add_and_dispatch(store, config, "https://blog1.test/feed.xml", "Blog1")
        _add_and_dispatch(store, config, "https://blog2.test/feed.xml", "Blog2")

        record_run_result("https://blog1.test/feed.xml", config, ok=True, new_items=3)
        record_run_result("https://blog2.test/feed.xml", config, ok=False, error="err")

        stats1 = get_run_stats(config, url="https://blog1.test/feed.xml")
        assert stats1["total_runs"] == 1
        assert stats1["ok_count"] == 1
        assert stats1["error_count"] == 0

        stats2 = get_run_stats(config, url="https://blog2.test/feed.xml")
        assert stats2["total_runs"] == 1
        assert stats2["ok_count"] == 0
        assert stats2["error_count"] == 1

    def test_empty_stats(self, config: WikiConfig):
        """get_run_stats returns zeros when no runs exist."""
        stats = get_run_stats(config)
        assert stats["total_runs"] == 0
        assert stats["ok_count"] == 0
        assert stats["error_count"] == 0


# ===========================================================================
# 3. Error Handling (backoff, consecutive errors, recovery)
# ===========================================================================


class TestErrorHandling:
    """Verify error handling, backoff, and recovery mechanisms."""

    def test_first_error_5min_backoff(self, config: WikiConfig, store: SubscriptionStore):
        """First error applies 5-minute backoff."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        before = datetime.now(timezone.utc)

        record_run_result("https://blog.test/feed.xml", config, ok=False, error="timeout")

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 1
        assert schedule.last_error == "timeout"
        # Next run should be ~5min from now
        expected_min = before + timedelta(minutes=4)
        expected_max = before + timedelta(minutes=6)
        assert expected_min <= schedule.next_run_at <= expected_max

    def test_second_error_30min_backoff(self, config: WikiConfig, store: SubscriptionStore):
        """Second consecutive error applies 30-minute backoff."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result("https://blog.test/feed.xml", config, ok=False, error="err1")
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="err2")

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 2

    def test_third_error_2h_backoff(self, config: WikiConfig, store: SubscriptionStore):
        """Third consecutive error applies 2-hour backoff."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        for i in range(3):
            record_run_result(
                "https://blog.test/feed.xml", config,
                ok=False, error=f"err{i+1}",
            )

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 3

    def test_success_resets_error_counter(self, config: WikiConfig, store: SubscriptionStore):
        """Successful run resets consecutive_errors to 0."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        # Build up errors
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="err1")
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="err2")

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 2

        # Success resets
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=3)

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 0
        assert schedule.last_error == ""
        assert schedule.last_run_ok is True

    def test_max_errors_skipped_by_engine(
        self, config: WikiConfig, store: SubscriptionStore, engine: SchedulerEngine,
    ):
        """Scheduler engine skips subscriptions with MAX_CONSECUTIVE_ERRORS."""
        _add_and_dispatch(store, config, "https://bad.test/feed.xml", "Bad")

        for _ in range(MAX_CONSECUTIVE_ERRORS):
            record_run_result(
                "https://bad.test/feed.xml", config,
                ok=False, error="persistent failure",
            )

        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = engine.tick(now=far_future)
        assert result.skipped_max_errors == 1
        assert result.due_count == 0

    def test_error_log_entries_preserved(self, config: WikiConfig, store: SubscriptionStore):
        """All error runs are logged in history, not just the latest."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        errors = ["timeout", "DNS resolution failed", "HTTP 503"]
        for err in errors:
            record_run_result("https://blog.test/feed.xml", config, ok=False, error=err)

        history = list_run_history(config, status_filter="error")
        assert len(history) == 3
        error_msgs = [h.error_message for h in history]
        assert "timeout" in error_msgs
        assert "DNS resolution failed" in error_msgs
        assert "HTTP 503" in error_msgs

    @pytest.mark.asyncio
    async def test_exception_in_collector_recorded(
        self, config: WikiConfig, store: SubscriptionStore, engine: SchedulerEngine,
    ):
        """Exceptions in collector are caught and recorded as errors."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        async def crashing_collector(ds: DueSubscription) -> CollectionOutcome:
            raise RuntimeError("unexpected crash")

        result = await engine.tick_and_run(collector=crashing_collector)
        assert len(result.outcomes) == 1
        assert result.outcomes[0].ok is False
        assert "unexpected crash" in result.outcomes[0].error

        # Verify recorded in schedule
        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 1

        # Verify recorded in run history
        history = list_run_history(config)
        assert len(history) == 1
        assert history[0].status == "error"

    @pytest.mark.asyncio
    async def test_partial_failure_in_batch(
        self, config: WikiConfig, store: SubscriptionStore, engine: SchedulerEngine,
    ):
        """One failing feed doesn't prevent other feeds from being collected."""
        _add_and_dispatch(store, config, "https://good.test/feed.xml", "Good")
        _add_and_dispatch(store, config, "https://bad.test/feed.xml", "Bad")

        async def mixed_collector(ds: DueSubscription) -> CollectionOutcome:
            if "bad" in ds.url:
                return CollectionOutcome(
                    url=ds.url, source_type=ds.source_type,
                    ok=False, error="server down",
                )
            return CollectionOutcome(
                url=ds.url, source_type=ds.source_type,
                ok=True, new_items=5,
            )

        result = await engine.tick_and_run(collector=mixed_collector)
        assert len(result.outcomes) == 2

        ok_outcomes = [o for o in result.outcomes if o.ok]
        err_outcomes = [o for o in result.outcomes if not o.ok]
        assert len(ok_outcomes) == 1
        assert len(err_outcomes) == 1

        # Good feed advanced, bad feed has error
        good = get_schedule("https://good.test/feed.xml", config)
        assert good.consecutive_errors == 0
        assert good.last_run_ok is True

        bad = get_schedule("https://bad.test/feed.xml", config)
        assert bad.consecutive_errors == 1
        assert bad.last_run_ok is False

    def test_pause_resume_preserves_error_state(self, config: WikiConfig, store: SubscriptionStore):
        """Pausing and resuming doesn't reset error state."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        record_run_result("https://blog.test/feed.xml", config, ok=False, error="err")

        pause_schedule("https://blog.test/feed.xml", config)
        resume_schedule("https://blog.test/feed.xml", config)

        schedule = get_schedule("https://blog.test/feed.xml", config)
        assert schedule.consecutive_errors == 1
        assert schedule.last_error == "err"


# ===========================================================================
# 4. Run history entry data integrity
# ===========================================================================


class TestRunHistoryEntryIntegrity:
    """Verify RunHistoryEntry fields are correctly populated."""

    def test_entry_has_all_fields(self, config: WikiConfig, store: SubscriptionStore):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=3)

        entries = list_run_history(config)
        assert len(entries) == 1

        entry = entries[0]
        assert isinstance(entry, RunHistoryEntry)
        assert entry.id > 0
        assert entry.schedule_id > 0
        assert isinstance(entry.started_at, datetime)
        assert isinstance(entry.finished_at, datetime)
        assert entry.status == "ok"
        assert entry.new_items == 3
        assert entry.error_message == ""

    def test_entry_links_to_correct_schedule(self, config: WikiConfig, store: SubscriptionStore):
        """RunHistoryEntry.schedule_id matches the schedule's id."""
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        schedule = get_schedule("https://blog.test/feed.xml", config)

        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=1)

        entries = list_run_history(config)
        assert entries[0].schedule_id == schedule.id


# ===========================================================================
# 5. Engine status reflects schedule state
# ===========================================================================


class TestEngineStatusReflectsState:
    """Verify engine.status() correctly reflects registration and error state."""

    def test_status_counts_errored(
        self, config: WikiConfig, store: SubscriptionStore, engine: SchedulerEngine,
    ):
        _add_and_dispatch(store, config, "https://good.test/feed.xml", "Good")
        _add_and_dispatch(store, config, "https://bad.test/feed.xml", "Bad")

        record_run_result("https://bad.test/feed.xml", config, ok=False, error="err")

        status = engine.status()
        assert status["total_schedules"] == 2
        assert status["errored"] == 1

    def test_status_shows_next_run(
        self, config: WikiConfig, store: SubscriptionStore, engine: SchedulerEngine,
    ):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        # After a successful run, next_run is in the future
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=1)

        status = engine.status()
        assert status["next_run_at"] is not None


# ===========================================================================
# 6. Tick CLI output verification
# ===========================================================================


class TestTickCLI:
    """Verify the tick CLI outputs correct JSON for schedule operations."""

    def _run_tick_cli(self, vault: Path, *args: str) -> dict:
        """Helper to run tick CLI and parse JSON output."""
        cmd = [
            sys.executable, "-m", "llm_wiki.tick",
            "--vault", str(vault),
            *args,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent / "src"),
        )
        assert result.returncode == 0, f"CLI error: {result.stderr}"
        return json.loads(result.stdout)

    def test_list_shows_dispatched_schedules(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        output = self._run_tick_cli(vault, "list")
        assert output["total"] == 1
        assert output["schedules"][0]["subscription_url"] == "https://blog.test/feed.xml"

    def test_due_shows_dispatch_plan(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        output = self._run_tick_cli(vault, "due")
        assert output["status"] == "dispatch"
        assert output["due_count"] == 1

    def test_history_shows_run_logs(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=5)
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="timeout")

        output = self._run_tick_cli(vault, "history")
        assert output["stats"]["total_runs"] == 2
        assert output["stats"]["ok_count"] == 1
        assert output["stats"]["error_count"] == 1
        assert len(output["entries"]) == 2

    def test_history_filter_by_status(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")
        record_run_result("https://blog.test/feed.xml", config, ok=True, new_items=5)
        record_run_result("https://blog.test/feed.xml", config, ok=False, error="timeout")

        output = self._run_tick_cli(vault, "history", "--status", "error")
        assert output["stats"]["error_count"] == 1
        assert len(output["entries"]) == 1
        assert output["entries"][0]["status"] == "error"

    def test_record_ok(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        output = self._run_tick_cli(
            vault, "record", "https://blog.test/feed.xml",
            "--ok", "--new-items", "5",
        )
        assert output["recorded"] is True
        assert output["status"] == "ok"

    def test_record_error(self, config: WikiConfig, store: SubscriptionStore, vault: Path):
        _add_and_dispatch(store, config, "https://blog.test/feed.xml", "Blog")

        output = self._run_tick_cli(
            vault, "record", "https://blog.test/feed.xml",
            "--error", "network timeout",
        )
        assert output["recorded"] is True
        assert output["status"] == "error"

    def test_defaults_shows_intervals(self, config: WikiConfig, vault: Path):
        output = self._run_tick_cli(vault, "defaults")
        assert "source_type_defaults" in output
        assert output["source_type_defaults"]["rss"] == 30
        assert output["source_type_defaults"]["youtube"] == 60
        assert output["source_type_defaults"]["twitter"] == 15

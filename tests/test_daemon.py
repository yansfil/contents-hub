"""Tests for the daemon core (daemon.py).

Covers:
- R5.1: Due subscription polling
- R5.2: Fetched items stored as raw in SQLite with dedup
- R5.5: Error handling (mapped to R8.1-R8.3 backoff behavior)
- R8.1: Transient errors trigger backoff
- R8.2: Permanent errors disable subscription
- R8.3: Successful fetch resets error state
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from llm_wiki.collectors.rss import FeedItem, FeedResult
from llm_wiki.collectors.youtube import YouTubeFeedResult, YouTubeVideo
from llm_wiki.config import WikiConfig
from llm_wiki.daemon import (
    DaemonTickResult,
    backoff_minutes,
    classify_error,
    daemon_tick,
    _query_due_subscriptions,
    _is_url_seen,
    _insert_raw_item,
    _start_job_run,
    _finish_job_run,
)
from llm_wiki.db import init_db


@pytest.fixture
def vault_path(tmp_path):
    """Create a temporary vault with .llm-wiki directory."""
    meta = tmp_path / ".llm-wiki"
    meta.mkdir()
    return tmp_path


@pytest.fixture
def config(vault_path):
    """Create a WikiConfig pointing to the temp vault."""
    return WikiConfig(vault_path=vault_path)


@pytest.fixture
def conn(config):
    """Create a DB connection with schema initialized."""
    c = init_db(config)
    yield c
    c.close()


def _insert_subscription(
    conn: sqlite3.Connection,
    url: str,
    *,
    title: str = "Test Feed",
    source_type: str = "rss",
    status: str = "active",
    interval_minutes: int = 30,
    last_fetched_at: str | None = None,
    consecutive_errors: int = 0,
) -> int:
    """Helper: insert a subscription row and return its id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT INTO subscriptions
            (url, title, source_type, status,
             schedule_interval_minutes, last_fetched_at,
             consecutive_errors, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            url, title, source_type, status,
            interval_minutes, last_fetched_at,
            consecutive_errors, now_iso, now_iso,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# classify_error / backoff
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_permanent_404(self):
        assert classify_error("Feed not found (404)") == "permanent"

    def test_permanent_403(self):
        assert classify_error("HTTP 403 Forbidden") == "permanent"

    def test_transient_timeout(self):
        assert classify_error("Request timed out after 30s") == "transient"

    def test_transient_500(self):
        assert classify_error("HTTP error: 500") == "transient"

    def test_transient_connection(self):
        assert classify_error("Connection refused") == "transient"

    def test_ambiguous(self):
        assert classify_error("some unknown error") == "ambiguous"


class TestBackoffMinutes:
    def test_first_error(self):
        assert backoff_minutes(0) == 5

    def test_second_error(self):
        assert backoff_minutes(1) == 30

    def test_third_error(self):
        assert backoff_minutes(2) == 120

    def test_beyond_max(self):
        assert backoff_minutes(10) == 120  # clamped to last


# ---------------------------------------------------------------------------
# R5.1: Due subscription polling
# ---------------------------------------------------------------------------


class TestDueSubscriptions:
    def test_never_fetched_is_due(self, conn):
        """Subscription with last_fetched_at=NULL should be due."""
        _insert_subscription(conn, "https://example.com/feed1")
        due = _query_due_subscriptions(conn)
        assert len(due) == 1
        assert due[0]["url"] == "https://example.com/feed1"

    def test_recently_fetched_not_due(self, conn):
        """Subscription fetched recently should NOT be due."""
        now_iso = datetime.now(timezone.utc).isoformat()
        _insert_subscription(
            conn, "https://example.com/feed2",
            last_fetched_at=now_iso,
            interval_minutes=30,
        )
        due = _query_due_subscriptions(conn)
        assert len(due) == 0

    def test_overdue_is_due(self, conn):
        """Subscription past its interval should be due."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_subscription(
            conn, "https://example.com/feed3",
            last_fetched_at=past,
            interval_minutes=30,
        )
        due = _query_due_subscriptions(conn)
        assert len(due) == 1

    def test_paused_not_due(self, conn):
        """Paused subscription should not be due."""
        _insert_subscription(
            conn, "https://example.com/feed4",
            status="paused",
        )
        due = _query_due_subscriptions(conn)
        assert len(due) == 0

    def test_manual_not_due(self, conn):
        """Manual schedule (interval=0) should not be due."""
        _insert_subscription(
            conn, "https://example.com/feed5",
            interval_minutes=0,
        )
        due = _query_due_subscriptions(conn)
        assert len(due) == 0

    def test_only_due_fetched(self, conn):
        """R5.1 GWT: 2 active subs, 1 due => only 1 fetched."""
        # Sub 1: never fetched (due)
        _insert_subscription(conn, "https://example.com/due")
        # Sub 2: just fetched (not due)
        now_iso = datetime.now(timezone.utc).isoformat()
        _insert_subscription(
            conn, "https://example.com/not-due",
            last_fetched_at=now_iso,
            interval_minutes=60,
        )
        due = _query_due_subscriptions(conn)
        assert len(due) == 1
        assert due[0]["url"] == "https://example.com/due"


# ---------------------------------------------------------------------------
# R5.2: Dedup via raw_items
# ---------------------------------------------------------------------------


class TestDedup:
    def test_url_not_seen(self, conn):
        assert not _is_url_seen(conn, "https://example.com/article1")

    def test_url_seen_after_insert(self, conn):
        sub_id = _insert_subscription(conn, "https://example.com/feed")
        _insert_raw_item(
            conn, url="https://example.com/article1",
            title="Article 1", subscription_id=sub_id,
        )
        conn.commit()
        assert _is_url_seen(conn, "https://example.com/article1")

    def test_insert_returns_id(self, conn):
        sub_id = _insert_subscription(conn, "https://example.com/feed")
        row_id = _insert_raw_item(
            conn, url="https://example.com/article1",
            title="Article 1", subscription_id=sub_id,
        )
        conn.commit()
        assert row_id > 0


# ---------------------------------------------------------------------------
# R8.1-R8.3 + R5.2: Full daemon_tick integration
# ---------------------------------------------------------------------------


def _make_feed_items(count: int) -> list[FeedItem]:
    """Create N fake FeedItems."""
    return [
        FeedItem(
            url=f"https://example.com/article-{i}",
            title=f"Article {i}",
            summary=f"Summary {i}",
        )
        for i in range(count)
    ]


class TestDaemonTick:
    @pytest.mark.asyncio
    async def test_tick_with_new_items(self, config, conn):
        """R5.2: Fetch 3 items, 0 dupes => 3 new raw_items."""
        _insert_subscription(conn, "https://example.com/feed")

        items = _make_feed_items(3)
        mock_result = FeedResult(
            ok=True, items=items, feed_title="Test Feed",
            feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.feeds_checked == 1
        assert result.feeds_ok == 1
        assert result.new_items_total == 3
        assert result.skipped_items_total == 0

        # Verify raw_items in DB
        rows = conn.execute("SELECT * FROM raw_items").fetchall()
        assert len(rows) == 3
        assert all(r["origin"] == "subscription" for r in rows)
        assert all(r["status"] == "raw" for r in rows)
        assert all(r["priority"] == 50 for r in rows)

    @pytest.mark.asyncio
    async def test_tick_dedup_skips_existing(self, config, conn):
        """R5.2: 10 items, 3 already in raw_items => 7 new, 3 skipped."""
        sub_id = _insert_subscription(conn, "https://example.com/feed")

        # Pre-insert 3 items
        for i in range(3):
            _insert_raw_item(
                conn, url=f"https://example.com/article-{i}",
                title=f"Old Article {i}", subscription_id=sub_id,
            )
        conn.commit()

        items = _make_feed_items(10)
        mock_result = FeedResult(
            ok=True, items=items, feed_title="Test Feed",
            feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.new_items_total == 7
        assert result.skipped_items_total == 3

        # Total raw_items = 3 pre-existing + 7 new = 10
        count = conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]
        assert count == 10

    @pytest.mark.asyncio
    async def test_tick_transient_error_backoff(self, config, conn):
        """R8.1: Transient error => consecutive_errors++, status stays active."""
        _insert_subscription(conn, "https://example.com/feed")

        mock_result = FeedResult(
            ok=False, error="Request timed out after 30s",
            feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.feeds_error == 1
        assert result.new_items_total == 0

        # Check subscription state
        row = conn.execute("SELECT * FROM subscriptions").fetchone()
        assert row["consecutive_errors"] == 1
        assert row["status"] == "active"  # stays active for transient
        assert "timed out" in row["last_error"]

    @pytest.mark.asyncio
    async def test_tick_permanent_error_disables(self, config, conn):
        """R8.2: Permanent error (404) => status='error'."""
        _insert_subscription(conn, "https://example.com/feed")

        mock_result = FeedResult(
            ok=False, error="Feed not found (404)",
            feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.feeds_error == 1

        row = conn.execute("SELECT * FROM subscriptions").fetchone()
        assert row["status"] == "error"
        assert "404" in row["last_error"]

    @pytest.mark.asyncio
    async def test_tick_success_resets_errors(self, config, conn):
        """R8.3: Successful fetch resets consecutive_errors to 0."""
        _insert_subscription(
            conn, "https://example.com/feed",
            consecutive_errors=2,
        )

        items = _make_feed_items(1)
        mock_result = FeedResult(
            ok=True, items=items, feed_title="Test Feed",
            feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.feeds_ok == 1

        row = conn.execute("SELECT * FROM subscriptions").fetchone()
        assert row["consecutive_errors"] == 0
        assert row["status"] == "active"
        assert row["last_error"] == ""

    @pytest.mark.asyncio
    async def test_tick_no_due_subs(self, config, conn):
        """No due subscriptions => empty tick, job run still recorded."""
        now_iso = datetime.now(timezone.utc).isoformat()
        _insert_subscription(
            conn, "https://example.com/feed",
            last_fetched_at=now_iso,
            interval_minutes=60,
        )

        result = await daemon_tick(config, conn=conn)
        assert result.feeds_checked == 0
        assert result.job_run_id > 0

        # Job run recorded with ok status
        job = conn.execute(
            "SELECT * FROM job_runs WHERE id = ?", (result.job_run_id,)
        ).fetchone()
        assert job["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tick_youtube_subscription(self, config, conn):
        """YouTube subscription uses youtube collector."""
        _insert_subscription(
            conn,
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC123",
            source_type="youtube",
        )

        videos = [
            YouTubeVideo(
                video_id="abc123",
                title="Video 1",
                url="https://www.youtube.com/watch?v=abc123",
                description="Desc",
            ),
        ]
        mock_result = YouTubeFeedResult(
            ok=True, videos=videos, channel_title="Test Channel",
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123",
        )

        with patch("llm_wiki.daemon.fetch_youtube_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.feeds_ok == 1
        assert result.new_items_total == 1

        row = conn.execute("SELECT * FROM raw_items").fetchone()
        assert row["url"] == "https://www.youtube.com/watch?v=abc123"
        assert row["origin"] == "subscription"


# ---------------------------------------------------------------------------
# Job runs tracking (R8)
# ---------------------------------------------------------------------------


class TestJobRuns:
    def test_start_and_finish(self, conn):
        """Job run lifecycle: start -> finish with counts."""
        job_id = _start_job_run(conn)
        assert job_id > 0

        row = conn.execute("SELECT * FROM job_runs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "running"
        assert row["finished_at"] is None

        _finish_job_run(
            conn, job_id,
            status="ok",
            items_total=10,
            items_new=7,
            items_error=0,
        )

        row = conn.execute("SELECT * FROM job_runs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "ok"
        assert row["finished_at"] is not None
        assert row["items_total"] == 10
        assert row["items_new"] == 7
        assert row["items_error"] == 0

    @pytest.mark.asyncio
    async def test_tick_creates_job_run(self, config, conn):
        """Each daemon tick creates a job_runs row."""
        _insert_subscription(conn, "https://example.com/feed")

        mock_result = FeedResult(
            ok=True, items=_make_feed_items(2),
            feed_title="Test", feed_url="https://example.com/feed",
        )

        with patch("llm_wiki.daemon.fetch_feed", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_result
            result = await daemon_tick(config, conn=conn)

        assert result.job_run_id > 0

        job = conn.execute(
            "SELECT * FROM job_runs WHERE id = ?", (result.job_run_id,)
        ).fetchone()
        assert job["status"] == "ok"
        assert job["items_new"] == 2
        assert job["finished_at"] is not None

"""Tests for Twitter incremental collector.

Tests the full incremental fetch flow:
- Cursor (since_id / since_timestamp) persistence in SQLite
- Filtering by since_id (Snowflake ID comparison)
- Source file creation
- End-to-end incremental collection with mocked HTTP
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from llm_wiki.collectors.twitter import (
    TwitterCollectionResult,
    collect_twitter_incremental,
    filter_new_items_by_since_id,
    filter_uncollected_items,
    load_cursors,
    save_cursors,
    save_tweet_as_source,
    _extract_max_tweet_id,
    _extract_max_timestamp,
)
from llm_wiki.config import WikiConfig, load_config
from llm_wiki.db import (
    CollectedTweet,
    FetchCursor,
    delete_collected_tweets,
    delete_fetch_cursors,
    filter_uncollected_tweet_ids,
    get_all_cursors,
    get_fetch_cursor,
    init_db,
    is_tweet_collected,
    record_collected_tweet,
    record_collected_tweets_batch,
    save_fetch_cursor,
)
from llm_wiki.fetchers.base import FetchedItem
from llm_wiki.subscriptions import Subscription


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".llm-wiki").mkdir()
    return vault


@pytest.fixture
def config(tmp_vault: Path) -> WikiConfig:
    """Create a WikiConfig pointing to the temp vault."""
    return load_config(str(tmp_vault))


@pytest.fixture
def db_conn(config: WikiConfig):
    """Get an initialized DB connection."""
    conn = init_db(config)
    yield conn
    conn.close()


@pytest.fixture
def sample_items() -> list[FetchedItem]:
    """Create sample FetchedItems mimicking tweet data."""
    return [
        FetchedItem(
            url="https://x.com/user/status/100003",
            title="Third tweet",
            summary="Third tweet content",
            author="@testuser",
            published_at=datetime(2024, 7, 3, tzinfo=timezone.utc),
            source_type="twitter",
            extra={"tweet_id": "100003", "like_count": 30},
        ),
        FetchedItem(
            url="https://x.com/user/status/100002",
            title="Second tweet",
            summary="Second tweet content",
            author="@testuser",
            published_at=datetime(2024, 7, 2, tzinfo=timezone.utc),
            source_type="twitter",
            extra={"tweet_id": "100002", "like_count": 20},
        ),
        FetchedItem(
            url="https://x.com/user/status/100001",
            title="First tweet",
            summary="First tweet content",
            author="@testuser",
            published_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
            source_type="twitter",
            extra={"tweet_id": "100001", "like_count": 10},
        ),
    ]


@pytest.fixture
def subscription() -> Subscription:
    """Create a sample Twitter subscription."""
    return Subscription(
        url="https://x.com/testuser",
        title="@testuser",
        source_type="twitter",
        lenses=["tech", "ai"],
    )


# ---------------------------------------------------------------------------
# Cursor persistence tests (SQLite)
# ---------------------------------------------------------------------------


class TestFetchCursorPersistence:
    """Tests for fetch cursor CRUD operations in SQLite."""

    def test_save_and_get_since_id(self, config: WikiConfig):
        """Save a since_id cursor and retrieve it."""
        save_fetch_cursor("https://x.com/user", "since_id", "12345", config)
        cursor = get_fetch_cursor("https://x.com/user", config, cursor_type="since_id")

        assert cursor is not None
        assert cursor.subscription_url == "https://x.com/user"
        assert cursor.cursor_type == "since_id"
        assert cursor.cursor_value == "12345"
        assert cursor.updated_at is not None

    def test_save_and_get_since_timestamp(self, config: WikiConfig):
        """Save a since_timestamp cursor and retrieve it."""
        ts = "2024-07-01T12:00:00+00:00"
        save_fetch_cursor("https://x.com/user", "since_timestamp", ts, config)
        cursor = get_fetch_cursor(
            "https://x.com/user", config, cursor_type="since_timestamp",
        )

        assert cursor is not None
        assert cursor.cursor_value == ts

    def test_get_nonexistent_cursor(self, config: WikiConfig):
        """Getting a cursor that doesn't exist returns None."""
        cursor = get_fetch_cursor("https://x.com/nobody", config)
        assert cursor is None

    def test_upsert_overwrites(self, config: WikiConfig):
        """Saving to the same (url, type) key overwrites the value."""
        save_fetch_cursor("https://x.com/user", "since_id", "100", config)
        save_fetch_cursor("https://x.com/user", "since_id", "200", config)

        cursor = get_fetch_cursor("https://x.com/user", config, cursor_type="since_id")
        assert cursor is not None
        assert cursor.cursor_value == "200"

    def test_get_all_cursors(self, config: WikiConfig):
        """Get all cursor types for a subscription."""
        save_fetch_cursor("https://x.com/user", "since_id", "999", config)
        save_fetch_cursor(
            "https://x.com/user", "since_timestamp",
            "2024-07-01T00:00:00+00:00", config,
        )

        cursors = get_all_cursors("https://x.com/user", config)
        assert len(cursors) == 2
        assert "since_id" in cursors
        assert "since_timestamp" in cursors
        assert cursors["since_id"].cursor_value == "999"

    def test_delete_cursors(self, config: WikiConfig):
        """Delete all cursors for a subscription."""
        save_fetch_cursor("https://x.com/user", "since_id", "123", config)
        save_fetch_cursor("https://x.com/user", "since_timestamp", "ts", config)

        deleted = delete_fetch_cursors("https://x.com/user", config)
        assert deleted == 2

        cursors = get_all_cursors("https://x.com/user", config)
        assert len(cursors) == 0

    def test_cursors_isolated_per_subscription(self, config: WikiConfig):
        """Different subscription URLs have independent cursors."""
        save_fetch_cursor("https://x.com/alice", "since_id", "100", config)
        save_fetch_cursor("https://x.com/bob", "since_id", "200", config)

        alice = get_fetch_cursor("https://x.com/alice", config, cursor_type="since_id")
        bob = get_fetch_cursor("https://x.com/bob", config, cursor_type="since_id")

        assert alice is not None and alice.cursor_value == "100"
        assert bob is not None and bob.cursor_value == "200"


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


class TestFilterNewItemsBySinceId:
    """Tests for since_id-based filtering."""

    def test_filters_old_items(self, sample_items: list[FetchedItem]):
        """Items with tweet_id <= since_id are filtered out."""
        new, skipped = filter_new_items_by_since_id(sample_items, "100002")

        assert len(new) == 1
        assert skipped == 2
        assert new[0].extra["tweet_id"] == "100003"

    def test_all_items_new(self, sample_items: list[FetchedItem]):
        """All items pass when since_id is very old."""
        new, skipped = filter_new_items_by_since_id(sample_items, "1")

        assert len(new) == 3
        assert skipped == 0

    def test_all_items_old(self, sample_items: list[FetchedItem]):
        """No items pass when since_id is very recent."""
        new, skipped = filter_new_items_by_since_id(sample_items, "999999")

        assert len(new) == 0
        assert skipped == 3

    def test_invalid_since_id_passes_all(self, sample_items: list[FetchedItem]):
        """Invalid since_id (non-numeric) → no filtering."""
        new, skipped = filter_new_items_by_since_id(sample_items, "not-a-number")

        assert len(new) == 3
        assert skipped == 0

    def test_items_without_tweet_id_included(self):
        """Items without tweet_id are conservatively included."""
        items = [
            FetchedItem(url="https://x.com/u/status/1", title="no id", extra={}),
            FetchedItem(
                url="https://x.com/u/status/2", title="has id",
                extra={"tweet_id": "50"},
            ),
        ]
        new, skipped = filter_new_items_by_since_id(items, "100")

        assert len(new) == 1  # only "no id" passes
        assert skipped == 1
        assert new[0].title == "no id"

    def test_empty_items_list(self):
        """Empty input → empty output."""
        new, skipped = filter_new_items_by_since_id([], "100")
        assert len(new) == 0
        assert skipped == 0


# ---------------------------------------------------------------------------
# Extract helpers
# ---------------------------------------------------------------------------


class TestExtractMaxTweetId:
    def test_extracts_highest(self, sample_items: list[FetchedItem]):
        assert _extract_max_tweet_id(sample_items) == "100003"

    def test_empty_list(self):
        assert _extract_max_tweet_id([]) is None

    def test_no_tweet_ids(self):
        items = [FetchedItem(url="u", title="t", extra={})]
        assert _extract_max_tweet_id(items) is None


class TestExtractMaxTimestamp:
    def test_extracts_latest(self, sample_items: list[FetchedItem]):
        result = _extract_max_timestamp(sample_items)
        assert result == datetime(2024, 7, 3, tzinfo=timezone.utc)

    def test_empty_list(self):
        assert _extract_max_timestamp([]) is None

    def test_no_timestamps(self):
        items = [FetchedItem(url="u", title="t")]
        assert _extract_max_timestamp(items) is None


# ---------------------------------------------------------------------------
# Cursor load/save integration
# ---------------------------------------------------------------------------


class TestLoadSaveCursors:
    def test_load_empty(self, config: WikiConfig):
        """First fetch — no cursors stored."""
        since_id, since_ts = load_cursors("https://x.com/new", config)
        assert since_id is None
        assert since_ts is None

    def test_save_and_load(self, config: WikiConfig, sample_items: list[FetchedItem]):
        """Save cursors from items, then load them back."""
        save_cursors("https://x.com/user", sample_items, config)

        since_id, since_ts = load_cursors("https://x.com/user", config)

        assert since_id == "100003"
        assert since_ts is not None
        assert since_ts == datetime(2024, 7, 3, tzinfo=timezone.utc)

    def test_save_updates_existing(self, config: WikiConfig):
        """Second save overwrites the first."""
        items_old = [
            FetchedItem(
                url="u1", title="t1", source_type="twitter",
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                extra={"tweet_id": "100"},
            ),
        ]
        items_new = [
            FetchedItem(
                url="u2", title="t2", source_type="twitter",
                published_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
                extra={"tweet_id": "200"},
            ),
        ]
        save_cursors("https://x.com/user", items_old, config)
        save_cursors("https://x.com/user", items_new, config)

        since_id, since_ts = load_cursors("https://x.com/user", config)
        assert since_id == "200"
        assert since_ts == datetime(2024, 7, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Collected tweets deduplication (DB-level)
# ---------------------------------------------------------------------------


class TestCollectedTweetsPersistence:
    """Tests for collected_tweets CRUD in SQLite."""

    def test_record_and_check(self, config: WikiConfig):
        """Record a tweet and verify it's detected as collected."""
        record_collected_tweet("12345", "https://x.com/user", "sources/t.md", config)
        assert is_tweet_collected("12345", config) is True
        assert is_tweet_collected("99999", config) is False

    def test_empty_tweet_id_not_collected(self, config: WikiConfig):
        """Empty tweet ID always returns False."""
        assert is_tweet_collected("", config) is False

    def test_idempotent_insert(self, config: WikiConfig):
        """Recording the same tweet twice doesn't error (INSERT OR IGNORE)."""
        record_collected_tweet("111", "https://x.com/a", "sources/a.md", config)
        record_collected_tweet("111", "https://x.com/b", "sources/b.md", config)
        assert is_tweet_collected("111", config) is True

    def test_batch_filter(self, config: WikiConfig):
        """Batch filter returns only uncollected IDs."""
        record_collected_tweet("100", "https://x.com/u", "s/1.md", config)
        record_collected_tweet("200", "https://x.com/u", "s/2.md", config)

        uncollected = filter_uncollected_tweet_ids(
            ["100", "200", "300", "400"], config,
        )
        assert uncollected == {"300", "400"}

    def test_batch_filter_empty(self, config: WikiConfig):
        """Empty input returns empty set."""
        assert filter_uncollected_tweet_ids([], config) == set()

    def test_batch_record(self, config: WikiConfig):
        """Batch record multiple tweets."""
        records = [
            ("501", "https://x.com/u", "s/1.md"),
            ("502", "https://x.com/u", "s/2.md"),
            ("503", "https://x.com/u", "s/3.md"),
        ]
        inserted = record_collected_tweets_batch(records, config)
        assert inserted == 3
        assert is_tweet_collected("501", config) is True
        assert is_tweet_collected("502", config) is True

    def test_batch_record_skips_duplicates(self, config: WikiConfig):
        """Batch record ignores already-recorded tweets."""
        record_collected_tweet("600", "https://x.com/u", "s/old.md", config)
        records = [
            ("600", "https://x.com/u", "s/old.md"),  # already exists
            ("601", "https://x.com/u", "s/new.md"),
        ]
        inserted = record_collected_tweets_batch(records, config)
        assert inserted == 1

    def test_delete_collected_tweets(self, config: WikiConfig):
        """Delete all records for a subscription."""
        record_collected_tweet("700", "https://x.com/alice", "s/1.md", config)
        record_collected_tweet("701", "https://x.com/alice", "s/2.md", config)
        record_collected_tweet("800", "https://x.com/bob", "s/3.md", config)

        deleted = delete_collected_tweets("https://x.com/alice", config)
        assert deleted == 2
        assert is_tweet_collected("700", config) is False
        assert is_tweet_collected("800", config) is True  # bob's untouched

    def test_cross_subscription_dedup(self, config: WikiConfig):
        """Same tweet collected via sub A is detected when sub B tries."""
        record_collected_tweet("999", "https://x.com/alice", "s/a.md", config)
        # Even though bob didn't collect it, the tweet ID is globally unique
        assert is_tweet_collected("999", config) is True


class TestFilterUncollectedItems:
    """Tests for the collector-level DB dedup filter."""

    def test_filters_already_collected(self, config: WikiConfig):
        """Items with tweet_ids in collected_tweets are filtered."""
        record_collected_tweet("100", "https://x.com/u", "s/1.md", config)
        record_collected_tweet("101", "https://x.com/u", "s/2.md", config)

        items = [
            FetchedItem(url="u1", title="old", extra={"tweet_id": "100"}),
            FetchedItem(url="u2", title="old2", extra={"tweet_id": "101"}),
            FetchedItem(url="u3", title="new", extra={"tweet_id": "102"}),
        ]
        new, skipped = filter_uncollected_items(items, config)
        assert len(new) == 1
        assert skipped == 2
        assert new[0].extra["tweet_id"] == "102"

    def test_items_without_id_included(self, config: WikiConfig):
        """Items without tweet_id are conservatively included."""
        items = [
            FetchedItem(url="u1", title="no id", extra={}),
            FetchedItem(url="u2", title="has id", extra={"tweet_id": "999"}),
        ]
        new, skipped = filter_uncollected_items(items, config)
        assert len(new) == 2
        assert skipped == 0

    def test_empty_items(self, config: WikiConfig):
        """Empty input returns empty output."""
        new, skipped = filter_uncollected_items([], config)
        assert len(new) == 0
        assert skipped == 0


# ---------------------------------------------------------------------------
# Source file writing
# ---------------------------------------------------------------------------


class TestSaveTweetAsSource:
    def test_creates_source_file(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """Creates a markdown source file with correct frontmatter."""
        item = FetchedItem(
            url="https://x.com/testuser/status/12345",
            title="Hello world tweet",
            summary="Hello world! This is a tweet.",
            author="@testuser",
            published_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
            source_type="twitter",
            extra={
                "tweet_id": "12345",
                "like_count": 42,
                "retweet_count": 10,
            },
        )

        path = save_tweet_as_source(item, subscription, config)

        assert path.exists()
        content = path.read_text()
        assert "type: twitter" in content
        assert "tweet_id: 12345" in content
        assert "Hello world tweet" in content
        assert "@testuser" in content
        assert "lenses:" in content
        assert "  - tech" in content

    def test_immutable_no_overwrite(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """Same tweet URL creates a new file (never overwrites)."""
        item = FetchedItem(
            url="https://x.com/testuser/status/99999",
            title="Dup tweet",
            source_type="twitter",
            extra={"tweet_id": "99999"},
        )

        path1 = save_tweet_as_source(item, subscription, config)
        path2 = save_tweet_as_source(item, subscription, config)

        assert path1.exists()
        assert path2.exists()
        assert path1 != path2


# ---------------------------------------------------------------------------
# End-to-end incremental collection (mocked HTTP)
# ---------------------------------------------------------------------------


# Sample twitterapi.io response
TIMELINE_RESPONSE_BATCH1 = {
    "data": {
        "tweets": [
            {
                "id": "2001",
                "text": "Tweet A",
                "url": "https://x.com/testuser/status/2001",
                "createdAt": "2024-07-10T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 50,
                "retweetCount": 20,
                "replyCount": 5,
            },
            {
                "id": "2000",
                "text": "Tweet B",
                "url": "https://x.com/testuser/status/2000",
                "createdAt": "2024-07-09T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 30,
                "retweetCount": 10,
                "replyCount": 2,
            },
        ]
    },
    "has_next_page": False,
}

TIMELINE_RESPONSE_BATCH2 = {
    "data": {
        "tweets": [
            {
                "id": "2003",
                "text": "Tweet D (newest)",
                "url": "https://x.com/testuser/status/2003",
                "createdAt": "2024-07-12T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 100,
            },
            {
                "id": "2002",
                "text": "Tweet C",
                "url": "https://x.com/testuser/status/2002",
                "createdAt": "2024-07-11T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 70,
            },
            {
                "id": "2001",
                "text": "Tweet A (already seen)",
                "url": "https://x.com/testuser/status/2001",
                "createdAt": "2024-07-10T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 55,
            },
        ]
    },
    "has_next_page": False,
}


class TestCollectTwitterIncremental:
    """End-to-end tests for incremental Twitter collection."""

    @respx.mock
    async def test_first_fetch_no_cursors(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """First fetch with no stored cursors — collects all tweets."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH1,
        )

        result = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result.ok is True
        assert result.new_items == 2
        assert result.skipped_items == 0
        assert result.since_id_before is None
        assert result.since_id_after == "2001"
        assert result.source_files is not None
        assert len(result.source_files) == 2

        # Verify cursors were saved
        since_id, since_ts = load_cursors(subscription.url, config)
        assert since_id == "2001"
        assert since_ts is not None

    @respx.mock
    async def test_incremental_fetch_filters_old(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """Second fetch — only new tweets (since_id > 2001) are collected.

        Two layers of filtering work together:
        1. TwitterFetcher's `since` timestamp filter removes tweets
           with created_at <= since_timestamp (time-based, coarse).
        2. Our since_id filter removes tweets with id <= since_id
           (ID-based, precise, catches edge cases where timestamps match).
        """
        # Simulate first fetch already completed: set cursors
        # Use a timestamp BEFORE tweet 2001 so the fetcher doesn't filter it
        save_fetch_cursor(subscription.url, "since_id", "2001", config)
        save_fetch_cursor(
            subscription.url, "since_timestamp",
            "2024-07-09T00:00:00+00:00", config,
        )

        # API returns 3 tweets including 2001 (since_timestamp is before 2001's date)
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH2,
        )

        result = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result.ok is True
        assert result.new_items == 2  # only tweets 2002, 2003
        assert result.skipped_items == 1  # tweet 2001 filtered by since_id
        assert result.since_id_before == "2001"
        assert result.since_id_after == "2003"

        # Verify cursors updated
        since_id, since_ts = load_cursors(subscription.url, config)
        assert since_id == "2003"

    @respx.mock
    async def test_first_fetch_records_collected_tweets(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """First fetch records all collected tweet IDs in the DB."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH1,
        )

        result = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result.ok is True
        assert result.new_items == 2

        # Verify tweet IDs recorded in collected_tweets table
        assert is_tweet_collected("2001", config) is True
        assert is_tweet_collected("2000", config) is True
        assert is_tweet_collected("9999", config) is False  # not fetched

    @respx.mock
    async def test_db_dedup_on_cursor_reset(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """After cursor reset, DB-level dedup prevents re-collecting tweets.

        Simulates: first fetch records tweets → cursors are reset →
        second fetch returns same tweets → DB dedup catches them.
        """
        # First fetch
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH1,
        )
        result1 = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )
        assert result1.ok is True
        assert result1.new_items == 2

        # Reset cursors (simulating manual reset or error recovery)
        delete_fetch_cursors(subscription.url, config)

        # Second fetch returns same tweets — DB dedup catches them
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH1,
        )
        result2 = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result2.ok is True
        assert result2.new_items == 0  # all caught by DB dedup
        assert result2.skipped_items == 2

    @respx.mock
    async def test_cross_subscription_dedup(
        self, config: WikiConfig,
    ):
        """Same tweet from different subscriptions is only saved once.

        Simulates: user subscribes to @alice and @bob, both retweet the same tweet.
        """
        sub_alice = Subscription(
            url="https://x.com/alice", title="@alice",
            source_type="twitter", lenses=["tech"],
        )
        sub_bob = Subscription(
            url="https://x.com/bob", title="@bob",
            source_type="twitter", lenses=["tech"],
        )

        # Both return the same tweet (ID 2000)
        shared_response = {
            "data": {
                "tweets": [{
                    "id": "2000",
                    "text": "Shared tweet",
                    "url": "https://x.com/alice/status/2000",
                    "createdAt": "2024-07-09T10:00:00Z",
                    "author": {"userName": "alice", "name": "Alice"},
                }]
            },
            "has_next_page": False,
        }

        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=shared_response,
        )
        r1 = await collect_twitter_incremental(sub_alice, config, api_key="k")
        assert r1.ok is True
        assert r1.new_items == 1

        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=shared_response,
        )
        r2 = await collect_twitter_incremental(sub_bob, config, api_key="k")
        assert r2.ok is True
        assert r2.new_items == 0  # caught by cross-subscription DB dedup
        assert r2.skipped_items >= 1

    @respx.mock
    async def test_no_new_tweets(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """When all tweets are old, no files are created."""
        save_fetch_cursor(subscription.url, "since_id", "9999", config)

        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TIMELINE_RESPONSE_BATCH1,
        )

        result = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result.ok is True
        assert result.new_items == 0
        assert result.skipped_items == 2
        assert result.source_files is None or len(result.source_files) == 0

    @respx.mock
    async def test_api_error_returns_failure(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """API error → result.ok is False with error message."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            500, text="Internal Server Error",
        )

        result = await collect_twitter_incremental(
            subscription, config, api_key="test-key",
        )

        assert result.ok is False
        assert result.error != ""

    @respx.mock
    async def test_no_api_key_returns_error(
        self, config: WikiConfig, subscription: Subscription,
    ):
        """No API key for profile URL → error."""
        result = await collect_twitter_incremental(
            subscription, config, api_key="",
        )

        assert result.ok is False
        assert "TWITTER_API_KEY" in result.error


# ---------------------------------------------------------------------------
# DB migration test
# ---------------------------------------------------------------------------


class TestDBMigration:
    """Tests for the database schema and migrations."""

    def test_fetch_cursors_table_exists(self, db_conn):
        """fetch_cursors table should exist after init_db."""
        row = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fetch_cursors'"
        ).fetchone()
        assert row is not None

    def test_collected_tweets_table_exists(self, db_conn):
        """collected_tweets table should exist after init_db (v3)."""
        row = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='collected_tweets'"
        ).fetchone()
        assert row is not None

    def test_collected_tweets_primary_key(self, db_conn):
        """Primary key on tweet_id prevents duplicates."""
        db_conn.execute(
            "INSERT INTO collected_tweets VALUES (?, ?, ?, ?)",
            ("t1", "url1", "s/1.md", "2024-01-01T00:00:00+00:00"),
        )
        # Same tweet_id → INSERT OR IGNORE should silently skip
        db_conn.execute(
            "INSERT OR IGNORE INTO collected_tweets VALUES (?, ?, ?, ?)",
            ("t1", "url2", "s/2.md", "2024-01-02T00:00:00+00:00"),
        )
        db_conn.commit()

        rows = db_conn.execute(
            "SELECT * FROM collected_tweets WHERE tweet_id = ?", ("t1",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["subscription_url"] == "url1"  # first insert wins

    def test_primary_key_constraint(self, db_conn):
        """Primary key on (subscription_url, cursor_type) prevents duplicates."""
        db_conn.execute(
            "INSERT INTO fetch_cursors VALUES (?, ?, ?, ?)",
            ("url1", "since_id", "100", "2024-01-01T00:00:00+00:00"),
        )
        # Same key → should replace with INSERT OR REPLACE
        db_conn.execute(
            "INSERT OR REPLACE INTO fetch_cursors VALUES (?, ?, ?, ?)",
            ("url1", "since_id", "200", "2024-01-02T00:00:00+00:00"),
        )
        db_conn.commit()

        rows = db_conn.execute(
            "SELECT * FROM fetch_cursors WHERE subscription_url = ?",
            ("url1",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["cursor_value"] == "200"

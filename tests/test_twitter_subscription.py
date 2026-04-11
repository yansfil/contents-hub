"""Tests for Twitter/X subscription registration and validation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from llm_wiki.config import WikiConfig
from llm_wiki.subscriptions import SubscriptionStore
from llm_wiki.twitter_subscription import (
    validate_twitter_url,
    validate_and_probe_twitter_url,
    add_twitter_subscription,
    TwitterValidationResult,
    TwitterSubscriptionResult,
    _canonical_subscription_url,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    v = tmp_path / "vault"
    v.mkdir(parents=True)
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    return SubscriptionStore(config)


# Sample API responses
FXEMBED_TWEET_RESPONSE = {
    "tweet": {
        "id": "12345",
        "text": "Hello from Twitter!",
        "url": "https://x.com/testuser/status/12345",
        "created_at": "2024-06-15T12:00:00Z",
        "author": {
            "screen_name": "testuser",
            "name": "Test User",
            "avatar_url": "https://pbs.twimg.com/profile/test.jpg",
        },
        "likes": 100,
        "retweets": 50,
        "replies": 10,
        "views": 5000,
    }
}

TWITTERAPI_TIMELINE_RESPONSE = {
    "data": {
        "tweets": [
            {
                "id": "99901",
                "text": "Latest tweet",
                "url": "https://x.com/testuser/status/99901",
                "createdAt": "2024-07-10T10:00:00Z",
                "author": {"userName": "testuser", "name": "Test User"},
                "likeCount": 50,
                "retweetCount": 20,
                "replyCount": 5,
            },
        ]
    },
    "has_next_page": False,
}


# ---------------------------------------------------------------------------
# Tests: validate_twitter_url (pure, no I/O)
# ---------------------------------------------------------------------------


class TestValidateTwitterUrl:
    def test_profile_url_x(self):
        result = validate_twitter_url("https://x.com/elonmusk")
        assert result.valid is True
        assert result.username == "elonmusk"
        assert result.url_type == "profile"
        assert "x.com" in result.url

    def test_profile_url_twitter(self):
        result = validate_twitter_url("https://twitter.com/OpenAI")
        assert result.valid is True
        assert result.username == "OpenAI"
        assert result.url_type == "profile"
        # Normalized to x.com
        assert "x.com" in result.url

    def test_status_url(self):
        result = validate_twitter_url("https://x.com/jack/status/20")
        assert result.valid is True
        assert result.username == "jack"
        assert result.url_type == "status"

    def test_nitter_url_normalized(self):
        result = validate_twitter_url("https://nitter.net/user123")
        assert result.valid is True
        assert result.username == "user123"
        assert "x.com" in result.url

    def test_trailing_slash(self):
        result = validate_twitter_url("https://x.com/user/")
        assert result.valid is True
        assert result.username == "user"

    def test_whitespace_trimmed(self):
        result = validate_twitter_url("  https://x.com/user  ")
        assert result.valid is True
        assert result.username == "user"

    def test_empty_url(self):
        result = validate_twitter_url("")
        assert result.valid is False
        assert "empty" in result.error.lower()

    def test_non_twitter_url(self):
        result = validate_twitter_url("https://youtube.com/channel/UC123")
        assert result.valid is False
        assert "Not a Twitter" in result.error

    def test_reserved_path(self):
        result = validate_twitter_url("https://x.com/home")
        assert result.valid is False
        assert "Cannot extract username" in result.error

    def test_settings_path(self):
        result = validate_twitter_url("https://x.com/settings")
        assert result.valid is False

    def test_invalid_username_chars(self):
        result = validate_twitter_url("https://x.com/user-name")
        assert result.valid is False

    def test_username_too_long(self):
        result = validate_twitter_url("https://x.com/" + "a" * 16)
        assert result.valid is False


# ---------------------------------------------------------------------------
# Tests: validate_and_probe_twitter_url (async, mocked HTTP)
# ---------------------------------------------------------------------------


class TestValidateAndProbe:
    @respx.mock
    async def test_profile_probe_with_api_key(self):
        """Profile URL probed via twitterapi.io."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TWITTERAPI_TIMELINE_RESPONSE,
        )

        result = await validate_and_probe_twitter_url(
            "https://x.com/testuser",
            api_key="test-key",
        )
        assert result.valid is True
        assert result.username == "testuser"

    @respx.mock
    async def test_profile_probe_without_api_key(self):
        """Profile URL without API key — still valid (warning only)."""
        result = await validate_and_probe_twitter_url(
            "https://x.com/someuser",
            api_key="",
        )
        # Should still be valid (URL format OK, just can't verify)
        assert result.valid is True
        assert result.username == "someuser"

    @respx.mock
    async def test_status_probe_fxembed_success(self):
        """Status URL probed via FxEmbed."""
        respx.get("https://api.fxtwitter.com/i/status/12345").respond(
            200, json=FXEMBED_TWEET_RESPONSE,
        )

        result = await validate_and_probe_twitter_url(
            "https://x.com/testuser/status/12345",
        )
        assert result.valid is True
        assert result.url_type == "status"

    @respx.mock
    async def test_status_probe_fxembed_fails(self):
        """Status URL where FxEmbed fails — probe fails."""
        respx.get("https://api.fxtwitter.com/i/status/99999").respond(404)

        result = await validate_and_probe_twitter_url(
            "https://x.com/user/status/99999",
            api_key="",
        )
        assert result.valid is False
        assert "Probe failed" in result.error

    async def test_non_twitter_fails_fast(self):
        """Non-Twitter URL fails without network calls."""
        result = await validate_and_probe_twitter_url("https://example.com/page")
        assert result.valid is False
        assert "Not a Twitter" in result.error


# ---------------------------------------------------------------------------
# Tests: _canonical_subscription_url
# ---------------------------------------------------------------------------


class TestCanonicalUrl:
    def test_profile_url(self):
        url = _canonical_subscription_url("https://x.com/elonmusk", "elonmusk")
        assert url == "https://x.com/elonmusk"

    def test_twitter_domain_normalized(self):
        url = _canonical_subscription_url("https://twitter.com/user", "user")
        assert url == "https://x.com/user"

    def test_status_url_kept(self):
        url = _canonical_subscription_url(
            "https://x.com/user/status/12345", "user"
        )
        assert url == "https://x.com/user/status/12345"


# ---------------------------------------------------------------------------
# Tests: add_twitter_subscription (full flow)
# ---------------------------------------------------------------------------


class TestAddTwitterSubscription:
    async def test_skip_probe_profile(self, store: SubscriptionStore, config: WikiConfig):
        """skip_probe mode works for profile URLs."""
        result = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            skip_probe=True,
        )

        assert result.ok is True
        assert result.username == "testuser"
        assert result.url == "https://x.com/testuser"
        assert result.subscription is not None
        assert result.subscription.title == "@testuser"

        # Verify persisted
        assert store.count == 1
        stored = store.get("https://x.com/testuser")
        assert stored is not None
        assert stored.title == "@testuser"

    async def test_skip_probe_custom_title(self, store: SubscriptionStore, config: WikiConfig):
        """Custom title overrides default @username."""
        result = await add_twitter_subscription(
            "https://x.com/elonmusk",
            store,
            config,
            title="Elon Musk",
            skip_probe=True,
        )

        assert result.ok is True
        assert result.subscription.title == "Elon Musk"

    async def test_skip_probe_with_lenses(self, store: SubscriptionStore, config: WikiConfig):
        """Lenses are stored with the subscription."""
        result = await add_twitter_subscription(
            "https://x.com/OpenAI",
            store,
            config,
            lenses=["ai", "research"],
            skip_probe=True,
        )

        assert result.ok is True
        assert result.subscription.lenses == ["ai", "research"]

    async def test_duplicate_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Cannot subscribe to the same account twice."""
        result1 = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            skip_probe=True,
        )
        assert result1.ok is True

        result2 = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            skip_probe=True,
        )
        assert result2.ok is False
        assert "Already subscribed" in result2.error

    async def test_duplicate_via_twitter_domain(self, store: SubscriptionStore, config: WikiConfig):
        """Same account via twitter.com domain is detected as duplicate."""
        result1 = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            skip_probe=True,
        )
        assert result1.ok is True

        result2 = await add_twitter_subscription(
            "https://twitter.com/testuser",
            store,
            config,
            skip_probe=True,
        )
        assert result2.ok is False
        assert "Already subscribed" in result2.error

    async def test_invalid_url_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Non-Twitter URLs are rejected."""
        result = await add_twitter_subscription(
            "https://example.com/page",
            store,
            config,
            skip_probe=True,
        )
        assert result.ok is False
        assert store.count == 0

    async def test_reserved_path_rejected(self, store: SubscriptionStore, config: WikiConfig):
        """Reserved paths like /home are rejected."""
        result = await add_twitter_subscription(
            "https://x.com/home",
            store,
            config,
            skip_probe=True,
        )
        assert result.ok is False
        assert store.count == 0

    @respx.mock
    async def test_full_flow_with_probe(self, store: SubscriptionStore, config: WikiConfig):
        """Full flow: validate → probe → subscribe → dispatch."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TWITTERAPI_TIMELINE_RESPONSE,
        )

        result = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            lenses=["tech"],
            api_key="test-key",
        )

        assert result.ok is True
        assert result.username == "testuser"
        assert result.subscription is not None
        assert result.subscription.lenses == ["tech"]
        assert store.count == 1

    async def test_status_url_subscription(self, store: SubscriptionStore, config: WikiConfig):
        """Single tweet URL can be subscribed (one-time capture)."""
        result = await add_twitter_subscription(
            "https://x.com/jack/status/20",
            store,
            config,
            skip_probe=True,
        )

        assert result.ok is True
        assert result.username == "jack"
        assert "status/20" in result.url


# ---------------------------------------------------------------------------
# Tests: Schedule dispatch integration
# ---------------------------------------------------------------------------


class TestScheduleDispatch:
    async def test_schedule_created_on_subscribe(self, store: SubscriptionStore, config: WikiConfig):
        """Subscribing creates a schedule entry in SQLite."""
        result = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            skip_probe=True,
        )
        assert result.ok is True

        from llm_wiki.dispatch import get_schedule
        schedule = get_schedule("https://x.com/testuser", config)
        assert schedule is not None
        assert schedule.source_type == "twitter"
        assert schedule.enabled is True
        assert schedule.interval_minutes == 15  # Twitter default

    async def test_custom_interval(self, store: SubscriptionStore, config: WikiConfig):
        """Custom interval_minutes is passed to scheduler."""
        result = await add_twitter_subscription(
            "https://x.com/testuser",
            store,
            config,
            interval_minutes=30,
            skip_probe=True,
        )
        assert result.ok is True

        from llm_wiki.dispatch import get_schedule
        schedule = get_schedule("https://x.com/testuser", config)
        assert schedule.interval_minutes == 30


# ---------------------------------------------------------------------------
# Tests: Fetcher registry integration
# ---------------------------------------------------------------------------


class TestFetcherRegistry:
    def test_twitter_fetcher_registered(self):
        """Twitter fetcher is auto-registered in the registry."""
        from llm_wiki.fetchers.registry import is_registered, get_fetcher
        assert is_registered("twitter") is True

        fetcher = get_fetcher("twitter", "https://x.com/testuser")
        assert fetcher is not None
        assert fetcher.source_type == "twitter"

    def test_get_fetcher_for_url_twitter(self):
        """get_fetcher_for_url auto-detects Twitter URLs."""
        from llm_wiki.fetchers.registry import get_fetcher_for_url

        fetcher = get_fetcher_for_url("https://x.com/testuser")
        assert fetcher is not None
        assert fetcher.source_type == "twitter"

    def test_get_fetcher_for_url_twitter_com(self):
        """get_fetcher_for_url works with twitter.com domain too."""
        from llm_wiki.fetchers.registry import get_fetcher_for_url

        fetcher = get_fetcher_for_url("https://twitter.com/user")
        assert fetcher is not None
        assert fetcher.source_type == "twitter"

    def test_registered_types_includes_twitter(self):
        """registered_types includes twitter."""
        from llm_wiki.fetchers.registry import registered_types
        assert "twitter" in registered_types()

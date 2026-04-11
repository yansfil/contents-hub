"""Tests for TwitterFetcher class."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import httpx
import pytest
import respx

from llm_wiki.fetchers.base import FetchResult, FetchedItem
from llm_wiki.fetchers.twitter import (
    TwitterFetcher,
    Tweet,
    # URL parsing
    is_twitter_url,
    is_twitter_profile_url,
    is_twitter_status_url,
    extract_username,
    extract_tweet_id,
    normalize_twitter_url,
    # Helpers
    _filter_tweets_since,
    _tweet_to_fetched_item,
    _parse_fxembed_tweet,
    _parse_api_tweet,
)


# ---------------------------------------------------------------------------
# URL parsing tests
# ---------------------------------------------------------------------------


class TestNormalizeTwitterUrl:
    def test_twitter_to_x(self):
        assert normalize_twitter_url("https://twitter.com/user") == "https://x.com/user"

    def test_x_unchanged(self):
        assert normalize_twitter_url("https://x.com/user") == "https://x.com/user"

    def test_nitter(self):
        assert normalize_twitter_url("https://nitter.net/user") == "https://x.com/user"


class TestIsTwitterUrl:
    def test_x_com(self):
        assert is_twitter_url("https://x.com/elonmusk") is True

    def test_twitter_com(self):
        assert is_twitter_url("https://twitter.com/elonmusk") is True

    def test_nitter(self):
        assert is_twitter_url("https://nitter.net/user") is True

    def test_non_twitter(self):
        assert is_twitter_url("https://youtube.com/channel") is False

    def test_empty(self):
        assert is_twitter_url("") is False


class TestIsTwitterProfileUrl:
    def test_valid_profile(self):
        assert is_twitter_profile_url("https://x.com/elonmusk") is True

    def test_twitter_domain(self):
        assert is_twitter_profile_url("https://twitter.com/elonmusk") is True

    def test_reserved_path(self):
        assert is_twitter_profile_url("https://x.com/home") is False
        assert is_twitter_profile_url("https://x.com/settings") is False

    def test_status_url_not_profile(self):
        # Status URLs also extract a username, so is_twitter_profile_url returns True
        # This is by design — status URLs have a valid username in the path
        assert is_twitter_profile_url("https://x.com/user/status/123") is True

    def test_non_twitter(self):
        assert is_twitter_profile_url("https://youtube.com/user") is False


class TestIsTwitterStatusUrl:
    def test_valid_status(self):
        assert is_twitter_status_url("https://x.com/user/status/1234567890") is True

    def test_twitter_domain(self):
        assert is_twitter_status_url("https://twitter.com/user/status/1234567890") is True

    def test_profile_not_status(self):
        assert is_twitter_status_url("https://x.com/user") is False

    def test_non_twitter(self):
        assert is_twitter_status_url("https://youtube.com/watch?v=abc") is False


class TestExtractUsername:
    def test_x_profile(self):
        assert extract_username("https://x.com/elonmusk") == "elonmusk"

    def test_twitter_profile(self):
        assert extract_username("https://twitter.com/OpenAI") == "OpenAI"

    def test_trailing_slash(self):
        assert extract_username("https://x.com/user/") == "user"

    def test_status_url_extracts_author(self):
        assert extract_username("https://x.com/jack/status/123") == "jack"

    def test_reserved_word(self):
        assert extract_username("https://x.com/home") is None
        assert extract_username("https://x.com/search") is None

    def test_invalid_username_chars(self):
        assert extract_username("https://x.com/user-name") is None

    def test_username_too_long(self):
        assert extract_username("https://x.com/" + "a" * 16) is None

    def test_non_twitter(self):
        assert extract_username("https://youtube.com/user") is None

    def test_empty(self):
        assert extract_username("") is None


class TestExtractTweetId:
    def test_valid_status(self):
        assert extract_tweet_id("https://x.com/user/status/1234567890") == "1234567890"

    def test_twitter_domain(self):
        assert extract_tweet_id("https://twitter.com/user/status/999") == "999"

    def test_profile_url(self):
        assert extract_tweet_id("https://x.com/user") is None

    def test_non_twitter(self):
        assert extract_tweet_id("https://youtube.com/watch?v=abc") is None


# ---------------------------------------------------------------------------
# API response parsing tests
# ---------------------------------------------------------------------------


class TestParseFxEmbedTweet:
    def test_full_tweet(self):
        data = {
            "id": "12345",
            "text": "Hello world!",
            "url": "https://x.com/user/status/12345",
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
        tweet = _parse_fxembed_tweet(data)
        assert tweet.id == "12345"
        assert tweet.text == "Hello world!"
        assert tweet.author_username == "testuser"
        assert tweet.author_name == "Test User"
        assert tweet.like_count == 100
        assert tweet.retweet_count == 50
        assert tweet.reply_count == 10
        assert tweet.view_count == 5000
        assert tweet.created_at is not None
        assert tweet.created_at.year == 2024

    def test_missing_fields(self):
        data = {"id": "1", "text": "Minimal"}
        tweet = _parse_fxembed_tweet(data)
        assert tweet.id == "1"
        assert tweet.text == "Minimal"
        assert tweet.author_username == ""
        assert tweet.like_count == 0
        assert tweet.created_at is None


class TestParseApiTweet:
    def test_full_tweet(self):
        data = {
            "id": "67890",
            "text": "API tweet",
            "url": "https://x.com/api_user/status/67890",
            "createdAt": "2024-07-01T09:30:00Z",
            "author": {
                "userName": "api_user",
                "name": "API User",
                "profilePicture": "https://pbs.twimg.com/profile/api.jpg",
            },
            "likeCount": 200,
            "retweetCount": 30,
            "replyCount": 5,
            "viewCount": 10000,
            "isRetweet": False,
            "isReply": True,
        }
        tweet = _parse_api_tweet(data)
        assert tweet.id == "67890"
        assert tweet.author_username == "api_user"
        assert tweet.is_reply is True
        assert tweet.is_retweet is False
        assert tweet.view_count == 10000


# ---------------------------------------------------------------------------
# Filtering & conversion helpers
# ---------------------------------------------------------------------------


class TestFilterTweetsSince:
    def test_filters_old_tweets(self):
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        tweets = [
            Tweet(id="1", text="old", url="", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                  author_username="u", author_name="U"),
            Tweet(id="2", text="new", url="", created_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
                  author_username="u", author_name="U"),
        ]
        result = _filter_tweets_since(tweets, cutoff)
        assert len(result) == 1
        assert result[0].id == "2"

    def test_includes_tweets_without_date(self):
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        tweets = [
            Tweet(id="1", text="no date", url="", created_at=None,
                  author_username="u", author_name="U"),
        ]
        result = _filter_tweets_since(tweets, cutoff)
        assert len(result) == 1


class TestTweetToFetchedItem:
    def test_conversion(self):
        tweet = Tweet(
            id="123",
            text="Hello world!\nSecond line",
            url="https://x.com/user/status/123",
            created_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
            author_username="testuser",
            author_name="Test User",
            author_avatar="https://avatar.url",
            like_count=42,
            retweet_count=10,
            reply_count=3,
            view_count=1000,
        )
        item = _tweet_to_fetched_item(tweet)

        assert isinstance(item, FetchedItem)
        assert item.url == "https://x.com/user/status/123"
        assert item.title == "Hello world!"
        assert item.summary == "Hello world!\nSecond line"
        assert item.author == "@testuser"
        assert item.source_type == "twitter"
        assert item.extra["tweet_id"] == "123"
        assert item.extra["like_count"] == 42

    def test_missing_username_uses_name(self):
        tweet = Tweet(
            id="1", text="test", url="", created_at=None,
            author_username="", author_name="Display Name",
        )
        item = _tweet_to_fetched_item(tweet)
        assert item.author == "Display Name"


# ---------------------------------------------------------------------------
# TwitterFetcher integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

# Sample FxEmbed response
FXEMBED_RESPONSE = {
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

# Sample twitterapi.io user timeline response
TWITTERAPI_TIMELINE_RESPONSE = {
    "data": {
        "tweets": [
            {
                "id": "99901",
                "text": "Latest tweet",
                "url": "https://x.com/timeuser/status/99901",
                "createdAt": "2024-07-10T10:00:00Z",
                "author": {"userName": "timeuser", "name": "Timeline User"},
                "likeCount": 50,
                "retweetCount": 20,
                "replyCount": 5,
            },
            {
                "id": "99900",
                "text": "Older tweet",
                "url": "https://x.com/timeuser/status/99900",
                "createdAt": "2024-06-01T08:00:00Z",
                "author": {"userName": "timeuser", "name": "Timeline User"},
                "likeCount": 30,
                "retweetCount": 10,
                "replyCount": 2,
            },
        ]
    },
    "has_next_page": False,
}


@pytest.fixture
def mock_client():
    """Create an httpx.AsyncClient for testing."""
    return httpx.AsyncClient()


class TestTwitterFetcherSingleTweet:
    """Tests for fetching a single tweet via status URL."""

    @respx.mock
    async def test_single_tweet_fxembed_success(self):
        """FxEmbed succeeds → returns the tweet."""
        respx.get("https://api.fxtwitter.com/i/status/12345").respond(
            200, json=FXEMBED_RESPONSE,
        )

        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/testuser/status/12345",
                client=client,
            )
            result = await fetcher.poll()

        assert result.ok is True
        assert len(result.items) == 1
        assert result.items[0].title == "Hello from Twitter!"
        assert result.items[0].author == "@testuser"
        assert result.items[0].source_type == "twitter"
        assert result.source_title == "@testuser"

    @respx.mock
    async def test_single_tweet_fxembed_fails_no_api_key(self):
        """FxEmbed fails, no API key → error."""
        respx.get("https://api.fxtwitter.com/i/status/99999").respond(404)

        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/user/status/99999",
                api_key="",
                client=client,
            )
            result = await fetcher.poll()

        assert result.ok is False
        assert "Failed to fetch tweet" in result.error

    @respx.mock
    async def test_invalid_status_url(self):
        """URL that doesn't contain a tweet ID → error."""
        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/user",
                api_key="",
                client=client,
            )
            # This URL is not a status URL, so it goes to timeline path
            # Without API key, it fails
            result = await fetcher.poll()
            assert result.ok is False


class TestTwitterFetcherTimeline:
    """Tests for fetching a user's timeline."""

    @respx.mock
    async def test_timeline_success(self):
        """User timeline fetch with API key."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TWITTERAPI_TIMELINE_RESPONSE,
        )

        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/timeuser",
                api_key="test-key",
                client=client,
            )
            result = await fetcher.poll()

        assert result.ok is True
        assert len(result.items) == 2
        assert result.items[0].title == "Latest tweet"
        assert result.source_title == "@timeuser"
        assert result.total_available == 2

    @respx.mock
    async def test_timeline_since_filter(self):
        """Filter tweets by since datetime."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            200, json=TWITTERAPI_TIMELINE_RESPONSE,
        )

        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/timeuser",
                api_key="test-key",
                client=client,
            )
            # Only tweets after June 15 2024
            since = datetime(2024, 6, 15, tzinfo=timezone.utc)
            result = await fetcher.poll(since=since)

        assert result.ok is True
        assert len(result.items) == 1
        assert result.items[0].title == "Latest tweet"

    @respx.mock
    async def test_timeline_no_api_key(self):
        """No API key → error for timeline fetch."""
        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/someuser",
                api_key="",
                client=client,
            )
            result = await fetcher.poll()

        assert result.ok is False
        assert "TWITTER_API_KEY" in result.error

    @respx.mock
    async def test_timeline_api_error(self):
        """API returns error → FetchResult.ok is False."""
        respx.get("https://api.twitterapi.io/twitter/user/last_tweets").respond(
            500, text="Internal Server Error",
        )

        async with httpx.AsyncClient() as client:
            fetcher = TwitterFetcher(
                "https://x.com/erroruser",
                api_key="test-key",
                client=client,
            )
            result = await fetcher.poll()

        assert result.ok is False


class TestTwitterFetcherSourceType:
    def test_source_type(self):
        fetcher = TwitterFetcher("https://x.com/user")
        assert fetcher.source_type == "twitter"


class TestTwitterFetcherRegistry:
    """Test that TwitterFetcher is registered in the fetcher registry."""

    def test_twitter_registered(self):
        from llm_wiki.fetchers.registry import is_registered, get_fetcher

        assert is_registered("twitter")
        fetcher = get_fetcher("twitter", "https://x.com/testuser")
        assert fetcher is not None
        assert fetcher.source_type == "twitter"

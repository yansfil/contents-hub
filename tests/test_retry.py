"""Tests for the retry logic with exponential backoff.

Verifies:
  1. Successful calls return immediately (no retry).
  2. Retryable errors trigger retries up to max_attempts.
  3. Non-retryable errors are raised immediately (no retry).
  4. Backoff delay increases with each attempt.
  5. RetryStats tracks attempts correctly.
  6. on_retry callback is invoked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_wiki.errors import (
    AuthError,
    ErrorType,
    NetworkError,
    NotFoundError,
    PipelineError,
    RateLimitError,
)
from llm_wiki.retry import RetryStats, retry_async, _calculate_delay


# ---------------------------------------------------------------------------
# retry_async: basic behavior
# ---------------------------------------------------------------------------


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Successful call returns immediately without retry."""
        fn = AsyncMock(return_value="ok")
        result = await retry_async(fn, max_attempts=3)
        assert result == "ok"
        fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        """Retries on transient (network) errors up to max_attempts."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Connection timed out")
            return "ok"

        result = await retry_async(
            flaky,
            max_attempts=3,
            base_delay=0.01,  # Fast for testing
            max_delay=0.05,
        )
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        """Non-retryable errors (auth, parse) are raised without retry."""
        call_count = 0

        async def auth_fail():
            nonlocal call_count
            call_count += 1
            raise AuthError("Invalid API key")

        with pytest.raises(PipelineError) as exc_info:
            await retry_async(auth_fail, max_attempts=3, base_delay=0.01)

        assert call_count == 1  # No retry
        assert exc_info.value.error_type == ErrorType.AUTH

    @pytest.mark.asyncio
    async def test_not_found_not_retried(self):
        """404 errors should not be retried."""
        fn = AsyncMock(side_effect=NotFoundError("404 Not Found"))
        with pytest.raises(PipelineError) as exc_info:
            await retry_async(fn, max_attempts=3, base_delay=0.01)

        fn.assert_awaited_once()
        assert exc_info.value.error_type == ErrorType.NOT_FOUND

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted(self):
        """Raises after all retry attempts are exhausted."""
        fn = AsyncMock(side_effect=TimeoutError("timeout"))

        with pytest.raises(PipelineError) as exc_info:
            await retry_async(fn, max_attempts=2, base_delay=0.01)

        assert fn.await_count == 2
        assert exc_info.value.error_type == ErrorType.NETWORK

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        """on_retry callback is invoked on each retry."""
        call_count = 0
        retries: list[tuple[int, PipelineError]] = []

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return "ok"

        def on_retry(attempt, error):
            retries.append((attempt, error))

        await retry_async(
            flaky,
            max_attempts=3,
            base_delay=0.01,
            on_retry=on_retry,
        )

        assert len(retries) == 2
        assert retries[0][0] == 1  # first retry attempt
        assert retries[1][0] == 2  # second retry attempt
        assert all(e.error_type == ErrorType.NETWORK for _, e in retries)

    @pytest.mark.asyncio
    async def test_raw_exception_classified(self):
        """Raw exceptions (not PipelineError) are classified before retry decision."""
        fn = AsyncMock(side_effect=ValueError("invalid literal"))

        # ValueError is classified as PARSE (non-retryable)
        with pytest.raises(PipelineError) as exc_info:
            await retry_async(fn, max_attempts=3, base_delay=0.01)

        fn.assert_awaited_once()  # No retry for parse errors
        assert exc_info.value.error_type == ErrorType.PARSE

    @pytest.mark.asyncio
    async def test_source_url_passed_to_classification(self):
        """source_url is included in classified errors."""
        fn = AsyncMock(side_effect=TimeoutError("timeout"))

        with pytest.raises(PipelineError) as exc_info:
            await retry_async(
                fn,
                max_attempts=1,
                source_url="https://feed.test/rss",
            )

        assert exc_info.value.source_url == "https://feed.test/rss"


# ---------------------------------------------------------------------------
# _calculate_delay
# ---------------------------------------------------------------------------


class TestCalculateDelay:
    def test_exponential_increase(self):
        err = NetworkError("timeout")
        err.retry_after = 0  # Don't use retry_after

        d1 = _calculate_delay(1, err, base_delay=2.0, max_delay=60.0, jitter=0.0)
        d2 = _calculate_delay(2, err, base_delay=2.0, max_delay=60.0, jitter=0.0)
        d3 = _calculate_delay(3, err, base_delay=2.0, max_delay=60.0, jitter=0.0)

        assert d1 == 2.0
        assert d2 == 4.0
        assert d3 == 8.0

    def test_max_delay_cap(self):
        err = NetworkError("timeout")
        err.retry_after = 0

        d = _calculate_delay(10, err, base_delay=2.0, max_delay=60.0, jitter=0.0)
        assert d == 60.0

    def test_retry_after_respected(self):
        err = RateLimitError("429", retry_after=120)

        d = _calculate_delay(1, err, base_delay=2.0, max_delay=300.0, jitter=0.0)
        assert d == 120.0

    def test_jitter_adds_variation(self):
        err = NetworkError("timeout")
        err.retry_after = 0

        delays = set()
        for _ in range(20):
            d = _calculate_delay(1, err, base_delay=2.0, max_delay=60.0, jitter=0.5)
            delays.add(round(d, 2))

        # With jitter, we should get different values
        assert len(delays) > 1

    def test_delay_never_negative(self):
        err = NetworkError("timeout")
        err.retry_after = 0

        for _ in range(50):
            d = _calculate_delay(1, err, base_delay=0.1, max_delay=60.0, jitter=1.0)
            assert d > 0


# ---------------------------------------------------------------------------
# RetryStats
# ---------------------------------------------------------------------------


class TestRetryStats:
    def test_empty_stats(self):
        stats = RetryStats()
        assert stats.total_retries == 0
        assert stats.has_retries is False
        assert stats.summary() == ""

    def test_record_retry(self):
        stats = RetryStats()
        err = NetworkError("timeout")
        stats.record_retry("https://ex.com/feed", 1, err)
        stats.record_retry("https://ex.com/feed", 2, err)

        assert stats.total_retries == 2
        assert stats.has_retries is True

    def test_summary_format(self):
        stats = RetryStats()
        err = NetworkError("timeout")
        stats.record_retry("https://a.com", 1, err)
        stats.record_retry("https://b.com", 1, err)

        summary = stats.summary()
        assert "2 time(s)" in summary
        assert "2 source(s)" in summary

    def test_to_dict(self):
        stats = RetryStats()
        err = NetworkError("timeout")
        stats.record_retry("https://ex.com", 1, err)

        d = stats.to_dict()
        assert d["total_retries"] == 1
        assert len(d["attempts"]) == 1
        assert d["attempts"][0]["source_url"] == "https://ex.com"
        assert d["attempts"][0]["error_type"] == "NETWORK"

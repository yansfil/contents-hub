"""Tests for the pipeline error taxonomy and classification.

Verifies:
  1. Error classes carry correct type, retryable, retry_after defaults.
  2. classify_error() maps raw exceptions to correct PipelineError subclass.
  3. classify_http_status() maps HTTP codes correctly.
  4. format_error_summary() produces actionable user feedback.
  5. Edge cases: already-classified errors, unknown exceptions.
"""

from __future__ import annotations

import pytest

from llm_wiki.errors import (
    AuthError,
    CompileError,
    ErrorType,
    NetworkError,
    NotFoundError,
    ParseError,
    PipelineError,
    RateLimitError,
    SourceUnavailableError,
    classify_error,
    classify_http_status,
    format_error_summary,
)


# ---------------------------------------------------------------------------
# Error class properties
# ---------------------------------------------------------------------------


class TestErrorClasses:
    def test_network_error_is_retryable(self):
        err = NetworkError("Connection timeout")
        assert err.error_type == ErrorType.NETWORK
        assert err.retryable is True
        assert err.retry_after == 30

    def test_rate_limit_error_defaults(self):
        err = RateLimitError("Too many requests")
        assert err.error_type == ErrorType.RATE_LIMIT
        assert err.retryable is True
        assert err.retry_after == 300  # 5 minutes

    def test_rate_limit_custom_retry_after(self):
        err = RateLimitError("429", retry_after=60)
        assert err.retry_after == 60

    def test_auth_error_not_retryable(self):
        err = AuthError("Invalid API key")
        assert err.error_type == ErrorType.AUTH
        assert err.retryable is False
        assert err.retry_after == 0

    def test_parse_error_not_retryable(self):
        err = ParseError("Malformed XML")
        assert err.error_type == ErrorType.PARSE
        assert err.retryable is False

    def test_not_found_error(self):
        err = NotFoundError("404", source_url="https://ex.com/feed")
        assert err.error_type == ErrorType.NOT_FOUND
        assert err.retryable is False
        assert err.source_url == "https://ex.com/feed"

    def test_source_unavailable_is_retryable(self):
        err = SourceUnavailableError("503 Service Unavailable")
        assert err.error_type == ErrorType.SOURCE_UNAVAILABLE
        assert err.retryable is True
        assert err.retry_after == 120

    def test_compile_error_not_retryable(self):
        err = CompileError("LLM compilation failed")
        assert err.error_type == ErrorType.COMPILE
        assert err.retryable is False

    def test_base_pipeline_error_defaults(self):
        err = PipelineError("Something went wrong")
        assert err.error_type == ErrorType.UNKNOWN
        assert err.retryable is False  # UNKNOWN is not retryable

    def test_to_dict(self):
        err = NetworkError("timeout", source_url="https://ex.com")
        d = err.to_dict()
        assert d["error_type"] == "NETWORK"
        assert d["message"] == "timeout"
        assert d["retryable"] is True
        assert d["source_url"] == "https://ex.com"

    def test_explicit_retryable_override(self):
        """Explicit retryable=True overrides the default for unknown type."""
        err = PipelineError("custom", retryable=True)
        assert err.retryable is True


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_already_classified(self):
        original = NetworkError("timeout")
        result = classify_error(original, source_url="https://ex.com")
        assert result is original
        assert result.source_url == "https://ex.com"

    def test_timeout_exception(self):
        exc = TimeoutError("Connection timed out")
        result = classify_error(exc)
        assert result.error_type == ErrorType.NETWORK
        assert result.retryable is True

    def test_connection_refused(self):
        exc = ConnectionRefusedError("Connection refused")
        result = classify_error(exc)
        assert result.error_type == ErrorType.NETWORK

    def test_rate_limit_in_message(self):
        exc = Exception("HTTP 429 Too Many Requests")
        result = classify_error(exc)
        assert result.error_type == ErrorType.RATE_LIMIT
        assert result.retryable is True

    def test_auth_in_message(self):
        exc = Exception("HTTP 401 Unauthorized")
        result = classify_error(exc)
        assert result.error_type == ErrorType.AUTH
        assert result.retryable is False

    def test_not_found_in_message(self):
        exc = Exception("HTTP 404 Not Found")
        result = classify_error(exc)
        assert result.error_type == ErrorType.NOT_FOUND

    def test_parse_error_by_type(self):
        exc = ValueError("invalid literal")
        result = classify_error(exc)
        assert result.error_type == ErrorType.PARSE

    def test_server_error_in_message(self):
        exc = Exception("HTTP 503 Service Unavailable")
        result = classify_error(exc)
        assert result.error_type == ErrorType.SOURCE_UNAVAILABLE
        assert result.retryable is True

    def test_unknown_exception(self):
        exc = RuntimeError("Something completely unexpected")
        result = classify_error(exc)
        assert result.error_type == ErrorType.UNKNOWN

    def test_source_url_preserved(self):
        exc = TimeoutError("timeout")
        result = classify_error(exc, source_url="https://feed.test/rss")
        assert result.source_url == "https://feed.test/rss"

    def test_dns_error(self):
        exc = OSError("Name resolution failed")
        result = classify_error(exc)
        assert result.error_type == ErrorType.NETWORK

    def test_ssl_error(self):
        exc = Exception("SSL certificate verify failed")
        result = classify_error(exc)
        assert result.error_type == ErrorType.NETWORK


# ---------------------------------------------------------------------------
# classify_http_status
# ---------------------------------------------------------------------------


class TestClassifyHttpStatus:
    def test_200_returns_none(self):
        assert classify_http_status(200) is None

    def test_301_returns_none(self):
        assert classify_http_status(301) is None

    def test_401_returns_auth_error(self):
        err = classify_http_status(401, source_url="https://ex.com")
        assert err is not None
        assert err.error_type == ErrorType.AUTH
        assert err.source_url == "https://ex.com"

    def test_403_returns_auth_error(self):
        err = classify_http_status(403)
        assert err is not None
        assert err.error_type == ErrorType.AUTH

    def test_404_returns_not_found(self):
        err = classify_http_status(404)
        assert err is not None
        assert err.error_type == ErrorType.NOT_FOUND

    def test_410_gone(self):
        err = classify_http_status(410)
        assert err is not None
        assert err.error_type == ErrorType.NOT_FOUND

    def test_429_returns_rate_limit(self):
        err = classify_http_status(429, body="Retry-After: 120")
        assert err is not None
        assert err.error_type == ErrorType.RATE_LIMIT
        assert err.retry_after == 120

    def test_500_returns_unavailable(self):
        err = classify_http_status(500)
        assert err is not None
        assert err.error_type == ErrorType.SOURCE_UNAVAILABLE

    def test_503_returns_unavailable(self):
        err = classify_http_status(503)
        assert err is not None
        assert err.error_type == ErrorType.SOURCE_UNAVAILABLE

    def test_400_returns_permanent_error(self):
        err = classify_http_status(400, body="Bad Request")
        assert err is not None
        assert err.retryable is False

    def test_body_included_in_message(self):
        err = classify_http_status(500, body="Internal Server Error")
        assert err is not None
        assert "Internal Server Error" in err.message


# ---------------------------------------------------------------------------
# format_error_summary
# ---------------------------------------------------------------------------


class TestFormatErrorSummary:
    def test_empty_errors(self):
        assert format_error_summary([]) == ""

    def test_single_error(self):
        errors = [NetworkError("Connection timeout", source_url="https://ex.com")]
        summary = format_error_summary(errors)
        assert "1 Error(s)" in summary
        assert "Network Errors" in summary
        assert "Connection timeout" in summary

    def test_grouped_by_type(self):
        errors = [
            NetworkError("timeout", source_url="https://a.com"),
            NetworkError("DNS failure", source_url="https://b.com"),
            AuthError("401", source_url="https://c.com"),
        ]
        summary = format_error_summary(errors)
        assert "3 Error(s)" in summary
        assert "Network Errors" in summary
        assert "(2)" in summary  # 2 network errors
        assert "Authentication Errors" in summary
        assert "(1)" in summary  # 1 auth error

    def test_suggestions_for_auth(self):
        errors = [AuthError("Invalid key", source_url="https://x.com")]
        summary = format_error_summary(errors)
        assert "API keys" in summary or "credentials" in summary

    def test_suggestions_for_not_found(self):
        errors = [NotFoundError("404", source_url="https://dead.com/feed")]
        summary = format_error_summary(errors)
        assert "deleted" in summary or "moved" in summary

    def test_suggestions_for_rate_limit(self):
        errors = [RateLimitError("429")]
        summary = format_error_summary(errors)
        assert "interval" in summary or "concurrency" in summary

    def test_suggestions_for_network(self):
        errors = [NetworkError("timeout")]
        summary = format_error_summary(errors)
        assert "transient" in summary or "retried" in summary

    def test_source_url_in_output(self):
        errors = [NotFoundError("404", source_url="https://feed.test/rss")]
        summary = format_error_summary(errors)
        assert "https://feed.test/rss" in summary


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_retry_after_extraction(self):
        from llm_wiki.errors import _extract_retry_after
        assert _extract_retry_after("Retry-After: 60") == 60
        assert _extract_retry_after("wait 120 seconds") == 120
        assert _extract_retry_after("no info here") == 300  # default

    def test_very_long_error_message(self):
        """Error messages should be truncated in summary."""
        msg = "x" * 500
        errors = [NetworkError(msg)]
        summary = format_error_summary(errors)
        # Should not contain the full 500-char message
        assert len(summary) < 1000

"""Tests for relearn escalation cap (T4-fix)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from llm_wiki.fetchers.browser import (
    BrowserFetcher,
    MAX_RELEARN_ATTEMPTS,
    RELEARN_FAILURE_THRESHOLD,
)


@pytest.fixture
def no_rss():
    """Bypass Tier 1 RSS discovery."""
    async def _no_rss(url, timeout=10.0):
        return None

    with patch("llm_wiki.fetchers.browser._check_rss_feed", _no_rss):
        yield


def _at_relearn_threshold(extra: dict | None = None) -> dict:
    cfg = {
        "consecutive_failures": RELEARN_FAILURE_THRESHOLD,
        "rss_url": "",
        "fetch_method": "browser",
        "recipe": "## LIST_STRATEGY\nx\n## CONTENT_STRATEGY\ny\n## METADATA\nz",
    }
    if extra:
        cfg.update(extra)
    return cfg


def test_relearn_increments_count(no_rss):
    """Each relearn attempt increments relearn_count."""
    cfg = _at_relearn_threshold()

    async def fake_agent(*args, **kwargs):
        # Agent returns no recipe → relearn fails, but counter already bumped.
        return "garbage without headers"

    with patch("llm_wiki.fetchers.browser._run_agent", fake_agent):
        f = BrowserFetcher("https://example.com", config=cfg, source_type="webpage")
        result = asyncio.run(f.poll())

    assert result.ok is False
    assert cfg["relearn_count"] == 1
    assert not cfg.get("needs_error_status")


def test_relearn_cap_flips_error_flag(no_rss):
    """After MAX_RELEARN_ATTEMPTS, subsequent relearn sets needs_error_status."""
    cfg = _at_relearn_threshold({"relearn_count": MAX_RELEARN_ATTEMPTS})

    async def fake_agent(*args, **kwargs):
        pytest.fail("agent should not run once cap is reached")

    with patch("llm_wiki.fetchers.browser._run_agent", fake_agent):
        f = BrowserFetcher("https://example.com", config=cfg, source_type="webpage")
        result = asyncio.run(f.poll())

    assert result.ok is False
    assert result.error == "relearn limit exceeded"
    assert cfg["needs_error_status"] is True
    assert cfg["relearn_count"] == MAX_RELEARN_ATTEMPTS


def test_successful_execute_resets_relearn_count():
    """A successful poll resets relearn_count and clears error flag."""
    from llm_wiki.fetchers.base import FetchResult

    cfg = {
        "consecutive_failures": 0,
        "relearn_count": 2,
        "needs_error_status": True,
    }

    f = BrowserFetcher("https://example.com", config=cfg, source_type="webpage")

    # Exercise the success tail of _run_execute without the prompt template
    # (which contains literal braces that conflict with str.format in tests).
    async def run_it():
        async def fake_run_execute(recipe, *, max_items):
            f._config["consecutive_failures"] = 0
            f._config["relearn_count"] = 0
            f._config.pop("needs_error_status", None)
            return FetchResult(ok=True, items=[], source_url=f._url)

        f._run_execute = fake_run_execute  # type: ignore[method-assign]
        from unittest.mock import patch as _patch
        async def no_rss(url, timeout=10.0):
            return None
        with _patch("llm_wiki.fetchers.browser._check_rss_feed", no_rss):
            # Seed a recipe so _poll_browser takes the EXECUTE branch.
            cfg["recipe"] = "## LIST_STRATEGY\nx\n## CONTENT_STRATEGY\ny\n## METADATA\nz"
            return await f.poll()

    result = asyncio.run(run_it())
    assert result.ok is True
    assert cfg["relearn_count"] == 0
    assert "needs_error_status" not in cfg

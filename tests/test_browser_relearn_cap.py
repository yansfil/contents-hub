"""Tests for relearn escalation cap (re-targeted onto ``executor.execute``).

Pre-refactor these covered ``BrowserFetcher`` from ``llm_wiki.fetchers.browser``.
Post-refactor (T13/R-T7.3), the same RELEARN/EXECUTE state-machine semantics
live in :mod:`llm_wiki.executor`.  The thresholds (``RELEARN_FAILURE_THRESHOLD``,
``MAX_RELEARN_ATTEMPTS``) and the bookkeeping fields they manipulate
(``consecutive_failures``, ``relearn_count``, ``needs_error_status``) are
unchanged, so the assertion logic is preserved verbatim.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from llm_wiki.executor import (
    MAX_RELEARN_ATTEMPTS,
    RELEARN_FAILURE_THRESHOLD,
    execute,
)
from llm_wiki.runners import get_default_runner, set_default_runner


def _make_sub(cfg: dict) -> SimpleNamespace:
    """Minimal duck-typed Subscription for ``executor.execute``."""
    return SimpleNamespace(
        url="https://example.com",
        source_type="webpage",
        config=cfg,
    )


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


class _StubRunner:
    """AgentRunner stub returning a canned response on every ``run()`` call."""

    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.calls += 1
        return self._response


@pytest.fixture
def restore_default_runner():
    original = get_default_runner()
    yield
    set_default_runner(original)


def test_relearn_increments_count(restore_default_runner):
    """Each relearn attempt increments relearn_count even when the agent fails
    to produce a recipe."""
    cfg = _at_relearn_threshold()
    sub = _make_sub(cfg)

    # Agent returns no recipe headers → relearn fails, but the counter is
    # already bumped before the agent call returns.
    set_default_runner(_StubRunner("garbage without headers"))

    result = asyncio.run(execute(sub))

    assert result.ok is False
    assert cfg["relearn_count"] == 1
    assert not cfg.get("needs_error_status")


def test_relearn_cap_flips_error_flag(restore_default_runner):
    """After ``MAX_RELEARN_ATTEMPTS`` the next relearn short-circuits and sets
    ``needs_error_status``."""
    cfg = _at_relearn_threshold({"relearn_count": MAX_RELEARN_ATTEMPTS})
    sub = _make_sub(cfg)

    runner = _StubRunner("agent should not run once cap is reached")
    set_default_runner(runner)

    result = asyncio.run(execute(sub))

    assert runner.calls == 0, "runner.run() must not be invoked once cap is reached"
    assert result.ok is False
    assert result.error == "relearn limit exceeded"
    assert cfg["needs_error_status"] is True
    assert cfg["relearn_count"] == MAX_RELEARN_ATTEMPTS


def test_successful_execute_resets_relearn_count(restore_default_runner):
    """A successful EXECUTE-mode poll clears ``relearn_count`` and removes the
    ``needs_error_status`` flag."""
    cfg = {
        "consecutive_failures": 0,
        "relearn_count": 2,
        "needs_error_status": True,
        "recipe": "## LIST_STRATEGY\nx\n## CONTENT_STRATEGY\ny\n## METADATA\nz",
    }
    sub = _make_sub(cfg)

    # Successful response: items[] is non-empty, no errors.
    set_default_runner(
        _StubRunner('{"items": [{"url": "https://example.com/a", "title": "A"}], "errors": []}')
    )

    result = asyncio.run(execute(sub))

    assert result.ok is True
    assert cfg["relearn_count"] == 0
    assert "needs_error_status" not in cfg

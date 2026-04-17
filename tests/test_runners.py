"""Tests for the AgentRunner abstraction."""

from __future__ import annotations

import asyncio

import pytest

from llm_wiki.runners import (
    AgentRunner,
    ClaudeSDKRunner,
    get_default_runner,
    set_default_runner,
)


def test_claude_sdk_runner_satisfies_protocol():
    runner = ClaudeSDKRunner()
    assert isinstance(runner, AgentRunner)


def test_default_runner_is_claude_sdk():
    runner = get_default_runner()
    assert isinstance(runner, ClaudeSDKRunner)


def test_default_runner_is_memoized():
    a = get_default_runner()
    b = get_default_runner()
    assert a is b


def test_set_default_runner_swaps_instance():
    class FakeRunner:
        async def run(self, prompt, *, max_turns=30, timeout=600.0):
            return f"fake:{prompt}"

    original = get_default_runner()
    try:
        fake = FakeRunner()
        set_default_runner(fake)  # type: ignore[arg-type]
        assert get_default_runner() is fake

        result = asyncio.run(fake.run("hi"))
        assert result == "fake:hi"
    finally:
        set_default_runner(original)


async def test_runner_browser_fetcher_uses_default_runner(monkeypatch):
    """BrowserFetcher._run_agent() should delegate to get_default_runner()."""
    from llm_wiki.fetchers.browser import _run_agent

    captured: dict = {}

    class FakeRunner:
        async def run(self, prompt, *, max_turns=30, timeout=600.0):
            captured["prompt"] = prompt
            captured["max_turns"] = max_turns
            captured["timeout"] = timeout
            return "OK"

    original = get_default_runner()
    try:
        set_default_runner(FakeRunner())  # type: ignore[arg-type]
        result = await _run_agent("hello", max_turns=5, timeout=1.0)
        assert result == "OK"
        assert captured == {"prompt": "hello", "max_turns": 5, "timeout": 1.0}
    finally:
        set_default_runner(original)

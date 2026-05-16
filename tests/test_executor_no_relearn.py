"""Regression tests for the simplified subscription executor.

Subscription fetches no longer run EXPLORE/RELEARN or rewrite recipes at
runtime. Agent-driven research belongs to the exploration workflow instead.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from contents_hub.executor import execute
from contents_hub.runners import get_default_runner, set_default_runner


def _make_sub(cfg: dict) -> SimpleNamespace:
    return SimpleNamespace(
        url="https://example.com",
        source_type="webpage",
        config=cfg,
    )


class _StubRunner:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.calls += 1
        self.prompts.append(prompt)
        return self._response


@pytest.fixture
def restore_default_runner():
    original = get_default_runner()
    yield
    set_default_runner(original)


def test_execute_ignores_legacy_relearn_flags_and_runs_existing_recipe(restore_default_runner):
    cfg = {
        "consecutive_failures": 3,
        "relearn_count": 2,
        "allow_relearn": True,
        "recipe": "## LIST_STRATEGY\nx\n## CONTENT_STRATEGY\ny\n## METADATA\nz",
    }
    sub = _make_sub(cfg)
    runner = _StubRunner(
        '{"items": [{"url": "https://example.com/a", "title": "A"}], "errors": []}'
    )
    set_default_runner(runner)

    result = asyncio.run(execute(sub))

    assert result.ok is True
    assert runner.calls == 1
    assert "Execute Prompt" in runner.prompts[0]
    assert "relearn_count" not in cfg
    assert "allow_relearn" not in cfg
    assert cfg["consecutive_failures"] == 0


def test_execute_does_not_explore_when_recipe_is_missing(restore_default_runner):
    cfg = {
        "allow_explore": True,
        "consecutive_failures": 0,
    }
    sub = _make_sub(cfg)
    sub.source_type = "unsupported.kind"
    runner = _StubRunner("should not be called")
    set_default_runner(runner)

    result = asyncio.run(execute(sub))

    assert result.ok is False
    assert "no built-in recipe" in result.error
    assert runner.calls == 0
    assert cfg["consecutive_failures"] == 1

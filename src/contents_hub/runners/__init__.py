"""Agent runner abstraction — decouples fetch/compile logic from a
specific LLM provider or agent harness (Claude SDK, Claude Code, Codex, etc.).

The base install defaults to a no-agent runner. Optional providers can be
selected with `CONTENTS_HUB_AGENT_RUNNER`.
"""

from __future__ import annotations

import os
from typing import Any

from contents_hub.runners.base import AgentRunner, AgentRunError
from contents_hub.runners.no_agent import NoAgentRunner

_DEFAULT: AgentRunner | None = None
_DEFAULT_TEXT: AgentRunner | None = None


def _selected_runner_name() -> str:
    return (os.environ.get("CONTENTS_HUB_AGENT_RUNNER") or "none").strip().lower()


def _build_runner(*, text_only: bool = False) -> AgentRunner:
    name = _selected_runner_name()
    if name in {"", "none", "off", "disabled"}:
        return NoAgentRunner()
    if name == "claude-sdk":
        try:
            from contents_hub.runners.claude_sdk import ClaudeSDKRunner
        except ModuleNotFoundError as exc:
            raise AgentRunError(
                "CONTENTS_HUB_AGENT_RUNNER=claude-sdk requires the Claude optional "
                "extra to be installed."
            ) from exc
        return ClaudeSDKRunner(text_only=text_only)
    raise AgentRunError(
        f"Unknown CONTENTS_HUB_AGENT_RUNNER={name!r}. Supported values: none, claude-sdk."
    )


def get_default_runner() -> AgentRunner:
    """Return the process-wide default runner (lazy-constructed)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = _build_runner()
    return _DEFAULT


def get_default_text_runner() -> AgentRunner:
    """Return the default runner for text-only synthesis calls.

    Tests that override the process-wide default runner keep working: when
    ``set_default_runner`` has installed a runner, text synthesis uses that
    instance. In a fresh CLI process, text synthesis uses a Claude SDK runner
    without MCP tools or browser plugins.
    """
    global _DEFAULT_TEXT
    if _DEFAULT is not None:
        return _DEFAULT
    if _DEFAULT_TEXT is None:
        _DEFAULT_TEXT = _build_runner(text_only=True)
    return _DEFAULT_TEXT


def set_default_runner(runner: AgentRunner) -> None:
    """Override the default runner (primarily for tests)."""
    global _DEFAULT
    _DEFAULT = runner


def __getattr__(name: str) -> Any:
    if name == "ClaudeSDKRunner":
        from contents_hub.runners.claude_sdk import ClaudeSDKRunner

        return ClaudeSDKRunner
    raise AttributeError(name)


__all__ = [
    "AgentRunner",
    "AgentRunError",
    "ClaudeSDKRunner",
    "NoAgentRunner",
    "get_default_runner",
    "get_default_text_runner",
    "set_default_runner",
]

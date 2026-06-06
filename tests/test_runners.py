"""Tests for the AgentRunner abstraction."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from contents_hub.runners import (
    AgentRunner,
    ClaudeSDKRunner,
    NoAgentRunner,
    get_default_runner,
    set_default_runner,
)
from contents_hub.tools import (
    ToolRegistry,
    ToolSpec,
    get_default_registry,
    set_default_registry,
)


def test_claude_sdk_runner_satisfies_protocol():
    runner = ClaudeSDKRunner()
    assert isinstance(runner, AgentRunner)


def test_default_runner_is_no_agent_by_default(monkeypatch):
    monkeypatch.delenv("CONTENTS_HUB_AGENT_RUNNER", raising=False)
    set_default_runner(None)  # type: ignore[arg-type]
    runner = get_default_runner()
    assert isinstance(runner, NoAgentRunner)


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


async def test_runner_executor_uses_default_runner():
    """``executor.execute`` should delegate every agent call to
    ``get_default_runner()`` (R-T14.1 / INV-1).

    Pre-refactor this test reached into ``contents_hub.fetchers.browser._run_agent``
    directly.  Post-refactor (T13/R-T7.3) the executor is the single
    runner-call-site, so we exercise it through ``execute()``.
    """
    from contents_hub.executor import execute

    captured: dict = {}

    class FakeRunner:
        async def run(self, prompt, *, max_turns=30, timeout=600.0):
            captured["prompt"] = prompt
            captured["max_turns"] = max_turns
            captured["timeout"] = timeout
            # Return non-item text; this test only verifies the runner call
            # funnel, not item parsing.
            return "OK"

    original = get_default_runner()
    try:
        set_default_runner(FakeRunner())  # type: ignore[arg-type]
        # Catalog source type → pinned built-in recipe → one runner.run() call.
        sub = SimpleNamespace(
            url="https://example.com/feed.xml",
            source_type="rss.feed",
            config={},
        )
        await execute(sub)
        assert captured["max_turns"] == 30
        assert captured["timeout"] == 600.0
        assert "https://example.com" in captured["prompt"]
    finally:
        set_default_runner(original)


# ---------------------------------------------------------------------------
# Tool registry injection (T6 / R-T2.1 / R-T13.1)
# ---------------------------------------------------------------------------


def test_default_tool_registry_has_builtin_tools():
    """The default registry is pre-populated with contractual builtin tools."""
    registry = get_default_registry()
    names = set(registry.list())
    expected = {
        "fetch_url",
        "parse_rss",
        "parse_html",
        "parse_json",
        "chromux_navigate",
        "chromux_extract",
        "chromux_scroll",
        "chromux_scroll_extract",
        "append_checkpoint",
        "extract_metadata",
        "persist_raw",
    }
    # Default registry must AT LEAST cover the eight contractual names.
    assert expected.issubset(names), f"missing builtin tools: {expected - names}"


async def test_default_browser_tool_lazy_handlers_are_callable():
    """Browser lazy handlers must resolve to coroutine handlers, not ToolSpec objects."""
    registry = get_default_registry()
    for name in (
        "chromux_navigate",
        "chromux_extract",
        "chromux_scroll",
        "chromux_scroll_extract",
    ):
        spec = registry.get(name)
        assert spec is not None
        result = await spec.handler()
        assert "missing or invalid" in result


async def test_default_checkpoint_lazy_handler_is_callable():
    registry = get_default_registry()
    spec = registry.get("append_checkpoint")
    assert spec is not None
    result = await spec.handler()
    assert "missing or invalid" in result


def test_set_default_registry_swaps_singleton():
    """``set_default_registry`` swaps the process-wide default the way
    ``set_default_runner`` does — used by tests to inject a clean registry
    instead of relying on a ``tools=`` kwarg pass-through (per learnings
    from earlier rounds: tests should use ``set_default_registry`` not the
    kwarg)."""
    original = get_default_registry()
    try:
        custom = ToolRegistry()
        custom.register(
            ToolSpec(
                name="fake_tool",
                description="test-only tool",
                input_schema={"type": "object", "properties": {}},
                handler=_noop_handler,
            )
        )
        set_default_registry(custom)
        assert get_default_registry() is custom
        assert "fake_tool" in custom.list()
    finally:
        set_default_registry(original)


def test_claude_sdk_runner_accepts_tool_registry_kwarg():
    """``ClaudeSDKRunner.__init__`` accepts a keyword-only ``tool_registry``
    parameter (R-T13.1).  We don't run the SDK here — only verify the
    constructor surface."""
    custom = ToolRegistry()
    runner = ClaudeSDKRunner(tool_registry=custom)
    # The runner must hold onto the custom registry, not the default
    # singleton.  The exact attribute name is implementation-detail; we
    # just assert it didn't raise on construction and is the correct type.
    assert isinstance(runner, ClaudeSDKRunner)


def test_sdk_plugin_path_uses_contents_hub_plugin(tmp_path, monkeypatch):
    from contents_hub.runners.claude_sdk import _resolve_project_plugin_path

    canonical = tmp_path / ".contents-hub" / "plugins" / "contents-hub-browser"
    (canonical / ".claude-plugin").mkdir(parents=True)
    (canonical / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTENTS_HUB_VAULT", raising=False)

    assert _resolve_project_plugin_path() == str(canonical)


def test_sdk_plugin_path_returns_none_without_contents_hub_plugin(tmp_path, monkeypatch):
    from contents_hub.runners.claude_sdk import _resolve_project_plugin_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTENTS_HUB_VAULT", raising=False)

    assert _resolve_project_plugin_path() is None


async def test_claude_sdk_runner_salvages_json_after_reader_error(monkeypatch):
    """If the SDK reader dies after a valid JSON result, keep the result."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    captured = {}

    async def fake_query(*, prompt, options, transport=None):
        captured["stderr"] = options.stderr
        captured["disallowed_tools"] = options.disallowed_tools
        options.stderr("real stderr line")
        yield AssistantMessage(
            content=[TextBlock(text='{"items": []}')],
            model="fake",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fake",
            result='{"items": [{"url": "https://example.test"}], "error": ""}',
        )
        raise Exception("Command failed with exit code 1")

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)

    runner = ClaudeSDKRunner(tool_registry=ToolRegistry())
    result = await runner.run("prompt", timeout=1)

    assert captured["stderr"] is not None
    assert {"WebFetch", "WebSearch"}.issubset(set(captured["disallowed_tools"]))
    assert json.loads(result)["items"][0]["url"] == "https://example.test"


async def test_claude_sdk_text_only_runner_skips_tools_and_plugins(monkeypatch):
    from claude_agent_sdk.types import ResultMessage

    captured = {}

    async def fake_query(*, prompt, options, transport=None):
        captured["allowed_tools"] = getattr(options, "allowed_tools", None)
        captured["mcp_servers"] = getattr(options, "mcp_servers", None)
        captured["plugins"] = getattr(options, "plugins", None)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fake",
            result="ok",
        )

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)

    runner = ClaudeSDKRunner(text_only=True)
    result = await runner.run("prompt", timeout=1)

    assert result == "ok"
    assert captured["allowed_tools"] in (None, [])
    assert captured["mcp_servers"] in (None, {})
    assert captured["plugins"] in (None, [])


async def test_claude_sdk_runner_includes_stderr_tail_when_no_json(monkeypatch):
    async def fake_query(*, prompt, options, transport=None):
        options.stderr("first diagnostic")
        options.stderr("second diagnostic")
        raise Exception("Command failed with exit code 1")
        yield  # pragma: no cover

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)

    runner = ClaudeSDKRunner(tool_registry=ToolRegistry())

    with pytest.raises(RuntimeError) as exc_info:
        await runner.run("prompt", timeout=1)

    message = str(exc_info.value)
    assert "Command failed with exit code 1" in message
    assert "Claude stderr tail:" in message
    assert "first diagnostic" in message
    assert "second diagnostic" in message


async def test_sdk_tool_adapter_caps_oversized_tool_results():
    from contents_hub.runners.claude_sdk import _wrap_handler_for_sdk

    async def huge_handler(**_kwargs):
        return "x" * 40000

    spec = ToolSpec(
        name="huge_tool",
        description="returns too much text",
        input_schema={"type": "object", "properties": {}},
        handler=huge_handler,
    )

    adapter = _wrap_handler_for_sdk(spec)
    result = await adapter({})
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert payload["tool"] == "huge_tool"
    assert payload["truncated"] is True
    assert payload["original_chars"] == 40000


def test_default_runner_resolves_rich_builtin_tool_schemas():
    """Default SDK runs should expose concrete ToolSpec schemas, not placeholders."""
    from contents_hub.runners.claude_sdk import _ensure_rich_builtin_tool_specs

    original = get_default_registry()
    try:
        placeholder = ToolRegistry()
        placeholder.register(
            ToolSpec(
                name="parse_rss",
                description="placeholder",
                input_schema={"type": "object", "properties": {}, "additionalProperties": True},
                handler=_noop_handler,
            )
        )
        set_default_registry(placeholder)

        _ensure_rich_builtin_tool_specs()

        spec = get_default_registry().get("parse_rss")
        assert spec is not None
        assert "xml" in spec.input_schema["properties"]
        assert spec.input_schema["required"] == ["xml"]
        assert "additionalProperties" not in spec.input_schema

        extract_spec = get_default_registry().get("chromux_extract")
        assert extract_spec is not None
        assert "anyOf" not in extract_spec.input_schema
        assert "allOf" not in extract_spec.input_schema
        assert "oneOf" not in extract_spec.input_schema
        assert extract_spec.input_schema["required"] == ["session_id"]

        scroll_extract_spec = get_default_registry().get("chromux_scroll_extract")
        assert scroll_extract_spec is not None
        assert scroll_extract_spec.input_schema["required"] == ["session_id", "selector"]

        checkpoint_spec = get_default_registry().get("append_checkpoint")
        assert checkpoint_spec is not None
        assert checkpoint_spec.input_schema["required"] == ["path"]
    finally:
        set_default_registry(original)


async def _noop_handler(**_kwargs) -> str:  # pragma: no cover - never invoked
    return "{}"

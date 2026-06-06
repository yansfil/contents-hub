"""Claude Agent SDK runner.

Wraps `claude_agent_sdk.query()` in the `AgentRunner` protocol. Preserves
the existing behavior from `fetchers/browser.py`: loads the bundled
the bundled browser plugin if discoverable, runs under
`permission_mode=bypassPermissions`, logs tool use and text turns.

R-T2.1 / R-T13.1 / R-T13.2 / R-T14.1:
- Accepts a keyword-only `tool_registry` parameter (default `None` →
  `tools.get_default_registry()`).
- Each `ToolSpec` in the registry is converted into an in-process MCP
  tool via the Claude Agent SDK's native `tool()` / `create_sdk_mcp_server()`
  helpers and exposed to the agent under the `mcp__contents_hub__<name>` namespace.
- `disallowed_tools=["Skill", "WebFetch", "WebSearch"]` guard,
  `max_turns=30` cap, and
  `timeout=600.0` wall-clock default are preserved verbatim.
- All `claude_agent_sdk` / `anthropic` imports remain confined to this
  module (INV-1, R-T14.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from contents_hub.naming import MCP_SERVER_NAME, METADATA_DIR, PRODUCT_NAME, VAULT_ENV_VARS
from contents_hub.runners.base import AgentRunner
from contents_hub.tools import ToolRegistry, ToolSpec, get_default_registry

logger = logging.getLogger(__name__)

# Namespace under which our SDK MCP server is registered. Tool names exposed
# to the agent become `mcp__<server-name>__<tool-name>` per the SDK's
# convention, so we keep this stable and short.
_MCP_SERVER_NAME = MCP_SERVER_NAME.canonical
_STDERR_TAIL_LINES = 40
_MAX_TOOL_RESULT_CHARS = int(
    os.environ.get("CONTENTS_HUB_AGENT_TOOL_RESULT_MAX_CHARS", "35000")
)


def _resolve_project_plugin_path() -> str | None:
    """Locate the bundled browser plugin directory.

    Walks up from the current working directory and configured vault env vars
    looking for `.contents-hub/plugins/contents-hub-browser`.
    Returns absolute path or None (the SDK will then rely on its own discovery).
    """
    candidates: list[Path] = [Path.cwd()]
    for env_name in VAULT_ENV_VARS.all:
        env_vault = os.environ.get(env_name)
        if env_vault:
            candidates.append(Path(env_vault).expanduser())

    plugin_names = (f"{PRODUCT_NAME.canonical}-browser",)

    seen: set[Path] = set()
    for start in candidates:
        start = start.resolve()
        for p in [start, *start.parents]:
            if p in seen:
                continue
            seen.add(p)
            for metadata_dir in METADATA_DIR.all:
                for plugin_name in plugin_names:
                    plugin_dir = p / metadata_dir / "plugins" / plugin_name
                    if (plugin_dir / ".claude-plugin" / "plugin.json").exists():
                        return str(plugin_dir)
    return None


def _wrap_handler_for_sdk(spec: ToolSpec):
    """Return an async function compatible with the SDK MCP tool contract.

    The Claude Agent SDK expects MCP tool handlers to take a single ``args``
    dict and return ``{"content": [{"type": "text", "text": ...}]}``. Our
    `ToolSpec.handler` instead takes arbitrary keyword arguments and returns
    a plain string. This adapter bridges the two.
    """

    async def _adapter(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await spec.handler(**(args or {}))
        except Exception as exc:  # noqa: BLE001 — surface to agent
            logger.exception("tool %s raised", spec.name)
            return {
                "content": [
                    {"type": "text", "text": f"Error in tool '{spec.name}': {exc}"}
                ],
                "is_error": True,
            }
        # Per AgentRunner.run protocol, handlers return str. If a handler
        # accidentally returns a dict/list, JSON-encode it for safety.
        if not isinstance(result, str):
            try:
                result = json.dumps(result, default=str)
            except Exception:  # pragma: no cover — defensive
                result = repr(result)
        if len(result) > _MAX_TOOL_RESULT_CHARS:
            result = _tool_result_too_large_payload(spec.name, result)
        return {"content": [{"type": "text", "text": result}]}

    _adapter.__name__ = f"sdk_adapter_{spec.name}"
    _adapter.__qualname__ = _adapter.__name__
    return _adapter


def _has_json_payload(text: str) -> bool:
    """Return true when ``text`` contains a complete JSON object/array.

    Claude Code sometimes exits non-zero after emitting a final JSON payload.
    The downstream contents-hub parsers can recover JSON from surrounding text,
    so this helper accepts either a full JSON string or a JSON-looking slice.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    try:
        json.loads(stripped)
        return True
    except json.JSONDecodeError:
        pass
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = stripped.find(start_char)
        end = stripped.rfind(end_char)
        if start < 0 or end <= start:
            continue
        try:
            json.loads(stripped[start : end + 1])
            return True
        except json.JSONDecodeError:
            continue
    return False


def _stderr_tail(stderr_lines: list[str]) -> str:
    return "\n".join(stderr_lines[-_STDERR_TAIL_LINES:]).strip()


def _tool_result_too_large_payload(tool_name: str, result: str) -> str:
    preview = result[:_MAX_TOOL_RESULT_CHARS].rstrip()
    return json.dumps(
        {
            "ok": False,
            "tool": tool_name,
            "error": (
                "tool result exceeded contents-hub compacting limit "
                f"({_MAX_TOOL_RESULT_CHARS} chars)"
            ),
            "truncated": True,
            "original_chars": len(result),
            "preview": preview,
        },
        ensure_ascii=False,
    )


def _runner_error_message(exc: Exception, stderr_lines: list[str]) -> str:
    tail = _stderr_tail(stderr_lines)
    if not tail:
        return str(exc)
    return f"{exc}\nClaude stderr tail:\n{tail}"


def _ensure_rich_builtin_tool_specs(registry: ToolRegistry | None = None) -> None:
    """Import builtin tool modules so their rich ToolSpecs replace placeholders.

    ``tools.registry`` can construct the default registry before sibling tool
    modules are imported, which leaves SDK-visible schemas permissive and
    empty. Importing these modules triggers their default-registry
    re-registration hooks before we expose tools to the agent.
    """
    from contents_hub.tools import browser, checkpoint, fetchers, metadata, parse, storage

    registry = registry if registry is not None else get_default_registry()
    registry.register(fetchers.get_spec())
    for spec in parse.get_specs():
        registry.register(spec)
    registry.register(browser.chromux_navigate)
    registry.register(browser.chromux_extract)
    registry.register(browser.chromux_scroll)
    registry.register(browser.chromux_scroll_extract)
    registry.register(checkpoint.append_checkpoint)
    registry.register(metadata.get_spec())
    registry.register(storage.persist_raw_tool)


def _registry_to_sdk_mcp_server(registry: ToolRegistry):
    """Convert a `ToolRegistry` to an SDK MCP server config + tool name list.

    Returns ``(mcp_server_config, allowed_tool_names)`` where
    ``allowed_tool_names`` is a list of ``mcp__<server>__<tool>`` strings to
    include in `ClaudeAgentOptions.allowed_tools`. If the registry is empty
    we return ``(None, [])`` so the runner falls back to the SDK's default
    builtin toolset (matching pre-refactor behavior).
    """
    from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

    specs = registry.all()
    if not specs:
        return None, []

    sdk_tools = []
    allowed: list[str] = []
    for spec in specs:
        sdk_tool = SdkMcpTool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            handler=_wrap_handler_for_sdk(spec),
        )
        sdk_tools.append(sdk_tool)
        allowed.append(f"mcp__{_MCP_SERVER_NAME}__{spec.name}")

    server = create_sdk_mcp_server(name=_MCP_SERVER_NAME, tools=sdk_tools)
    return server, allowed


class ClaudeSDKRunner(AgentRunner):
    """In-process agent runner backed by `claude_agent_sdk`.

    Args:
        model: Claude model identifier (passed through to the SDK).
        permission_mode: SDK permission mode (kept at ``bypassPermissions``
            so background agent runs do not block on prompts).
        tool_registry: Optional `ToolRegistry` whose `ToolSpec` entries are
            converted to SDK-native MCP tools and exposed to the agent.
            ``None`` (the default) resolves to `tools.get_default_registry()`
            at run-time so test harnesses can swap the default via
            `tools.set_default_registry()` between constructor and `run()`.
    """

    def __init__(
        self,
        *,
        model: str = "sonnet",
        permission_mode: str = "bypassPermissions",
        tool_registry: ToolRegistry | None = None,
        text_only: bool = False,
    ) -> None:
        self._model = model
        self._permission_mode = permission_mode
        # Stored as-is; `None` is resolved lazily inside `_run_raw` so a test
        # that calls `set_default_registry()` after construction still wins.
        self._tool_registry = tool_registry
        self._text_only = bool(text_only)

    async def run(
        self,
        prompt: str,
        *,
        max_turns: int = 30,
        timeout: float = 600.0,
    ) -> str:
        return await asyncio.wait_for(
            self._run_raw(prompt, max_turns=max_turns),
            timeout=timeout,
        )

    def _resolve_registry(self) -> ToolRegistry:
        if self._tool_registry is not None:
            return self._tool_registry
        registry = get_default_registry()
        if registry.get("persist_exploration_raw") is None:
            _ensure_rich_builtin_tool_specs(registry)
        return registry

    async def _run_raw(self, prompt: str, *, max_turns: int) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, TextBlock, query
        from claude_agent_sdk.types import AssistantMessage, ResultMessage

        plugins: list[dict] = []
        mcp_server = None
        registry_allowed_tools: list[str] = []
        registry = None
        if not self._text_only:
            plugin_path = _resolve_project_plugin_path()
            if plugin_path:
                plugins.append({"type": "local", "path": plugin_path})
                logger.info("SDK browser plugin <- %s", plugin_path)

            # Convert ToolRegistry → SDK MCP server (R-T2.1).
            registry = self._resolve_registry()
            mcp_server, registry_allowed_tools = _registry_to_sdk_mcp_server(registry)

        stderr_lines: list[str] = []

        def _capture_stderr(line: str) -> None:
            cleaned = (line or "").rstrip()
            if not cleaned:
                return
            stderr_lines.append(cleaned)
            if len(stderr_lines) > _STDERR_TAIL_LINES:
                del stderr_lines[: len(stderr_lines) - _STDERR_TAIL_LINES]
            logger.warning("[agent stderr] %s", cleaned[:1000])

        options_kwargs: dict = dict(
            permission_mode=self._permission_mode,
            max_turns=max_turns,
            model=self._model,
            # Browser-backed runs should use the contents-hub tool boundary
            # instead of silently switching to unrelated retrieval paths.
            # Agent delegation is intentionally allowed: autonomous exploration
            # runs now carry a run-aware persistence tool, so delegated work can
            # still save through the same narrow boundary.
            disallowed_tools=["Skill", "WebFetch", "WebSearch"],
            stderr=_capture_stderr,
        )
        if plugins:
            options_kwargs["plugins"] = plugins
        if mcp_server is not None:
            options_kwargs["mcp_servers"] = {_MCP_SERVER_NAME: mcp_server}
            # ``allowed_tools`` is forwarded verbatim to the underlying
            # Claude Code CLI as ``--allowedTools`` (see
            # claude_agent_sdk/_internal/transport/subprocess_cli.py:196),
            # which treats it as an EXCLUSIVE allowlist. If we set it to
            # only the mcp__contents_hub__* names, the agent loses access to
            # the built-in ``Bash`` tool — which our recipe templates
            # (recipes/templates/{execute,explore}_prompt.md) explicitly
            # instruct the agent to use for chromux invocations
            # (``CHROMUX_PROFILE=contents-hub chromux …`` with legacy profile
            # fallback). We therefore append
            # ``Bash`` to the allowlist alongside the registry-derived MCP
            # names so chromux scripting keeps working.
            options_kwargs["allowed_tools"] = [*registry_allowed_tools, "Bash"]
            logger.info(
                "SDK tool registry: %d tools (%s) + Bash",
                len(registry_allowed_tools),
                ", ".join(s.name for s in registry.all()) if registry is not None else "",
            )

        last_text = ""
        turn = 0
        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(**options_kwargs),
            ):
                if isinstance(message, AssistantMessage):
                    turn += 1
                    for block in message.content:
                        cls_name = type(block).__name__
                        if cls_name == "ToolUseBlock":
                            tool_name = getattr(block, "name", "?")
                            args = getattr(block, "input", {})
                            preview = str(args)[:120]
                            logger.info("[agent turn %d] tool: %s %s", turn, tool_name, preview)
                        elif isinstance(block, TextBlock):
                            last_text = block.text
                            snippet = (block.text or "").strip().replace("\n", " ")[:160]
                            if snippet:
                                logger.info("[agent turn %d] text: %s", turn, snippet)
                elif isinstance(message, ResultMessage):
                    if message.result:
                        last_text = message.result
                    logger.info("[agent done] turns=%d result_chars=%d", turn, len(last_text or ""))
        except Exception as exc:
            if _has_json_payload(last_text):
                logger.warning(
                    "agent reader failed after valid JSON payload; returning salvaged result. "
                    "error=%s stderr_tail=%s",
                    exc,
                    _stderr_tail(stderr_lines)[:2000],
                )
                return last_text
            raise RuntimeError(_runner_error_message(exc, stderr_lines)) from exc
        return last_text

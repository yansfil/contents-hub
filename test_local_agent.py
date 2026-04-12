"""Probe: does claude_agent_sdk load a project-local plugin from
``.llm-wiki/plugins/llm-wiki-browser``?

Spawns the top-level agent with SDK ``plugins`` pointing at the bundled
plugin, then asks it to delegate to ``llm-wiki-browser:browser-explorer``.
Success = subagent navigates to example.com and returns the page title.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, TextBlock, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage

PLUGIN_PATH = (
    Path(__file__).parent / ".llm-wiki" / "plugins" / "llm-wiki-browser"
).resolve()

PROMPT = (
    "Spawn the `llm-wiki-browser:browser-explorer` subagent via the Agent tool. "
    "Ask it to open https://example.com, take a snapshot, and report the page's "
    "<h1> text. Then return the subagent's answer verbatim, prefixed exactly "
    "with `LOCAL_PLUGIN_OK:` on one line."
)


async def main() -> int:
    assert (PLUGIN_PATH / ".claude-plugin" / "plugin.json").exists(), \
        f"plugin.json missing at {PLUGIN_PATH}"

    last_text = ""
    async for message in query(
        prompt=PROMPT,
        options=ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            max_turns=12,
            model="sonnet",
            plugins=[{"type": "local", "path": str(PLUGIN_PATH)}],
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                cls = type(block).__name__
                if cls == "ToolUseBlock":
                    name = getattr(block, "name", "?")
                    inp = str(getattr(block, "input", {}))[:200]
                    print(f"[tool] {name} {inp}")
                elif isinstance(block, TextBlock):
                    print(f"[text] {block.text[:240]}")
                    last_text = block.text
        elif isinstance(message, ResultMessage):
            if message.result:
                last_text = message.result
            print(f"[done] {len(last_text)} chars")

    print("=" * 60)
    print("FINAL:", last_text.strip()[:600])
    ok = "LOCAL_PLUGIN_OK" in last_text
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

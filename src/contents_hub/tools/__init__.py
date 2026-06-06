"""Tools layer — `ToolSpec` / `ToolRegistry` surface for the agent executor.

`ToolSpec` is a frozen dataclass describing a single agent-callable tool.
`ToolRegistry` is the process-wide registry of tools the executor exposes to
the runner. The default registry is pre-populated with the contractual builtin
tool names enumerated in contracts.md → "ToolRegistry", plus browser collection
helpers:

    fetch_url, parse_rss, parse_html, parse_json,
    chromux_navigate, chromux_extract, chromux_scroll, chromux_scroll_extract,
    append_checkpoint, extract_metadata, persist_raw

`get_default_registry` / `set_default_registry` mirror the existing
`runners.get_default_runner` / `set_default_runner` pattern.
"""

from contents_hub.tools.base import ToolHandler, ToolSpec
from contents_hub.tools.registry import (
    ToolRegistry,
    get_default_registry,
    set_default_registry,
)

__all__ = [
    "ToolHandler",
    "ToolSpec",
    "ToolRegistry",
    "get_default_registry",
    "set_default_registry",
]

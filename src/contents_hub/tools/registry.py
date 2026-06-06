"""ToolRegistry — process-wide registry of `ToolSpec` entries.

Mirrors the `runners.{get,set}_default_runner` pattern: a private module
singleton with explicit override hooks for tests.

The default registry is pre-populated with the contractual builtin tools
defined by contracts.md → "ToolRegistry", plus collection helpers used by
browser-backed exploration:

    fetch_url, parse_rss, parse_html, parse_json,
    chromux_navigate, chromux_extract, chromux_scroll, chromux_scroll_extract,
    append_checkpoint, extract_metadata, persist_raw

Each builtin's *handler* is resolved lazily (`importlib.import_module` on
first call) so that this module can be imported safely even before the
sibling handler modules (`tools/fetchers.py`, `tools/parse.py`,
`tools/browser.py`, `tools/metadata.py`, `tools/storage.py`) finish landing
in concurrent tasks T4/T5. Once those modules exist, the lazy proxy
dispatches transparently to the real coroutine.
"""

from __future__ import annotations

import importlib
from typing import Any

from contents_hub.tools.base import ToolHandler, ToolSpec


class ToolRegistry:
    """Mutable, ordered collection of `ToolSpec` entries keyed by name."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register `spec`. Re-registering an existing name overwrites."""
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """Return the spec registered under `name`, or None."""
        return self._specs.get(name)

    def all(self) -> list[ToolSpec]:
        """Return all registered specs in insertion order."""
        return list(self._specs.values())

    # Convenience helper used by tests and the SDK adapter to enumerate names
    # without forcing callers to map() over `.all()`.
    def list(self) -> list[str]:
        return list(self._specs.keys())

    def __contains__(self, name: str) -> bool:  # pragma: no cover - trivial
        return name in self._specs

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._specs)


# ---------------------------------------------------------------------------
# Lazy handler proxies
# ---------------------------------------------------------------------------

# Mapping of builtin tool name → (module path, attribute name) where the
# real async handler lives. Modules are imported on first invocation so the
# registry can be assembled before T4/T5 finish writing the handler files.
_BUILTIN_HANDLERS: dict[str, tuple[str, str]] = {
    "fetch_url":         ("contents_hub.tools.fetchers", "fetch_url"),
    "parse_rss":         ("contents_hub.tools.parse",    "parse_rss"),
    "parse_html":        ("contents_hub.tools.parse",    "parse_html"),
    "parse_json":        ("contents_hub.tools.parse",    "parse_json"),
    "chromux_navigate":       ("contents_hub.tools.browser",     "chromux_navigate_handler"),
    "chromux_extract":        ("contents_hub.tools.browser",     "chromux_extract_handler"),
    "chromux_scroll":         ("contents_hub.tools.browser",     "chromux_scroll_handler"),
    "chromux_scroll_extract": ("contents_hub.tools.browser",     "chromux_scroll_extract_handler"),
    "append_checkpoint":      ("contents_hub.tools.checkpoint",  "append_checkpoint_handler"),
    "extract_metadata":  ("contents_hub.tools.metadata", "extract_metadata"),
    # NOTE: ``persist_raw`` (the function) requires a ``conn=`` kwarg that
    # the agent has no way to supply — calling it via the MCP adapter would
    # crash with TypeError. Point the lazy proxy at ``persist_raw_handler``,
    # the agent-callable shim that returns a structured error/diagnostic
    # JSON instead. (storage.py:_register_default also re-registers the
    # full ``persist_raw_tool`` ToolSpec on first import, mirroring the
    # fetchers/metadata pattern.)
    "persist_raw":       ("contents_hub.tools.storage",  "persist_raw_handler"),
}

# One-line description per builtin. Concrete handler modules may refine
# these via re-registration once their richer schemas are ready, but the
# default-registry contract only requires the name slots to be present.
_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "fetch_url":         "HTTP GET a URL and return its body as text.",
    "parse_rss":         "Parse RSS/Atom feed XML into a list of items.",
    "parse_html":        "Parse an HTML document and extract structured fields.",
    "parse_json":        "Parse a JSON document and return the decoded value.",
    "chromux_navigate":       "Navigate the chromux browser to a URL and return rendered HTML.",
    "chromux_extract":        "Extract elements from the chromux page using a selector strategy.",
    "chromux_scroll":         "Scroll a chromux browser session without Bash.",
    "chromux_scroll_extract": "Extract repeated cards across bounded chromux scroll passes.",
    "append_checkpoint":      "Append raw item candidates to a JSONL run checkpoint.",
    "extract_metadata":  "Extract Open Graph / oEmbed / RSS metadata from a page.",
    "persist_raw":       "Insert FetchedItem rows into raw_items with INSERT-OR-IGNORE dedup.",
}


def _make_lazy_handler(name: str) -> ToolHandler:
    """Build a coroutine that imports the real handler on first call.

    The proxy preserves the async-callable contract demanded by ToolSpec
    while letting the registry be populated before sibling modules exist.
    """
    module_path, attr_name = _BUILTIN_HANDLERS[name]

    async def _lazy(*args: Any, **kwargs: Any) -> str:
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:  # pragma: no cover - exercised post-T4/T5
            raise RuntimeError(
                f"Tool handler module '{module_path}' for tool '{name}' is "
                f"not yet available; ensure T4/T5 have landed."
            ) from exc
        try:
            handler = getattr(module, attr_name)
        except AttributeError as exc:  # pragma: no cover
            raise RuntimeError(
                f"Tool handler '{attr_name}' missing in module '{module_path}'"
            ) from exc
        return await handler(*args, **kwargs)

    _lazy.__name__ = f"lazy_{name}"
    _lazy.__qualname__ = f"lazy_{name}"
    return _lazy


def _build_default_registry() -> ToolRegistry:
    """Construct a fresh registry pre-populated with the eight builtins."""
    registry = ToolRegistry()
    for name in _BUILTIN_HANDLERS:
        spec = ToolSpec(
            name=name,
            description=_BUILTIN_DESCRIPTIONS[name],
            # Minimal placeholder schema. T4/T5 may re-register with a
            # richer schema; that overwrite is intentional and safe.
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            handler=_make_lazy_handler(name),
        )
        registry.register(spec)
    return registry


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors runners.get_default_runner / set_default_runner)
# ---------------------------------------------------------------------------

_DEFAULT: ToolRegistry | None = None


def get_default_registry() -> ToolRegistry:
    """Return the process-wide default registry (lazy-constructed)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = _build_default_registry()
    return _DEFAULT


def set_default_registry(registry: ToolRegistry) -> None:
    """Override the default registry (primarily for tests)."""
    global _DEFAULT
    _DEFAULT = registry


__all__ = [
    "ToolRegistry",
    "get_default_registry",
    "set_default_registry",
]

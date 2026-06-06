"""Chromux-driven browser tools.

These `ToolSpec` handlers absorb chromux CLI invocation logic that currently
lives inline in ``contents_hub.fetchers.browser``. They are exposed as
agent-callable tools so that the executor can drive a chromux session through
the registered tool layer rather than reaching into a fetcher class.

Design notes:

- The chromux CLI is invoked via :mod:`subprocess`, matching the pattern in
  ``fetchers/browser.py``.  We never import chromux as a Python module; the
  contract is the CLI surface.
- The ``CHROMUX_PROFILE`` env var is the only profile routing knob. It points
  at the canonical ``contents-hub`` profile.
- Handlers are async coroutines returning a JSON-encoded string per the
  ``ToolHandler`` protocol in :mod:`contents_hub.tools.base`. The string body is
  what the agent reads on a tool result.
- All chromux CLI calls run inside ``asyncio.to_thread`` to avoid blocking
  the event loop. The wall-clock cap mirrors the per-call timeout used by
  the existing browser fetcher (10s for ``close``, 60s for an interactive
  navigate/extract step).
- These tools never raise into the runner: failures are flattened into a
  ``{"ok": false, "error": "..."}`` JSON body so the agent can decide how
  to react.

These handlers keep browser automation behind the shared tool boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import time
from typing import Any

from contents_hub.chromux import (
    CHROMUX_PROFILE_NAME,
    chromux_automation_env,
    is_foreground_fetch_allowed,
    prepare_chromux_for_background_fetch,
    resolve_chromux_profile,
    track_chromux_session,
)
from contents_hub.tools.base import ToolSpec

logger = logging.getLogger(__name__)

# Canonical Chromux profile name; runtime calls use resolve_chromux_profile().
CHROMUX_PROFILE = CHROMUX_PROFILE_NAME

# Per-call timeouts (seconds). Match fetchers/browser.py defaults so behavior
# is unchanged when a call-site swaps the legacy fetcher for these handlers.
_NAVIGATE_TIMEOUT_SECONDS = 60.0
_EXTRACT_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Session helpers (shared with fetchers/browser.py until T14)
# ---------------------------------------------------------------------------


def _make_session_id(url: str) -> str:
    """Stable per-URL chromux session id (8-hex-char prefix)."""
    return "wiki-" + hashlib.md5(url.encode()).hexdigest()[:8]


def _run_chromux(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Synchronous chromux CLI call. Wrap with ``asyncio.to_thread``.

    Returns the ``CompletedProcess`` regardless of exit status; callers
    inspect ``returncode``.
    """
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=timeout,
    )


def _run_js_args(session_id: str, expression: str) -> list[str]:
    """Build a chromux `run` command for a browser-side JS expression."""
    code = f"return await js({json.dumps(expression)});"
    return ["chromux", "run", session_id, code]


def _sleep_args(session_id: str, wait_ms: int) -> list[str]:
    return ["chromux", "run", session_id, f"await sleep({wait_ms}); return true;"]


# ---------------------------------------------------------------------------
# chromux_navigate
# ---------------------------------------------------------------------------


_NAVIGATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Absolute URL to navigate to in the chromux profile session.",
        },
        "session_id": {
            "type": "string",
            "description": (
                "Optional explicit chromux session id. If omitted, derived "
                "from a hash of the URL so repeat calls reuse the tab."
            ),
        },
        "session": {
            "type": "string",
            "description": "Alias for session_id, accepted for agent compatibility.",
        },
        "wait_ms": {
            "type": "integer",
            "description": "Optional wait in milliseconds after navigation completes.",
            "minimum": 0,
        },
    },
    "required": ["url"],
}


async def chromux_navigate_handler(**kwargs: Any) -> str:
    """Open or reuse a chromux tab and navigate to ``url``.

    Returns a JSON object string of the form::

        {"ok": true, "session_id": "wiki-...", "url": "https://..."}
        {"ok": false, "error": "..."}

    The tool intentionally does no DOM inspection — extraction is a
    separate step (``chromux_extract``) so the agent can drive a multi-step
    flow (navigate → wait → extract) without re-running navigation.
    """
    url = kwargs.get("url")
    if not url or not isinstance(url, str):
        return json.dumps({"ok": False, "error": "missing or invalid 'url' argument"})

    session_id = (
        kwargs.get("session_id") or kwargs.get("session") or _make_session_id(url)
    )
    wait_ms = int(kwargs.get("wait_ms") or 0)
    chromux_profile: str | None = None
    if not is_foreground_fetch_allowed():
        prep = prepare_chromux_for_background_fetch()
        if not prep.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "error": prep.get("error") or "chromux setup failed",
                    "failure_reason": prep.get("failure_reason"),
                    "session_id": session_id,
                }
            )
        chromux_profile = str(prep.get("profile") or "") or None

    args = [
        "chromux",
        "open",
        session_id,
        url,
    ]
    env = chromux_automation_env(resolve_chromux_profile(chromux_profile))

    try:
        proc = await asyncio.to_thread(
            _run_chromux, args, env=env, timeout=_NAVIGATE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        logger.warning("chromux navigate timed out for %s (session=%s)", url, session_id)
        return json.dumps(
            {"ok": False, "error": "chromux navigate timed out", "session_id": session_id}
        )
    except FileNotFoundError:
        # chromux binary missing — surface as agent-visible error rather than crash.
        return json.dumps({"ok": False, "error": "chromux CLI not found on PATH"})
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("chromux navigate raised: %s", e)
        return json.dumps({"ok": False, "error": f"chromux navigate failed: {e}"})

    if proc.returncode != 0:
        return json.dumps(
            {
                "ok": False,
                "error": (proc.stderr or proc.stdout or "chromux navigate non-zero exit").strip(),
                "session_id": session_id,
            }
        )

    if wait_ms > 0:
        wait_args = _sleep_args(session_id, wait_ms)
        try:
            wait_proc = await asyncio.to_thread(
                _run_chromux, wait_args, env=env, timeout=_NAVIGATE_TIMEOUT_SECONDS
            )
            if wait_proc.returncode != 0:
                # Preserve navigation success; the post-navigation sleep is best-effort.
                await asyncio.to_thread(time.sleep, wait_ms / 1000)
        except Exception:
            await asyncio.to_thread(time.sleep, wait_ms / 1000)

    track_chromux_session(session_id)
    return json.dumps(
        {
            "ok": True,
            "session_id": session_id,
            "url": url,
            "stdout": (proc.stdout or "").strip(),
        }
    )


chromux_navigate = ToolSpec(
    name="chromux_navigate",
    description=(
        "Navigate the chromux browser session (Chrome under the contents-hub "
        "profile) to a target URL. Reuses a per-URL session id by default. "
        "Returns JSON with the session id; pair with chromux_extract."
    ),
    input_schema=_NAVIGATE_INPUT_SCHEMA,
    handler=chromux_navigate_handler,
)


# ---------------------------------------------------------------------------
# chromux_extract / chromux_scroll / chromux_scroll_extract
# ---------------------------------------------------------------------------


_EXTRACT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": (
                "chromux session id returned by chromux_navigate. Required "
                "so the extraction targets the right tab."
            ),
        },
        "session": {
            "type": "string",
            "description": "Alias for session_id, accepted for agent compatibility.",
        },
        "selector": {
            "type": "string",
            "description": (
                "Optional CSS selector to scope extraction. Omit to grab "
                "the whole page DOM/text."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["html", "text", "links"],
            "description": (
                "Extraction mode. 'html' returns serialized DOM; 'text' "
                "returns visible text only; 'links' returns anchor hrefs."
            ),
        },
        "attributes": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional attribute names to extract from matched elements. "
                "When set, returns JSON records instead of raw html/text."
            ),
        },
        "multiple": {
            "type": "boolean",
            "description": "When true, extract all selector matches instead of only the first.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum matched elements to return when multiple=true.",
            "minimum": 1,
            "maximum": 500,
        },
        "include_text": {
            "type": "boolean",
            "description": "Include innerText/textContent in attribute extraction records.",
        },
    },
    "required": ["session_id"],
}


def _attribute_extract_js(
    *,
    selector: str,
    attributes: list[str],
    multiple: bool,
    limit: int,
    include_text: bool,
) -> str:
    """Build browser-side JS for structured element extraction."""
    selector_json = json.dumps(selector)
    attributes_json = json.dumps(attributes)
    multiple_json = "true" if multiple else "false"
    include_text_json = "true" if include_text else "false"
    return f"""
(() => {{
  const selector = {selector_json};
  const attributes = {attributes_json};
  const multiple = {multiple_json};
  const includeText = {include_text_json};
  const limit = {limit};
  const elements = multiple
    ? Array.from(document.querySelectorAll(selector)).slice(0, limit)
    : Array.from([document.querySelector(selector)].filter(Boolean));
  const records = elements.map((el) => {{
    const record = {{}};
    for (const name of attributes) {{
      if (name === "text" || name === "innerText") {{
        record[name] = (el.innerText || "").trim();
      }} else if (name === "textContent") {{
        record[name] = (el.textContent || "").trim();
      }} else if (name in el && typeof el[name] !== "function") {{
        const value = el[name];
        record[name] = value == null ? "" : String(value);
      }} else {{
        record[name] = el.getAttribute(name) || "";
      }}
    }}
    if (includeText && !("text" in record)) {{
      record.text = (el.innerText || el.textContent || "").trim();
    }}
    return record;
  }});
  return JSON.stringify(multiple ? records : (records[0] || null));
}})()
""".strip()


def _scroll_js(*, direction: str, pixels: int) -> str:
    signed_pixels = pixels if direction == "down" else -pixels
    return f"window.scrollBy(0, {signed_pixels}); true"


async def _best_effort_wait(wait_ms: int) -> None:
    if wait_ms > 0:
        await asyncio.to_thread(time.sleep, wait_ms / 1000)


def _parse_records(data: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _dedupe_key(record: dict[str, Any], unique_by: str) -> str:
    if unique_by and record.get(unique_by):
        return str(record[unique_by])
    for fallback in ("href", "url", "data-urn", "id", "text"):
        if record.get(fallback):
            return str(record[fallback])
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


async def chromux_extract_handler(**kwargs: Any) -> str:
    """Extract content from a previously-opened chromux session.

    Returns a JSON object string with shape::

        {"ok": true, "mode": "html|text|links", "data": "..."}
        {"ok": false, "error": "..."}
    """
    session_id = kwargs.get("session_id") or kwargs.get("session")
    if not session_id or not isinstance(session_id, str):
        return json.dumps(
            {"ok": False, "error": "missing or invalid 'session_id' argument"}
        )

    mode = kwargs.get("mode") or "html"
    if mode not in ("html", "text", "links"):
        return json.dumps({"ok": False, "error": f"unsupported mode: {mode}"})

    selector = kwargs.get("selector")
    raw_attributes = kwargs.get("attributes") or []
    attributes = [
        str(attribute)
        for attribute in raw_attributes
        if isinstance(attribute, str) and attribute.strip()
    ]
    multiple = bool(kwargs.get("multiple"))
    try:
        limit = int(kwargs.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = min(max(limit, 1), 500)
    include_text = bool(kwargs.get("include_text", True))

    env = chromux_automation_env(resolve_chromux_profile())
    if attributes:
        if not selector or not isinstance(selector, str):
            return json.dumps(
                {"ok": False, "error": "'selector' is required when extracting attributes"}
            )
        js = _attribute_extract_js(
            selector=selector,
            attributes=attributes,
            multiple=multiple,
            limit=limit,
            include_text=include_text,
        )
        args = _run_js_args(session_id, js)
    elif mode == "html":
        js = "document.documentElement.outerHTML"
        if selector and isinstance(selector, str):
            js = f"document.querySelector({json.dumps(selector)})?.outerHTML || ''"
        args = _run_js_args(session_id, js)
    elif mode == "text":
        if selector and isinstance(selector, str):
            js = f"document.querySelector({json.dumps(selector)})?.innerText || ''"
            args = _run_js_args(session_id, js)
        else:
            args = ["chromux", "snapshot", session_id]
    else:
        selector_query = (
            f"{selector} a[href]"
            if selector and isinstance(selector, str)
            else "a[href]"
        )
        js = (
            "JSON.stringify(Array.from(document.querySelectorAll("
            + json.dumps(selector_query)
            + ")).map(a => ({text: (a.innerText || '').trim(), href: a.href})).filter(x => x.href).slice(0, 200))"
        )
        args = _run_js_args(session_id, js)

    try:
        proc = await asyncio.to_thread(
            _run_chromux, args, env=env, timeout=_EXTRACT_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        logger.warning("chromux extract timed out (session=%s)", session_id)
        return json.dumps({"ok": False, "error": "chromux extract timed out"})
    except FileNotFoundError:
        return json.dumps({"ok": False, "error": "chromux CLI not found on PATH"})
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("chromux extract raised: %s", e)
        return json.dumps({"ok": False, "error": f"chromux extract failed: {e}"})

    if proc.returncode != 0:
        return json.dumps(
            {
                "ok": False,
                "error": (proc.stderr or proc.stdout or "chromux extract non-zero exit").strip(),
            }
        )

    data = proc.stdout or ""
    payload: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "selector": selector or "",
        "data": data,
    }
    if attributes:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = None
        payload["attributes"] = attributes
        payload["multiple"] = multiple
        if multiple:
            payload["items"] = parsed if isinstance(parsed, list) else []
        else:
            payload["item"] = parsed if isinstance(parsed, dict) else None
    return json.dumps(payload)


chromux_extract = ToolSpec(
    name="chromux_extract",
    description=(
        "Extract HTML, visible text, or anchor links from a chromux session "
        "previously opened by chromux_navigate. Returns JSON with the "
        "extracted payload under 'data'."
    ),
    input_schema=_EXTRACT_INPUT_SCHEMA,
    handler=chromux_extract_handler,
)


_SCROLL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "chromux session id returned by chromux_navigate.",
        },
        "session": {
            "type": "string",
            "description": "Alias for session_id, accepted for agent compatibility.",
        },
        "direction": {
            "type": "string",
            "enum": ["down", "up"],
            "description": "Scroll direction.",
        },
        "pixels": {
            "type": "integer",
            "description": "Pixels to scroll. Defaults to 900.",
            "minimum": 1,
            "maximum": 5000,
        },
        "wait_ms": {
            "type": "integer",
            "description": "Optional wait after scrolling, in milliseconds.",
            "minimum": 0,
            "maximum": 10000,
        },
    },
    "required": ["session_id"],
}


async def chromux_scroll_handler(**kwargs: Any) -> str:
    """Scroll an existing chromux session without forcing the agent into Bash."""
    session_id = kwargs.get("session_id") or kwargs.get("session")
    if not session_id or not isinstance(session_id, str):
        return json.dumps(
            {"ok": False, "error": "missing or invalid 'session_id' argument"}
        )

    direction = kwargs.get("direction") or "down"
    if direction not in ("down", "up"):
        return json.dumps({"ok": False, "error": f"unsupported direction: {direction}"})

    try:
        pixels = int(kwargs.get("pixels") or 900)
    except (TypeError, ValueError):
        pixels = 900
    pixels = min(max(pixels, 1), 5000)

    try:
        wait_ms = int(kwargs.get("wait_ms") or 0)
    except (TypeError, ValueError):
        wait_ms = 0
    wait_ms = min(max(wait_ms, 0), 10000)

    env = chromux_automation_env(resolve_chromux_profile())
    args = _run_js_args(session_id, _scroll_js(direction=direction, pixels=pixels))
    try:
        proc = await asyncio.to_thread(
            _run_chromux, args, env=env, timeout=_EXTRACT_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "chromux scroll timed out"})
    except FileNotFoundError:
        return json.dumps({"ok": False, "error": "chromux CLI not found on PATH"})
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("chromux scroll raised: %s", e)
        return json.dumps({"ok": False, "error": f"chromux scroll failed: {e}"})

    if proc.returncode != 0:
        return json.dumps(
            {
                "ok": False,
                "error": (proc.stderr or proc.stdout or "chromux scroll non-zero exit").strip(),
            }
        )

    await _best_effort_wait(wait_ms)
    return json.dumps(
        {
            "ok": True,
            "session_id": session_id,
            "direction": direction,
            "pixels": pixels,
            "wait_ms": wait_ms,
        }
    )


chromux_scroll = ToolSpec(
    name="chromux_scroll",
    description=(
        "Scroll an existing chromux session up or down without using Bash. "
        "Use between extraction passes when a page lazy-loads feed cards."
    ),
    input_schema=_SCROLL_INPUT_SCHEMA,
    handler=chromux_scroll_handler,
)


_SCROLL_EXTRACT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "chromux session id returned by chromux_navigate.",
        },
        "session": {
            "type": "string",
            "description": "Alias for session_id, accepted for agent compatibility.",
        },
        "selector": {
            "type": "string",
            "description": "CSS selector for repeated cards/items to collect.",
        },
        "attributes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Attribute names to collect from each matched card.",
        },
        "include_text": {
            "type": "boolean",
            "description": "Include visible text in each record when not already requested.",
        },
        "max_scrolls": {
            "type": "integer",
            "description": "Maximum scroll steps after the first extraction pass.",
            "minimum": 0,
            "maximum": 20,
        },
        "limit_per_pass": {
            "type": "integer",
            "description": "Maximum matched elements to read per extraction pass.",
            "minimum": 1,
            "maximum": 500,
        },
        "max_items": {
            "type": "integer",
            "description": "Stop once this many unique records have been collected.",
            "minimum": 1,
            "maximum": 500,
        },
        "unique_by": {
            "type": "string",
            "description": "Preferred field for dedupe, e.g. href, url, data-urn.",
        },
        "scroll_pixels": {
            "type": "integer",
            "description": "Pixels to scroll between passes.",
            "minimum": 1,
            "maximum": 5000,
        },
        "wait_ms": {
            "type": "integer",
            "description": "Wait after each scroll, in milliseconds.",
            "minimum": 0,
            "maximum": 10000,
        },
        "stop_when_no_new": {
            "type": "boolean",
            "description": "Stop when a pass adds no new unique records.",
        },
    },
    "required": ["session_id", "selector"],
}


async def chromux_scroll_extract_handler(**kwargs: Any) -> str:
    """Extract repeated card records across bounded scroll passes."""
    session_id = kwargs.get("session_id") or kwargs.get("session")
    if not session_id or not isinstance(session_id, str):
        return json.dumps(
            {"ok": False, "error": "missing or invalid 'session_id' argument"}
        )
    selector = kwargs.get("selector")
    if not selector or not isinstance(selector, str):
        return json.dumps({"ok": False, "error": "missing or invalid 'selector' argument"})

    raw_attributes = kwargs.get("attributes") or []
    attributes = [
        str(attribute)
        for attribute in raw_attributes
        if isinstance(attribute, str) and attribute.strip()
    ]
    if not attributes:
        attributes = ["href", "text"]

    try:
        max_scrolls = int(kwargs.get("max_scrolls") or 3)
    except (TypeError, ValueError):
        max_scrolls = 3
    max_scrolls = min(max(max_scrolls, 0), 20)

    try:
        limit_per_pass = int(kwargs.get("limit_per_pass") or 100)
    except (TypeError, ValueError):
        limit_per_pass = 100
    limit_per_pass = min(max(limit_per_pass, 1), 500)

    try:
        max_items = int(kwargs.get("max_items") or 100)
    except (TypeError, ValueError):
        max_items = 100
    max_items = min(max(max_items, 1), 500)

    try:
        scroll_pixels = int(kwargs.get("scroll_pixels") or 1000)
    except (TypeError, ValueError):
        scroll_pixels = 1000
    scroll_pixels = min(max(scroll_pixels, 1), 5000)

    try:
        wait_ms = int(kwargs.get("wait_ms") or 800)
    except (TypeError, ValueError):
        wait_ms = 800
    wait_ms = min(max(wait_ms, 0), 10000)

    include_text = bool(kwargs.get("include_text", True))
    unique_by = str(kwargs.get("unique_by") or "")
    stop_when_no_new = bool(kwargs.get("stop_when_no_new", True))
    env = chromux_automation_env(resolve_chromux_profile())

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    new_counts: list[int] = []
    stopped_reason = "max_scrolls"
    extract_js = _attribute_extract_js(
        selector=selector,
        attributes=attributes,
        multiple=True,
        limit=limit_per_pass,
        include_text=include_text,
    )

    for pass_index in range(max_scrolls + 1):
        extract_args = _run_js_args(session_id, extract_js)
        try:
            proc = await asyncio.to_thread(
                _run_chromux,
                extract_args,
                env=env,
                timeout=_EXTRACT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "ok": False,
                    "error": "chromux scroll extract timed out",
                    "items": records,
                    "new_counts": new_counts,
                }
            )
        except FileNotFoundError:
            return json.dumps({"ok": False, "error": "chromux CLI not found on PATH"})
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("chromux scroll extract raised: %s", e)
            return json.dumps({"ok": False, "error": f"chromux scroll extract failed: {e}"})

        if proc.returncode != 0:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        proc.stderr
                        or proc.stdout
                        or "chromux scroll extract non-zero exit"
                    ).strip(),
                    "items": records,
                    "new_counts": new_counts,
                }
            )

        added = 0
        for record in _parse_records(proc.stdout or ""):
            key = _dedupe_key(record, unique_by)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
            added += 1
            if len(records) >= max_items:
                stopped_reason = "max_items"
                break
        new_counts.append(added)

        if len(records) >= max_items:
            break
        if pass_index >= max_scrolls:
            break
        if added == 0 and stop_when_no_new:
            stopped_reason = "no_new_items"
            break

        scroll_args = _run_js_args(session_id, _scroll_js(direction="down", pixels=scroll_pixels))
        try:
            scroll_proc = await asyncio.to_thread(
                _run_chromux,
                scroll_args,
                env=env,
                timeout=_EXTRACT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            stopped_reason = "scroll_timeout"
            break
        if scroll_proc.returncode != 0:
            stopped_reason = "scroll_failed"
            break
        await _best_effort_wait(wait_ms)

    return json.dumps(
        {
            "ok": True,
            "session_id": session_id,
            "selector": selector,
            "attributes": attributes,
            "items": records,
            "item_count": len(records),
            "passes": len(new_counts),
            "new_counts": new_counts,
            "stopped_reason": stopped_reason,
        }
    )


chromux_scroll_extract = ToolSpec(
    name="chromux_scroll_extract",
    description=(
        "Collect repeated feed/search cards from a chromux session across a "
        "bounded scroll loop, deduping records by href/url/data-urn/text. Use "
        "for list harvest before opening detail pages."
    ),
    input_schema=_SCROLL_EXTRACT_INPUT_SCHEMA,
    handler=chromux_scroll_extract_handler,
)


def _register_default() -> None:
    """Overwrite placeholder browser specs with rich schemas."""
    try:
        from contents_hub.tools.registry import get_default_registry

        registry = get_default_registry()
        registry.register(chromux_navigate)
        registry.register(chromux_extract)
        registry.register(chromux_scroll)
        registry.register(chromux_scroll_extract)
    except Exception:  # noqa: BLE001
        logger.debug("chromux tools: deferred default-registry registration")


_register_default()


__all__ = [
    "CHROMUX_PROFILE",
    "chromux_navigate",
    "chromux_navigate_handler",
    "chromux_extract",
    "chromux_extract_handler",
    "chromux_scroll",
    "chromux_scroll_handler",
    "chromux_scroll_extract",
    "chromux_scroll_extract_handler",
]

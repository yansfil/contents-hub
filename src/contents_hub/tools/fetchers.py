"""HTTP fetch ToolSpec handler.

Provides ``fetch_url`` — a ToolSpec-registered async handler that performs
a plain HTTP GET and returns a compact, agent-readable representation by
default. Raw bodies are available only when callers explicitly request
``mode="raw"``. This keeps agent contexts from being filled by large HTML,
RSS, or JSON responses while preserving an escape hatch for internal callers
that need the original body.

The handler signature matches the ToolSpec contract:
    ``Callable[..., Awaitable[str]]``
Each handler accepts keyword arguments matching ``input_schema`` and returns
a JSON-serialized string the agent can read.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


# Mirrors the User-Agent / Accept defaults previously hard-coded in
# collectors/rss.py and collectors/youtube.py so behavior is unchanged.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = (
    "application/rss+xml, application/atom+xml, "
    "application/xml, text/xml, text/html, application/json, */*"
)
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_CHARS = 8000
DEFAULT_MAX_ITEMS = 10
_RAW_MODE_MAX_CHARS = 20000
_JSON_PREVIEW_MAX_CHARS = 2000
_ITEM_TEXT_MAX_CHARS = 600
_LINK_TEXT_MAX_CHARS = 160
_MAX_LINKS = 80
_HEADER_VALUE_MAX_CHARS = 240
_HEADER_ALLOWLIST = {
    "content-type",
    "content-length",
    "last-modified",
    "etag",
    "date",
    "server",
    "location",
    "cache-control",
    "x-ratelimit-used",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}

# Transient retry policy. A flaky upstream (e.g. youtube.com/feeds returning a
# spurious 404, or a 429/5xx hiccup) should not surface as a hard failure that
# makes the LIST agent flail for dozens of turns — bounded retry with short
# backoff absorbs it. Non-retryable client errors (400/401/403/404-on-real-404)
# still fall through after the attempts are exhausted; the caller sees the last
# response either way.
_RETRYABLE_STATUS = frozenset({404, 408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.5


async def fetch_url(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    follow_redirects: bool = True,
    binary: bool = False,
    mode: str = "auto",
    max_chars: int | None = DEFAULT_MAX_CHARS,
    max_items: int = DEFAULT_MAX_ITEMS,
    selector: str | None = None,
    include_links: bool = True,
) -> str:
    """Perform an HTTP request and return the result as a JSON string.

    Returned JSON shape::

        {
          "ok":          bool,
          "status":      int,                # HTTP status code (0 on transport failure)
          "url":         str,                # final URL after redirects
          "headers":     dict[str, str],     # response headers
          "content_type": str,
          "mode":        str,                # raw | feed | json | html | text
          "body":        str,                # raw text only in raw mode; compact text otherwise
          "markdown":    str,                # compact readable HTML/text extraction
          "items":       list[dict],         # compact feed/JSON candidate items
          "links":       list[dict],         # compact extracted links when available
          "truncated":   bool,
          "raw_body_chars": int,
          "body_base64": str,                # base64 of bytes when binary=True
          "error":       str,                # populated only when ok=False
          "error_type":  str,                # "timeout" | "http" | "network" | "unknown"
        }

    The handler returns a string (per ToolSpec.handler contract:
    ``Callable[..., Awaitable[str]]``) so it can be passed back to the agent
    SDK without further marshaling.
    """
    request_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": DEFAULT_ACCEPT,
    }
    if headers:
        request_headers.update(headers)

    last_failure: dict[str, Any] | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=follow_redirects
            ) as client:
                resp = await client.request(method, url, headers=request_headers)

            result: dict[str, Any] = {
                "ok": 200 <= resp.status_code < 400,
                "status": resp.status_code,
                "url": str(resp.url),
                "headers": _compact_headers(resp.headers),
                "content_type": resp.headers.get("content-type", ""),
                "mode": "raw" if binary else "",
                "body": "",
                "markdown": "",
                "items": [],
                "links": [],
                "truncated": False,
                "raw_body_chars": 0,
                "body_base64": (
                    _truncate_base64(resp.content, max_chars=max_chars) if binary else ""
                ),
                "error": "",
                "error_type": "",
            }
            if not binary:
                result.update(
                    await _compact_response_payload(
                        text=resp.text,
                        url=str(resp.url),
                        content_type=resp.headers.get("content-type", ""),
                        requested_mode=mode,
                        max_chars=max_chars,
                        max_items=max_items,
                        selector=selector,
                        include_links=include_links,
                    )
                )
            if result["ok"]:
                return json.dumps(result)

            result["error"] = f"HTTP {resp.status_code}"
            result["error_type"] = "http"
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                last_failure = result
                await asyncio.sleep(_BACKOFF_BASE_S * attempt)
                logger.debug(
                    "fetch_url retry %d/%d for %s after HTTP %d",
                    attempt, _MAX_ATTEMPTS, url, resp.status_code,
                )
                continue
            return json.dumps(result)

        except httpx.TimeoutException as exc:
            last_failure = {
                "ok": False,
                "status": 0,
                "url": url,
                "headers": {},
                "content_type": "",
                "mode": "",
                "body": "",
                "markdown": "",
                "items": [],
                "links": [],
                "truncated": False,
                "raw_body_chars": 0,
                "body_base64": "",
                "error": f"Request timed out after {timeout}s",
                "error_type": "timeout",
            }
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_S * attempt)
                logger.debug(
                    "fetch_url retry %d/%d for %s after timeout: %s",
                    attempt, _MAX_ATTEMPTS, url, exc,
                )
                continue
            logger.debug("fetch_url timeout for %s: %s", url, exc)
            return json.dumps(last_failure)
        except httpx.HTTPError as exc:
            last_failure = {
                "ok": False,
                "status": 0,
                "url": url,
                "headers": {},
                "content_type": "",
                "mode": "",
                "body": "",
                "markdown": "",
                "items": [],
                "links": [],
                "truncated": False,
                "raw_body_chars": 0,
                "body_base64": "",
                "error": f"HTTP error: {exc}",
                "error_type": "network",
            }
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_S * attempt)
                logger.debug(
                    "fetch_url retry %d/%d for %s after transport error: %s",
                    attempt, _MAX_ATTEMPTS, url, exc,
                )
                continue
            logger.debug("fetch_url HTTP error for %s: %s", url, exc)
            return json.dumps(last_failure)
        except Exception as exc:  # noqa: BLE001
            logger.debug("fetch_url unexpected error for %s: %s", url, exc)
            return json.dumps(
                {
                    "ok": False,
                    "status": 0,
                    "url": url,
                    "headers": {},
                    "content_type": "",
                    "mode": "",
                    "body": "",
                    "markdown": "",
                    "items": [],
                    "links": [],
                    "truncated": False,
                    "raw_body_chars": 0,
                    "body_base64": "",
                    "error": f"Unexpected error: {exc}",
                    "error_type": "unknown",
                }
            )

    # Loop exhausted (all attempts were retryable failures).
    return json.dumps(last_failure or {
        "ok": False,
        "status": 0,
        "url": url,
        "headers": {},
        "content_type": "",
        "mode": "",
        "body": "",
        "markdown": "",
        "items": [],
        "links": [],
        "truncated": False,
        "raw_body_chars": 0,
        "body_base64": "",
        "error": "fetch failed after retries",
        "error_type": "unknown",
    })


async def _compact_response_payload(
    *,
    text: str,
    url: str,
    content_type: str,
    requested_mode: str,
    max_chars: int | None,
    max_items: int,
    selector: str | None,
    include_links: bool,
) -> dict[str, Any]:
    mode = _resolve_mode(requested_mode, content_type, text)
    raw_body_chars = len(text or "")
    if mode == "raw":
        limit = _RAW_MODE_MAX_CHARS if max_chars is None else max_chars
        body, truncated = _truncate_text(text, limit)
        return {
            "mode": "raw",
            "body": body,
            "markdown": "",
            "items": [],
            "links": [],
            "truncated": truncated,
            "raw_body_chars": raw_body_chars,
        }
    if mode == "feed":
        compact = await _compact_feed(text, feed_url=url, max_items=max_items)
        compact["raw_body_chars"] = raw_body_chars
        return compact
    if mode == "json":
        compact = _compact_json(text, max_items=max_items, max_chars=max_chars)
        compact["raw_body_chars"] = raw_body_chars
        return compact
    if mode == "html":
        compact = _compact_html(
            text,
            base_url=url,
            selector=selector,
            max_chars=max_chars,
            include_links=include_links,
        )
        compact["raw_body_chars"] = raw_body_chars
        return compact

    body, truncated = _truncate_text(_collapse_ws(text), max_chars)
    return {
        "mode": "text",
        "body": body,
        "markdown": body,
        "items": [],
        "links": [],
        "truncated": truncated,
        "raw_body_chars": raw_body_chars,
    }


def _resolve_mode(requested_mode: str, content_type: str, text: str) -> str:
    requested = (requested_mode or "auto").lower().strip()
    if requested in {"raw", "feed", "json", "html", "text"}:
        return requested

    content_type_l = (content_type or "").lower()
    stripped = (text or "").lstrip()
    if "json" in content_type_l or stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if (
        "rss" in content_type_l
        or "atom" in content_type_l
        or "xml" in content_type_l
        or stripped.startswith("<?xml")
        or stripped.startswith("<rss")
        or stripped.startswith("<feed")
    ):
        return "feed"
    if "html" in content_type_l or "<html" in stripped[:500].lower():
        return "html"
    return "text"


async def _compact_feed(text: str, *, feed_url: str, max_items: int) -> dict[str, Any]:
    try:
        from contents_hub.tools.parse import parse_rss

        parsed = json.loads(
            await parse_rss(xml=text, feed_url=feed_url, max_items=max(1, max_items))
        )
    except Exception as exc:  # noqa: BLE001
        fallback, truncated = _truncate_text(_collapse_ws(text), DEFAULT_MAX_CHARS)
        return {
            "mode": "feed",
            "body": fallback,
            "markdown": fallback,
            "items": [],
            "links": [],
            "truncated": truncated,
            "error": f"Feed compacting failed: {exc}",
            "error_type": "parse",
        }
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        fallback, truncated = _truncate_text(_collapse_ws(text), DEFAULT_MAX_CHARS)
        return {
            "mode": "feed",
            "body": fallback,
            "markdown": fallback,
            "items": [],
            "links": [],
            "truncated": truncated,
            "error": str(parsed.get("error") if isinstance(parsed, dict) else ""),
            "error_type": "parse",
        }
    items = [
        _compact_item_dict(item)
        for item in (parsed.get("items") or [])[: max(1, max_items)]
        if isinstance(item, dict)
    ]
    return {
        "mode": "feed",
        "body": "",
        "markdown": "",
        "feed_title": str(parsed.get("feed_title") or ""),
        "feed_url": str(parsed.get("feed_url") or feed_url),
        "is_podcast": bool(parsed.get("is_podcast")),
        "items": items,
        "links": [],
        "truncated": False,
    }


def _compact_json(text: str, *, max_items: int, max_chars: int | None) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        fallback, truncated = _truncate_text(_collapse_ws(text), max_chars)
        return {
            "mode": "json",
            "body": fallback,
            "markdown": fallback,
            "items": [],
            "links": [],
            "truncated": truncated,
            "error": f"JSON parse error: {exc}",
            "error_type": "parse",
        }

    items = [_compact_item_dict(item) for item in _json_candidate_items(data, max_items)]
    preview = _json_preview(data, max_chars=max_chars or _JSON_PREVIEW_MAX_CHARS)
    return {
        "mode": "json",
        "body": "",
        "markdown": "",
        "items": items,
        "links": [],
        "json_preview": preview,
        "truncated": len(preview) >= (max_chars or _JSON_PREVIEW_MAX_CHARS),
    }


def _compact_html(
    html: str,
    *,
    base_url: str,
    selector: str | None,
    max_chars: int | None,
    include_links: bool,
) -> dict[str, Any]:
    parser = _ReadableHTMLParser(base_url=base_url, selector=selector)
    parser.feed(html or "")
    parser.close()
    selected_error = parser.selector_error
    markdown_raw = parser.text()
    markdown, truncated = _truncate_text(markdown_raw, max_chars)
    links = parser.links if include_links else []
    result = {
        "mode": "html",
        "body": markdown,
        "markdown": markdown,
        "title": parser.title,
        "meta": parser.meta,
        "items": [],
        "links": links,
        "truncated": truncated,
    }
    if selected_error:
        result["selector_error"] = selected_error
    return result


class _ReadableHTMLParser(HTMLParser):
    _IGNORE_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
    }
    _BLOCK_TAGS = {
        "article",
        "main",
        "section",
        "div",
        "p",
        "br",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
    }

    def __init__(self, *, base_url: str, selector: str | None):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        raw_selector = (selector or "").strip()
        self.selector_error = "" if _selector_supported(raw_selector) else (
            "Only simple tag, .class, #id, tag.class, tag#id, [attr], and [attr=value] selectors are supported"
        )
        self.selector = "" if self.selector_error else raw_selector
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()
        self._text_parts: list[str] = []
        self._title_parts: list[str] = []
        self._ignore_depth = 0
        self._body_depth = 0
        self._saw_body_tag = False
        self._selector_depth = 0
        self._in_title = False
        self._current_link: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag in self._IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = str(attr_map.get("name") or attr_map.get("property") or "").strip()
            content = str(attr_map.get("content") or "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = _truncate_text(_collapse_ws(content), _ITEM_TEXT_MAX_CHARS)[0]
                if key in {"og:title", "twitter:title", "title"} and not self.title:
                    self.title = self.meta[key]
        if tag == "body":
            self._saw_body_tag = True
            self._body_depth += 1
        if self.selector and not self.selector_error:
            if self._selector_depth:
                self._selector_depth += 1
            elif _selector_matches(tag, attr_map, self.selector):
                self._selector_depth = 1
        if tag == "a" and self._is_capturing():
            href = str(attr_map.get("href") or "").strip()
            self._current_link = {"href": href, "text_parts": []} if href else None
        if tag in self._BLOCK_TAGS and self._is_capturing():
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORE_TAGS and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return
        if tag == "title":
            self._in_title = False
            if not self.title:
                self.title = _collapse_ws(" ".join(self._title_parts))
        if tag == "body" and self._body_depth:
            self._body_depth -= 1
        if tag == "a" and self._current_link is not None:
            self._finish_link()
        if tag in self._BLOCK_TAGS and self._is_capturing():
            self._append_break()
        if self.selector and self._selector_depth:
            self._selector_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        value = _collapse_ws(data)
        if not value:
            return
        if self._in_title:
            self._title_parts.append(value)
            return
        if not self._is_capturing():
            return
        self._text_parts.append(value)
        if self._current_link is not None:
            self._current_link["text_parts"].append(value)

    def text(self) -> str:
        text = "\n".join(part for part in self._text_parts if part != "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _is_capturing(self) -> bool:
        if self.selector:
            return bool(self._selector_depth)
        return self._body_depth > 0 or not self._saw_body_tag

    def _append_break(self) -> None:
        if self._text_parts and self._text_parts[-1] != "\n":
            self._text_parts.append("\n")

    def _finish_link(self) -> None:
        assert self._current_link is not None
        href = str(self._current_link.get("href") or "").strip()
        absolute = urljoin(self.base_url, href)
        if absolute and absolute not in self._seen_links and len(self.links) < _MAX_LINKS:
            self._seen_links.add(absolute)
            link_text = _truncate_text(
                _collapse_ws(" ".join(self._current_link.get("text_parts") or [])),
                _LINK_TEXT_MAX_CHARS,
            )[0]
            self.links.append({"url": absolute, "text": link_text})
        self._current_link = None


def _selector_supported(selector: str) -> bool:
    if not selector:
        return True
    return not any(token in selector for token in (" ", ",", ">", "+", "~"))


def _selector_matches(tag: str, attrs: dict[str, str], selector: str) -> bool:
    if not selector:
        return False
    attr_match = re.fullmatch(r"\[([a-zA-Z0-9_:-]+)(?:=['\"]?([^'\"]+)['\"]?)?\]", selector)
    if attr_match:
        key = attr_match.group(1).lower()
        expected = attr_match.group(2)
        if expected is None:
            return key in attrs
        return attrs.get(key) == expected
    wanted_tag = ""
    wanted_id = ""
    wanted_class = ""
    rest = selector
    if "#" in rest:
        before, after = rest.split("#", 1)
        wanted_tag = before
        wanted_id = after
    elif "." in rest:
        before, after = rest.split(".", 1)
        wanted_tag = before
        wanted_class = after
    elif rest.startswith("#"):
        wanted_id = rest[1:]
    elif rest.startswith("."):
        wanted_class = rest[1:]
    else:
        wanted_tag = rest
    if wanted_tag and tag != wanted_tag.lower():
        return False
    if wanted_id and attrs.get("id") != wanted_id:
        return False
    if wanted_class:
        classes = set(str(attrs.get("class") or "").split())
        if wanted_class not in classes:
            return False
    return True


def _json_candidate_items(data: Any, max_items: int) -> list[dict[str, Any]]:
    if isinstance(data, list):
        reddit_children: list[dict[str, Any]] = []
        for entry in data:
            children = _json_path(entry, ("data", "children"))
            if isinstance(children, list):
                reddit_children.extend(
                    _json_item_source(item)
                    for item in children
                    if isinstance(_json_item_source(item), dict)
                )
        if reddit_children:
            return reddit_children[: max(1, max_items)]

    for path in (
        ("hits",),
        ("items",),
        ("results",),
        ("posts",),
        ("data", "children"),
        ("data", "items"),
        ("data", "results"),
    ):
        value = _json_path(data, path)
        if isinstance(value, list):
            return [
                _json_item_source(item)
                for item in value[: max(1, max_items)]
                if isinstance(_json_item_source(item), dict)
            ]
    if isinstance(data, list):
        return [
            _json_item_source(item)
            for item in data[: max(1, max_items)]
            if isinstance(_json_item_source(item), dict)
        ]
    return []


def _json_item_source(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return value["data"]
    return value if isinstance(value, dict) else {}


def _json_path(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _compact_item_dict(item: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    key_aliases = {
        "title": ("title", "story_title", "name"),
        "url": ("url", "story_url", "link", "permalink"),
        "summary": ("summary", "description", "selftext", "body", "text"),
        "author": ("author", "by", "user", "creator"),
        "published_at": ("published_at", "created_at", "pubDate", "updated"),
    }
    for output_key, aliases in key_aliases.items():
        for alias in aliases:
            value = item.get(alias)
            if value not in (None, ""):
                compact[output_key] = _compact_url(value) if output_key == "url" else _compact_scalar(value)
                break
    for key in (
        "id",
        "objectID",
        "points",
        "score",
        "num_comments",
        "comment_count",
        "created_at_i",
        "subreddit",
        "domain",
    ):
        if key in item and item.get(key) not in (None, ""):
            compact[key] = _compact_scalar(item.get(key))
    return compact


def _compact_headers(headers: Any) -> dict[str, str]:
    compact: dict[str, str] = {}
    for key, value in dict(headers).items():
        normalized = str(key).lower()
        if normalized not in _HEADER_ALLOWLIST:
            continue
        compact[normalized] = _truncate_text(
            _collapse_ws(str(value)), _HEADER_VALUE_MAX_CHARS
        )[0]
    return compact


def _compact_url(value: Any) -> str:
    text = _compact_scalar(value)
    if isinstance(text, str) and text.startswith("/"):
        return urljoin("https://www.reddit.com", text)
    return str(text)


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_text(_collapse_ws(value), _ITEM_TEXT_MAX_CHARS)[0]
    return value


def _json_preview(data: Any, *, max_chars: int) -> str:
    preview = _truncate_json_value(data)
    text = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    return _truncate_text(text, max_chars)[0]


def _truncate_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        if isinstance(value, (dict, list)):
            return f"<{type(value).__name__}>"
        return _compact_scalar(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                out["..."] = f"{len(value) - index} more keys"
                break
            out[str(key)] = _truncate_json_value(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        out = [_truncate_json_value(item, depth=depth + 1) for item in value[:20]]
        if len(value) > 20:
            out.append(f"... {len(value) - 20} more items")
        return out
    return _compact_scalar(value)


def _truncate_text(text: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars is None:
        max_chars = DEFAULT_MAX_CHARS
    if max_chars <= 0 or len(text or "") <= max_chars:
        return text or "", False
    return (text or "")[:max_chars].rstrip() + "\n...[truncated]", True


def _truncate_base64(content: bytes, *, max_chars: int | None) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return _truncate_text(encoded, max_chars)[0]


_WS_RE = re.compile(r"\s+")


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


# ToolSpec registration -----------------------------------------------------
# Constructed lazily so importing this module never fails even if T3 has not
# yet committed tools/base.py (the orchestrator runs T3 and T4 concurrently).
def _build_spec() -> Any:
    from contents_hub.tools.base import ToolSpec  # local import — avoids cycles

    return ToolSpec(
        name="fetch_url",
        description=(
            "Perform an HTTP GET (or specified method) against a URL and "
            "return a compact JSON string by default. RSS/Atom feeds become "
            "bounded item lists, JSON APIs become compact previews and item "
            "candidates, and HTML pages become readable markdown-ish text. "
            "Use mode='raw' only when the original response body is required."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute http(s) URL to fetch.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Request timeout in seconds.",
                    "default": DEFAULT_TIMEOUT,
                },
                "headers": {
                    "type": "object",
                    "description": (
                        "Optional additional request headers. Merged on top "
                        "of the default User-Agent / Accept headers."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method.",
                    "default": "GET",
                },
                "follow_redirects": {
                    "type": "boolean",
                    "description": "Whether to follow HTTP redirects.",
                    "default": True,
                },
                "binary": {
                    "type": "boolean",
                    "description": (
                        "If true, return body bytes base64-encoded under "
                        "'body_base64' instead of decoded text."
                    ),
                    "default": False,
                },
                "mode": {
                    "type": "string",
                    "description": (
                        "Response compacting mode. 'auto' detects feed/json/html/text. "
                        "'raw' returns the original text body, capped unless max_chars=0."
                    ),
                    "enum": ["auto", "raw", "feed", "json", "html", "text"],
                    "default": "auto",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "Maximum characters for compact text/raw previews. "
                        "Use 0 only when an internal caller truly needs an uncapped raw body."
                    ),
                    "default": DEFAULT_MAX_CHARS,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum feed/JSON candidate items to return.",
                    "default": DEFAULT_MAX_ITEMS,
                },
                "selector": {
                    "type": "string",
                    "description": (
                        "Optional CSS selector used only for HTML mode to scope extraction."
                    ),
                },
                "include_links": {
                    "type": "boolean",
                    "description": "Whether compact HTML extraction should include links.",
                    "default": True,
                },
            },
            "required": ["url"],
        },
        handler=fetch_url,
    )


def get_spec() -> Any:
    """Return the ToolSpec for ``fetch_url``.

    Built lazily so this module can be imported without requiring
    ``tools/base.py`` to be fully populated yet (T3 runs concurrently).
    """
    return _build_spec()


def _register_default() -> None:
    """Re-register the rich ToolSpec on the default registry.

    T3 pre-populates the default registry with a placeholder spec whose
    ``input_schema`` is empty. We overwrite it with our schema so that
    SDK adapters surfacing tool definitions to the agent see the full
    parameter list. Overwrite-on-register is intentional and supported
    (see learnings.json T3 round 2).
    """
    try:
        from contents_hub.tools.registry import get_default_registry

        get_default_registry().register(_build_spec())
    except Exception:  # noqa: BLE001
        # Registry may not yet be importable in unusual import orderings;
        # the lazy proxy already in the default registry will still call
        # our async handler correctly, so this is non-fatal.
        logger.debug("fetch_url: deferred default-registry registration")


_register_default()


__all__ = ["fetch_url", "get_spec"]

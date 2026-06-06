"""Parser ToolSpec handlers — RSS, HTML, JSON.

ToolSpec-registered async handlers for parsing fetched content. Logic
absorbed from ``collectors/rss.py`` and ``collectors/youtube.py`` so the
new ``tools/`` layer carries the parsing rules previously living in the
deprecated ``collectors/`` and ``fetchers/`` subpackages.

Each handler conforms to the ToolSpec contract:
``Callable[..., Awaitable[str]]`` — async, returns a JSON string the agent
can read directly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree as ET

from dateutil.parser import parse as parse_date

logger = logging.getLogger(__name__)


# Common XML namespaces (mirrored verbatim from collectors/rss.py to preserve
# parser behavior exactly).
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

DEFAULT_MAX_ITEMS = 100


# ---------------------------------------------------------------------------
# parse_rss
# ---------------------------------------------------------------------------


async def parse_rss(
    xml: str,
    *,
    feed_url: str = "",
    max_items: int = DEFAULT_MAX_ITEMS,
) -> str:
    """Parse RSS 2.0, RSS 1.0 (RDF), or Atom 1.0 XML into a JSON string.

    Returned JSON shape::

        {
          "ok":         bool,
          "feed_title": str,
          "feed_url":   str,
          "is_podcast": bool,
          "items":      [ <item>, ... ],
          "error":      str
        }

    Each ``item`` carries: ``url``, ``title``, ``summary``, ``author``,
    ``published_at`` (ISO 8601 or null), ``tags``, ``content_html``,
    ``enclosure_url``, ``enclosure_type``, ``enclosure_length``.
    Also recognises the YouTube ``yt:videoId`` element for YouTube Atom feeds
    and exposes it under ``video_id`` when present.
    """
    try:
        root = ET.fromstring(xml)  # noqa: S314
    except ET.ParseError as exc:
        return json.dumps(
            _err_payload(feed_url, f"XML parse error: {exc}")
        )

    tag = _strip_ns(root.tag)
    if tag == "feed":
        result = _parse_atom(root, feed_url=feed_url, max_items=max_items)
    elif tag == "rss":
        channel = root.find("channel")
        if channel is None:
            return json.dumps(_err_payload(feed_url, "RSS feed missing <channel>"))
        result = _parse_rss2(channel, feed_url=feed_url, max_items=max_items)
    elif tag == "RDF":
        result = _parse_rss1(root, feed_url=feed_url, max_items=max_items)
    else:
        return json.dumps(
            _err_payload(feed_url, f"Unknown feed format: root tag <{root.tag}>")
        )

    return json.dumps(result)


# ---------------------------------------------------------------------------
# parse_html
# ---------------------------------------------------------------------------


_HTML_TITLE_RE = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_HTML_META_RE = re.compile(
    r"""<meta\s+[^>]*?(?:name|property)\s*=\s*['"]([^'"]+)['"][^>]*?content\s*=\s*['"]([^'"]*)['"][^>]*?/?>""",
    re.IGNORECASE,
)
_HTML_LINK_RE = re.compile(
    r"""<a\s+[^>]*?href\s*=\s*['"]([^'"]+)['"][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_HTML_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def parse_html(
    html: str,
    *,
    base_url: str = "",
    max_links: int = 200,
) -> str:
    """Extract title, meta tags, links, and visible text from an HTML page.

    This is a deliberately small regex-based implementation that mirrors the
    "best effort" extraction the previous ``BrowserFetcher`` recipes relied
    on. Real DOM-level work goes through ``chromux_*`` tools.

    Returned JSON shape::

        {
          "ok":     bool,
          "title":  str,
          "meta":   { name_or_property: content, ... },
          "links":  [ {"url": str, "text": str}, ... ],
          "text":   str,                  # visible text, whitespace-collapsed
          "error":  str
        }
    """
    if not html:
        return json.dumps(
            {
                "ok": False,
                "title": "",
                "meta": {},
                "links": [],
                "text": "",
                "error": "Empty HTML input",
            }
        )

    title_match = _HTML_TITLE_RE.search(html)
    title = _collapse_ws(_strip_tags(title_match.group(1))) if title_match else ""

    meta: dict[str, str] = {}
    for name, content in _HTML_META_RE.findall(html):
        if name and name not in meta:
            meta[name] = content

    links: list[dict[str, str]] = []
    for href, text in _HTML_LINK_RE.findall(html):
        if len(links) >= max_links:
            break
        url = _absolutize(href, base_url)
        if not url:
            continue
        links.append({"url": url, "text": _collapse_ws(_strip_tags(text))})

    cleaned = _HTML_SCRIPT_STYLE_RE.sub(" ", html)
    visible = _collapse_ws(_strip_tags(cleaned))

    return json.dumps(
        {
            "ok": True,
            "title": title,
            "meta": meta,
            "links": links,
            "text": visible,
            "error": "",
        }
    )


# ---------------------------------------------------------------------------
# parse_json
# ---------------------------------------------------------------------------


async def parse_json(
    body: str,
    *,
    path: str = "",
) -> str:
    """Parse a JSON body string and optionally extract a sub-tree by JSON path.

    The ``path`` argument is a simple dotted/bracketed expression like
    ``data.items[0].title``; not a full JSONPath implementation (kept
    intentionally tiny — no new deps per INV-11).

    Returned JSON shape::

        {
          "ok":     bool,
          "value":  <any JSON value>,
          "error":  str
        }
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return json.dumps(
            {"ok": False, "value": None, "error": f"JSON parse error: {exc}"}
        )

    if not path:
        return json.dumps({"ok": True, "value": data, "error": ""})

    try:
        value = _resolve_path(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "value": None, "error": f"Path resolution failed: {exc}"}
        )

    return json.dumps({"ok": True, "value": value, "error": ""})


# ---------------------------------------------------------------------------
# RSS parsing helpers (absorbed from collectors/rss.py + collectors/youtube.py)
# ---------------------------------------------------------------------------


def _err_payload(feed_url: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "feed_title": "",
        "feed_url": feed_url,
        "is_podcast": False,
        "items": [],
        "error": error,
    }


def _parse_atom(
    root: ET.Element, *, feed_url: str, max_items: int
) -> dict[str, Any]:
    feed_title = _text(root, "atom:title") or ""
    items: list[dict[str, Any]] = []
    has_audio = False

    for entry in root.findall("atom:entry", _NS)[:max_items]:
        link = _atom_link(entry)
        if not link or not _is_valid_url(link):
            # YouTube entries embed link as <link href="..."/> too; if
            # not present we fall back to constructing from videoId below.
            video_id = _text(entry, "yt:videoId") or ""
            if video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
            else:
                continue

        title = _text(entry, "atom:title") or ""
        summary = (
            _text(entry, "atom:summary")
            or _text(entry, "media:description")
            or ""
        )
        content_html = _text(entry, "atom:content") or ""
        author = _text(entry, "atom:author/atom:name") or ""
        published_raw = (
            _text(entry, "atom:published") or _text(entry, "atom:updated") or ""
        )
        published = _parse_datetime(published_raw)

        tags = [
            cat.get("term", "")
            for cat in entry.findall("atom:category", _NS)
            if cat.get("term")
        ]

        enc_url, enc_type, enc_length = "", "", None
        enc_link = entry.find('atom:link[@rel="enclosure"]', _NS)
        if enc_link is not None:
            enc_url = enc_link.get("href", "")
            enc_type = enc_link.get("type", "")
            raw_len = enc_link.get("length")
            enc_length = int(raw_len) if raw_len and raw_len.isdigit() else None
            if enc_type.startswith("audio/"):
                has_audio = True

        item = {
            "url": _normalize_url(link),
            "title": title,
            "summary": summary,
            "author": author,
            "published_at": published.isoformat() if published else None,
            "tags": tags,
            "content_html": content_html,
            "enclosure_url": enc_url,
            "enclosure_type": enc_type,
            "enclosure_length": enc_length,
        }
        video_id = _text(entry, "yt:videoId")
        if video_id:
            item["video_id"] = video_id
        items.append(item)

    if not items:
        return _err_payload(feed_url, "No valid entries in Atom feed")

    return {
        "ok": True,
        "feed_title": feed_title,
        "feed_url": feed_url,
        "is_podcast": has_audio,
        "items": items,
        "error": "",
    }


def _parse_rss2(
    channel: ET.Element, *, feed_url: str, max_items: int
) -> dict[str, Any]:
    feed_title = _text_direct(channel, "title") or ""
    items: list[dict[str, Any]] = []
    has_audio = False

    for item in channel.findall("item")[:max_items]:
        link = _text_direct(item, "link")
        if not link or not _is_valid_url(link):
            guid_el = item.find("guid")
            if guid_el is not None:
                is_perma = guid_el.get("isPermaLink", "true")
                if is_perma.lower() != "false":
                    guid_text = (guid_el.text or "").strip()
                    if _is_valid_url(guid_text):
                        link = guid_text

        if not link or not _is_valid_url(link):
            continue

        title = _text_direct(item, "title") or ""
        summary = _text_direct(item, "description") or ""
        content_html = _text(item, "content:encoded") or ""
        author = (
            _text(item, "dc:creator") or _text_direct(item, "author") or ""
        )
        published = _parse_datetime(_text_direct(item, "pubDate") or "")

        tags = [
            (cat.text or "").strip()
            for cat in item.findall("category")
            if cat.text and cat.text.strip()
        ]

        enc_url, enc_type, enc_length = "", "", None
        enc_el = item.find("enclosure")
        if enc_el is not None:
            enc_url = enc_el.get("url", "")
            enc_type = enc_el.get("type", "")
            raw_len = enc_el.get("length")
            enc_length = int(raw_len) if raw_len and raw_len.isdigit() else None
            if enc_type.startswith("audio/"):
                has_audio = True

        items.append(
            {
                "url": _normalize_url(link),
                "title": title,
                "summary": summary,
                "author": author,
                "published_at": published.isoformat() if published else None,
                "tags": tags,
                "content_html": content_html,
                "enclosure_url": enc_url,
                "enclosure_type": enc_type,
                "enclosure_length": enc_length,
            }
        )

    if not items:
        return _err_payload(feed_url, "No valid items in RSS 2.0 feed")

    return {
        "ok": True,
        "feed_title": feed_title,
        "feed_url": feed_url,
        "is_podcast": has_audio,
        "items": items,
        "error": "",
    }


def _parse_rss1(
    root: ET.Element, *, feed_url: str, max_items: int
) -> dict[str, Any]:
    channel = root.find("rss1:channel", _NS)
    feed_title = ""
    if channel is not None:
        feed_title = _text(channel, "rss1:title") or ""

    items: list[dict[str, Any]] = []
    for item in root.findall("rss1:item", _NS)[:max_items]:
        link = item.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or ""
        if not link:
            link = _text(item, "rss1:link") or ""
        if not link or not _is_valid_url(link):
            continue

        title = _text(item, "rss1:title") or ""
        summary = _text(item, "rss1:description") or ""
        author = _text(item, "dc:creator") or ""
        published = _parse_datetime(_text(item, "dc:date") or "")

        items.append(
            {
                "url": _normalize_url(link),
                "title": title,
                "summary": summary,
                "author": author,
                "published_at": published.isoformat() if published else None,
                "tags": [],
                "content_html": "",
                "enclosure_url": "",
                "enclosure_type": "",
                "enclosure_length": None,
            }
        )

    if not items:
        return _err_payload(feed_url, "No valid items in RSS 1.0 (RDF) feed")

    return {
        "ok": True,
        "feed_title": feed_title,
        "feed_url": feed_url,
        "is_podcast": False,
        "items": items,
        "error": "",
    }


def _atom_link(entry: ET.Element) -> str | None:
    for rel in ("alternate", None):
        for link in entry.findall("atom:link", _NS):
            link_rel = link.get("rel")
            if rel == "alternate" and link_rel == "alternate":
                return link.get("href")
            if rel is None and link_rel is None:
                return link.get("href")
    first = entry.find("atom:link", _NS)
    return first.get("href") if first is not None else None


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _strip_ns(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _text(el: ET.Element, path: str) -> str | None:
    child = el.find(path, _NS)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _text_direct(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _is_valid_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunparse(parsed._replace(path=path))
    except Exception:  # noqa: BLE001
        return url


def _parse_datetime(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parse_date(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OverflowError):
        logger.debug("Failed to parse date: %s", s)
        return None


def _strip_tags(s: str) -> str:
    return _HTML_TAG_RE.sub(" ", s or "")


def _collapse_ws(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


def _absolutize(href: str, base_url: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith(("http://", "https://", "mailto:")):
        return href
    if not base_url:
        return href
    try:
        from urllib.parse import urljoin

        return urljoin(base_url, href)
    except Exception:  # noqa: BLE001
        return href


_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _resolve_path(data: Any, path: str) -> Any:
    """Tiny JSON-pointer-ish resolver for ``a.b[0].c`` style paths."""
    cur: Any = data
    for match in _PATH_TOKEN_RE.finditer(path):
        key, idx = match.group(1), match.group(2)
        if idx is not None:
            cur = cur[int(idx)]
        else:
            cur = cur[key]
    return cur


# ---------------------------------------------------------------------------
# ToolSpec registration
# ---------------------------------------------------------------------------


def _spec_parse_rss() -> Any:
    from contents_hub.tools.base import ToolSpec

    return ToolSpec(
        name="parse_rss",
        description=(
            "Parse RSS 2.0, RSS 1.0 (RDF), or Atom 1.0 XML and return the "
            "feed metadata plus an items[] list as a JSON string. Recognises "
            "YouTube's yt:videoId extension."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "xml": {
                    "type": "string",
                    "description": "Raw XML body of the feed.",
                },
                "feed_url": {
                    "type": "string",
                    "description": "Source URL (echoed back for context).",
                    "default": "",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum number of items to return.",
                    "default": DEFAULT_MAX_ITEMS,
                },
            },
            "required": ["xml"],
        },
        handler=parse_rss,
    )


def _spec_parse_html() -> Any:
    from contents_hub.tools.base import ToolSpec

    return ToolSpec(
        name="parse_html",
        description=(
            "Best-effort extraction of title, meta tags, anchor links, and "
            "visible text from an HTML document. Use when chromux is not "
            "needed and the static HTML is sufficient."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "Raw HTML body."},
                "base_url": {
                    "type": "string",
                    "description": (
                        "Base URL used to resolve relative anchor hrefs."
                    ),
                    "default": "",
                },
                "max_links": {
                    "type": "integer",
                    "description": "Cap on number of anchors returned.",
                    "default": 200,
                },
            },
            "required": ["html"],
        },
        handler=parse_html,
    )


def _spec_parse_json() -> Any:
    from contents_hub.tools.base import ToolSpec

    return ToolSpec(
        name="parse_json",
        description=(
            "Parse a JSON body string and optionally extract a sub-tree by "
            "a simple dotted/bracketed path like 'data.items[0].title'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "Raw JSON text to parse.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Optional dotted/bracketed path, e.g. "
                        "'data.items[0].title'. Empty string returns the "
                        "full parsed value."
                    ),
                    "default": "",
                },
            },
            "required": ["body"],
        },
        handler=parse_json,
    )


def get_specs() -> list[Any]:
    """Return the three ToolSpec instances exposed by this module."""
    return [_spec_parse_rss(), _spec_parse_html(), _spec_parse_json()]


def _register_default() -> None:
    """Overwrite the default registry's placeholder specs with the rich
    ones declared above. See learnings.json T3 round 2."""
    try:
        from contents_hub.tools.registry import get_default_registry

        registry = get_default_registry()
        for spec in get_specs():
            registry.register(spec)
    except Exception:  # noqa: BLE001
        logger.debug("parse: deferred default-registry registration")


_register_default()


__all__ = [
    "parse_rss",
    "parse_html",
    "parse_json",
    "get_specs",
]

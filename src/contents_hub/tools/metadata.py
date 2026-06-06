"""Metadata extraction ToolSpec handler.

``extract_metadata`` distills a small set of canonical fields (title, summary,
author, published_at, tags) from an arbitrary input — whether that input is
already-parsed JSON (e.g. the output of ``parse_html`` / ``parse_rss``) or
raw HTML / XML / JSON. Logic mirrors the cross-source normalisation rules
previously embedded inside ``fetchers/rss.py`` (``_feed_item_to_fetched``)
and ``fetchers/youtube.py`` (``_video_to_fetched_item``).

The handler signature follows the ToolSpec contract:
``Callable[..., Awaitable[str]]`` — async, returns a JSON string.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from dateutil.parser import parse as parse_date

logger = logging.getLogger(__name__)


# Open Graph / Twitter / standard meta names the executor cares about.
_TITLE_KEYS = ("og:title", "twitter:title", "title")
_DESC_KEYS = ("og:description", "twitter:description", "description")
_AUTHOR_KEYS = ("author", "article:author", "twitter:creator", "dc.creator")
_PUBLISHED_KEYS = (
    "article:published_time",
    "datePublished",
    "publish_date",
    "pubdate",
    "date",
)
_TAGS_KEYS = ("article:tag", "keywords", "news_keywords")

_TITLE_TAG_RE = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_META_RE = re.compile(
    r"""<meta\s+[^>]*?(?:name|property)\s*=\s*['"]([^'"]+)['"][^>]*?content\s*=\s*['"]([^'"]*)['"][^>]*?/?>""",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def extract_metadata(
    source: str | dict | list,
    *,
    source_type: str = "auto",
    url: str = "",
) -> str:
    """Extract canonical metadata fields from arbitrary content.

    Args:
        source: Either a parsed object (dict / list — typically the output of
            ``parse_html`` or ``parse_rss``), an HTML string, an XML string,
            or a JSON string. Auto-detected unless ``source_type`` is given.
        source_type: One of ``"auto"``, ``"html"``, ``"json"``, ``"parsed"``.
        url: Optional canonical URL the metadata refers to.

    Returns a JSON string of shape::

        {
          "ok":            bool,
          "url":           str,
          "title":         str,
          "summary":       str,
          "author":        str,
          "published_at":  str | null,   # ISO 8601 or null
          "tags":          [str, ...],
          "extra":         { ... },      # raw meta tags or feed-specific bits
          "error":         str
        }
    """
    try:
        data = _coerce(source, source_type)
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {
                "ok": False,
                "url": url,
                "title": "",
                "summary": "",
                "author": "",
                "published_at": None,
                "tags": [],
                "extra": {},
                "error": f"Could not coerce source: {exc}",
            }
        )

    title = ""
    summary = ""
    author = ""
    published_at: datetime | None = None
    tags: list[str] = []
    extra: dict[str, Any] = {}

    if isinstance(data, dict):
        # parse_html-style output (has "meta" + "title")
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if meta:
            title = data.get("title", "") or _first_meta(meta, _TITLE_KEYS)
            summary = _first_meta(meta, _DESC_KEYS)
            author = _first_meta(meta, _AUTHOR_KEYS)
            published_at = _parse_dt(_first_meta(meta, _PUBLISHED_KEYS))
            tags = _split_tags(_first_meta(meta, _TAGS_KEYS))
            extra = {"meta": meta}
        else:
            # parse_rss-style item or feed-result
            title = data.get("title", "") or data.get("feed_title", "")
            summary = data.get("summary", "")
            author = data.get("author", "")
            published_at = _parse_dt(data.get("published_at"))
            raw_tags = data.get("tags") or []
            tags = [t for t in raw_tags if isinstance(t, str) and t]
            extra = {
                k: v
                for k, v in data.items()
                if k
                not in {
                    "title",
                    "summary",
                    "author",
                    "published_at",
                    "tags",
                    "url",
                    "ok",
                    "error",
                }
            }
            if not url:
                url = data.get("url", "") or ""

    elif isinstance(data, list):
        return json.dumps(
            {
                "ok": False,
                "url": url,
                "title": "",
                "summary": "",
                "author": "",
                "published_at": None,
                "tags": [],
                "extra": {},
                "error": "Cannot extract metadata from a bare list",
            }
        )
    else:
        return json.dumps(
            {
                "ok": False,
                "url": url,
                "title": "",
                "summary": "",
                "author": "",
                "published_at": None,
                "tags": [],
                "extra": {},
                "error": f"Unsupported source type: {type(data).__name__}",
            }
        )

    return json.dumps(
        {
            "ok": True,
            "url": url,
            "title": _clean(title),
            "summary": _clean(summary),
            "author": _clean(author),
            "published_at": published_at.isoformat() if published_at else None,
            "tags": tags,
            "extra": extra,
            "error": "",
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce(source: Any, source_type: str) -> Any:
    if source_type == "parsed" or isinstance(source, (dict, list)):
        return source

    if not isinstance(source, str):
        raise TypeError(
            f"source must be str|dict|list, got {type(source).__name__}"
        )

    if source_type == "json":
        return json.loads(source)

    if source_type == "html":
        return _html_to_dict(source)

    # auto-detect
    stripped = source.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(source)
        except json.JSONDecodeError:
            pass  # fall through to HTML
    return _html_to_dict(source)


def _html_to_dict(html: str) -> dict[str, Any]:
    title_match = _TITLE_TAG_RE.search(html)
    title = _clean(_strip_tags(title_match.group(1))) if title_match else ""
    meta: dict[str, str] = {}
    for name, content in _META_RE.findall(html):
        if name and name not in meta:
            meta[name] = content
    return {"title": title, "meta": meta}


def _first_meta(meta: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        # case-insensitive lookup
        for candidate, value in meta.items():
            if candidate.lower() == key.lower() and value:
                return value
    return ""


def _split_tags(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[;,]", raw)
    return [p.strip() for p in parts if p.strip()]


def _parse_dt(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s
    if not isinstance(s, str):
        return None
    try:
        dt = parse_date(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OverflowError):
        logger.debug("extract_metadata: failed to parse date %r", s)
        return None


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub(" ", s or "")


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


# ---------------------------------------------------------------------------
# ToolSpec registration
# ---------------------------------------------------------------------------


def _build_spec() -> Any:
    from contents_hub.tools.base import ToolSpec

    return ToolSpec(
        name="extract_metadata",
        description=(
            "Extract canonical metadata (title, summary, author, "
            "published_at, tags) from raw HTML, raw JSON, or already-parsed "
            "output of parse_html / parse_rss. Returns a JSON string."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "description": (
                        "Raw HTML / JSON string, or an already-parsed dict "
                        "(e.g. the output of parse_html or one item from "
                        "parse_rss.items[])."
                    ),
                },
                "source_type": {
                    "type": "string",
                    "description": (
                        "Hint about how to interpret ``source``: 'auto', "
                        "'html', 'json', or 'parsed'."
                    ),
                    "default": "auto",
                    "enum": ["auto", "html", "json", "parsed"],
                },
                "url": {
                    "type": "string",
                    "description": "Canonical URL the metadata refers to.",
                    "default": "",
                },
            },
            "required": ["source"],
        },
        handler=extract_metadata,
    )


def get_spec() -> Any:
    """Return the ToolSpec for ``extract_metadata``."""
    return _build_spec()


def _register_default() -> None:
    """Overwrite the default registry's placeholder spec with the rich one.
    See learnings.json T3 round 2."""
    try:
        from contents_hub.tools.registry import get_default_registry

        get_default_registry().register(_build_spec())
    except Exception:  # noqa: BLE001
        logger.debug("extract_metadata: deferred default-registry registration")


_register_default()


__all__ = ["extract_metadata", "get_spec"]

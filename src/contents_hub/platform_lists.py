"""Deterministic LIST extractors for browser-backed social platforms.

These helpers keep platform-specific DOM parsing out of ``executor.py`` while
still using the same Chromux tool handlers as the agent path. They only return
identity candidates; detail/content extraction remains in the executor recipe
pipeline.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import subprocess
from typing import Any
from urllib.parse import quote, urlparse

from contents_hub.chromux import resolve_chromux_profile
from contents_hub.models import ListItem
from contents_hub.tools.browser import (
    _run_chromux,
    chromux_extract_handler,
    chromux_navigate_handler,
)

logger = logging.getLogger(__name__)

_LINKEDIN_ACTIVITY_URN_RE = re.compile(r"^urn:li:activity:(\d+)$")
_STATUS_HREF_RE = re.compile(r"""href=["']([^"']*/status/\d+[^"']*)["']""")
_TIME_DATETIME_RE = re.compile(r"""<time\b[^>]*\bdatetime=["']([^"']+)["']""")
_STATUS_PATH_RE = re.compile(r"^/([^/?#]+)/status/(\d+)(?:[/?#].*)?$")
_WEB_STATUS_PATH_RE = re.compile(r"^/i/web/status/(\d+)(?:[/?#].*)?$")

_X_SOURCE_TYPES = {"x", "x.profile", "twitter", "twitter.profile"}
_LINKEDIN_SOURCE_TYPES = {"linkedin", "linkedin.profile"}
_CHROMUX_EVAL_TIMEOUT_SECONDS = 60.0
_X_RECOVERY_SCROLLS = 2
_CHROMUX_TRANSIENT_ERROR_MARKERS = (
    "Failed to acquire lock",
)


class PlatformListTransientError(RuntimeError):
    """Transient Chromux/runtime failure while using a deterministic LIST path."""


def _raise_if_transient_chromux_error(error: object) -> None:
    message = str(error or "")
    if any(marker in message for marker in _CHROMUX_TRANSIENT_ERROR_MARKERS):
        raise PlatformListTransientError(message)


def is_x_profile_source(source_type: str, url: str) -> bool:
    if source_type in _X_SOURCE_TYPES:
        return True
    host = urlparse(url).netloc.lower()
    return host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}


def is_linkedin_profile_source(source_type: str, url: str) -> bool:
    if source_type in _LINKEDIN_SOURCE_TYPES:
        return True
    host = urlparse(url).netloc.lower()
    return host.endswith("linkedin.com") and "/in/" in urlparse(url).path


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _linkedin_activity_url(urn: str) -> str | None:
    urn = urn.strip()
    if not _LINKEDIN_ACTIVITY_URN_RE.match(urn):
        return None
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _linkedin_activity_feed_url(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.linkedin.com"
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "in":
        return f"{scheme}://{netloc}/in/{parts[1]}/recent-activity/all/"
    return profile_url


def linkedin_list_items_from_records(records: list[dict[str, Any]]) -> list[ListItem]:
    items: list[ListItem] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        urn = str(record.get("data-urn") or "").strip()
        url = _linkedin_activity_url(urn)
        if not url or url in seen:
            continue
        seen.add(url)
        text = str(record.get("text") or "").strip()
        items.append(
            ListItem(
                item_key=f"linkedin:activity:{urn.removeprefix('urn:li:activity:')}",
                url=url,
                card_text=text,
                source_payload={"urn": urn},
            )
        )
    return items


def _x_profile_handle(profile_url: str) -> str:
    parts = [part for part in urlparse(profile_url).path.split("/") if part]
    if parts:
        return parts[0].lstrip("@")
    return ""


def _x_profile_url(profile_url: str) -> str:
    handle = _x_profile_handle(profile_url)
    return f"https://x.com/{handle}" if handle else profile_url


def _x_search_url(handle: str) -> str:
    query = quote(f"from:{handle} -filter:replies")
    return f"https://x.com/search?q={query}&src=typed_query&f=live"


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _is_x_pinned(lines: list[str]) -> bool:
    if not lines:
        return False
    first = lines[0].casefold()
    return first in {"pinned", "고정", "고정된 게시물"} or "pinned post" in first


def _is_x_repost(lines: list[str]) -> bool:
    header = "\n".join(lines[:6]).casefold()
    repost_markers = (
        " reposted",
        "reposted by",
        "reposted this",
        "재게시",
        "리포스트",
    )
    return any(marker in header for marker in repost_markers)


def _is_x_promoted(lines: list[str]) -> bool:
    header = {line.casefold() for line in lines[:8]}
    return "promoted" in header or "ad" in header


def _normalize_x_status_url(
    raw_href: str,
    handle: str,
    *,
    allow_other_handles: bool = False,
) -> tuple[str, str, str] | None:
    raw_href = html.unescape(raw_href).strip()
    parsed = urlparse(raw_href)
    path = parsed.path if parsed.scheme else urlparse(f"https://x.com{raw_href}").path

    match = _STATUS_PATH_RE.match(path)
    if match:
        href_handle, status_id = match.groups()
        if (
            handle
            and href_handle.casefold() != handle.casefold()
            and not allow_other_handles
        ):
            return None
        return f"https://x.com/{href_handle}/status/{status_id}", status_id, href_handle

    match = _WEB_STATUS_PATH_RE.match(path)
    if match and handle:
        status_id = match.group(1)
        return f"https://x.com/{handle}/status/{status_id}", status_id, handle

    return None


def _x_status_candidates(
    article_html: str,
    handle: str,
    *,
    allow_other_handles: bool = False,
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for match in _STATUS_HREF_RE.finditer(article_html):
        normalized = _normalize_x_status_url(
            match.group(1),
            handle,
            allow_other_handles=allow_other_handles,
        )
        if normalized is None:
            continue
        url, status_id, status_author = normalized
        if status_id in seen:
            continue
        seen.add(status_id)
        candidates.append((url, status_id, status_author))
    return candidates


def x_list_items_from_article_records(
    records: list[dict[str, Any]],
    *,
    profile_url: str,
) -> list[ListItem]:
    handle = _x_profile_handle(profile_url)
    items: list[ListItem] = []
    seen: set[str] = set()

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        text = str(record.get("text") or "").strip()
        article_html = str(record.get("outerHTML") or "")
        lines = _non_empty_lines(text)
        is_repost = _is_x_repost(lines)
        if _is_x_pinned(lines) or _is_x_promoted(lines):
            continue

        candidates = _x_status_candidates(
            article_html,
            handle,
            allow_other_handles=is_repost,
        )
        if not candidates:
            continue
        url, status_id, status_author = candidates[0]
        if url in seen:
            continue
        seen.add(url)

        datetime_match = _TIME_DATETIME_RE.search(article_html)
        published_hint = html.unescape(datetime_match.group(1)) if datetime_match else ""
        title_hint = " ".join(lines[:4])[:120]
        items.append(
            ListItem(
                item_key=f"x:status:{status_id}",
                url=url,
                title_hint=title_hint,
                published_hint=published_hint,
                card_text=text,
                source_payload={
                    "handle": handle,
                    "dom_index": index,
                    "is_repost": is_repost,
                    "reposted_by": handle if is_repost else "",
                    "status_author": status_author,
                },
            )
        )

    return items


async def _chromux_run_js(session_id: str, js: str) -> bool:
    env = {**os.environ, "CHROMUX_PROFILE": resolve_chromux_profile()}
    try:
        proc = await asyncio.to_thread(
            _run_chromux,
            ["chromux", "run", session_id, f"return await js({json.dumps(js)});"],
            env=env,
            timeout=_CHROMUX_EVAL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


async def _scroll_x_timeline(session_id: str) -> bool:
    ok = await _chromux_run_js(
        session_id,
        "window.scrollBy(0, Math.max(900, Math.floor(window.innerHeight * 0.9))); true",
    )
    if ok:
        await asyncio.sleep(2)
    return ok


async def _extract_x_article_records(
    session_id: str,
    *,
    max_items: int,
) -> list[dict[str, Any]] | None:
    extracted = _parse_json_object(
        await chromux_extract_handler(
            session_id=session_id,
            selector='article[data-testid="tweet"]',
            attributes=["text", "outerHTML"],
            multiple=True,
            limit=max_items + 12,
        )
    )
    if not extracted.get("ok"):
        _raise_if_transient_chromux_error(extracted.get("error"))
        logger.info(
            "platform LIST x extract failed session=%s error=%s",
            session_id,
            extracted.get("error"),
        )
        return None

    records = extracted.get("items")
    return records if isinstance(records, list) else None


async def _x_items_from_url(
    url: str,
    *,
    profile_url: str,
    max_items: int,
    recovery_scrolls: int,
) -> tuple[list[ListItem] | None, bool, int]:
    nav = _parse_json_object(
        await chromux_navigate_handler(url=url, wait_ms=5000)
    )
    if not nav.get("ok"):
        _raise_if_transient_chromux_error(nav.get("error"))
        logger.info(
            "platform LIST x navigate failed url=%s error=%s",
            url,
            nav.get("error"),
        )
        return None, False, 0

    session_id = str(nav.get("session_id") or "")
    saw_records = False
    last_count = 0
    items: list[ListItem] = []
    for attempt in range(recovery_scrolls + 1):
        records = await _extract_x_article_records(
            session_id,
            max_items=max_items,
        )
        if records is None:
            return None, saw_records, last_count
        last_count = len(records)
        if records:
            saw_records = True
            items = x_list_items_from_article_records(records, profile_url=profile_url)
            if items:
                break
        if attempt >= recovery_scrolls:
            break
        if not await _scroll_x_timeline(session_id):
            break

    return items[:max_items], saw_records, last_count


async def list_linkedin_profile_items(
    profile_url: str,
    *,
    max_items: int,
) -> list[ListItem] | None:
    feed_url = _linkedin_activity_feed_url(profile_url)
    nav = _parse_json_object(
        await chromux_navigate_handler(url=feed_url, wait_ms=5000)
    )
    if not nav.get("ok"):
        _raise_if_transient_chromux_error(nav.get("error"))
        logger.info(
            "platform LIST linkedin navigate failed url=%s error=%s",
            profile_url,
            nav.get("error"),
        )
        return None

    session_id = str(nav.get("session_id") or "")
    extracted = _parse_json_object(
        await chromux_extract_handler(
            session_id=session_id,
            selector="[data-urn]",
            attributes=["data-urn", "text"],
            multiple=True,
            limit=max_items + 10,
        )
    )
    if not extracted.get("ok"):
        _raise_if_transient_chromux_error(extracted.get("error"))
        logger.info(
            "platform LIST linkedin extract failed url=%s error=%s",
            profile_url,
            extracted.get("error"),
        )
        return None

    records = extracted.get("items")
    if not isinstance(records, list):
        return None
    items = linkedin_list_items_from_records(records)[:max_items]
    if not items:
        return None
    logger.info("platform LIST linkedin url=%s items=%d", profile_url, len(items))
    return items


async def list_x_profile_items(
    profile_url: str,
    *,
    max_items: int,
) -> list[ListItem] | None:
    normalized_url = _x_profile_url(profile_url)
    items, saw_records, article_count = await _x_items_from_url(
        normalized_url,
        profile_url=normalized_url,
        max_items=max_items,
        recovery_scrolls=_X_RECOVERY_SCROLLS,
    )
    if items is None:
        return None

    handle = _x_profile_handle(normalized_url)
    if not items and saw_records and handle:
        search_items, search_saw_records, search_article_count = await _x_items_from_url(
            _x_search_url(handle),
            profile_url=normalized_url,
            max_items=max_items,
            recovery_scrolls=0,
        )
        if search_items is None:
            return None
        if search_items:
            items = search_items
            article_count = search_article_count
        elif search_saw_records:
            article_count = max(article_count, search_article_count)

    if not saw_records:
        return None
    logger.info(
        "platform LIST x url=%s articles=%d items=%d",
        profile_url,
        article_count,
        len(items),
    )
    return items[:max_items]

"""Neutral data models shared across fetchers, tools, executor, and api.

Relocated from ``fetchers/base.py`` (FetchedItem, FetchResult) and
``fetchers/failure.py`` (FetchFailureReason, infer_from_error) per the
single-executor refactor (OD-4). Field shapes are frozen by contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class ListItem:
    item_key: str
    url: str
    title_hint: str = ""
    published_hint: str = ""
    card_text: str = ""
    source_payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ListFetchResult:
    ok: bool
    items: list[ListItem] = field(default_factory=list)
    source_url: str = ""
    total_available: int = 0
    error: str = ""
    failure_reason: str = ""


@dataclass(frozen=True)
class FullItem:
    item_key: str
    url: str
    title: str
    author: str = ""
    published_at: Optional[datetime] = None
    body_markdown: str = ""
    body_status: str = ""
    assets: list[dict] = field(default_factory=list)
    source_payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedItem:
    url: str
    title: str
    summary: str = ""
    author: str = ""
    published_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    content_html: str = ""
    source_type: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    items: list[FetchedItem] = field(default_factory=list)
    source_title: str = ""
    source_url: str = ""
    total_available: int = 0
    error: str = ""
    error_type: str = ""
    failure_reason: str = ""


class FetchFailureReason(str, Enum):
    LOGIN_REQUIRED = "login_required"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    STRUCTURE_CHANGED = "structure_changed"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> "FetchFailureReason | None":
        if not raw:
            return None
        key = str(raw).strip().lower()
        for member in cls:
            if member.value == key:
                return member
        return None


_LOGIN_HINTS = (
    "login required",
    "not authenticated",
    "/login",
    "/uas/login",
    "sign in",
    "로그인",
    "401",
)
_TIMEOUT_HINTS = ("timed out", "timeout", "deadline exceeded")
_NETWORK_HINTS = ("dns", "connection refused", "connection reset", "ssl", "tls")
_NOT_FOUND_HINTS = ("404", "not found", "does not exist")
_BLOCKED_HINTS = ("captcha", "rate limit", "429", "403", "forbidden", "blocked")
_STRUCTURE_HINTS = (
    "selector",
    "missing element",
    "structure changed",
    "no items found",
)


def infer_from_error(error: str | None) -> FetchFailureReason:
    if not error:
        return FetchFailureReason.UNKNOWN
    low = error.lower()
    if any(h in low for h in _LOGIN_HINTS):
        return FetchFailureReason.LOGIN_REQUIRED
    if any(h in low for h in _TIMEOUT_HINTS):
        return FetchFailureReason.TIMEOUT
    if any(h in low for h in _STRUCTURE_HINTS):
        return FetchFailureReason.STRUCTURE_CHANGED
    if any(h in low for h in _NOT_FOUND_HINTS):
        return FetchFailureReason.NOT_FOUND
    if any(h in low for h in _BLOCKED_HINTS):
        return FetchFailureReason.BLOCKED
    if any(h in low for h in _NETWORK_HINTS):
        return FetchFailureReason.NETWORK
    return FetchFailureReason.UNKNOWN


__all__ = [
    "FetchedItem",
    "FullItem",
    "ListFetchResult",
    "ListItem",
    "FetchResult",
    "FetchFailureReason",
    "infer_from_error",
]

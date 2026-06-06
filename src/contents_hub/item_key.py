"""Canonical identity for raw_items.

The `raw_items.url` column is a string key that must be stable for the
*same logical item* across re-fetches. For real URLs we strip tracking
params and other non-canonical noise. For items without a usable URL
(synthesized summaries, emailed newsletters, etc.) we fall back to a
content hash prefixed with `content://` so the column stays dense and
the UNIQUE(subscription_id, url) constraint still dedups them.
"""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query-string keys that are always tracking noise; strip before use.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "utm_id", "utm_name", "utm_brand",
        "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid",
        "mc_cid", "mc_eid",
        "igshid", "ref", "ref_src", "ref_url",
        "source", "spm", "yclid", "_hsmi", "_hsenc",
    }
)


def normalize_url(url: str) -> str:
    """Return a canonical form of `url`, suitable as a dedup key.

    - Lowercases the host.
    - Drops the fragment.
    - Strips known tracking query params (utm_*, fbclid, gclid, ...).
    - Removes trailing `/` from non-root paths.

    Unusable input (empty, non-HTTP) is returned unchanged.
    """
    if not url:
        return url
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return url.strip()

    host = (parsed.hostname or "").lower()
    # Preserve port if present.
    if parsed.port:
        host = f"{host}:{parsed.port}"

    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    kept_pairs = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(kept_pairs, doseq=True)

    return urlunparse((parsed.scheme, host, path, parsed.params, query, ""))


def item_key(item: Any, subscription_id: int | str | None) -> str:
    """Return the canonical key for a fetched item.

    If the item has a usable URL, returns `normalize_url(url)`. Otherwise
    returns `content://{subscription_id}/{hash}` where hash is the first
    16 hex chars of sha256(title|body|published_at).

    `item` may be a dict (trial-result samples) or a FetchedItem dataclass.
    """
    if isinstance(item, dict):
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        body = (item.get("body") or item.get("content_html") or "").strip()
        published_at = str(item.get("published_at") or "")
    else:
        url = (getattr(item, "url", "") or "").strip()
        title = (getattr(item, "title", "") or "").strip()
        # FetchedItem stores body in content_html
        body = (
            getattr(item, "body", None)
            or getattr(item, "content_html", "")
            or ""
        ).strip()
        pa = getattr(item, "published_at", None)
        published_at = pa.isoformat() if pa is not None and hasattr(pa, "isoformat") else str(pa or "")

    if url:
        return normalize_url(url)

    sub = subscription_id if subscription_id is not None else "_"
    digest = hashlib.sha256(
        f"{title}|{body}|{published_at}".encode("utf-8")
    ).hexdigest()[:16]
    return f"content://{sub}/{digest}"


__all__ = ["normalize_url", "item_key"]

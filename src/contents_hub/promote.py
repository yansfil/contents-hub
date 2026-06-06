"""Promote a raw_item into an immutable source file under vault/sources/.

Replaces the removed classify/promote pipeline with the smallest possible
implementation: read the raw_item from SQLite, write a Markdown file with
Obsidian frontmatter, flip the row's status to `promoted`, return the path.

No lenses, no tagging, no content-fetching — whatever is stored in
`raw_items.content_summary` is the body. For browser-fetched items the
summary already contains the scraped content; for RSS/YouTube it's the
feed summary.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from contents_hub.config import WikiConfig
from contents_hub.db import get_db
from contents_hub.frontmatter import Frontmatter, assemble_markdown

logger = logging.getLogger(__name__)


class PromoteError(RuntimeError):
    """Raised when a raw_item cannot be promoted."""


def _slugify(title: str, *, max_len: int = 60) -> str:
    """Produce a filesystem-safe slug from a title."""
    s = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    s = s.strip("-")
    return s[:max_len] or "untitled"


def _short_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def source_filename(title: str, url: str, collected_at: str) -> str:
    """Deterministic source note filename: ``{YYYYMMDD}-{slug}-{hash}.md``.

    Public so Lens Inbox can derive the relative path text for promoted
    candidates without persisting it (R-T2.5).
    """
    try:
        dt = datetime.fromisoformat(collected_at)
    except ValueError:
        dt = datetime.now(timezone.utc)
    return f"{dt.strftime('%Y%m%d')}-{_slugify(title or url)}-{_short_hash(url)}.md"


def _build_frontmatter(row: dict, sub_row: dict | None) -> Frontmatter:
    source_type = (sub_row or {}).get("source_type") or "webpage"
    return Frontmatter(
        source_type=source_type,
        url=row["url"],
        title=row["title"] or row["url"],
        collected_at=row["collected_at"],
        status="pending",
        extra={"origin": row.get("origin") or "subscription"},
    )


def _promote_with_conn(
    config: WikiConfig, raw_item_id: int, conn: sqlite3.Connection
) -> Path:
    row = conn.execute(
        "SELECT * FROM raw_items WHERE id = ?", (raw_item_id,)
    ).fetchone()
    if row is None:
        raise PromoteError(f"raw_item {raw_item_id} not found")
    row = dict(row)

    if row["status"] == "promoted":
        raise PromoteError(f"raw_item {raw_item_id} already promoted")

    sub_row = None
    if row.get("subscription_id"):
        sr = conn.execute(
            "SELECT source_type FROM subscriptions WHERE id = ?",
            (row["subscription_id"],),
        ).fetchone()
        if sr is not None:
            sub_row = dict(sr)

    fm = _build_frontmatter(row, sub_row)
    body_md = (row.get("content_summary") or "").strip()
    title = row["title"] or row["url"]

    sources_dir = config.sources_path
    sources_dir.mkdir(parents=True, exist_ok=True)
    filename = source_filename(title, row["url"], row["collected_at"])
    path = sources_dir / filename

    body = f"# {title}\n\n"
    if body_md:
        body += f"> {body_md}\n\n"
    body += f"Source: {row['url']}\n"

    path.write_text(assemble_markdown(fm.to_dict(), body), encoding="utf-8")

    conn.execute(
        "UPDATE raw_items SET status='promoted', updated_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), raw_item_id),
    )
    conn.commit()
    logger.info("Promoted raw_item %d -> %s", raw_item_id, path)
    return path


def promote_raw_item(
    config: WikiConfig,
    raw_item_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> Path:
    """Promote one raw_item to a vault/sources/*.md file.

    Args:
        config: Wiki configuration.
        raw_item_id: Primary key of the row to promote.
        conn: Optional existing DB connection (for use inside daemon tick
            where a connection is already open). If omitted, opens a new one.

    Returns:
        Absolute path to the written source file.

    Raises:
        PromoteError: raw_item not found or already promoted.
    """
    if conn is not None:
        return _promote_with_conn(config, raw_item_id, conn)
    with get_db(config) as owned_conn:
        return _promote_with_conn(config, raw_item_id, owned_conn)

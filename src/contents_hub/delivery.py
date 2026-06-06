"""Delivery payload generation for channel adapters."""

from __future__ import annotations

import json
from typing import Any

from contents_hub.config import WikiConfig
from contents_hub.db import get_db


def _loads_json_array(raw: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _raw_item_card(row) -> dict[str, Any]:
    return {
        "payload_type": "raw_item",
        "raw_item_id": int(row["id"]),
        "digest_id": None,
        "title": row["title"] or row["url"],
        "url": row["url"],
        "summary": row["content_summary"] or row["body"] or "",
        "source_type": row["source_type"] or "",
        "origin": row["origin"] or "",
        "status": row["status"] or "",
        "collected_at": row["collected_at"],
        "published_at": row["published_at"],
    }


def _digest_card(row) -> dict[str, Any]:
    sections = _loads_json_array(row["sections_json"] or "[]")
    return {
        "payload_type": "digest",
        "raw_item_id": None,
        "digest_id": int(row["id"]),
        "title": row["title"] or f"Digest #{row['id']}",
        "item_count": int(row["item_count"] or 0),
        "created_at": row["created_at"],
        "status": row["status"] or "",
        "summary": (row["content_md"] or "")[:1000],
        "sections": sections,
    }


def pending_delivery_payload(
    config: WikiConfig,
    *,
    payload_type: str = "all",
    limit: int = 20,
) -> dict[str, Any]:
    """Return adapter-ready cards for undelivered raw items and digests."""
    payload_type = (payload_type or "all").strip().lower()
    if payload_type not in {"all", "raw_item", "digest"}:
        raise ValueError("payload_type must be one of: all, raw_item, digest")
    limit = max(1, int(limit or 20))

    cards: list[dict[str, Any]] = []
    with get_db(config) as conn:
        if payload_type in {"all", "raw_item"}:
            rows = conn.execute(
                """
                SELECT ri.*, s.source_type AS source_type
                FROM raw_items ri
                LEFT JOIN subscriptions s ON s.id = ri.subscription_id
                WHERE ri.status = 'raw'
                  AND NOT EXISTS (
                      SELECT 1 FROM outbound_messages om
                      WHERE om.payload_type = 'raw_item'
                        AND om.raw_item_id = ri.id
                  )
                ORDER BY ri.priority DESC, ri.collected_at DESC, ri.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            cards.extend(_raw_item_card(row) for row in rows)

        if payload_type in {"all", "digest"} and len(cards) < limit:
            rows = conn.execute(
                """
                SELECT *
                FROM digests d
                WHERE d.status = 'ok'
                  AND NOT EXISTS (
                      SELECT 1 FROM outbound_messages om
                      WHERE om.payload_type = 'digest'
                        AND om.digest_id = d.id
                  )
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT ?
                """,
                (limit - len(cards),),
            ).fetchall()
            cards.extend(_digest_card(row) for row in rows)

    return {
        "ok": True,
        "payload_type": payload_type,
        "count": len(cards),
        "items": cards,
    }

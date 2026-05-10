from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.lenses import (
    LensMatch,
    classify_items_for_lenses,
    evaluate_post_fetch_lenses,
    insert_lens_matches,
    load_enabled_default_lenses,
    load_subscription_raw_items,
)
from llm_wiki.subscriptions import SubscriptionStore


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    return cfg


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_lens(conn: sqlite3.Connection, lens_id: str, *, keywords=None, enabled=1, name="", description=""):
    now = _now()
    conn.execute(
        """INSERT INTO lenses (id, name, description, keywords, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (lens_id, name, description, json.dumps(keywords or []), enabled, now, now),
    )


def _add_raw_item(conn: sqlite3.Connection, sub_id: int, *, title="", summary="", body="") -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO raw_items
           (url, title, body, origin, priority, status, subscription_id,
            content_summary, collected_at, updated_at)
           VALUES (?, ?, ?, 'subscription', 50, 'raw', ?, ?, ?, ?)""",
        (f"https://example.com/{title or sub_id}-{now}", title, body, sub_id, summary, now, now),
    )
    return int(cur.lastrowid)


def test_load_enabled_default_lenses_preserves_default_scope(vault):
    store = SubscriptionStore(vault)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example",
        source_type="rss.feed",
        lenses=["ai", "disabled", "missing", "product"],
    )

    conn = init_db(vault)
    try:
        _add_lens(conn, "product", keywords=["launch"])
        _add_lens(conn, "disabled", keywords=["secret"], enabled=0)
        _add_lens(conn, "other", keywords=["other"])
        _add_lens(conn, "ai", keywords=["AI"])
        conn.commit()

        lenses = load_enabled_default_lenses(conn, int(sub.id))
    finally:
        conn.close()

    assert [lens.id for lens in lenses] == ["ai", "product"]
    assert lenses[0].keywords == ("AI",)


def test_load_subscription_raw_items_filters_by_subscription_and_ids(vault):
    store = SubscriptionStore(vault)
    sub_a = store.add(url="https://a.example.com/feed.xml", title="A", source_type="rss.feed")
    sub_b = store.add(url="https://b.example.com/feed.xml", title="B", source_type="rss.feed")

    conn = init_db(vault)
    try:
        id_a = _add_raw_item(conn, int(sub_a.id), title="AI item")
        id_b = _add_raw_item(conn, int(sub_b.id), title="Wrong subscription")
        conn.commit()

        rows = load_subscription_raw_items(conn, int(sub_a.id), [id_b, id_a])
    finally:
        conn.close()

    assert [row.id for row in rows] == [id_a]
    assert rows[0].title == "AI item"


def test_classify_uses_keywords_without_runner(monkeypatch):
    from llm_wiki import filter as filter_module
    from llm_wiki.lenses import LensCriteria, RawLensItem

    async def fail_filter_items(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("keyword lenses must not call filter_items")

    monkeypatch.setattr(filter_module, "filter_items", fail_filter_items)

    matches = asyncio.run(
        classify_items_for_lenses(
            [LensCriteria(id="ai", keywords=("machine learning",))],
            [
                RawLensItem(id=1, title="Sports", content_summary="Football"),
                RawLensItem(id=2, title="Research", body="New machine learning paper"),
            ],
        )
    )

    assert matches == [LensMatch(raw_item_id=2, lens_id="ai")]


def test_evaluate_uses_filter_items_outside_write_transaction(vault, monkeypatch):
    store = SubscriptionStore(vault)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example",
        source_type="rss.feed",
        lenses=["criteria"],
    )

    conn = init_db(vault)
    try:
        _add_lens(conn, "criteria", name="AI research", description="AI research only")
        item_id = _add_raw_item(conn, int(sub.id), title="AI paper", summary="research")
        conn.commit()

        from llm_wiki import filter as filter_module

        async def fake_filter_items(prompt, items, **kwargs):
            assert prompt == "AI research only"
            assert conn.in_transaction is False
            assert [item["id"] for item in items] == [item_id]
            return {item_id}

        monkeypatch.setattr(filter_module, "filter_items", fake_filter_items)

        inserted = asyncio.run(evaluate_post_fetch_lenses(vault, int(sub.id), [item_id], conn=conn))
        rows = conn.execute(
            "SELECT raw_item_id, lens_id FROM raw_item_lenses"
        ).fetchall()
    finally:
        conn.close()

    assert inserted == 1
    assert [(row["raw_item_id"], row["lens_id"]) for row in rows] == [(item_id, "criteria")]


def test_insert_lens_matches_rolls_back_partial_write_on_error(vault):
    store = SubscriptionStore(vault)
    sub = store.add(url="https://example.com/feed.xml", title="Example", source_type="rss.feed")

    conn = init_db(vault)
    try:
        _add_lens(conn, "ok", keywords=["ok"])
        item_id = _add_raw_item(conn, int(sub.id), title="OK")
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            insert_lens_matches(
                conn,
                [
                    LensMatch(raw_item_id=item_id, lens_id="ok"),
                    LensMatch(raw_item_id=item_id, lens_id="missing-lens"),
                ],
            )

        rows = conn.execute("SELECT raw_item_id, lens_id FROM raw_item_lenses").fetchall()
    finally:
        conn.close()

    assert rows == []


def test_evaluate_post_fetch_lenses_isolates_failures_and_keeps_raw_rows(vault, monkeypatch):
    store = SubscriptionStore(vault)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example",
        source_type="rss.feed",
        lenses=["criteria"],
    )

    conn = init_db(vault)
    try:
        _add_lens(conn, "criteria", name="Needs runner")
        item_id = _add_raw_item(conn, int(sub.id), title="AI paper")
        conn.commit()

        from llm_wiki import filter as filter_module

        async def broken_filter_items(*args, **kwargs):
            raise RuntimeError("runner unavailable")

        monkeypatch.setattr(filter_module, "filter_items", broken_filter_items)

        inserted = asyncio.run(evaluate_post_fetch_lenses(vault, int(sub.id), [item_id], conn=conn))
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE id = ?", (item_id,)).fetchone()[0]
        lens_count = conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0]
    finally:
        conn.close()

    assert inserted == 0
    assert raw_count == 1
    assert lens_count == 0

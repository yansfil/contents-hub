"""Unit tests for the canonical item_key helper.

item_key is the string we write into raw_items.url — it must be stable
per logical item, dedup-friendly, and fall back to a content hash when
a real URL isn't available.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from contents_hub.config import WikiConfig
from contents_hub.db import init_db
from contents_hub.item_key import item_key, normalize_url


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # utm_* and fragment stripped
        (
            "https://example.com/post?utm_source=email&utm_medium=news#top",
            "https://example.com/post",
        ),
        # fbclid stripped, real param kept
        (
            "https://example.com/p?fbclid=xyz&id=42",
            "https://example.com/p?id=42",
        ),
        # trailing slash trimmed on non-root
        ("https://example.com/path/", "https://example.com/path"),
        # root slash preserved
        ("https://example.com/", "https://example.com/"),
        # host lowercased
        ("https://Example.COM/Path", "https://example.com/Path"),
        # no-op on clean URL
        ("https://a.example.com/b?c=1", "https://a.example.com/b?c=1"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_url_passes_through_non_http():
    # Non-HTTP inputs are returned unchanged (after strip).
    assert normalize_url("content://sub/abc123") == "content://sub/abc123"
    assert normalize_url("") == ""


# ---------------------------------------------------------------------------
# item_key fallback behavior
# ---------------------------------------------------------------------------


def test_item_key_uses_normalized_url_when_present():
    item = {"url": "https://ex.com/a?utm_source=x", "title": "T"}
    assert item_key(item, 7) == "https://ex.com/a"


def test_item_key_falls_back_to_content_hash():
    item = {"url": "", "title": "Hello", "body": "world", "published_at": "2026-04-17"}
    k = item_key(item, 42)
    assert k.startswith("content://42/")
    assert len(k.split("/")[-1]) == 16  # sha256 prefix


def test_item_key_content_hash_is_deterministic():
    item = {"title": "T", "body": "B", "published_at": "2026-04-17"}
    assert item_key(item, 1) == item_key(item, 1)


def test_item_key_content_hash_differs_per_content():
    a = item_key({"title": "A", "body": "x"}, 1)
    b = item_key({"title": "B", "body": "x"}, 1)
    assert a != b


# ---------------------------------------------------------------------------
# DB UNIQUE(subscription_id, url) enforcement
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".contents-hub").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


def test_raw_items_unique_constraint(vault):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(vault.meta_path / "state.db") as conn:
        # Create a dummy subscription to satisfy FK.
        conn.execute(
            """INSERT INTO subscriptions
               (url, title, source_type, status, schedule_interval_minutes,
                default_lens_ids, config, created_at, updated_at)
               VALUES ('https://s.test/', 'S', 'webpage', 'active', 60, '[]', '{}', ?, ?)""",
            (now, now),
        )
        sub_id = conn.execute("SELECT id FROM subscriptions").fetchone()[0]

        # First insert: OK.
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, '', ?, ?, ?)",
            ("https://s.test/a", "A", sub_id, now, now),
        )
        assert cur.rowcount == 1

        # Duplicate (same sub_id + url): IGNORE → rowcount == 0, no error.
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, '', ?, ?, ?)",
            ("https://s.test/a", "A dup", sub_id, now, now),
        )
        assert cur.rowcount == 0

        # Same URL under a different sub: allowed (scoped by sub_id).
        conn.execute(
            """INSERT INTO subscriptions
               (url, title, source_type, status, schedule_interval_minutes,
                default_lens_ids, config, created_at, updated_at)
               VALUES ('https://s2.test/', 'S2', 'webpage', 'active', 60, '[]', '{}', ?, ?)""",
            (now, now),
        )
        sub2 = conn.execute(
            "SELECT id FROM subscriptions WHERE url = 'https://s2.test/'"
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, '', ?, ?, ?)",
            ("https://s.test/a", "A in sub2", sub2, now, now),
        )
        assert cur.rowcount == 1

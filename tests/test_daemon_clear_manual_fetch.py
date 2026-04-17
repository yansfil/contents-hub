"""Regression: _update_subscription_success clears a stale
`config.last_manual_fetch` so a previous manual fetch error does not
continue to render a red banner on the detail page after a later fetch
(manual or daemon) succeeds.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.daemon import _update_subscription_success
from llm_wiki.db import init_db


@pytest.fixture
def tmp_vault_config(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


def _seed_sub(cfg, *, config_json: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """INSERT INTO subscriptions
                 (url, title, source_type, status,
                  schedule_interval_minutes,
                  default_lens_ids, config,
                  created_at, updated_at)
               VALUES (?, ?, ?, 'active', 30, '[]', ?, ?, ?)""",
            ("https://example.com/", "Ex", "webpage", config_json, now, now),
        )
        conn.commit()
        return cur.lastrowid


def test_success_clears_last_manual_fetch(tmp_vault_config):
    cfg = tmp_vault_config
    stale = json.dumps({
        "last_manual_fetch": {
            "ok": False,
            "error": "execute agent timed out",
            "finished_at": "2026-04-12T08:25:56+00:00",
        },
        "recipe_base": "webpage",
        "filter_prompt": "keep",
    })
    sub_id = _seed_sub(cfg, config_json=stale)

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.row_factory = sqlite3.Row
        _update_subscription_success(conn, sub_id, new_items=3, interval_minutes=30)
        conn.commit()
        row = conn.execute(
            "SELECT config FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()

    new_cfg = json.loads(row["config"])
    assert "last_manual_fetch" not in new_cfg
    # Other config keys are untouched
    assert new_cfg["recipe_base"] == "webpage"
    assert new_cfg["filter_prompt"] == "keep"


def test_success_is_noop_when_no_last_manual_fetch(tmp_vault_config):
    """Ensure json_remove on a missing key doesn't blow up."""
    cfg = tmp_vault_config
    clean = json.dumps({"filter_prompt": "stay"})
    sub_id = _seed_sub(cfg, config_json=clean)

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.row_factory = sqlite3.Row
        _update_subscription_success(conn, sub_id, new_items=0, interval_minutes=30)
        conn.commit()
        row = conn.execute(
            "SELECT config FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()

    assert json.loads(row["config"]) == {"filter_prompt": "stay"}

"""After Keep (or Fetch Now), raw_items get the same filter+promote pass
as the daemon tick. Stubs the runner so we never hit the real agent.

Failing this test means one of the 3 ingest paths skipped the filter hook,
which is exactly the drift we want to catch.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from contents_hub.config import WikiConfig
from contents_hub.db import init_db
from contents_hub.subscriptions import SubscriptionStatus, SubscriptionStore
from contents_hub.web.app import create_app


class _StubFilterRunner:
    """Fake AgentRunner that returns a deterministic JSON matching the first
    sample id. Lets us assert promote was wired without spinning up Claude."""

    def __init__(self, matched_ids):
        self._ids = matched_ids

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        import json

        return json.dumps({"matched_ids": list(self._ids)})


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


@pytest.fixture
def client(vault):
    app = create_app(vault)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_default_runner():
    """Our tests mutate the process-wide default runner; restore after each."""
    from contents_hub import runners as _runners

    before = _runners._DEFAULT  # type: ignore[attr-defined]
    yield
    _runners._DEFAULT = before  # type: ignore[attr-defined]


def _seed_with_samples(cfg, *, filter_prompt: str) -> int:
    store = SubscriptionStore(cfg)
    store.add(
        url="https://example.com/",
        title="Example",
        source_type="webpage",
    )
    store.set_status("https://example.com/", SubscriptionStatus.VALIDATING)
    store.update_config(
        "https://example.com/",
        {
            "filter_prompt": filter_prompt,
            "trial_result": {
                "ok": True,
                "items": 2,
                "error": "",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "recipe_mode": "catalog",
                "samples": [
                    {
                        "url": "https://example.com/ai-post",
                        "title": "AI post (matches)",
                        "body": "This is about AI/ML.",
                        "published_at": "2026-04-17T00:00:00+00:00",
                    },
                    {
                        "url": "https://example.com/sports-post",
                        "title": "Sports post (should not match)",
                        "body": "Football.",
                        "published_at": "2026-04-16T00:00:00+00:00",
                    },
                ],
            },
        },
    )
    sub = store.get("https://example.com/")
    return int(sub.id)


def test_keep_runs_filter_and_promotes_matches(vault, client, monkeypatch):
    """Seeded trial has two samples. Filter is configured to match only the
    first (AI) one. Keep should commit both as raw_items, then promote the
    matched one via the filter hook — end state: 1 promoted, 1 raw."""
    sub_id = _seed_with_samples(vault, filter_prompt="AI/ML posts only")

    # Wire a deterministic runner so `apply_filter_and_promote` doesn't call
    # the real LLM. The sub's first sample will become raw_item id 1; we tell
    # the stub to return {matched_ids: [1]}.
    from contents_hub.runners import set_default_runner

    set_default_runner(_StubFilterRunner(matched_ids=[1]))

    resp = client.post(f"/subscriptions/{sub_id}/keep")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["inserted"] == 2

    # Confirm one promoted, one raw — filter ran against committed rows.
    with sqlite3.connect(vault.meta_path / "state.db") as conn:
        rows = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM raw_items "
                "WHERE subscription_id = ? GROUP BY status",
                (sub_id,),
            ).fetchall()
        )
    assert rows.get("promoted") == 1
    assert rows.get("raw") == 1


def test_keep_without_filter_prompt_is_still_fine(vault, client, monkeypatch):
    """Subscription without filter_prompt: Keep still commits samples, filter
    no-ops, all rows stay raw."""
    store = SubscriptionStore(vault)
    store.add(url="https://example.com/", title="E", source_type="webpage")
    store.set_status("https://example.com/", SubscriptionStatus.VALIDATING)
    store.update_config(
        "https://example.com/",
        {
            "trial_result": {
                "ok": True, "items": 1, "error": "",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "recipe_mode": "catalog",
                "samples": [
                    {
                        "url": "https://example.com/x",
                        "title": "X", "body": "body",
                        "published_at": None,
                    },
                ],
            },
        },
    )
    sub = store.get("https://example.com/")

    # No runner call should happen when filter_prompt is empty; stub anyway
    # with an aggressive matcher to catch accidental invocation.
    from contents_hub.runners import set_default_runner

    set_default_runner(_StubFilterRunner(matched_ids=[1]))

    resp = client.post(f"/subscriptions/{sub.id}/keep")
    assert resp.status_code == 200

    with sqlite3.connect(vault.meta_path / "state.db") as conn:
        rows = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM raw_items "
                "WHERE subscription_id = ? GROUP BY status",
                (int(sub.id),),
            ).fetchall()
        )
    # filter_prompt absent → no promote.
    assert rows.get("raw") == 1
    assert "promoted" not in rows

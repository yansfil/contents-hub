"""Pending-approval (validating) subscription flow.

Covers:
- The VALIDATING status round-trips through the store.
- /subscriptions/{id}/keep flips a validating sub to active and drops
  trial-only config keys.
- /subscriptions/{id}/discard deletes the subscription and its raw_items.
- /subscriptions/{id}/retry_validation clears trial_result and sample items
  but preserves trial_pre_recipe and status=validating.
- The three endpoints are no-ops for non-validating subs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.subscriptions import (
    SubscriptionStatus,
    SubscriptionStore,
)
from llm_wiki.web.app import create_app


@dataclass
class _StubPollResult:
    ok: bool = True
    items: list = field(default_factory=list)
    error: str = ""


class _StubFetcher:
    """No-op BrowserFetcher: keeps the background task from hitting the agent
    so tests can assert the endpoint's synchronous effects in isolation."""

    def __init__(self, *args, **kwargs):
        self._updated: dict = {}

    async def poll(self, *args, **kwargs):
        return _StubPollResult()

    def get_updated_config(self):
        return self._updated


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


@pytest.fixture(autouse=True)
def _stub_browser_fetcher(monkeypatch):
    """Globally stub out BrowserFetcher so any background fetch in tests is a no-op."""
    monkeypatch.setattr(
        "llm_wiki.fetchers.browser.BrowserFetcher", _StubFetcher, raising=True
    )


@pytest.fixture
def client(vault):
    app = create_app(vault)
    return TestClient(app)


def _seed_validating_sub(
    cfg,
    *,
    url: str = "https://example.com/",
    with_trial_result: bool = True,
    trial_ok: bool = True,
) -> int:
    store = SubscriptionStore(cfg)
    store.add(url=url, title="Example", source_type="webpage")
    store.set_status(url, SubscriptionStatus.VALIDATING)
    extras: dict = {"trial_pre_recipe": "", "trial_pre_had_override": False}
    if with_trial_result:
        extras["trial_result"] = {
            "ok": trial_ok,
            "items": 3 if trial_ok else 0,
            "error": "" if trial_ok else "explore timed out",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "recipe_mode": "new" if trial_ok else "failed",
            "recipe": "LIST: ...\nCONTENT: ...\n" if trial_ok else "",
        }
    store.update_config(url, extras)
    sub = store.get(url)
    return int(sub.id)


def _add_raw_item(cfg, sub_id: int, url: str = "https://example.com/a") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "INSERT INTO raw_items (url, title, origin, priority, status, "
            "subscription_id, collected_at, updated_at) "
            "VALUES (?, 'T', 'subscription', 50, 'raw', ?, ?, ?)",
            (url, sub_id, now, now),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Enum wiring
# ---------------------------------------------------------------------------


def test_validating_status_round_trips(vault):
    store = SubscriptionStore(vault)
    store.add(url="https://example.com/", title="E", source_type="webpage")
    store.set_status("https://example.com/", SubscriptionStatus.VALIDATING)
    sub = store.get("https://example.com/")
    assert sub.status == SubscriptionStatus.VALIDATING


# ---------------------------------------------------------------------------
# Keep
# ---------------------------------------------------------------------------


def test_keep_activates_and_strips_trial_keys(vault, client):
    sub_id = _seed_validating_sub(vault)

    resp = client.post(f"/subscriptions/{sub_id}/keep")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    store = SubscriptionStore(vault)
    sub = store.get_by_id(str(sub_id))
    assert sub.status == SubscriptionStatus.ACTIVE
    assert "trial_result" not in sub.config
    assert "trial_pre_recipe" not in sub.config
    assert "trial_pre_had_override" not in sub.config


def test_keep_is_noop_for_active_sub(vault, client):
    store = SubscriptionStore(vault)
    store.add(url="https://example.com/", title="E", source_type="webpage")
    # default status is ACTIVE — don't flip to validating
    sub = store.get("https://example.com/")

    resp = client.post(f"/subscriptions/{sub.id}/keep")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


# ---------------------------------------------------------------------------
# Discard
# ---------------------------------------------------------------------------


def test_discard_deletes_sub_and_raw_items(vault, client):
    sub_id = _seed_validating_sub(vault)
    _add_raw_item(vault, sub_id)

    resp = client.post(f"/subscriptions/{sub_id}/discard")
    assert resp.status_code == 200
    assert resp.json()["status"] == "discarded"

    store = SubscriptionStore(vault)
    assert store.get_by_id(str(sub_id)) is None
    with sqlite3.connect(vault.meta_path / "state.db") as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM raw_items WHERE subscription_id = ?", (sub_id,)
        ).fetchone()[0]
        assert n == 0


def test_discard_is_noop_for_active_sub(vault, client):
    store = SubscriptionStore(vault)
    store.add(url="https://example.com/", title="E", source_type="webpage")
    sub = store.get("https://example.com/")

    resp = client.post(f"/subscriptions/{sub.id}/discard")
    assert resp.json()["status"] == "skipped"
    # sub still exists
    assert store.get_by_id(sub.id) is not None


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_retry_clears_stale_items_and_reruns_trial(vault, client):
    sub_id = _seed_validating_sub(vault, with_trial_result=True, trial_ok=False)
    _add_raw_item(vault, sub_id, url="https://example.com/stale")

    resp = client.post(f"/subscriptions/{sub_id}/retry_validation")
    assert resp.status_code == 200
    assert resp.json()["status"] == "retrying"

    # After retry, the stub fetcher re-populates trial_result (ok, 0 items)
    # and the stale raw_item was cleared before the new trial ran.
    store = SubscriptionStore(vault)
    sub = store.get_by_id(str(sub_id))
    assert sub.status == SubscriptionStatus.VALIDATING
    # The failed trial_result was replaced with a fresh one.
    tr = sub.config["trial_result"]
    assert tr["ok"] is True
    assert tr["items"] == 0
    assert "trial_pre_recipe" in sub.config

    with sqlite3.connect(vault.meta_path / "state.db") as conn:
        urls = [
            r[0]
            for r in conn.execute(
                "SELECT url FROM raw_items WHERE subscription_id = ?", (sub_id,)
            ).fetchall()
        ]
        # Stale pre-retry item was cleared; stub returns no new items.
        assert "https://example.com/stale" not in urls


def test_retry_is_noop_for_active_sub(vault, client):
    store = SubscriptionStore(vault)
    store.add(url="https://example.com/", title="E", source_type="webpage")
    sub = store.get("https://example.com/")

    resp = client.post(f"/subscriptions/{sub.id}/retry_validation")
    assert resp.json()["status"] == "skipped"

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.web.app import create_app


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    cfg.meta_path.mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


@pytest.fixture
def client(vault):
    return TestClient(create_app(vault))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _seed_lens(cfg, lens_id: str, *, name: str = "", enabled: bool = True) -> None:
    now = _now()
    with get_db(cfg) as conn:
        conn.execute(
            """INSERT INTO lenses
               (id, name, description, keywords, enabled, created_at, updated_at)
               VALUES (?, ?, ?, '[]', ?, ?, ?)""",
            (
                lens_id,
                name,
                f"{lens_id} description",
                1 if enabled else 0,
                now,
                now,
            ),
        )


def test_exploration_creation_page_exposes_request_surfaces_lenses_and_name(
    vault, client
):
    _seed_lens(vault, "ai", name="AI Lens")
    _seed_lens(vault, "disabled", enabled=False)

    resp = client.get("/explorations")

    assert resp.status_code == 200
    assert 'name="original_request"' in resp.text
    assert 'name="display_name"' in resp.text
    assert 'value="threads.feed"' in resp.text
    assert "Threads home feed" in resp.text
    assert 'value="threads.search"' in resp.text
    assert "Threads search" in resp.text
    assert "X exploration" in resp.text
    assert "Later extension; not available in the MVP." in resp.text
    assert 'name="lens_ids" value="ai"' in resp.text
    assert "AI Lens" in resp.text
    assert 'value="disabled"' not in resp.text


def test_exploration_draft_post_persists_without_subscription_or_recipe(vault, client):
    _seed_lens(vault, "ai", name="AI Lens")
    _seed_lens(vault, "disabled", enabled=False)

    resp = client.post(
        "/explorations/drafts",
        data={
            "original_request": "Find practical agent workflow posts on Threads",
            "display_name": "Agent workflow scouting",
            "target_surfaces": ["threads.feed", "threads.search", "x.search"],
            "lens_ids": ["ai", "disabled"],
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/explorations?msg=Draft+saved")
    with get_db(vault) as conn:
        row = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()
        assert row[0] == 0
        draft = conn.execute(
            """SELECT display_name, original_request, target_surfaces, lens_ids,
                      status
               FROM explorations"""
        ).fetchone()

    assert draft["display_name"] == "Agent workflow scouting"
    assert draft["original_request"] == "Find practical agent workflow posts on Threads"
    assert json.loads(draft["target_surfaces"]) == [
        "threads.feed",
        "threads.search",
    ]
    assert json.loads(draft["lens_ids"]) == ["ai"]
    assert draft["status"] == "draft"


def test_exploration_draft_post_suggests_display_name_and_shows_status_feedback(
    vault, client
):
    resp = client.post(
        "/explorations/drafts",
        data={
            "original_request": (
                "Map Korean founders discussing AI product distribution"
            ),
            "target_surfaces": "threads.search",
        },
    )

    assert resp.status_code == 200
    assert "Draft saved:" in resp.text
    assert "is not registered until validation is approved" in resp.text
    assert "Map Korean founders discussing AI product" in resp.text
    assert "Draft" in resp.text

    with get_db(vault) as conn:
        draft = conn.execute(
            "SELECT display_name, target_surfaces, lens_ids, status FROM explorations"
        ).fetchone()

    assert draft["display_name"] == "Map Korean founders discussing AI product"
    assert json.loads(draft["target_surfaces"]) == ["threads.search"]
    assert json.loads(draft["lens_ids"]) == []
    assert draft["status"] == "draft"


def test_exploration_draft_requires_natural_language_request(vault, client):
    resp = client.post(
        "/explorations/drafts",
        data={"original_request": "   ", "display_name": "Empty"},
    )

    assert resp.status_code == 200
    assert "Exploration request is required" in resp.text
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM explorations").fetchone()[0] == 0

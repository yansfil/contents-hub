from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import ExplorationStore
from contents_hub.runners import get_default_runner, set_default_runner
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


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


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


def test_exploration_detail_shows_validation_review_layers(vault, client):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Agent workflow review",
        original_request="Find implementation-heavy Threads posts",
        target_surfaces=["threads.search"],
        lens_ids=["ai"],
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={
            "target_surfaces": ["threads.search"],
            "collection_approach": "Search Threads for implementation details",
        },
        process_summary="Opened Threads search and kept implementation examples.",
        raw_trace={
            "steps": ["open Threads search", "scroll once", "extract candidates"],
            "skipped": ["general commentary"],
        },
        preview_items=[
            {
                "url": "https://threads.test/post/1",
                "title": "Agent workflow teardown",
                "summary": "Shows concrete implementation details.",
                "source_surface": "threads.search",
                "collected_at": "2026-05-14T00:00:00Z",
            }
        ],
        preview_lens_matches=[
            {
                "url": "https://threads.test/post/1",
                "lens_id": "ai",
                "summary": "Matches implementation lens.",
            }
        ],
        finished_at=_now(),
    )

    resp = client.get(f"/explorations/{exploration.id}")

    assert resp.status_code == 200
    assert "Preview candidates found" in resp.text
    assert "Agent workflow teardown" in resp.text
    assert "Opened Threads search" in resp.text
    assert "implementation details" in resp.text
    assert "Matches implementation lens." in resp.text
    assert "Strategy" in resp.text
    assert "Process details" in resp.text
    assert "Revise and validate" in resp.text
    assert f'name="validation_attempt_id" value="{attempt.id}"' in resp.text


def test_exploration_revision_uses_natural_language_and_prior_evidence(
    vault,
    client,
):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Founder AI threads",
        original_request="Find founder AI distribution posts",
        target_surfaces=["threads.search"],
    )
    prior = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="failed",
        strategy_snapshot={"collection_approach": "general search"},
        process_summary="Found broad AI commentary only.",
        preview_items=[{"url": "https://threads.test/broad"}],
        error="No implementation-heavy candidates.",
        finished_at=_now(),
    )
    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "target_surfaces": ["threads.search"],
                    "collection_approach": "Search for founder implementation posts",
                    "candidate_selection": "Keep concrete distribution examples",
                    "extraction_approach": "Extract title, URL, and summary",
                    "stop_limits": {
                        "max_items": 2,
                        "max_pages": 1,
                        "max_scrolls": 1,
                        "timeout_seconds": 30,
                    },
                    "lens_alignment_notes": "Prefer implementation detail.",
                }
            ),
            json.dumps(
                {
                    "process_summary": "Retried with narrower distribution terms.",
                    "preview_items": [
                        {
                            "url": "https://threads.test/narrow",
                            "title": "Founder distribution detail",
                            "summary": "Concrete distribution example.",
                        }
                    ],
                    "preview_lens_matches": [],
                    "raw_trace": {"steps": ["search narrow terms"]},
                    "chromux_session_ids": ["exploration-review"],
                    "error": "",
                }
            ),
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        resp = client.post(
            f"/explorations/{exploration.id}/revise",
            data={
                "revision_instruction": "Narrow this to concrete distribution details",
                "prior_attempt_id": str(prior.id),
            },
            follow_redirects=False,
        )
    finally:
        set_default_runner(original)

    assert resp.status_code == 303
    assert "Revision+validation+attempt+2+succeeded" in resp.headers["location"]
    assert "Narrow this to concrete distribution details" in runner.prompts[0]
    assert "Found broad AI commentary only." in runner.prompts[0]

    with get_db(vault) as conn:
        row = conn.execute(
            """SELECT attempt_number, status, preview_items_json
               FROM exploration_validation_attempts
               WHERE exploration_id = ?
               ORDER BY attempt_number DESC
               LIMIT 1""",
            (exploration.id,),
        ).fetchone()

    assert row["attempt_number"] == 2
    assert row["status"] == "succeeded"
    assert json.loads(row["preview_items_json"])[0]["title"] == (
        "Founder distribution detail"
    )


def test_exploration_revision_requires_instruction(vault, client):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Needs revision",
        original_request="Find posts",
    )

    resp = client.post(
        f"/explorations/{exploration.id}/revise",
        data={"revision_instruction": "   "},
    )

    assert resp.status_code == 200
    assert "Revision instruction is required" in resp.text

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


def test_exploration_approval_registers_only_successful_validation(vault, client):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Approve me",
        original_request="Find good Threads posts",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"collection_approach": "approved strategy"},
        preview_items=[{"url": "https://threads.test/preview"}],
        preview_lens_matches=[{"lens_id": "ai", "summary": "match"}],
        finished_at=_now(),
    )

    resp = client.post(
        f"/explorations/{exploration.id}/approve",
        data={"validation_attempt_id": str(attempt.id)},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "Registered+strategy+version+1" in resp.headers["location"]
    with get_db(vault) as conn:
        exploration_row = conn.execute(
            """SELECT status, approved_strategy_version_id
               FROM explorations
               WHERE id = ?""",
            (exploration.id,),
        ).fetchone()
        strategy_row = conn.execute(
            """SELECT version, strategy_snapshot, validation_attempt_id
               FROM exploration_strategy_versions
               WHERE exploration_id = ?""",
            (exploration.id,),
        ).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0

    assert exploration_row["status"] == "registered"
    assert exploration_row["approved_strategy_version_id"] is not None
    assert strategy_row["version"] == 1
    assert strategy_row["validation_attempt_id"] == attempt.id
    assert json.loads(strategy_row["strategy_snapshot"]) == {
        "collection_approach": "approved strategy"
    }

    detail = client.get(f"/explorations/{exploration.id}")
    assert detail.status_code == 200
    assert "Registered" in detail.text


def test_exploration_approval_rejects_failed_validation(vault, client):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Do not approve",
        original_request="Find posts",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="failed",
        strategy_snapshot={"collection_approach": "bad strategy"},
        error="auth failed",
        finished_at=_now(),
    )

    resp = client.post(
        f"/explorations/{exploration.id}/approve",
        data={"validation_attempt_id": str(attempt.id)},
    )

    assert resp.status_code == 200
    assert "only succeeded validation attempts can be approved" in resp.text
    with get_db(vault) as conn:
        exploration_row = conn.execute(
            "SELECT status, approved_strategy_version_id FROM explorations"
        ).fetchone()
        strategy_count = conn.execute(
            "SELECT COUNT(*) FROM exploration_strategy_versions"
        ).fetchone()[0]

    assert exploration_row["status"] == "draft"
    assert exploration_row["approved_strategy_version_id"] is None
    assert strategy_count == 0


def test_registered_exploration_manual_run_persists_raw_items_and_review_links(
    vault,
    client,
):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Manual run",
        original_request="Find implementation posts",
        target_surfaces=["threads.search"],
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={
            "target_surfaces": ["threads.search"],
            "collection_approach": "Search for implementation posts",
        },
        preview_items=[{"url": "https://threads.test/preview"}],
        finished_at=_now(),
    )
    store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "url": "https://threads.test/run/1",
                            "title": "Run item",
                            "summary": "Persist this manual run item.",
                            "source_surface": "threads.search",
                        }
                    ],
                    "raw_trace": {"steps": ["open", "extract"]},
                    "chromux_session_ids": ["manual-run"],
                    "error": "",
                }
            )
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        resp = client.post(
            f"/explorations/{exploration.id}/run",
            follow_redirects=False,
        )
    finally:
        set_default_runner(original)

    assert resp.status_code == 303
    assert "Manual+run+succeeded:+1+found,+1+new" in resp.headers["location"]
    assert "Run this approved feed exploration once" in runner.prompts[0]
    with get_db(vault) as conn:
        run = conn.execute(
            """SELECT status, items_found, items_inserted
               FROM exploration_runs
               WHERE exploration_id = ?""",
            (exploration.id,),
        ).fetchone()
        raw_item = conn.execute(
            "SELECT id, title, url, subscription_id FROM raw_items"
        ).fetchone()
        discovery = conn.execute(
            """SELECT owner_type, owner_run_id, exploration_id
               FROM raw_item_discoveries"""
        ).fetchone()

    assert run["status"] == "succeeded"
    assert run["items_found"] == 1
    assert run["items_inserted"] == 1
    assert raw_item["title"] == "Run item"
    assert raw_item["url"] == "https://threads.test/run/1"
    assert raw_item["subscription_id"] is None
    assert discovery["owner_type"] == "exploration_run"
    assert discovery["exploration_id"] == exploration.id

    detail = client.get(f"/explorations/{exploration.id}")
    assert detail.status_code == 200
    assert "Manual runs only in this MVP" in detail.text
    assert "Run item" in detail.text
    assert f"/lens-inbox?raw_item_id={raw_item['id']}" in detail.text


def test_manual_run_requires_registered_exploration(vault, client):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Draft only",
        original_request="Find posts",
    )

    resp = client.post(f"/explorations/{exploration.id}/run")

    assert resp.status_code == 200
    assert "exploration must be approved before manual run" in resp.text
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0


def test_exploration_web_journey_draft_validate_approve_run_and_lens_review(
    vault,
    client,
):
    _seed_lens(vault, "ai", name="AI Lens")
    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "target_surfaces": ["threads.search"],
                    "collection_approach": "Search Threads for AI implementation",
                    "candidate_selection": "Keep implementation-heavy posts",
                    "extraction_approach": "Extract title, url, summary",
                    "stop_limits": {
                        "max_items": 2,
                        "max_pages": 1,
                        "max_scrolls": 1,
                        "timeout_seconds": 30,
                    },
                    "lens_alignment_notes": "Match AI implementation notes.",
                }
            ),
            json.dumps(
                {
                    "process_summary": "Validated Threads search path.",
                    "preview_items": [
                        {
                            "url": "https://threads.test/preview",
                            "title": "Preview AI implementation",
                            "summary": "Preview only.",
                        }
                    ],
                    "preview_lens_matches": [
                        {
                            "url": "https://threads.test/preview",
                            "lens_id": "ai",
                            "summary": "Preview match.",
                        }
                    ],
                    "raw_trace": {"steps": ["search", "preview"]},
                    "chromux_session_ids": ["validation-tab"],
                    "error": "",
                }
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "url": "https://threads.test/run/final",
                            "title": "Final AI implementation",
                            "summary": "Persisted run item.",
                            "source_surface": "threads.search",
                        }
                    ],
                    "raw_trace": {"steps": ["search", "persist"]},
                    "chromux_session_ids": ["run-tab"],
                    "error": "",
                }
            ),
            json.dumps(
                {
                    "matches": [
                        {
                            "id": 1,
                            "summary": "AI implementation match.",
                            "bullets": ["Concrete workflow detail."],
                        }
                    ]
                }
            ),
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        draft_resp = client.post(
            "/explorations/drafts",
            data={
                "original_request": "Find AI implementation posts",
                "display_name": "AI implementation scout",
                "target_surfaces": "threads.search",
                "lens_ids": "ai",
            },
            follow_redirects=False,
        )
        assert draft_resp.status_code == 303
        with get_db(vault) as conn:
            exploration_id = conn.execute("SELECT id FROM explorations").fetchone()[0]

        validate_resp = client.post(
            f"/explorations/{exploration_id}/validate",
            follow_redirects=False,
        )
        assert validate_resp.status_code == 303
        with get_db(vault) as conn:
            attempt_id = conn.execute(
                "SELECT id FROM exploration_validation_attempts"
            ).fetchone()[0]

        approve_resp = client.post(
            f"/explorations/{exploration_id}/approve",
            data={"validation_attempt_id": str(attempt_id)},
            follow_redirects=False,
        )
        assert approve_resp.status_code == 303

        run_resp = client.post(
            f"/explorations/{exploration_id}/run",
            follow_redirects=False,
        )
    finally:
        set_default_runner(original)

    assert run_resp.status_code == 303
    assert "Manual+run+succeeded" in run_resp.headers["location"]
    detail = client.get(f"/explorations/{exploration_id}")
    assert "Registered" in detail.text
    assert "Final AI implementation" in detail.text
    assert "/lens-inbox?raw_item_id=" in detail.text

    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM raw_item_discoveries").fetchone()[0]
            == 1
        )

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
    assert 'name="recipe_markdown"' in resp.text
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


def test_exploration_post_registers_with_recipe_without_subscription(vault, client):
    _seed_lens(vault, "ai", name="AI Lens")
    _seed_lens(vault, "disabled", enabled=False)

    resp = client.post(
        "/explorations",
        data={
            "original_request": "Find practical agent workflow posts on Threads",
            "recipe_markdown": "# Goal\n\nFind practical agent workflow posts.",
            "display_name": "Agent workflow scouting",
            "target_surfaces": ["threads.feed", "threads.search", "x.search"],
            "lens_ids": ["ai", "disabled"],
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/explorations?msg=Exploration+registered")
    with get_db(vault) as conn:
        row = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()
        assert row[0] == 0
        exploration = conn.execute(
            """SELECT display_name, original_request, target_surfaces, lens_ids,
                      status, approved_strategy_version_id
               FROM explorations"""
        ).fetchone()
        strategy = conn.execute(
            """SELECT version, strategy_snapshot, validation_attempt_id
               FROM exploration_strategy_versions"""
        ).fetchone()

    assert exploration["display_name"] == "Agent workflow scouting"
    assert exploration["original_request"] == "Find practical agent workflow posts on Threads"
    assert json.loads(exploration["target_surfaces"]) == [
        "threads.feed",
        "threads.search",
    ]
    assert json.loads(exploration["lens_ids"]) == ["ai"]
    assert exploration["status"] == "registered"
    assert exploration["approved_strategy_version_id"] is not None
    assert strategy["version"] == 1
    assert strategy["validation_attempt_id"] is None
    assert json.loads(strategy["strategy_snapshot"]) == {
        "recipe_markdown": "# Goal\n\nFind practical agent workflow posts."
    }


def test_exploration_draft_post_suggests_display_name_and_shows_status_feedback(
    vault, client
):
    resp = client.post(
        "/explorations",
        data={
            "original_request": (
                "Map Korean founders discussing AI product distribution"
            ),
            "recipe_markdown": "# Goal\n\nMap founder distribution posts.",
            "target_surfaces": "threads.search",
        },
    )

    assert resp.status_code == 200
    assert "Exploration registered:" in resp.text
    assert "Map Korean founders discussing AI product" in resp.text
    assert "registered" in resp.text

    with get_db(vault) as conn:
        exploration = conn.execute(
            "SELECT display_name, target_surfaces, lens_ids, status FROM explorations"
        ).fetchone()

    assert exploration["display_name"] == "Map Korean founders discussing AI product"
    assert json.loads(exploration["target_surfaces"]) == ["threads.search"]
    assert json.loads(exploration["lens_ids"]) == []
    assert exploration["status"] == "registered"


def test_exploration_draft_requires_natural_language_request(vault, client):
    resp = client.post(
        "/explorations",
        data={"original_request": "   ", "display_name": "Empty"},
    )

    assert resp.status_code == 200
    assert "Exploration request is required" in resp.text
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM explorations").fetchone()[0] == 0


def test_exploration_creation_requires_recipe_markdown(vault, client):
    resp = client.post(
        "/explorations",
        data={
            "original_request": "Find posts",
            "recipe_markdown": "   ",
            "display_name": "No recipe",
        },
    )

    assert resp.status_code == 200
    assert "Recipe Markdown is required" in resp.text
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM explorations").fetchone()[0] == 0


def test_exploration_detail_shows_registered_recipe(vault, client):
    store = ExplorationStore(vault)
    exploration, strategy = store.create_registered_with_recipe(
        display_name="Agent workflow review",
        original_request="Find implementation-heavy Threads posts",
        recipe_markdown="# Goal\n\nFind implementation-heavy Threads posts.",
        target_surfaces=["threads.search"],
        lens_ids=["ai"],
    )

    resp = client.get(f"/explorations/{exploration.id}")

    assert resp.status_code == 200
    assert "Registered" in resp.text
    assert f"Recipe version {strategy.version}" in resp.text
    assert "Find implementation-heavy Threads posts." in resp.text
    assert "Run validation" not in resp.text
    assert "Approve" not in resp.text
    assert "Revise and validate" not in resp.text


def test_registered_exploration_manual_run_persists_raw_items_and_review_links(
    vault,
    client,
):
    store = ExplorationStore(vault)
    exploration, _strategy = store.create_registered_with_recipe(
        display_name="Manual run",
        original_request="Find implementation posts",
        recipe_markdown="# Goal\n\nFind implementation posts.",
        target_surfaces=["threads.search"],
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
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "url": "https://threads.test/run/1",
                            "title": "Run item enriched",
                            "summary": "Persist this enriched manual run item.",
                            "content_html": "<p>Detail</p>",
                            "source_surface": "threads.search",
                            "content_status": "detail_enriched",
                        }
                    ],
                    "raw_trace": {"steps": ["open-detail", "extract"]},
                    "chromux_session_ids": ["manual-run-detail"],
                    "error": "",
                }
            ),
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
    assert "Run Phase 1 of this approved feed exploration once" in runner.prompts[0]
    assert "Run Phase 2 of this approved feed exploration once" in runner.prompts[1]
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
    assert raw_item["title"] == "Run item enriched"
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
    assert "exploration must be registered before manual run" in resp.text
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0


def test_exploration_web_journey_register_run_and_lens_review(
    vault,
    client,
):
    _seed_lens(vault, "ai", name="AI Lens")
    runner = _SequenceRunner(
        [
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
                        "items": [
                            {
                                "url": "https://threads.test/run/final",
                                "title": "Final AI implementation",
                                "summary": "Persisted enriched run item.",
                                "content_html": "<p>Detail</p>",
                                "source_surface": "threads.search",
                                "content_status": "detail_enriched",
                            }
                        ],
                        "raw_trace": {"steps": ["open-detail", "extract"]},
                        "chromux_session_ids": ["run-detail-tab"],
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
            "/explorations",
            data={
                "original_request": "Find AI implementation posts",
                "recipe_markdown": "# Goal\n\nFind AI implementation posts.",
                "display_name": "AI implementation scout",
                "target_surfaces": "threads.search",
                "lens_ids": "ai",
            },
            follow_redirects=False,
        )
        assert draft_resp.status_code == 303
        with get_db(vault) as conn:
            exploration_id = conn.execute("SELECT id FROM explorations").fetchone()[0]

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

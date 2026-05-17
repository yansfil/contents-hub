from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from contents_hub.cli import main
from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import ExplorationStore
from contents_hub.runners import get_default_runner, set_default_runner
from contents_hub.tools import get_default_registry


class _SequenceRunner:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        response = self.responses.pop(0)
        payload = json.loads(response)
        if "items" in payload:
            spec = get_default_registry().get("persist_exploration_raw")
            assert spec is not None
            items = []
            for item in payload["items"]:
                item = dict(item)
                item.setdefault("selection_reason", "test candidate")
                item.setdefault("content_status", "detail_enriched")
                items.append(item)
            await spec.handler(items=items)
            return json.dumps(
                {
                    "summary": "persisted test items",
                    "sources_attempted": ["test"],
                    "stopped_reason": "complete",
                    "chromux_session_ids": payload.get("chromux_session_ids", []),
                    "error": payload.get("error", ""),
                }
            )
        return response


class _WorkflowRunner:
    def __init__(self):
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if "single autonomous mission" in prompt:
            spec = get_default_registry().get("persist_exploration_raw")
            assert spec is not None
            items = []
            for surface in ["news", "web", "x", "reddit", "linkedin"]:
                title = (
                    f"OpenAI AI news from {surface}"
                    if surface in {"news", "web"}
                    else f"Claude Code workflow tip from {surface}"
                )
                items.append(
                    {
                        "url": f"https://example.test/{surface.replace('.', '-')}",
                        "title": title,
                        "summary": title,
                        "content_html": title,
                        "author": "tester",
                        "published_at": None,
                        "source_surface": surface,
                        "selection_reason": "matches recipe",
                        "content_status": "detail_enriched",
                    }
                )
            await spec.handler(items=items)
            return json.dumps(
                {
                    "summary": "persisted autonomous items",
                    "sources_attempted": ["news", "web", "x", "reddit", "linkedin"],
                    "stopped_reason": "complete",
                    "chromux_session_ids": ["chx-autonomous"],
                    "error": "",
                }
            )

        if "You summarize Lens-matched raw items" in prompt:
            ids = sorted({int(value) for value in re.findall(r'"id":\s*(\d+)', prompt)})
            return json.dumps(
                {
                    "items": [
                        {
                            "id": item_id,
                            "summary": f"summary {item_id}",
                            "bullets": [f"bullet {item_id}"],
                        }
                        for item_id in ids
                    ]
                }
            )

        raise AssertionError(f"unexpected prompt: {prompt[:120]}")


def _seed_lens(
    cfg: WikiConfig,
    lens_id: str,
    *,
    keywords: list[str],
    enabled: bool = True,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db(cfg) as conn:
        conn.execute(
            """INSERT INTO lenses
               (id, name, description, keywords, enabled, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?, ?)""",
            (
                lens_id,
                lens_id,
                json.dumps(keywords),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()


def _read_json(capsys):
    out = capsys.readouterr().out.strip()
    return json.loads(out)


def test_explore_cli_requires_recipe_and_creates_no_row(tmp_path, capsys):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()

    rc = main(
        [
            "--vault",
            str(tmp_path),
            "explore",
            "Threads feed에서 최근 바이브코딩 노하우 글을 찾기",
            "--surface",
            "threads.feed",
            "--lens-id",
            "ai",
        ]
    )

    payload = _read_json(capsys)
    assert rc == 1
    assert payload == {"ok": False, "error": "--recipe is required"}

    with get_db(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM explorations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0


def test_exploration_cli_add_recipe_and_run(tmp_path, capsys):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    recipe_path = tmp_path / "recipe.md"
    recipe_path.write_text("# Goal\n\nFind Threads tips.\n", encoding="utf-8")
    create_rc = main(
        [
            "--vault",
            str(tmp_path),
            "exploration",
            "add",
            "Threads search에서 바이브코딩 팁 찾기",
            "--surface",
            "threads.search",
            "--recipe",
            str(recipe_path),
        ]
    )
    created = _read_json(capsys)
    exploration_id = created["exploration"]["exploration_id"]
    assert create_rc == 0
    assert created["exploration"]["status"] == "registered"
    assert created["exploration"]["target_surfaces"] == ["threads.search"]
    assert created["strategy_version"]["version"] == 1
    assert created["strategy_version"]["validation_attempt_id"] is None
    assert created["strategy_version"]["strategy_snapshot"] == {
        "recipe_markdown": "# Goal\n\nFind Threads tips."
    }

    original = get_default_runner()
    try:
        set_default_runner(
            _SequenceRunner(
                """
                {
                  "items": [
                    {
                      "url": "https://threads.test/a",
                      "title": "Tip A",
                      "summary": "Concrete workflow",
                      "content_html": "",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.search"
                    }
                  ],
                  "raw_trace": {"steps": ["search", "extract"]},
                  "chromux_session_ids": ["explore-run-cli"],
                  "error": ""
                }
                """,
                """
                {
                  "items": [
                    {
                      "url": "https://threads.test/a",
                      "title": "Tip A enriched",
                      "summary": "Concrete workflow with details",
                      "content_html": "<p>Details</p>",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.search",
                      "content_status": "detail_enriched"
                    }
                  ],
                  "raw_trace": {"steps": ["open-detail", "extract"]},
                  "chromux_session_ids": ["explore-run-cli-detail"],
                  "error": ""
                }
                """
            )
        )
        run_rc = main(
            [
                "--vault",
                str(tmp_path),
                "exploration",
                "run",
                str(exploration_id),
            ]
        )
        ran = _read_json(capsys)
    finally:
        set_default_runner(original)

    assert run_rc == 0
    assert ran["run"]["status"] == "succeeded"
    assert ran["run"]["items_found"] == 1
    assert ran["run"]["items_inserted"] == 1

    with get_db(cfg) as conn:
        raw = conn.execute(
            "SELECT origin, subscription_id FROM raw_items WHERE url = ?",
            ("https://threads.test/a",),
        ).fetchone()
        assert raw["origin"] == "exploration"
        assert raw["subscription_id"] is None


def test_exploration_cli_yaml_recipe_runs_autonomous_and_default_lenses(
    tmp_path,
    capsys,
):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    _seed_lens(cfg, "ai-news", keywords=["OpenAI"])
    _seed_lens(cfg, "vibe-coding", keywords=["Claude Code"])
    _seed_lens(cfg, "disabled", keywords=["OpenAI"], enabled=False)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        """
goal: Collect May 16 AI news and practical agent workflow tips as raw_items.
keep:
  - Concrete AI news from May 16.
  - Useful Claude Code, vibe coding, or agent workflow tips.
skip:
  - Pure opinion without actionable details.
sources:
  - surface: news
    search: AI news May 16
  - surface: web
    search: agentic workflow May 16
  - surface: x
    search: Claude Code workflow
  - surface: reddit
    search: vibe coding agent workflow
  - surface: linkedin
    search: AI agent workflow
runtime:
  max_minutes: 10
  target_items: 12
""".strip(),
        encoding="utf-8",
    )

    create_rc = main(
        [
            "--vault",
            str(tmp_path),
            "exploration",
            "add",
            "2026-05-16 AI news and social workflow tips",
            "--recipe",
            str(recipe_path),
        ]
    )
    created = _read_json(capsys)
    exploration_id = created["exploration"]["exploration_id"]
    assert create_rc == 0
    assert created["exploration"]["target_surfaces"] == [
        "news",
        "web",
        "x",
        "reddit",
        "linkedin",
    ]
    assert created["exploration"]["lens_ids"] == []
    assert created["strategy_version"]["strategy_snapshot"]["recipe_yaml"]["runtime"][
        "target_items"
    ] == 12

    runner = _WorkflowRunner()
    original = get_default_runner()
    try:
        set_default_runner(runner)
        run_rc = main(
            [
                "--vault",
                str(tmp_path),
                "exploration",
                "run",
                str(exploration_id),
            ]
        )
        ran = _read_json(capsys)
    finally:
        set_default_runner(original)

    assert run_rc == 0
    assert ran["run"]["status"] == "succeeded"
    assert ran["run"]["items_found"] == 5
    assert ran["run"]["items_inserted"] == 5
    fanout_prompts = [
        prompt
        for prompt in runner.prompts
        if "Run one fanout item from this approved feed exploration" in prompt
    ]
    assert len(fanout_prompts) == 0
    autonomous_prompts = [
        prompt for prompt in runner.prompts if "single autonomous mission" in prompt
    ]
    assert len(autonomous_prompts) == 1

    with get_db(cfg) as conn:
        trace_path = conn.execute(
            "SELECT raw_trace_artifact_path FROM exploration_runs WHERE id = ?",
            (ran["run"]["run_id"],),
        ).fetchone()["raw_trace_artifact_path"]
        trace = json.loads((cfg.meta_path / trace_path).read_text(encoding="utf-8"))
        assert trace["orchestration"] == "autonomous_agent"
        assert trace["persist_summary"]["inserted"] == 5
        lens_rows = conn.execute(
            """SELECT lens_id, COUNT(*) AS count
               FROM raw_item_lenses
               GROUP BY lens_id
               ORDER BY lens_id"""
        ).fetchall()
        assert [(row["lens_id"], row["count"]) for row in lens_rows] == [
            ("ai-news", 2),
            ("vibe-coding", 3),
        ]


def test_removed_exploration_commands_are_absent_from_help(tmp_path, capsys):
    init_db(WikiConfig(vault_path=tmp_path)).close()

    try:
        main(["--vault", str(tmp_path), "exploration", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "validate" not in out
    assert "approve" not in out
    assert "revise" not in out


def test_exploration_cli_run_all_runs_registered_only(tmp_path, capsys):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    store = ExplorationStore(cfg)
    first, _ = store.create_registered_with_recipe(
        display_name="First registered",
        original_request="Find first",
        recipe_markdown="# Goal\n\nFind first",
    )
    second, _ = store.create_registered_with_recipe(
        display_name="Second registered",
        original_request="Find second",
        recipe_markdown="# Goal\n\nFind second",
    )
    store.create_draft(
        display_name="Draft should not run",
        original_request="Do not run this draft",
    )

    original = get_default_runner()
    try:
        set_default_runner(
            _SequenceRunner(
                """
                {
                  "items": [
                    {
                      "url": "https://threads.test/run-all-2",
                      "title": "Run all 2",
                      "summary": "Second result",
                      "content_html": "",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.feed"
                    }
                  ],
                  "raw_trace": {"steps": ["feed"]},
                  "chromux_session_ids": [],
                  "error": ""
                }
                """,
                """
                {
                  "items": [
                    {
                      "url": "https://threads.test/run-all-1",
                      "title": "Run all 1",
                      "summary": "First result",
                      "content_html": "",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.search"
                    }
                  ],
                  "raw_trace": {"steps": ["search"]},
                  "chromux_session_ids": [],
                  "error": ""
                }
                """,
            )
        )
        rc = main(
            [
                "--vault",
                str(tmp_path),
                "exploration",
                "run-all",
            ]
        )
        payload = _read_json(capsys)
    finally:
        set_default_runner(original)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["total"] == 2
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0
    assert payload["items_found"] == 2
    assert payload["items_inserted"] == 2
    assert [entry["display_name"] for entry in payload["per_exploration"]] == [
        "Second registered",
        "First registered",
    ]

    with get_db(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM exploration_runs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 2

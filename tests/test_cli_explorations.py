from __future__ import annotations

import json

from contents_hub.cli import main
from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import ExplorationStore
from contents_hub.runners import get_default_runner, set_default_runner


class _SequenceRunner:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


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
                      "url": "https://threads.test/run-all-2",
                      "title": "Run all 2 detail",
                      "summary": "Second result detail",
                      "content_html": "<p>Second</p>",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.feed",
                      "content_status": "detail_enriched"
                    }
                  ],
                  "raw_trace": {"steps": ["feed-detail"]},
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
                """
                {
                  "items": [
                    {
                      "url": "https://threads.test/run-all-1",
                      "title": "Run all 1 detail",
                      "summary": "First result detail",
                      "content_html": "<p>First</p>",
                      "author": "tester",
                      "published_at": null,
                      "source_surface": "threads.search",
                      "content_status": "detail_enriched"
                    }
                  ],
                  "raw_trace": {"steps": ["search-detail"]},
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

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import (
    ARTIFACTS_DIRNAME,
    ExplorationStore,
    ExplorationStrategyRunner,
)
from contents_hub.lens_inbox import query_lens_inbox
from contents_hub.lenses import (
    evaluate_exploration_preview_lenses,
    evaluate_exploration_run_lenses,
)
from contents_hub.models import FetchedItem
from contents_hub.runners import get_default_runner, set_default_runner
from contents_hub.tools.storage import persist_raw_sync


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    return cfg


def test_exploration_schema_is_separate_from_subscriptions(vault):
    store = ExplorationStore(vault)

    exploration = store.create_draft(
        display_name="AI builders on Threads",
        original_request="Find thoughtful Threads posts from indie AI builders",
        target_surfaces=["threads.search"],
        lens_ids=["ai"],
    )

    with get_db(vault) as conn:
        sub_count = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        row = conn.execute(
            """SELECT display_name, original_request, target_surfaces, lens_ids,
                      status, approved_strategy_version_id
               FROM explorations WHERE id = ?""",
            (exploration.id,),
        ).fetchone()

    assert sub_count == 0
    assert row["display_name"] == "AI builders on Threads"
    assert row["original_request"] == (
        "Find thoughtful Threads posts from indie AI builders"
    )
    assert row["target_surfaces"] == '["threads.search"]'
    assert row["lens_ids"] == '["ai"]'
    assert row["status"] == "draft"
    assert row["approved_strategy_version_id"] is None


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


def _seed_lens(
    cfg: WikiConfig,
    lens_id: str,
    *,
    keywords: list[str] | None = None,
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
                json.dumps(keywords or []),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()


def test_validation_attempts_keep_history_and_file_backed_trace(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Frontend leads",
        original_request="Find senior frontend engineers discussing agents",
        target_surfaces=["threads.feed", "linkedin.search"],
        lens_ids=["frontend"],
    )

    first = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="failed",
        strategy_snapshot={"surface": "threads.feed", "limit": 3},
        process_summary="Opened feed and found no matching posts.",
        raw_trace={"steps": ["open", "scroll"]},
        preview_items=[],
        preview_lens_matches=[],
        error="no candidates",
        chromux_session_ids=["cx-1"],
        finished_at="2026-05-14T00:00:01+00:00",
    )
    second = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"surface": "linkedin.search", "limit": 5},
        process_summary="Searched LinkedIn and sampled relevant posts.",
        raw_trace="raw action transcript",
        preview_items=[{"url": "https://example.test/post", "title": "Post"}],
        preview_lens_matches=[{"url": "https://example.test/post", "lens": "frontend"}],
        chromux_session_ids=["cx-2", "cx-3"],
        finished_at="2026-05-14T00:01:00+00:00",
    )

    assert first.attempt_number == 1
    assert second.attempt_number == 2
    assert second.strategy_snapshot["surface"] == "linkedin.search"
    assert second.preview_items == [
        {"url": "https://example.test/post", "title": "Post"}
    ]
    assert second.preview_lens_matches == [
        {"url": "https://example.test/post", "lens": "frontend"}
    ]
    assert second.chromux_session_ids == ["cx-2", "cx-3"]
    assert second.raw_trace_artifact_path is not None
    assert second.raw_trace_artifact_path.startswith(f"{ARTIFACTS_DIRNAME}/")
    assert (vault.meta_path / second.raw_trace_artifact_path).read_text(
        encoding="utf-8"
    ) == "raw action transcript"

    with get_db(vault) as conn:
        assert (
            conn.execute(
                """SELECT COUNT(*) FROM exploration_validation_attempts
                   WHERE exploration_id = ?""",
                (exploration.id,),
            ).fetchone()[0]
            == 2
        )
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


def test_exploration_preview_lens_matches_are_artifact_only(vault):
    _seed_lens(vault, "ai", keywords=["agent"])
    _seed_lens(vault, "disabled", keywords=["agent"], enabled=False)
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Agent builders",
        original_request="Find posts about agent builders",
        target_surfaces=["threads.search"],
        lens_ids=["ai", "disabled", "missing"],
    )
    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "id": 1,
                            "summary": "Agent builder launch note.",
                            "bullets": ["Mentions agent workflows."],
                        }
                    ]
                }
            )
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        matches = asyncio.run(
            evaluate_exploration_preview_lenses(
                vault,
                exploration.id,
                [
                    {
                        "url": "https://threads.test/post/1",
                        "title": "Agent builder note",
                        "summary": "A useful agent workflow update.",
                    }
                ],
            )
        )
    finally:
        set_default_runner(original)

    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "agent builders"},
        preview_items=[{"url": "https://threads.test/post/1"}],
        preview_lens_matches=matches,
    )

    assert len(runner.prompts) == 1
    assert attempt.preview_lens_matches == [
        {
            "candidate_index": 0,
            "url": "https://threads.test/post/1",
            "title": "Agent builder note",
            "lens_id": "ai",
            "summary": "Agent builder launch note.",
            "bullets": ["Mentions agent workflows."],
        }
    ]
    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


def test_approved_strategy_versions_are_append_only_and_runs_reference_version(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Agent infra",
        original_request="Find posts about agent infrastructure",
        target_surfaces=["threads.search"],
        lens_ids=["ai"],
    )
    attempt_one = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "agent infra", "limit": 3},
    )
    version_one = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt_one.id,
    )
    attempt_two = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "agent infra OR browser agents", "limit": 5},
    )
    version_two = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt_two.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=version_two.id,
        status="ok",
        items_found=4,
        items_inserted=3,
        raw_trace={"actions": ["search", "extract"]},
        chromux_session_ids=["run-cx"],
        finished_at="2026-05-14T00:02:00+00:00",
    )

    updated = store.get(exploration.id)
    assert version_one.version == 1
    assert version_two.version == 2
    assert updated.status == "registered"
    assert updated.approved_strategy_version_id == version_two.id
    assert run.strategy_version_id == version_two.id
    assert run.items_found == 4
    assert run.items_inserted == 3
    assert run.chromux_session_ids == ["run-cx"]
    assert run.raw_trace_artifact_path is not None
    assert (vault.meta_path / run.raw_trace_artifact_path).exists()

    with pytest.raises(ValueError):
        other = store.create_draft(
            display_name="Other",
            original_request="Find other posts",
        )
        store.record_run(
            exploration_id=other.id,
            strategy_version_id=version_two.id,
            status="ok",
        )


def test_trace_artifacts_are_deleted_with_attempt_run_or_exploration(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Cleanup",
        original_request="Find cleanup posts",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "cleanup"},
        raw_trace="validation trace",
    )
    version = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=version.id,
        status="ok",
        raw_trace="run trace",
    )
    attempt_path = vault.meta_path / attempt.raw_trace_artifact_path
    run_path = vault.meta_path / run.raw_trace_artifact_path

    assert attempt_path.exists()
    assert run_path.exists()
    assert store.delete_validation_attempt(attempt.id) is True
    assert not attempt_path.exists()
    assert run_path.exists()
    assert store.delete_run(run.id) is True
    assert not run_path.exists()

    new_attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "cleanup v2"},
        raw_trace="another validation trace",
    )
    new_version = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=new_attempt.id,
    )
    new_run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=new_version.id,
        status="ok",
        raw_trace="another run trace",
    )
    new_path = vault.meta_path / new_attempt.raw_trace_artifact_path
    new_run_path = vault.meta_path / new_run.raw_trace_artifact_path
    assert new_path.exists()
    assert new_run_path.exists()
    assert store.delete_exploration(exploration.id) is True
    assert not new_path.exists()
    assert not new_run_path.exists()


def test_exploration_run_items_converge_on_existing_subscription_raw_item(vault):
    store = ExplorationStore(vault)
    now = datetime.now(timezone.utc).isoformat()
    with get_db(vault) as conn:
        conn.execute(
            """INSERT INTO subscriptions
               (url, title, source_type, status, schedule_interval_minutes,
                default_lens_ids, config, created_at, updated_at)
               VALUES ('https://source.test/', 'Source', 'webpage', 'active',
                       60, '[]', '{}', ?, ?)""",
            (now, now),
        )
        sub_id = conn.execute("SELECT id FROM subscriptions").fetchone()[0]
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, origin, priority, status, subscription_id,
                content_summary, collected_at, updated_at)
               VALUES ('https://example.test/post', 'Existing', 'Keep body',
                       'subscription', 50, 'raw', ?, 'Keep summary', ?, ?)""",
            (sub_id, now, now),
        )
        raw_item_id = conn.execute("SELECT id FROM raw_items").fetchone()[0]
        conn.execute(
            """INSERT INTO lenses (id, name, description, keywords, enabled,
                                  created_at, updated_at)
               VALUES ('ai', 'AI', '', '[]', 1, ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO raw_item_lenses
               (raw_item_id, lens_id, summary, bullets_json)
               VALUES (?, 'ai', 'Relevant', '["agent"]')""",
            (raw_item_id,),
        )
        conn.commit()

    exploration = store.create_draft(
        display_name="AI builders",
        original_request="Find AI builder posts",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "ai builders"},
    )
    strategy = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=strategy.id,
        status="ok",
    )

    result = store.persist_run_items(
        exploration_id=exploration.id,
        run_id=run.id,
        items=[
            FetchedItem(
                url="https://example.test/post?utm_source=threads#frag",
                title="Duplicate",
                summary="New summary",
                content_html="New body",
            )
        ],
    )

    assert result.inserted == 0
    assert result.skipped == 1
    assert result.raw_item_ids == (raw_item_id,)
    with get_db(vault) as conn:
        rows = conn.execute("SELECT * FROM raw_items").fetchall()
        assert len(rows) == 1
        assert rows[0]["origin"] == "subscription"
        assert rows[0]["body"] == "Keep body"
        discoveries = conn.execute(
            """SELECT owner_type, owner_id, owner_label, owner_run_id,
                      exploration_id, strategy_version, deleted_at
               FROM raw_item_discoveries
               WHERE raw_item_id = ?""",
            (raw_item_id,),
        ).fetchall()
        assert [d["owner_type"] for d in discoveries] == ["exploration_run"]
        assert discoveries[0]["owner_id"] == run.id
        assert discoveries[0]["owner_label"] == "AI builders"
        assert discoveries[0]["owner_run_id"] == run.id
        assert discoveries[0]["exploration_id"] == exploration.id
        assert discoveries[0]["strategy_version"] == strategy.version
        assert discoveries[0]["deleted_at"] is None

        inbox = query_lens_inbox(conn, sources_dirname="sources", status="raw")
        assert inbox["candidate_count"] == 1
        assert inbox["candidates"][0].id == raw_item_id


def test_exploration_run_items_store_exploration_origin_without_subscription(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Research leads",
        original_request="Find research leads",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "research"},
    )
    strategy = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=strategy.id,
        status="ok",
    )

    result = store.persist_run_items(
        exploration_id=exploration.id,
        run_id=run.id,
        items=[
            FetchedItem(
                url="https://research.test/item",
                title="Research item",
                summary="Summary",
                content_html="Body",
            )
        ],
    )

    assert result.inserted == 1
    with get_db(vault) as conn:
        row = conn.execute("SELECT * FROM raw_items").fetchone()
        assert row["origin"] == "exploration"
        assert row["subscription_id"] is None
        attribution = conn.execute(
            "SELECT owner_type, owner_id FROM raw_item_discoveries"
        ).fetchone()
    assert attribution["owner_type"] == "exploration_run"
    assert attribution["owner_id"] == run.id


def test_registered_exploration_manual_run_persists_checkpoint_items_on_timeout(vault):
    class _CheckpointTimeoutRunner:
        def __init__(self):
            self.prompts: list[str] = []

        async def run(self, prompt, *, max_turns=30, timeout=600.0):
            self.prompts.append(prompt)
            checkpoint = next(
                line
                for line in prompt.splitlines()
                if line.endswith(".jsonl")
                and f"{ARTIFACTS_DIRNAME}/run-" in line.replace("\\", "/")
            )
            with open(checkpoint, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "url": "https://threads.test/checkpoint/1",
                            "title": "Checkpoint item",
                            "summary": "Accepted before timeout.",
                            "source_surface": "threads.search",
                        }
                    )
                    + "\n"
                )
            raise asyncio.TimeoutError()

    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Checkpoint run",
        original_request="Find useful implementation posts",
        target_surfaces=["threads.search"],
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "implementation posts"},
    )
    store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    runner = _CheckpointTimeoutRunner()

    run = asyncio.run(
        ExplorationStrategyRunner(vault, runner=runner).run_registered(
            exploration.id,
            timeout=1,
        )
    )

    assert run.status == "partial"
    assert run.items_found == 1
    assert run.items_inserted == 1
    assert run.error == "manual run timed out after 1s"
    assert "Incremental checkpointing is required" in runner.prompts[0]
    with get_db(vault) as conn:
        raw_item = conn.execute("SELECT title, url FROM raw_items").fetchone()
        discovery = conn.execute(
            "SELECT owner_type, owner_run_id FROM raw_item_discoveries"
        ).fetchone()
    assert raw_item["title"] == "Checkpoint item"
    assert raw_item["url"] == "https://threads.test/checkpoint/1"
    assert discovery["owner_type"] == "exploration_run"
    assert discovery["owner_run_id"] == run.id


def test_exploration_run_lenses_write_lens_inbox_metadata_shape(vault):
    _seed_lens(vault, "ai", keywords=["agent"])
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Agent research",
        original_request="Find agent research posts",
        lens_ids=["ai"],
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "agent research"},
    )
    strategy = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=strategy.id,
        status="ok",
    )
    result = store.persist_run_items(
        exploration_id=exploration.id,
        run_id=run.id,
        items=[
            FetchedItem(
                url="https://research.test/agent",
                title="Agent research item",
                summary="Agent systems research summary.",
                content_html="Agent systems research body.",
            )
        ],
    )
    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "id": result.raw_item_ids[0],
                            "summary": "Agent systems research summary.",
                            "bullets": ["Preserves Lens-specific metadata."],
                        }
                    ]
                }
            )
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        inserted = asyncio.run(
            evaluate_exploration_run_lenses(
                vault,
                exploration.id,
                result.raw_item_ids,
            )
        )
    finally:
        set_default_runner(original)

    assert inserted == 1
    with get_db(vault) as conn:
        row = conn.execute(
            """SELECT raw_item_id, lens_id, summary, bullets_json
               FROM raw_item_lenses"""
        ).fetchone()
        assert row["raw_item_id"] == result.raw_item_ids[0]
        assert row["lens_id"] == "ai"
        assert row["summary"] == "Agent systems research summary."
        assert json.loads(row["bullets_json"]) == [
            "Preserves Lens-specific metadata."
        ]
        inbox = query_lens_inbox(conn, sources_dirname="sources", status="raw")
        assert inbox["candidate_count"] == 1
        candidate = inbox["candidates"][0]
        assert candidate.subscription_id is None
        assert candidate.lenses[0].id == "ai"
        assert candidate.lenses[0].summary == "Agent systems research summary."
        assert candidate.lenses[0].bullets == (
            "Preserves Lens-specific metadata.",
        )


def test_deleting_exploration_preserves_raw_items_and_tombstones_attribution(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Public label",
        original_request="Sensitive full request that must not be tombstoned",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="succeeded",
        strategy_snapshot={"query": "sensitive"},
    )
    strategy = store.approve_strategy(
        exploration_id=exploration.id,
        validation_attempt_id=attempt.id,
    )
    run = store.record_run(
        exploration_id=exploration.id,
        strategy_version_id=strategy.id,
        status="ok",
    )
    result = store.persist_run_items(
        exploration_id=exploration.id,
        run_id=run.id,
        items=[
            FetchedItem(
                url="https://audit.test/item",
                title="Audit item",
                summary="Summary",
                content_html="Body",
            )
        ],
    )
    raw_item_id = result.raw_item_ids[0]
    with get_db(vault) as conn:
        conn.execute(
            "UPDATE raw_items SET status = 'promoted', body = 'Preserved' WHERE id = ?",
            (raw_item_id,),
        )
        conn.commit()

    assert store.delete_exploration(exploration.id) is True

    with get_db(vault) as conn:
        raw = conn.execute("SELECT status, body FROM raw_items WHERE id = ?", (raw_item_id,)).fetchone()
        assert raw["status"] == "promoted"
        assert raw["body"] == "Preserved"
        tombstone = conn.execute(
            """SELECT owner_type, owner_id, owner_label, owner_run_id,
                      deleted_owner_id, deleted_owner_label, deleted_at,
                      discovered_at, run_at, strategy_version
               FROM raw_item_discoveries
               WHERE raw_item_id = ?""",
            (raw_item_id,),
        ).fetchone()
        assert tombstone["owner_type"] == "exploration_run"
        assert tombstone["owner_id"] is None
        assert tombstone["owner_label"] == ""
        assert tombstone["owner_run_id"] == run.id
        assert tombstone["deleted_owner_id"] == exploration.id
        assert tombstone["deleted_owner_label"] == "Public label"
        assert tombstone["deleted_at"] is not None
        assert tombstone["discovered_at"]
        assert tombstone["run_at"]
        assert tombstone["strategy_version"] == strategy.version
        assert "Sensitive full request" not in dict(tombstone).values()


class _ExplorationRunnerStub:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def run(self, prompt: str, *, max_turns: int = 30, timeout: float = 600.0):
        self.calls.append(
            {"prompt": prompt, "max_turns": max_turns, "timeout": timeout}
        )
        if not self.responses:
            raise AssertionError("unexpected runner call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.asyncio
async def test_strategy_runner_compiles_without_subscription_recipe_fields(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Threads AI builders",
        original_request="Find thoughtful Threads posts from AI founders",
        target_surfaces=["threads.feed"],
        lens_ids=["ai"],
    )
    runner = _ExplorationRunnerStub(
        """
        {
          "target_surfaces": ["threads.feed", "threads.search"],
          "collection_approach": "Open the home feed, then run a search.",
          "candidate_selection": "Keep founder posts with concrete lessons.",
          "extraction_approach": "Extract permalink, author, text, and date.",
          "stop_limits": {
            "max_items": 4,
            "max_pages": 2,
            "max_scrolls": 3,
            "timeout_seconds": 90
          },
          "lens_alignment_notes": "Prefer items matching the ai Lens.",
          "recipe_id": "must-not-persist"
        }
        """
    )

    strategy = await ExplorationStrategyRunner(vault, runner=runner).compile_strategy(
        exploration
    )
    snapshot = strategy.as_dict()

    assert runner.calls
    assert "recipe_base" in runner.calls[0]["prompt"]
    assert snapshot["target_surfaces"] == ["threads.feed", "threads.search"]
    assert snapshot["collection_approach"] == "Open the home feed, then run a search."
    assert snapshot["candidate_selection"] == "Keep founder posts with concrete lessons."
    assert snapshot["extraction_approach"] == "Extract permalink, author, text, and date."
    assert snapshot["stop_limits"]["max_items"] == 4
    assert snapshot["lens_alignment_notes"] == "Prefer items matching the ai Lens."
    assert "recipe_id" not in snapshot


@pytest.mark.asyncio
async def test_validate_draft_records_bounded_preview_as_artifact_only(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Threads operators",
        original_request="Find operator posts about AI workflow design",
        target_surfaces=["threads.search"],
        lens_ids=["ops"],
    )
    runner = _ExplorationRunnerStub(
        """
        {
          "target_surfaces": ["threads.search"],
          "collection_approach": "Search Threads for AI workflow design.",
          "candidate_selection": "Keep concrete operator writeups.",
          "extraction_approach": "Extract post URL, title, and rationale.",
          "stop_limits": {
            "max_items": 2,
            "max_pages": 1,
            "max_scrolls": 1,
            "timeout_seconds": 60
          },
          "lens_alignment_notes": "Compare against ops Lens."
        }
        """,
        """
        {
          "process_summary": "Searched Threads, sampled two posts, stopped at item cap.",
          "preview_items": [
            {"url": "https://threads.test/a", "title": "A"},
            {"url": "https://threads.test/b", "title": "B"},
            {"url": "https://threads.test/c", "title": "C"}
          ],
          "preview_lens_matches": [
            {"url": "https://threads.test/a", "lens_id": "ops", "summary": "fits"}
          ],
          "raw_trace": {"steps": ["search", "open", "extract"]},
          "chromux_session_ids": ["explore-1"],
          "error": ""
        }
        """,
    )

    attempt = await ExplorationStrategyRunner(
        vault,
        store=store,
        runner=runner,
    ).validate_draft(exploration.id)

    assert len(runner.calls) == 2
    assert runner.calls[1]["max_turns"] == 4
    assert runner.calls[1]["timeout"] == 60.0
    assert attempt.status == "succeeded"
    assert attempt.strategy_snapshot["target_surfaces"] == ["threads.search"]
    assert attempt.strategy_snapshot["stop_limits"]["max_items"] == 2
    assert [item["url"] for item in attempt.preview_items] == [
        "https://threads.test/a",
        "https://threads.test/b",
    ]
    assert attempt.preview_lens_matches == [
        {"url": "https://threads.test/a", "lens_id": "ops", "summary": "fits"}
    ]
    assert attempt.chromux_session_ids == ["explore-1"]
    assert attempt.raw_trace_artifact_path is not None
    assert (vault.meta_path / attempt.raw_trace_artifact_path).exists()

    with get_db(vault) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_validate_draft_failure_does_not_mark_other_explorations_failed(vault):
    store = ExplorationStore(vault)
    failing = store.create_draft(
        display_name="Failing",
        original_request="Find failing posts",
    )
    other = store.create_draft(
        display_name="Other",
        original_request="Find unrelated posts",
        status="registered",
    )
    runner = _ExplorationRunnerStub(RuntimeError("agent unavailable"))

    attempt = await ExplorationStrategyRunner(
        vault,
        store=store,
        runner=runner,
    ).validate_draft(failing.id)

    assert attempt.status == "failed"
    assert attempt.error == "agent unavailable"
    assert store.get(failing.id).status == "draft"
    assert store.get(other.id).status == "registered"


def test_subscription_persistence_records_discovery_attribution(vault):
    now = datetime.now(timezone.utc)
    with get_db(vault) as conn:
        conn.execute(
            """INSERT INTO subscriptions
               (url, title, source_type, status, schedule_interval_minutes,
                default_lens_ids, config, created_at, updated_at)
               VALUES ('https://source.test/', 'Source', 'webpage', 'active',
                       60, '[]', '{}', ?, ?)""",
            (now.isoformat(), now.isoformat()),
        )
        sub_id = conn.execute("SELECT id FROM subscriptions").fetchone()[0]
        summary = persist_raw_sync(
            conn,
            [
                FetchedItem(
                    url="https://source.test/a",
                    title="A",
                    summary="Summary",
                    published_at=now,
                )
            ],
            sub_id,
        )
        duplicate = persist_raw_sync(
            conn,
            [
                FetchedItem(
                    url="https://source.test/a?utm_campaign=x",
                    title="A again",
                    summary="Summary",
                    published_at=now,
                )
            ],
            sub_id,
        )
        conn.commit()

        assert summary["inserted"] == 1
        assert duplicate["skipped"] == 1
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]
        discovery_count = conn.execute(
            "SELECT COUNT(*) FROM raw_item_discoveries"
        ).fetchone()[0]
        assert raw_count == 1
        assert discovery_count == 1


def test_v8_database_migrates_to_exploration_schema(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    conn = init_db(cfg)
    conn.execute("UPDATE schema_version SET version = 8")
    conn.execute("DROP TABLE explorations")
    conn.execute("DROP TABLE exploration_validation_attempts")
    conn.execute("DROP TABLE exploration_strategy_versions")
    conn.execute("DROP TABLE exploration_runs")
    conn.commit()
    conn.close()

    migrated = init_db(cfg)
    try:
        assert (
            migrated.execute("SELECT version FROM schema_version").fetchone()[0] == 10
        )
        tables = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "explorations",
            "exploration_validation_attempts",
            "exploration_strategy_versions",
            "exploration_runs",
            "raw_item_discoveries",
        }.issubset(tables)
    finally:
        migrated.close()


def test_deleting_missing_or_unsafe_artifact_reference_is_noop(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Unsafe",
        original_request="Find unsafe path cases",
    )
    attempt = store.record_validation_attempt(
        exploration_id=exploration.id,
        status="failed",
        strategy_snapshot={},
        raw_trace="trace",
    )

    with get_db(vault) as conn:
        conn.execute(
            """UPDATE exploration_validation_attempts
               SET raw_trace_artifact_path = '../outside.json'
               WHERE id = ?""",
            (attempt.id,),
        )
        conn.commit()

    assert store.delete_validation_attempt(attempt.id) is True


def test_schema_requires_run_strategy_version(vault):
    store = ExplorationStore(vault)
    exploration = store.create_draft(
        display_name="Bad run",
        original_request="Find bad run cases",
    )

    with pytest.raises(sqlite3.IntegrityError):
        with get_db(vault) as conn:
            conn.execute(
                """INSERT INTO exploration_runs
                   (exploration_id, strategy_version_id, status, started_at,
                    created_at, updated_at)
                   VALUES (?, ?, 'running', 'now', 'now', 'now')""",
                (exploration.id, 999),
            )

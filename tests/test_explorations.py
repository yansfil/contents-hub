from __future__ import annotations

import sqlite3

import pytest

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import ARTIFACTS_DIRNAME, ExplorationStore


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
            migrated.execute("SELECT version FROM schema_version").fetchone()[0] == 9
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

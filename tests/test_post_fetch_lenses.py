from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from contents_hub.api import collect_all_due, fetch_subscription
from contents_hub.cli import main as cli_main
from contents_hub.config import WikiConfig
from contents_hub.db import init_db
from contents_hub.lenses import evaluate_post_fetch_lenses
from contents_hub.models import FetchResult
from contents_hub.runners import get_default_runner, set_default_runner
from contents_hub.subscriptions import SubscriptionStore


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


class _FailingRunner:
    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        raise RuntimeError("missing provider")


def _cfg(tmp_path: Path) -> WikiConfig:
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    return cfg


def _seed_lens(
    cfg: WikiConfig,
    lens_id: str,
    *,
    name: str = "",
    description: str = "",
    keywords: list[str] | None = None,
    enabled: bool = True,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """INSERT INTO lenses
               (id, name, description, keywords, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                lens_id,
                name or lens_id,
                description,
                json.dumps(keywords or []),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()


def _fetch_runner_for(
    *,
    title: str,
    body: str,
    url: str = "https://example.com/post-1",
    lens_response: str | None = None,
) -> _SequenceRunner:
    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    responses = [
        json.dumps(
            {
                "items": [
                    {
                        "url": url,
                        "title_hint": title,
                    }
                ],
                "errors": [],
                "failure_reason": None,
            }
        ),
        json.dumps(
            {
                "items": [
                    {
                        "url": url,
                        "title": title,
                        "summary": body[:40],
                        "body_markdown": body,
                        "published_at": published,
                        "body_status": "full",
                    }
                ],
                "errors": [],
                "failure_reason": None,
            }
        ),
    ]
    if lens_response is not None:
        responses.append(lens_response)
    return _SequenceRunner(responses)


def test_fetch_subscription_records_enabled_default_lens_matches_for_new_items(
    tmp_path,
):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai", keywords=["artificial intelligence", "machine learning"])
    _seed_lens(cfg, "disabled", keywords=["machine learning"], enabled=False)
    _seed_lens(cfg, "non-default", keywords=["machine learning"])

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["ai", "disabled", "missing-lens"],
    )
    runner = _fetch_runner_for(
        title="Machine learning systems update",
        body="Practical notes on artificial intelligence evaluation.",
        lens_response=json.dumps(
            {
                "matches": [
                    {
                        "id": 1,
                        "summary": "Machine learning evaluation update.",
                        "bullets": [
                            "Covers AI evaluation.",
                            "Relevant to the AI lens.",
                            "Captures a model-quality concern.",
                            "Mentions operational measurement.",
                            "Highlights review workflow.",
                            "Preserves the final implementation note.",
                        ],
                    }
                ]
            }
        ),
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert len(runner.prompts) == 3
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        raw_rows = conn.execute(
            "SELECT id, status FROM raw_items WHERE subscription_id = ?",
            (int(sub.id),),
        ).fetchall()
        lens_rows = conn.execute(
            "SELECT raw_item_id, lens_id, summary, bullets_json "
            "FROM raw_item_lenses ORDER BY lens_id",
        ).fetchall()

    assert len(raw_rows) == 1
    assert raw_rows[0][1] == "raw"
    assert lens_rows == [
        (
            raw_rows[0][0],
            "ai",
            "Machine learning evaluation update.",
            (
                '["Covers AI evaluation.", "Relevant to the AI lens.", '
                '"Captures a model-quality concern.", '
                '"Mentions operational measurement.", '
                '"Highlights review workflow.", '
                '"Preserves the final implementation note."]'
            ),
        )
    ]


def test_post_fetch_lens_records_exact_keyword_match_without_model_match(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "smoke", keywords=["Smoke"])
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, origin, priority, status, subscription_id,
                content_summary, metadata_json, published_at, collected_at, updated_at)
               VALUES (?, ?, ?, 'subscription', 0, 'raw', ?, ?, '{}', ?, ?, ?)""",
            (
                "https://example.com/smoke",
                "Smoke RSS Item",
                "Smoke RSS item body for first launch.",
                int(sub.id),
                "Smoke RSS item body for first launch.",
                now,
                now,
                now,
            ),
        )
        raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

    runner = _SequenceRunner(['{"matches": []}'])
    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        inserted = asyncio.run(evaluate_post_fetch_lenses(cfg, int(sub.id), [raw_id]))
    finally:
        set_default_runner(original)

    assert inserted == 1
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT lens_id, summary FROM raw_item_lenses WHERE raw_item_id = ?",
            (raw_id,),
        ).fetchone()
    assert row == ("smoke", "Smoke RSS item body for first launch.")


def test_post_fetch_lens_keeps_exact_keyword_match_when_classifier_fails(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "smoke", keywords=["Smoke"])
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, origin, priority, status, subscription_id,
                content_summary, metadata_json, published_at, collected_at, updated_at)
               VALUES (?, ?, ?, 'subscription', 0, 'raw', ?, ?, '{}', ?, ?, ?)""",
            (
                "https://example.com/smoke",
                "Smoke RSS Item",
                "Smoke RSS item body for first launch.",
                int(sub.id),
                "Smoke RSS item body for first launch.",
                now,
                now,
                now,
            ),
        )
        raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

    original = get_default_runner()
    try:
        set_default_runner(_FailingRunner())  # type: ignore[arg-type]
        inserted = asyncio.run(evaluate_post_fetch_lenses(cfg, int(sub.id), [raw_id]))
    finally:
        set_default_runner(original)

    assert inserted == 1
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT lens_id, summary FROM raw_item_lenses WHERE raw_item_id = ?",
            (raw_id,),
        ).fetchone()
    assert row == ("smoke", "Smoke RSS item body for first launch.")


def test_collect_all_due_records_lenses_for_new_items_without_changing_tick_counts(
    tmp_path,
):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai", keywords=["llm"])
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["ai"],
    )
    runner = _fetch_runner_for(
        title="LLM evaluation digest",
        body="A weekly LLM systems note.",
        lens_response=json.dumps(
            {
                "matches": [
                    {
                        "id": 1,
                        "summary": "Weekly LLM systems note.",
                        "bullets": ["LLM-focused update."],
                    }
                ]
            }
        ),
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(collect_all_due(cfg))
    finally:
        set_default_runner(original)

    assert result.total == 1
    assert result.new == 1
    assert result.errors == 0
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            """SELECT ril.lens_id
               FROM raw_item_lenses ril
               JOIN raw_items ri ON ri.id = ril.raw_item_id
               WHERE ri.subscription_id = ?""",
            (int(sub.id),),
        ).fetchone()
    assert row == ("ai",)


def test_duplicate_fetch_does_not_re_evaluate_existing_raw_item_for_lenses(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai", keywords=["llm"])
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["ai"],
    )
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, subscription_id, collected_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "https://example.com/post-1",
                "Existing LLM item",
                "already here",
                int(sub.id),
                now,
                now,
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "url": "https://example.com/post-1/",
                            "title_hint": "Existing LLM item",
                        }
                    ],
                    "errors": [],
                    "failure_reason": None,
                }
            )
        ]
    )
    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert result.items == []
    assert len(runner.prompts) == 1
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


def test_keyword_lens_uses_semantic_classifier_not_substring_gate(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(
        cfg,
        "workflow",
        name="AI coding workflow",
        description="Practical AI-assisted coding workflows and agentic development practice",
        keywords=["AI 코딩"],
    )
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["workflow"],
    )
    runner = _fetch_runner_for(
        title="알고리즘이 판단했는데요: AI 코드 시대의 리뷰",
        body="개발자가 생성형 모델로 앱을 만들고 검증 루틴을 설계하는 사례를 다룬 글.",
        lens_response=json.dumps(
            {
                "matches": [
                    {
                        "id": 1,
                        "summary": "AI 코드 시대의 개발 검증 흐름을 다룬 글.",
                        "bullets": ["AI 앱 개발", "검증 루틴", "코드 리뷰 workflow"],
                    }
                ]
            }
        ),
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert len(runner.prompts) == 3
    assert "keywords" in runner.prompts[-1].lower()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT lens_id, summary FROM raw_item_lenses"
        ).fetchone()
    assert row == ("workflow", "AI 코드 시대의 개발 검증 흐름을 다룬 글.")


def test_lens_classifier_failure_does_not_roll_back_raw_fetch_success(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(
        cfg, "semantic", name="AI research", description="Items about AI research"
    )
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["semantic"],
    )
    # Only LIST and CONTENT responses are supplied. If the no-keyword Lens path
    # asks the runner to classify semantically, the third call raises; the API
    # must isolate that optional Lens failure from raw persistence.
    runner = _fetch_runner_for(title="AI paper", body="A research summary.")

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


def test_semantic_lens_ignores_classifier_ids_outside_loaded_raw_items(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(
        cfg, "semantic", name="AI research", description="Items about AI research"
    )
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        lenses=["semantic"],
    )
    other_sub = store.add(
        url="https://other.example.com/feed.xml",
        title="Other Feed",
        source_type="rss.feed",
    )
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, subscription_id, collected_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "https://example.com/post-1",
                "Correct subscription item",
                "AI research",
                int(sub.id),
                now,
                now,
            ),
        )
        correct_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO raw_items
               (url, title, body, subscription_id, collected_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "https://other.example.com/post-1",
                "Wrong subscription item",
                "AI research",
                int(other_sub.id),
                now,
                now,
            ),
        )
        wrong_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

    runner = _SequenceRunner(
        [
            json.dumps(
                {
                    "matches": [
                        {
                            "id": wrong_id,
                            "summary": "Wrong subscription summary.",
                            "bullets": ["Should be ignored."],
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
            evaluate_post_fetch_lenses(cfg, int(sub.id), [correct_id])
        )
    finally:
        set_default_runner(original)

    assert inserted == 0
    assert len(runner.prompts) == 1
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_item_lenses").fetchone()[0] == 0


def test_fetch_cli_stdout_remains_single_json_object(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    async def _fake_fetch(config, sub_ref, *, max_items=10):
        return FetchResult(ok=True, source_url="https://example.com/feed.xml", items=[])

    monkeypatch.setattr("contents_hub.cli.fetch_subscription", _fake_fetch)

    exit_code = cli_main(["--vault", str(tmp_path), "fetch", str(sub.id)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    assert payload == {
        "ok": True,
        "subscription_id": int(sub.id),
        "new_items": 0,
        "skipped": 0,
        "items": [],
        "error": None,
        "failure_reason": None,
    }


def test_fetch_all_cli_stdout_remains_single_json_object(monkeypatch, tmp_path, capsys):
    _cfg(tmp_path)

    async def _fake_collect_all_active(
        config,
        *,
        include_error=False,
        per_subscription_timeout_seconds=120.0,
        concurrency=1,
    ):
        assert include_error is True
        assert per_subscription_timeout_seconds == 120.0
        assert concurrency == 1
        return SimpleNamespace(
            total=2,
            new=0,
            skipped=2,
            errors=0,
            duration_seconds=0.123,
            per_subscription=[],
        )

    monkeypatch.setattr("contents_hub.cli.collect_all_active", _fake_collect_all_active)

    exit_code = cli_main(["--vault", str(tmp_path), "fetch-all"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    assert payload["ok"] is True
    assert payload["subscription_id"] == -1
    assert payload["total"] == 2
    assert payload["new_items"] == 0
    assert payload["skipped"] == 2


def test_fetch_all_cli_forwards_concurrency(monkeypatch, tmp_path, capsys):
    _cfg(tmp_path)

    async def _fake_collect_all_active(
        config,
        *,
        include_error=False,
        per_subscription_timeout_seconds=120.0,
        concurrency=1,
    ):
        assert include_error is True
        assert per_subscription_timeout_seconds == 5.0
        assert concurrency == 3
        return SimpleNamespace(
            total=0,
            new=0,
            skipped=0,
            errors=0,
            duration_seconds=0.0,
            per_subscription=[],
        )

    monkeypatch.setattr("contents_hub.cli.collect_all_active", _fake_collect_all_active)

    exit_code = cli_main(
        [
            "--vault",
            str(tmp_path),
            "fetch-all",
            "--timeout-per-sub",
            "5",
            "--concurrency",
            "3",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True

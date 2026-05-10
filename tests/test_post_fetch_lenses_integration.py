from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from llm_wiki.api import collect_all_due, fetch_subscription
from llm_wiki.cli import main as cli_main
from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.models import FetchResult
from llm_wiki.runners import get_default_runner, set_default_runner
from llm_wiki.subscriptions import SubscriptionStore


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


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


def _fetch_runner_for(*, title: str, body: str, url: str = "https://example.com/post-1") -> _SequenceRunner:
    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    return _SequenceRunner(
        [
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
    )


def test_fetch_subscription_records_enabled_default_lens_matches_for_new_items(tmp_path):
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
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert len(runner.prompts) == 2
    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        raw_rows = conn.execute(
            "SELECT id, status FROM raw_items WHERE subscription_id = ?",
            (int(sub.id),),
        ).fetchall()
        lens_rows = conn.execute(
            "SELECT raw_item_id, lens_id FROM raw_item_lenses ORDER BY lens_id",
        ).fetchall()

    assert len(raw_rows) == 1
    assert raw_rows[0][1] == "raw"
    assert lens_rows == [(raw_rows[0][0], "ai")]


def test_collect_all_due_records_lenses_for_new_items_without_changing_tick_counts(tmp_path):
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


def test_lens_classifier_failure_does_not_roll_back_raw_fetch_success(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "semantic", name="AI research", description="Items about AI research")
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

    monkeypatch.setattr("llm_wiki.cli.fetch_subscription", _fake_fetch)

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

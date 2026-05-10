from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

from llm_wiki.api import collect_all_due, fetch_subscription
from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.recipes import RecipeRegistry
from llm_wiki.runners import get_default_runner, set_default_runner
from llm_wiki.source_router import classify
from llm_wiki.source_types import SOURCE_TYPES
from llm_wiki.subscriptions import SubscriptionStore


class _StubRunner:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        return self.response


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


def test_classify_returns_canonical_source_type_and_recipe_pin():
    info = classify("https://www.youtube.com/@openai")

    assert info["source_type"] == "youtube.channel"
    assert info["recipe_id"] == "youtube.channel.default"
    assert info["recipe_version"] == 1
    assert info["execution_method"] == "feed"


def test_subscription_add_pins_default_recipe(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    assert sub.source_type == "rss.feed"
    assert sub.config["recipe_id"] == "rss.feed.default"
    assert sub.config["recipe_version"] == 1
    assert sub.config["fetch_method"] == "feed"
    assert RecipeRegistry.get_recipe(sub)


def test_every_catalog_source_type_has_a_seed_recipe():
    for spec in SOURCE_TYPES:
        sub = type(
            "S",
            (),
            {
                "url": "https://example.com",
                "source_type": spec.id,
                "config": {},
            },
        )()
        assert RecipeRegistry.get_recipe(sub), spec.id


def test_fetch_subscription_persists_items_with_catalog_recipe(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    runner = _StubRunner(
        """
        {
          "items": [
            {
              "url": "https://example.com/post-1",
              "title": "Post 1",
              "summary": "summary",
              "content": "body",
              "published_at": "%s"
            }
          ],
          "errors": []
        }
        """
        % published
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert result.items[0].source_type == "rss.feed"
    assert result.items[0].extra["fetch_method"] == "feed"
    assert runner.prompts
    assert "rss.feed.default" in store.get_by_id(sub.id).config["recipe_id"]

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT title, body, published_at FROM raw_items WHERE subscription_id = ?",
            (int(sub.id),),
        ).fetchone()

    assert row == ("Post 1", "body", published)


def test_fetch_subscription_diffs_before_content_fetch(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    existing_url = "https://example.com/post-1"

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "INSERT INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                existing_url,
                "Existing",
                "body",
                int(sub.id),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1/",
                  "title_hint": "Existing"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
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
    assert result.total_available == 1
    assert len(runner.prompts) == 1
    assert "LIST_STRATEGY 만" in runner.prompts[0]


def test_collect_all_due_diffs_before_content_fetch(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    existing_url = "https://example.com/post-1"

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "INSERT INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                existing_url,
                "Existing",
                "body",
                int(sub.id),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1/",
                  "title_hint": "Existing"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(collect_all_due(cfg))
    finally:
        set_default_runner(original)

    assert result.total == 1
    assert result.new == 0
    assert result.skipped == 1
    assert result.errors == 0
    assert len(runner.prompts) == 1
    assert "LIST_STRATEGY 만" in runner.prompts[0]


def test_fetch_subscription_content_fetches_only_new_list_items(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1",
                  "title_hint": "Post 1"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """,
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1",
                  "title": "Post 1",
                  "summary": "summary",
                  "body_markdown": "body",
                  "published_at": "%s",
                  "body_status": "full"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
            % published,
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert len(result.items) == 1
    assert len(runner.prompts) == 2
    assert "LIST_STRATEGY 만" in runner.prompts[0]
    assert "CONTENT_STRATEGY + METADATA" in runner.prompts[1]
    assert '"url": "https://example.com/post-1"' in runner.prompts[1]

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT title, body, published_at FROM raw_items WHERE subscription_id = ?",
            (int(sub.id),),
        ).fetchone()

    assert row == ("Post 1", "body", published)

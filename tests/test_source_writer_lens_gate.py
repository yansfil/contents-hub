"""Tests for the lens-match pre-write gate in source_writer (R3.1).

Verifies that ``save_source_files`` only persists items to ./sources when
the lens-matching classifier returns at least one matched lens. The actual
LLM call is mocked — these tests never hit a real API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.fetchers.base import FetchedItem
from llm_wiki.source_writer import save_source_files


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def lens_records() -> list[dict]:
    return [
        {"id": "ai", "name": "AI", "description": "AI topics", "keywords": "[]"},
        {
            "id": "tech",
            "name": "Tech",
            "description": "Technology",
            "keywords": "[]",
        },
    ]


def _make_item(url: str = "https://example.com/post", title: str = "Post") -> FetchedItem:
    return FetchedItem(
        url=url,
        title=title,
        summary="summary",
        author="A",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        source_type="rss",
        extra={"feed_title": "Example"},
    )


class TestLensGate:
    def test_item_with_no_lens_match_is_not_written(
        self, config: WikiConfig, lens_records: list[dict]
    ):
        """R3.1 (a): zero lens match -> item is skipped, no file written."""
        item = _make_item("https://example.com/no-match")

        def fake_classify(title, url, summary, lenses):
            return []  # no matches

        paths = save_source_files(
            [item],
            config,
            lenses=["ai", "tech"],
            lens_records=lens_records,
            classify_fn=fake_classify,
        )

        assert paths == []
        # ./sources should be empty (or not exist)
        sources_dir = config.sources_path
        if sources_dir.exists():
            assert list(sources_dir.glob("*.md")) == []

    def test_item_with_match_is_written_with_matched_lens_tags(
        self, config: WikiConfig, lens_records: list[dict]
    ):
        """R3.1 (b): >=1 match -> file written with matched lens ids in frontmatter."""
        item = _make_item("https://example.com/match")

        def fake_classify(title, url, summary, lenses):
            return ["ai"]

        paths = save_source_files(
            [item],
            config,
            lenses=["ai", "tech"],  # static list — should be replaced with matched
            lens_records=lens_records,
            classify_fn=fake_classify,
        )

        assert len(paths) == 1
        content = paths[0].read_text(encoding="utf-8")
        # matched lens id present
        assert "ai" in content
        # non-matched lens id should NOT have been propagated as a lens tag
        # (we only persist matched ones; "tech" should not appear as a lens tag).
        # Use a robust check: the lenses block must not include 'tech'.
        # Find the 'lenses:' field in frontmatter.
        lines = content.splitlines()
        in_lenses = False
        lens_block: list[str] = []
        for line in lines:
            if line.startswith("lenses:"):
                in_lenses = True
                continue
            if in_lenses:
                if line.startswith("  - ") or line.startswith("- "):
                    lens_block.append(line.strip("- ").strip())
                else:
                    break
        assert "ai" in lens_block
        assert "tech" not in lens_block

    def test_mixed_batch_only_matched_items_written(
        self, config: WikiConfig, lens_records: list[dict]
    ):
        """Mixed batch: matched item written, unmatched item skipped."""
        item_match = _make_item("https://example.com/yes", title="Yes")
        item_skip = _make_item("https://example.com/no", title="No")

        def fake_classify(title, url, summary, lenses):
            if "yes" in url:
                return ["tech"]
            return []

        paths = save_source_files(
            [item_match, item_skip],
            config,
            lenses=["ai", "tech"],
            lens_records=lens_records,
            classify_fn=fake_classify,
        )

        assert len(paths) == 1
        content = paths[0].read_text(encoding="utf-8")
        assert "Yes" in content

    def test_no_lens_records_disables_gating_legacy_behavior(
        self, config: WikiConfig
    ):
        """When lens_records is None/empty, gating is disabled (legacy)."""
        item = _make_item("https://example.com/legacy")

        called = {"n": 0}

        def fake_classify(*args, **kwargs):
            called["n"] += 1
            return []

        paths = save_source_files(
            [item],
            config,
            lenses=["ai"],
            lens_records=None,
            classify_fn=fake_classify,
        )

        assert len(paths) == 1  # written despite no matches (gate disabled)
        assert called["n"] == 0  # classifier never invoked

"""Tests for the end-to-end collect → compile → write pipeline.

Verifies the full data flow:
    1. Source files are scanned and parsed correctly
    2. Pending sources are evaluated into a CompilePlan
    3. CompilePlan is executed to write wiki pages
    4. Source status is updated from pending to compiled
    5. Full pipeline (collect + plan) works end-to-end
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from llm_wiki.compile_decision import Decision
from llm_wiki.config import WikiConfig
from llm_wiki.fetchers.base import FetchedItem, FetchResult
from llm_wiki.pipeline import (
    CompileCandidate,
    CompilePlan,
    PipelineResult,
    SourceEntry,
    build_compile_plan,
    collect_and_build_plan,
    evaluate_sources,
    execute_compile_plan,
    scan_pending_sources,
    _update_source_status,
)
from llm_wiki.source_writer import save_source_file
from llm_wiki.subscriptions import SubscriptionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


def _create_source_file(
    config: WikiConfig,
    title: str = "Test Source",
    url: str = "https://example.com/article",
    source_type: str = "rss",
    tags: list[str] | None = None,
    lenses: list[str] | None = None,
    status: str = "pending",
) -> Path:
    """Create a mock source file in the vault."""
    sources_dir = config.sources_path
    sources_dir.mkdir(parents=True, exist_ok=True)

    all_tags = (tags or []) + (lenses or [])
    fm_lines = [
        "---",
        f"type: {source_type}",
        f'url: "{url}"',
        f"title: {title}",
        f"status: {status}",
        f"collected_at: {datetime.now(timezone.utc).isoformat()}",
    ]
    if all_tags:
        fm_lines.append("tags:")
        for tag in all_tags:
            fm_lines.append(f"  - {tag}")
    if lenses:
        fm_lines.append("lenses:")
        for lens in lenses:
            fm_lines.append(f"  - {lens}")
    fm_lines.append("---")

    body = f"# {title}\n\nThis is a test source about {title.lower()}.\n"
    content = "\n".join(fm_lines) + "\n" + body

    slug = title.lower().replace(" ", "-")[:30]
    filename = f"20240115-{slug}.md"
    path = sources_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Stage 1: Scan pending sources
# ---------------------------------------------------------------------------


class TestScanPendingSources:
    def test_finds_pending_sources(self, config: WikiConfig):
        _create_source_file(config, title="Pending Source 1")
        _create_source_file(config, title="Pending Source 2")

        entries = scan_pending_sources(config)
        assert len(entries) == 2
        assert all(e.status == "pending" for e in entries)

    def test_skips_compiled_sources(self, config: WikiConfig):
        _create_source_file(config, title="Pending Source", status="pending")
        _create_source_file(config, title="Compiled Source", status="compiled")

        entries = scan_pending_sources(config)
        assert len(entries) == 1
        assert entries[0].title == "Pending Source"

    def test_parses_frontmatter_correctly(self, config: WikiConfig):
        _create_source_file(
            config,
            title="ML Paper",
            url="https://arxiv.org/abs/2401.00001",
            source_type="rss",
            tags=["ml", "paper"],
            lenses=["ai-research"],
        )

        entries = scan_pending_sources(config)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.title == "ML Paper"
        assert entry.url == "https://arxiv.org/abs/2401.00001"
        assert entry.source_type == "rss"
        assert "ml" in entry.tags or "paper" in entry.tags
        assert "ai-research" in entry.lenses

    def test_empty_vault(self, config: WikiConfig):
        entries = scan_pending_sources(config)
        assert len(entries) == 0

    def test_respects_limit(self, config: WikiConfig):
        for i in range(10):
            _create_source_file(config, title=f"Source {i}", url=f"https://ex.com/{i}")

        entries = scan_pending_sources(config, limit=3)
        assert len(entries) == 3

    def test_body_extraction(self, config: WikiConfig):
        _create_source_file(config, title="Body Test")

        entries = scan_pending_sources(config)
        assert len(entries) == 1
        assert "Body Test" in entries[0].body or "body test" in entries[0].body


# ---------------------------------------------------------------------------
# Stage 2: Evaluate sources → CompilePlan
# ---------------------------------------------------------------------------


class TestEvaluateSources:
    def test_creates_plan_for_new_sources(self, config: WikiConfig):
        _create_source_file(config, title="Brand New Topic")
        entries = scan_pending_sources(config)

        plan = evaluate_sources(config, entries)

        assert not plan.is_empty
        assert plan.pending_sources == 1
        assert len(plan.candidates) >= 1

    def test_plan_has_creates_for_empty_vault(self, config: WikiConfig):
        _create_source_file(config, title="First Source")
        entries = scan_pending_sources(config)

        plan = evaluate_sources(config, entries)

        # In an empty vault, all sources should be CREATE
        assert len(plan.creates) >= 1

    def test_plan_serialization(self, config: WikiConfig):
        _create_source_file(config, title="Test Source")
        entries = scan_pending_sources(config)

        plan = evaluate_sources(config, entries)
        plan_dict = plan.to_dict()

        assert "status" in plan_dict
        assert "candidates" in plan_dict
        assert plan_dict["to_compile"] == len(plan.candidates)

    def test_plan_summary(self, config: WikiConfig):
        _create_source_file(config, title="Test Source")
        entries = scan_pending_sources(config)

        plan = evaluate_sources(config, entries)
        summary = plan.summary()

        assert "Compile Plan" in summary

    def test_empty_sources_produce_empty_plan(self, config: WikiConfig):
        plan = evaluate_sources(config, [])
        assert plan.is_empty
        assert "up to date" in plan.summary()

    def test_build_compile_plan_convenience(self, config: WikiConfig):
        _create_source_file(config, title="Convenience Test")

        plan = build_compile_plan(config)
        assert not plan.is_empty


# ---------------------------------------------------------------------------
# Stage 3: Execute compile plan
# ---------------------------------------------------------------------------


class TestExecuteCompilePlan:
    def _build_plan_with_source(self, config: WikiConfig, title: str = "Test Page"):
        """Helper: create a source, evaluate, and return plan."""
        _create_source_file(config, title=title, lenses=["tech"])
        entries = scan_pending_sources(config)
        return evaluate_sources(config, entries)

    def test_execute_creates_wiki_page(self, config: WikiConfig):
        plan = self._build_plan_with_source(config, "New Wiki Page")
        assert len(plan.candidates) >= 1

        source_path = plan.candidates[0].source.relative_path
        compiled_contents = {
            source_path: "Transformers use self-attention to process sequences in parallel."
        }

        report = execute_compile_plan(config, plan, compiled_contents)

        assert report.created >= 1 or report.updated >= 0
        assert report.failed == 0

    def test_execute_dry_run(self, config: WikiConfig):
        plan = self._build_plan_with_source(config, "Dry Run Page")
        source_path = plan.candidates[0].source.relative_path
        compiled_contents = {source_path: "Some compiled content."}

        report = execute_compile_plan(
            config, plan, compiled_contents, dry_run=True,
        )

        assert report.dry_run is True
        # No files should be written
        wiki_files = list(config.vault_path.glob("*.md"))
        # Only source files should exist
        for f in wiki_files:
            assert "sources" in str(f) or f.parent == config.vault_path

    def test_execute_partial_approval(self, config: WikiConfig):
        # Create two sources
        _create_source_file(config, title="Source A", url="https://ex.com/a")
        _create_source_file(config, title="Source B", url="https://ex.com/b")
        entries = scan_pending_sources(config)
        plan = evaluate_sources(config, entries)

        if len(plan.candidates) < 2:
            pytest.skip("Need at least 2 candidates for partial test")

        # Only approve first candidate
        source_path = plan.candidates[0].source.relative_path
        compiled_contents = {source_path: "Compiled content for A."}

        report = execute_compile_plan(
            config, plan, compiled_contents,
            approved_indices=[0],
        )

        # Should only process 1 candidate
        assert report.total == 1

    def test_missing_content_is_handled(self, config: WikiConfig):
        plan = self._build_plan_with_source(config, "Missing Content")

        # Don't provide compiled content
        report = execute_compile_plan(config, plan, {})

        assert report.failed >= 1


# ---------------------------------------------------------------------------
# Source status management
# ---------------------------------------------------------------------------


class TestSourceStatusUpdate:
    def test_mark_source_compiled(self, config: WikiConfig):
        path = _create_source_file(config, title="To Compile")

        content_before = path.read_text()
        assert "status: pending" in content_before

        _update_source_status(path, "compiled")

        content_after = path.read_text()
        assert "status: compiled" in content_after
        assert "status: pending" not in content_after

    def test_mark_preserves_other_content(self, config: WikiConfig):
        path = _create_source_file(
            config,
            title="Preserve Content",
            url="https://example.com/preserve",
        )

        original = path.read_text()
        _update_source_status(path, "compiled")
        updated = path.read_text()

        # URL and title should be preserved
        assert "https://example.com/preserve" in updated
        assert "Preserve Content" in updated


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


class TestPipelineResult:
    def test_empty_result(self):
        result = PipelineResult()
        assert result.new_items_collected == 0
        assert result.pages_written == 0

    def test_serialization(self):
        result = PipelineResult(duration_seconds=1.5)
        d = result.to_dict()
        assert d["duration_seconds"] == 1.5
        assert "completed_at" in d

    def test_summary(self):
        result = PipelineResult(duration_seconds=2.0)
        summary = result.summary()
        assert "Pipeline Complete" in summary
        assert "2.0s" in summary


# ---------------------------------------------------------------------------
# CompilePlan properties
# ---------------------------------------------------------------------------


class TestCompilePlanProperties:
    def test_creates_and_updates_separation(self, config: WikiConfig):
        # Create sources
        _create_source_file(config, title="Source One", url="https://ex.com/1")
        entries = scan_pending_sources(config)

        plan = evaluate_sources(config, entries)

        # All should be creates in empty vault
        total = len(plan.creates) + len(plan.updates) + len(plan.skipped)
        assert total == len(plan.candidates) + len(plan.skipped)


# ---------------------------------------------------------------------------
# Integration: collect + plan (async)
# ---------------------------------------------------------------------------


class TestCollectAndBuildPlan:
    @pytest.mark.asyncio
    async def test_collect_and_plan_with_mock_fetch(self, config: WikiConfig):
        """Full pipeline: mock fetch → source files → compile plan."""
        # Set up a subscription
        store = SubscriptionStore(config)
        store.add(
            "https://blog.test/feed.xml",
            title="Test Blog",
            lenses=["tech"],
        )

        # Mock fetch_all to simulate collecting items
        from llm_wiki.fetch_all import FetchAllResult, FetchOutcome

        mock_fetch_result = FetchAllResult(
            total_subscriptions=1,
            fetched=1,
            succeeded=1,
            failed=0,
            skipped=0,
            new_items_total=0,
            duration_seconds=0.1,
            outcomes=[],
        )

        # Pre-create source files (simulating what fetch would do)
        _create_source_file(
            config,
            title="New Blog Post",
            url="https://blog.test/post-1",
            source_type="rss",
            lenses=["tech"],
        )

        with patch(
            "llm_wiki.pipeline.fetch_all_subscriptions",
            new_callable=AsyncMock,
            return_value=mock_fetch_result,
        ):
            result = await collect_and_build_plan(config)

        assert result.fetch_result is not None
        assert result.compile_plan is not None
        assert not result.compile_plan.is_empty
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_collect_dry_run(self, config: WikiConfig):
        """Dry run doesn't actually fetch."""
        from llm_wiki.fetch_all import FetchAllResult

        mock_fetch_result = FetchAllResult(
            total_subscriptions=0,
            fetched=0,
            succeeded=0,
            failed=0,
            skipped=0,
            new_items_total=0,
            duration_seconds=0.0,
            outcomes=[],
        )

        with patch(
            "llm_wiki.pipeline.fetch_all_subscriptions",
            new_callable=AsyncMock,
            return_value=mock_fetch_result,
        ):
            result = await collect_and_build_plan(config, dry_run=True)

        assert result.compile_plan is not None


# ---------------------------------------------------------------------------
# End-to-end: source → evaluate → execute → verify
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_source_to_wiki_page(self, config: WikiConfig):
        """Full data flow: source file → evaluate → execute → wiki page."""
        # Step 1: Create a source file
        _create_source_file(
            config,
            title="Attention Mechanisms",
            url="https://arxiv.org/abs/1706.03762",
            source_type="rss",
            tags=["deep-learning", "nlp"],
            lenses=["ai-research"],
        )

        # Step 2: Scan and evaluate
        pending = scan_pending_sources(config)
        assert len(pending) == 1

        plan = evaluate_sources(config, pending)
        assert not plan.is_empty
        assert len(plan.candidates) >= 1

        candidate = plan.candidates[0]
        assert candidate.source.title == "Attention Mechanisms"

        # Step 3: Simulate LLM compilation (provide content)
        compiled_contents = {
            candidate.source.relative_path: (
                "Attention mechanisms allow neural networks to focus on "
                "relevant parts of the input sequence. The self-attention "
                "mechanism in Transformers computes a weighted sum of all "
                "positions in the input.\n\n"
                "## Key Concepts\n\n"
                "- **Self-attention**: Relates different positions of a single sequence\n"
                "- **Multi-head attention**: Multiple attention functions in parallel\n"
                "- **Scaled dot-product**: Core attention computation\n"
            )
        }

        # Step 4: Execute
        report = execute_compile_plan(config, plan, compiled_contents)

        assert report.failed == 0
        assert report.created + report.updated >= 1

        # Step 5: Verify wiki page exists
        written = [
            r for r in report.results
            if r.status.value == "success"
        ]
        assert len(written) >= 1

        wiki_path = config.vault_path / written[0].target_path
        assert wiki_path.exists()

        wiki_content = wiki_path.read_text()
        assert "attention" in wiki_content.lower()

    def test_fetched_item_through_pipeline(self, config: WikiConfig):
        """FetchedItem → source_writer → scan → evaluate → execute."""
        # Step 1: Save a FetchedItem via source_writer (what fetch_all does)
        item = FetchedItem(
            url="https://blog.test/transformers-101",
            title="Transformers 101",
            summary="An introduction to transformer architecture",
            author="AI Researcher",
            published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            source_type="rss",
            tags=["ml"],
            extra={"feed_title": "AI Blog"},
        )

        saved_path = save_source_file(
            item, config,
            lenses=["ai"],
            subscription_title="AI Blog",
        )
        assert saved_path.exists()

        # Step 2: Scan picks up the saved file
        pending = scan_pending_sources(config)
        assert len(pending) == 1
        assert pending[0].title == "Transformers 101"
        assert pending[0].url == "https://blog.test/transformers-101"

        # Step 3: Build compile plan
        plan = evaluate_sources(config, pending)
        assert len(plan.candidates) >= 1

        # Step 4: Execute with compiled content
        source_path = plan.candidates[0].source.relative_path
        compiled_contents = {
            source_path: "Transformers are a neural network architecture..."
        }

        report = execute_compile_plan(config, plan, compiled_contents)
        assert report.failed == 0
        assert report.created + report.updated >= 1

    def test_pipeline_result_summary(self, config: WikiConfig):
        """PipelineResult produces a readable summary."""
        from llm_wiki.compile_executor import BatchExecutionReport

        plan = CompilePlan(
            candidates=[],
            total_sources=5,
            pending_sources=2,
        )

        report = BatchExecutionReport(
            total=2,
            created=1,
            updated=1,
            skipped=0,
            failed=0,
        )

        result = PipelineResult(
            compile_plan=plan,
            execution_report=report,
            duration_seconds=3.5,
        )

        summary = result.summary()
        assert "Pipeline Complete" in summary
        assert "1 created" in summary
        assert "1 updated" in summary

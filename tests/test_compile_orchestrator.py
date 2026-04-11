"""Tests for compile_orchestrator — Lens-based compile orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.compile_decision import Decision, DecisionResult
from llm_wiki.compile_evaluate import (
    EvaluationResult,
    OverlapLevel,
    SimilarityAssessment,
)
from llm_wiki.compile_orchestrator import (
    CompileJob,
    CompileOrchestrator,
    CompileTier,
    JobStatus,
    OrchestrationPlan,
    _slugify_title,
    _source_matches_lens,
    route_sources,
)
from llm_wiki.compile_service import CompileMode
from llm_wiki.config import WikiConfig
from llm_wiki.lens import CompileStrategy, Lens, LensStore
from llm_wiki.pipeline import CompileCandidate, CompilePlan, SourceEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temp vault directory with lenses."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / "lenses").mkdir()
    return vault


@pytest.fixture
def config(tmp_vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=tmp_vault)


@pytest.fixture
def ai_lens() -> Lens:
    return Lens(
        id="ai-research",
        name="AI Research",
        description="Machine learning and AI topics",
        keywords=["machine learning", "transformer", "llm"],
        default_tags=["ai", "research"],
        wiki_directory="topics/ai",
        compile_strategy=CompileStrategy.MERGE,
        compile_instructions="Write in technical prose. Include paper refs.",
        priority=0,
    )


@pytest.fixture
def devops_lens() -> Lens:
    return Lens(
        id="devops",
        name="DevOps",
        keywords=["kubernetes", "docker", "ci-cd"],
        default_tags=["devops"],
        wiki_directory="topics/devops",
        compile_strategy=CompileStrategy.PER_SOURCE,
        priority=1,
    )


@pytest.fixture
def news_lens() -> Lens:
    return Lens(
        id="weekly-news",
        name="Weekly News",
        keywords=["news", "announcement"],
        default_tags=["news"],
        wiki_directory="logs/weekly",
        compile_strategy=CompileStrategy.APPEND,
        priority=2,
    )


@pytest.fixture
def lens_store(config: WikiConfig, ai_lens: Lens, devops_lens: Lens, news_lens: Lens) -> LensStore:
    store = LensStore(config)
    store.save(ai_lens)
    store.save(devops_lens)
    store.save(news_lens)
    return store


def _make_source(
    path: str = "sources/rss/2024-01-15-test.md",
    title: str = "Test Article",
    url: str = "https://example.com/test",
    source_type: str = "rss",
    tags: list[str] | None = None,
    lenses: list[str] | None = None,
    body: str = "This is test content about machine learning.",
) -> SourceEntry:
    return SourceEntry(
        path=Path(path),
        relative_path=path,
        title=title,
        url=url,
        source_type=source_type,
        tags=tags or [],
        lenses=lenses or [],
        body=body,
    )


def _make_candidate(
    source: SourceEntry | None = None,
    action: Decision = Decision.CREATE,
    target_title: str = "",
    lens_directory: str = "",
) -> CompileCandidate:
    if source is None:
        source = _make_source()

    title = target_title or source.title

    decision = DecisionResult(
        source_path=source.relative_path,
        decision=action,
        target_title=title,
        reason="Test",
    )
    assessment = SimilarityAssessment(overlap_level=OverlapLevel.NONE)
    evaluation = EvaluationResult(
        source_path=source.relative_path,
        action=action,
        decision=decision,
        assessment=assessment,
    )
    return CompileCandidate(
        source=source,
        evaluation=evaluation,
        lens_directory=lens_directory or (source.lenses[0] if source.lenses else ""),
    )


# ---------------------------------------------------------------------------
# Tests: _slugify_title
# ---------------------------------------------------------------------------


class TestSlugifyTitle:
    def test_basic(self):
        assert _slugify_title("Transformer Architecture") == "transformer-architecture"

    def test_special_chars(self):
        assert _slugify_title("What is RLHF?") == "what-is-rlhf"

    def test_empty(self):
        assert _slugify_title("") == "untitled"

    def test_multiple_spaces(self):
        assert _slugify_title("Hello   World") == "hello-world"


# ---------------------------------------------------------------------------
# Tests: _source_matches_lens
# ---------------------------------------------------------------------------


class TestSourceMatchesLens:
    def test_title_match(self, ai_lens: Lens):
        source = _make_source(title="New Transformer Model Released")
        assert _source_matches_lens(source, ai_lens) is True

    def test_tag_match(self, ai_lens: Lens):
        source = _make_source(title="Some Article", tags=["llm", "gpt"])
        assert _source_matches_lens(source, ai_lens) is True

    def test_body_match(self, ai_lens: Lens):
        source = _make_source(
            title="Paper Review",
            body="This paper introduces a novel transformer architecture for...",
        )
        assert _source_matches_lens(source, ai_lens) is True

    def test_no_match(self, ai_lens: Lens):
        source = _make_source(
            title="Cooking Recipe",
            tags=["food"],
            body="How to make pasta",
        )
        assert _source_matches_lens(source, ai_lens) is False

    def test_no_keywords(self):
        lens = Lens(id="empty", name="Empty", keywords=[])
        source = _make_source()
        assert _source_matches_lens(source, lens) is False


# ---------------------------------------------------------------------------
# Tests: CompileJob
# ---------------------------------------------------------------------------


class TestCompileJob:
    def test_vault_path(self):
        job = CompileJob(
            job_id="ai-merge-0",
            lens_id="ai",
            lens_directory="topics/ai",
            target_title="Transformer Architecture",
        )
        assert job.vault_path == "topics/ai/transformer-architecture.md"

    def test_vault_path_no_directory(self):
        job = CompileJob(
            job_id="test-0",
            lens_id="test",
            target_title="Hello World",
        )
        assert job.vault_path == "hello-world.md"

    def test_vault_path_no_title(self):
        job = CompileJob(job_id="test-0", lens_id="test")
        assert job.vault_path == ""

    def test_set_result_valid(self):
        from llm_wiki.compile_service import CompileRequest, SourceGroup, SourceNote

        note = SourceNote(path="test.md", title="Test", body="Content")
        group = SourceGroup(sources=[note], primary_title="Test")
        request = CompileRequest(source_group=group)

        job = CompileJob(
            job_id="test-0",
            lens_id="test",
            request=request,
            target_title="Test",
        )

        response = """\
## Overview

This is a test wiki page about something.

```json
{"title": "Test Page", "tags": ["test"], "wikilinks": [], "summary": "A test page"}
```"""

        result = job.set_result(response)
        assert job.status == JobStatus.COMPLETED
        assert result.title == "Test Page"
        assert result.is_valid
        assert "test" in result.tags

    def test_set_result_empty(self):
        from llm_wiki.compile_service import CompileRequest, SourceGroup, SourceNote

        note = SourceNote(path="test.md", title="Test", body="Content")
        group = SourceGroup(sources=[note], primary_title="Test")
        request = CompileRequest(source_group=group)

        job = CompileJob(
            job_id="test-0",
            lens_id="test",
            request=request,
            target_title="Test",
        )

        result = job.set_result("")
        assert job.status == JobStatus.FAILED

    def test_set_result_no_request(self):
        job = CompileJob(job_id="test-0", lens_id="test")
        with pytest.raises(ValueError, match="no CompileRequest"):
            job.set_result("some response")

    def test_mark_failed(self):
        job = CompileJob(job_id="test-0", lens_id="test")
        job.mark_failed("Network error")
        assert job.status == JobStatus.FAILED
        assert job.error == "Network error"

    def test_mark_skipped(self):
        job = CompileJob(job_id="test-0", lens_id="test")
        job.mark_skipped("No content")
        assert job.status == JobStatus.SKIPPED

    def test_summary_line(self):
        job = CompileJob(
            job_id="ai-merge-0",
            lens_id="ai-research",
            lens_name="AI Research",
            target_title="Transformers",
            compile_strategy=CompileStrategy.MERGE,
            source_paths=["a.md", "b.md"],
        )
        line = job.summary_line()
        assert "Transformers" in line
        assert "ai-research" in line
        assert "merge" in line
        assert "2 sources" in line

    def test_build_prompt_with_instructions(self):
        from llm_wiki.compile_service import CompileRequest, SourceGroup, SourceNote

        note = SourceNote(path="test.md", title="Test", body="Content about AI")
        group = SourceGroup(sources=[note], primary_title="Test")
        request = CompileRequest(source_group=group)

        job = CompileJob(
            job_id="test-0",
            lens_id="ai",
            lens_name="AI Research",
            request=request,
            compile_instructions="Write in technical prose.",
        )

        prompt = job.build_prompt()
        assert "Lens-Specific Instructions" in prompt
        assert "AI Research" in prompt
        assert "Write in technical prose" in prompt


# ---------------------------------------------------------------------------
# Tests: CompileTier
# ---------------------------------------------------------------------------


class TestCompileTier:
    def test_is_complete_all_done(self):
        tier = CompileTier(
            priority=0,
            jobs=[
                CompileJob(job_id="a", lens_id="x", status=JobStatus.COMPLETED),
                CompileJob(job_id="b", lens_id="y", status=JobStatus.FAILED),
                CompileJob(job_id="c", lens_id="z", status=JobStatus.SKIPPED),
            ],
        )
        assert tier.is_complete is True

    def test_is_complete_pending(self):
        tier = CompileTier(
            priority=0,
            jobs=[
                CompileJob(job_id="a", lens_id="x", status=JobStatus.COMPLETED),
                CompileJob(job_id="b", lens_id="y", status=JobStatus.PENDING),
            ],
        )
        assert tier.is_complete is False

    def test_summary(self):
        tier = CompileTier(
            priority=0,
            jobs=[
                CompileJob(job_id="a", lens_id="x", status=JobStatus.COMPLETED),
                CompileJob(job_id="b", lens_id="y", status=JobStatus.PENDING),
            ],
        )
        summary = tier.summary()
        assert "Priority 0" in summary
        assert "2 jobs" in summary


# ---------------------------------------------------------------------------
# Tests: OrchestrationPlan
# ---------------------------------------------------------------------------


class TestOrchestrationPlan:
    def test_empty_plan(self):
        plan = OrchestrationPlan()
        assert plan.is_empty
        assert plan.total_jobs == 0
        assert "No compile jobs" in plan.summary()

    def test_all_jobs(self):
        plan = OrchestrationPlan(
            tiers=[
                CompileTier(priority=0, jobs=[
                    CompileJob(job_id="a", lens_id="x"),
                ]),
                CompileTier(priority=1, jobs=[
                    CompileJob(job_id="b", lens_id="y"),
                    CompileJob(job_id="c", lens_id="z"),
                ]),
            ],
            total_jobs=3,
        )
        assert len(plan.all_jobs) == 3
        assert not plan.is_empty

    def test_next_tier(self):
        plan = OrchestrationPlan(
            tiers=[
                CompileTier(priority=0, jobs=[
                    CompileJob(job_id="a", lens_id="x", status=JobStatus.COMPLETED),
                ]),
                CompileTier(priority=1, jobs=[
                    CompileJob(job_id="b", lens_id="y", status=JobStatus.PENDING),
                ]),
            ],
            total_jobs=2,
        )
        tier = plan.next_tier()
        assert tier is not None
        assert tier.priority == 1

    def test_next_tier_all_done(self):
        plan = OrchestrationPlan(
            tiers=[
                CompileTier(priority=0, jobs=[
                    CompileJob(job_id="a", lens_id="x", status=JobStatus.COMPLETED),
                ]),
            ],
            total_jobs=1,
        )
        assert plan.next_tier() is None
        assert plan.is_complete

    def test_compiled_contents(self):
        from llm_wiki.compile_service import CompileResult

        job = CompileJob(
            job_id="a",
            lens_id="x",
            status=JobStatus.COMPLETED,
            source_paths=["sources/test.md"],
            result=CompileResult(title="Test", body="Wiki content here"),
        )
        plan = OrchestrationPlan(
            tiers=[CompileTier(priority=0, jobs=[job])],
            total_jobs=1,
        )
        contents = plan.compiled_contents
        assert "sources/test.md" in contents
        assert contents["sources/test.md"] == "Wiki content here"

    def test_to_dict(self):
        plan = OrchestrationPlan(
            tiers=[
                CompileTier(priority=0, jobs=[
                    CompileJob(
                        job_id="ai-merge-0",
                        lens_id="ai",
                        lens_name="AI",
                        target_title="Transformers",
                        compile_strategy=CompileStrategy.MERGE,
                        source_paths=["a.md"],
                    ),
                ]),
            ],
            total_jobs=1,
            total_sources=1,
            lens_count=1,
        )
        d = plan.to_dict()
        assert d["total_jobs"] == 1
        assert d["tiers"][0]["priority"] == 0
        assert d["tiers"][0]["jobs"][0]["lens_id"] == "ai"


# ---------------------------------------------------------------------------
# Tests: CompileOrchestrator
# ---------------------------------------------------------------------------


class TestCompileOrchestrator:
    def test_build_plan_empty(self, config: WikiConfig, lens_store: LensStore):
        orch = CompileOrchestrator(config, lens_store)
        plan_input = CompilePlan(candidates=[], total_sources=0)
        result = orch.build_plan(plan_input)
        assert result.is_empty
        assert result.total_jobs == 0

    def test_build_plan_merge_strategy(self, config: WikiConfig, lens_store: LensStore):
        """MERGE strategy: two sources with same target → one job."""
        orch = CompileOrchestrator(config, lens_store)

        s1 = _make_source(
            path="sources/rss/s1.md",
            title="Transformer Architecture",
            lenses=["ai-research"],
            body="Content about transformers",
        )
        s2 = _make_source(
            path="sources/rss/s2.md",
            title="Transformer Architecture",
            lenses=["ai-research"],
            body="More about attention mechanisms",
        )

        c1 = _make_candidate(
            source=s1,
            target_title="Transformer Architecture",
            lens_directory="ai-research",
        )
        c2 = _make_candidate(
            source=s2,
            target_title="Transformer Architecture",
            lens_directory="ai-research",
        )

        plan_input = CompilePlan(
            candidates=[c1, c2],
            total_sources=2,
            pending_sources=2,
        )

        result = orch.build_plan(plan_input)
        assert result.total_jobs == 1  # merged into one job
        assert result.total_sources == 2

        job = result.all_jobs[0]
        assert job.lens_id == "ai-research"
        assert job.compile_strategy == CompileStrategy.MERGE
        assert job.source_count == 2
        assert job.lens_directory == "topics/ai"
        assert "Write in technical prose" in job.compile_instructions

    def test_build_plan_per_source_strategy(self, config: WikiConfig, lens_store: LensStore):
        """PER_SOURCE strategy: each source gets its own job."""
        orch = CompileOrchestrator(config, lens_store)

        s1 = _make_source(
            path="sources/rss/d1.md",
            title="K8s Guide",
            lenses=["devops"],
        )
        s2 = _make_source(
            path="sources/rss/d2.md",
            title="Docker Tips",
            lenses=["devops"],
        )

        c1 = _make_candidate(source=s1, lens_directory="devops")
        c2 = _make_candidate(source=s2, lens_directory="devops")

        plan_input = CompilePlan(candidates=[c1, c2], total_sources=2, pending_sources=2)
        result = orch.build_plan(plan_input)

        assert result.total_jobs == 2  # one per source
        for job in result.all_jobs:
            assert job.compile_strategy == CompileStrategy.PER_SOURCE
            assert job.source_count == 1
            assert job.lens_directory == "topics/devops"

    def test_build_plan_append_strategy(self, config: WikiConfig, lens_store: LensStore):
        """APPEND strategy: sources grouped by target page."""
        orch = CompileOrchestrator(config, lens_store)

        s1 = _make_source(
            path="sources/rss/n1.md",
            title="AI News This Week",
            lenses=["weekly-news"],
        )
        s2 = _make_source(
            path="sources/rss/n2.md",
            title="AI News This Week",
            lenses=["weekly-news"],
        )

        c1 = _make_candidate(source=s1, target_title="AI News This Week", lens_directory="weekly-news")
        c2 = _make_candidate(source=s2, target_title="AI News This Week", lens_directory="weekly-news")

        plan_input = CompilePlan(candidates=[c1, c2], total_sources=2, pending_sources=2)
        result = orch.build_plan(plan_input)

        assert result.total_jobs == 1  # grouped into one append job
        job = result.all_jobs[0]
        assert job.compile_strategy == CompileStrategy.APPEND
        assert job.source_count == 2

    def test_build_plan_priority_tiers(self, config: WikiConfig, lens_store: LensStore):
        """Jobs are organized into tiers by lens priority."""
        orch = CompileOrchestrator(config, lens_store)

        s_ai = _make_source(path="sources/ai.md", title="AI Article", lenses=["ai-research"])
        s_devops = _make_source(path="sources/devops.md", title="DevOps Article", lenses=["devops"])
        s_news = _make_source(path="sources/news.md", title="News Update", lenses=["weekly-news"])

        candidates = [
            _make_candidate(source=s_ai, lens_directory="ai-research"),
            _make_candidate(source=s_devops, lens_directory="devops"),
            _make_candidate(source=s_news, lens_directory="weekly-news"),
        ]

        plan_input = CompilePlan(candidates=candidates, total_sources=3, pending_sources=3)
        result = orch.build_plan(plan_input)

        assert len(result.tiers) == 3
        assert result.tiers[0].priority == 0  # ai-research
        assert result.tiers[1].priority == 1  # devops
        assert result.tiers[2].priority == 2  # weekly-news

    def test_build_plan_same_priority_same_tier(self, config: WikiConfig, lens_store: LensStore):
        """Lenses with same priority are in the same tier (parallel)."""
        # Override devops lens to priority 0 (same as ai-research)
        devops = lens_store.load("devops")
        assert devops is not None
        devops_p0 = Lens(
            id=devops.id,
            name=devops.name,
            keywords=devops.keywords,
            default_tags=devops.default_tags,
            wiki_directory=devops.wiki_directory,
            compile_strategy=devops.compile_strategy,
            priority=0,  # same as ai-research
        )
        lens_store.save(devops_p0)

        orch = CompileOrchestrator(config, lens_store)

        s_ai = _make_source(path="sources/ai.md", title="AI Article", lenses=["ai-research"])
        s_devops = _make_source(path="sources/devops.md", title="K8s Guide", lenses=["devops"])

        candidates = [
            _make_candidate(source=s_ai, lens_directory="ai-research"),
            _make_candidate(source=s_devops, lens_directory="devops"),
        ]

        plan_input = CompilePlan(candidates=candidates, total_sources=2, pending_sources=2)
        result = orch.build_plan(plan_input)

        assert len(result.tiers) == 1  # same priority → same tier
        assert result.tiers[0].job_count == 2  # two jobs in parallel

    def test_build_plan_lens_filter(self, config: WikiConfig, lens_store: LensStore):
        """Lens filter excludes non-matching lenses."""
        orch = CompileOrchestrator(config, lens_store)

        s_ai = _make_source(path="sources/ai.md", title="AI Article", lenses=["ai-research"])
        s_devops = _make_source(path="sources/devops.md", title="K8s Guide", lenses=["devops"])

        candidates = [
            _make_candidate(source=s_ai, lens_directory="ai-research"),
            _make_candidate(source=s_devops, lens_directory="devops"),
        ]

        plan_input = CompilePlan(candidates=candidates, total_sources=2, pending_sources=2)
        result = orch.build_plan(plan_input, lens_filter=["ai-research"])

        assert result.total_jobs == 1
        assert result.all_jobs[0].lens_id == "ai-research"

    def test_build_plan_disabled_lens_skipped(self, config: WikiConfig, lens_store: LensStore):
        """Disabled lenses are skipped."""
        # Disable ai-research
        disabled = Lens(
            id="ai-research",
            name="AI Research",
            enabled=False,
        )
        lens_store.save(disabled)

        orch = CompileOrchestrator(config, lens_store)

        s_ai = _make_source(path="sources/ai.md", title="AI Article", lenses=["ai-research"])
        candidates = [_make_candidate(source=s_ai, lens_directory="ai-research")]

        plan_input = CompilePlan(candidates=candidates, total_sources=1, pending_sources=1)
        result = orch.build_plan(plan_input)

        assert result.total_jobs == 0

    def test_build_plan_unknown_lens_fallback(self, config: WikiConfig, lens_store: LensStore):
        """Unknown lens creates a fallback lens definition."""
        orch = CompileOrchestrator(config, lens_store)

        source = _make_source(path="sources/x.md", title="Mystery Article", lenses=["unknown-lens"])
        candidates = [_make_candidate(source=source, lens_directory="unknown-lens")]

        plan_input = CompilePlan(candidates=candidates, total_sources=1, pending_sources=1)
        result = orch.build_plan(plan_input)

        assert result.total_jobs == 1
        job = result.all_jobs[0]
        assert job.lens_id == "unknown-lens"
        # Fallback uses MERGE strategy
        assert job.compile_strategy == CompileStrategy.MERGE

    def test_merge_different_targets_separate_jobs(self, config: WikiConfig, lens_store: LensStore):
        """MERGE: sources with different targets become separate jobs."""
        orch = CompileOrchestrator(config, lens_store)

        s1 = _make_source(path="sources/s1.md", title="Transformers", lenses=["ai-research"])
        s2 = _make_source(path="sources/s2.md", title="RLHF Training", lenses=["ai-research"])

        c1 = _make_candidate(source=s1, target_title="Transformers", lens_directory="ai-research")
        c2 = _make_candidate(source=s2, target_title="RLHF Training", lens_directory="ai-research")

        plan_input = CompilePlan(candidates=[c1, c2], total_sources=2, pending_sources=2)
        result = orch.build_plan(plan_input)

        assert result.total_jobs == 2  # different targets → separate jobs

    def test_compile_request_has_lens_tags(self, config: WikiConfig, lens_store: LensStore):
        """CompileRequest includes lens default_tags."""
        orch = CompileOrchestrator(config, lens_store)

        source = _make_source(
            path="sources/test.md",
            title="Test Article",
            tags=["custom"],
            lenses=["ai-research"],
        )
        candidates = [_make_candidate(source=source, lens_directory="ai-research")]

        plan_input = CompilePlan(candidates=candidates, total_sources=1, pending_sources=1)
        result = orch.build_plan(plan_input)

        job = result.all_jobs[0]
        assert job.request is not None
        # Should include both lens default tags and source tags
        assert "ai" in job.request.suggested_tags
        assert "research" in job.request.suggested_tags
        assert "custom" in job.request.suggested_tags

    def test_build_plan_from_sources(self, config: WikiConfig, lens_store: LensStore):
        """build_plan_from_sources creates candidates automatically."""
        orch = CompileOrchestrator(config, lens_store)

        sources = [
            _make_source(path="sources/a.md", title="AI Topic", lenses=["ai-research"]),
            _make_source(path="sources/b.md", title="K8s Topic", lenses=["devops"]),
        ]

        result = orch.build_plan_from_sources(sources)
        assert result.total_jobs == 2
        assert result.lens_count == 2


# ---------------------------------------------------------------------------
# Tests: OrchestrationPlan.report()
# ---------------------------------------------------------------------------


class TestOrchestrationReport:
    def test_report_with_results(self):
        from llm_wiki.compile_service import CompileResult

        job1 = CompileJob(
            job_id="ai-0",
            lens_id="ai-research",
            lens_name="AI Research",
            target_title="Transformers",
            compile_strategy=CompileStrategy.MERGE,
            source_paths=["a.md", "b.md"],
            status=JobStatus.COMPLETED,
            result=CompileResult(title="Transformers", body="x " * 200, tags=["ai"]),
        )
        job2 = CompileJob(
            job_id="ai-1",
            lens_id="ai-research",
            lens_name="AI Research",
            target_title="RLHF",
            compile_strategy=CompileStrategy.MERGE,
            source_paths=["c.md"],
            status=JobStatus.FAILED,
            error="LLM timeout",
        )

        plan = OrchestrationPlan(
            tiers=[CompileTier(priority=0, jobs=[job1, job2])],
            total_jobs=2,
            total_sources=3,
            lens_count=1,
        )

        report = plan.report()
        assert "AI Research" in report
        assert "1/2" in report  # 1 completed out of 2
        assert "ERROR" in report


# ---------------------------------------------------------------------------
# Tests: route_sources
# ---------------------------------------------------------------------------


class TestRouteSources:
    def test_no_lenses(self, config: WikiConfig, tmp_vault: Path):
        # Remove lenses dir
        import shutil
        lenses_dir = tmp_vault / "lenses"
        if lenses_dir.exists():
            shutil.rmtree(lenses_dir)

        result = route_sources(config)
        assert result["status"] == "no_lenses"

    def test_no_sources(self, config: WikiConfig, lens_store: LensStore):
        result = route_sources(config)
        assert result["status"] == "no_sources"

    def test_with_sources(self, config: WikiConfig, lens_store: LensStore, tmp_vault: Path):
        # Create a pending source file
        sources_dir = tmp_vault / "sources" / "rss"
        sources_dir.mkdir(parents=True, exist_ok=True)
        source_file = sources_dir / "2024-01-15-test.md"
        source_file.write_text(
            "---\n"
            "title: Transformer Overview\n"
            "url: https://example.com/transformers\n"
            "type: rss\n"
            "status: pending\n"
            "tags:\n  - ai\n"
            "lenses:\n  - ai-research\n"
            "---\n\n"
            "Content about transformers and machine learning.\n",
            encoding="utf-8",
        )

        result = route_sources(config)
        assert result["status"] == "ready"
        assert result["total_sources"] == 1
        assert "ai-research" in result["lenses"]
        assert result["lenses"]["ai-research"]["source_count"] == 1


# ---------------------------------------------------------------------------
# Tests: End-to-end job execution flow
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    def test_tier_by_tier_execution(self, config: WikiConfig, lens_store: LensStore):
        """Simulate tier-by-tier execution."""
        orch = CompileOrchestrator(config, lens_store)

        s_ai = _make_source(path="sources/ai.md", title="AI Article", lenses=["ai-research"], body="ML content")
        s_devops = _make_source(path="sources/devops.md", title="Docker Guide", lenses=["devops"], body="Docker content")

        candidates = [
            _make_candidate(source=s_ai, lens_directory="ai-research"),
            _make_candidate(source=s_devops, lens_directory="devops"),
        ]

        plan_input = CompilePlan(candidates=candidates, total_sources=2, pending_sources=2)
        plan = orch.build_plan(plan_input)

        assert not plan.is_complete
        assert len(plan.tiers) == 2

        # Execute tier 0 (ai-research, priority 0)
        tier0 = plan.next_tier()
        assert tier0 is not None
        assert tier0.priority == 0

        for job in tier0.pending_jobs:
            response = f"""\
## Overview

This is compiled wiki content about {job.target_title}.

```json
{{"title": "{job.target_title}", "tags": ["ai"], "wikilinks": [], "summary": "Test"}}
```"""
            job.set_result(response)

        assert tier0.is_complete

        # Execute tier 1 (devops, priority 1)
        tier1 = plan.next_tier()
        assert tier1 is not None
        assert tier1.priority == 1

        for job in tier1.pending_jobs:
            response = f"""\
## Guide

Docker tips and tricks.

```json
{{"title": "{job.target_title}", "tags": ["devops"], "wikilinks": [], "summary": "Test"}}
```"""
            job.set_result(response)

        assert tier1.is_complete
        assert plan.is_complete

        # Check compiled contents
        contents = plan.compiled_contents
        assert "sources/ai.md" in contents
        assert "sources/devops.md" in contents

        # Check report
        report = plan.report()
        assert "2/2 completed" in report

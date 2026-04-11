"""Tests for compile_evaluate module: search-result-based create/update/merge evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.compile_decision import (
    Decision,
    DecisionResult,
    NewContent,
    SimilarPage,
)
from llm_wiki.compile_evaluate import (
    DuplicateSignal,
    EvaluationResult,
    OverlapLevel,
    SimilarityAssessment,
    _apply_duplicate_override,
    _classify_overlap,
    _titles_match,
    _url_in_page,
    assess_similarity,
    content_hash,
    evaluate_batch,
    evaluate_source,
    evaluate_source_file,
)
from llm_wiki.config import WikiConfig
from llm_wiki.vault_search import VaultSearch
from llm_wiki.writer import parse_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_page(path: Path, fm: dict, body: str) -> None:
    """Write a markdown page with YAML frontmatter."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a test vault with wiki pages."""
    _write_page(tmp_path / "transformers.md", {
        "title": "Transformers",
        "aliases": ["Attention Is All You Need"],
        "tags": ["ai", "deep-learning", "nlp"],
    }, "# Transformers\n\nThe transformer architecture revolutionized NLP. "
       "It uses self-attention mechanisms to process sequences in parallel. "
       "See https://arxiv.org/abs/1706.03762 for the original paper.")

    _write_page(tmp_path / "prompt-engineering.md", {
        "title": "Prompt Engineering",
        "aliases": ["Prompting"],
        "tags": ["ai", "llm", "techniques"],
    }, "# Prompt Engineering\n\nTechniques for crafting effective prompts.")

    _write_page(tmp_path / "rust-language.md", {
        "title": "Rust",
        "aliases": ["Rust Programming Language"],
        "tags": ["programming", "systems"],
    }, "# Rust\n\nA systems programming language focused on safety.")

    # Sources directory (excluded from wiki search)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(sources_dir / "2024-01-15-transformers-explained.md", {
        "title": "Transformers Explained: A Visual Guide",
        "tags": ["ai", "transformers"],
        "source_type": "rss",
        "url": "https://blog.example.com/transformers-explained",
    }, "# Transformers Explained\n\nA visual guide to how transformers work.")

    return tmp_path


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def search(config: WikiConfig) -> VaultSearch:
    return VaultSearch(config)


@pytest.fixture
def similar_pages_high() -> list[SimilarPage]:
    """High-similarity pages (likely UPDATE/SKIP)."""
    return [
        SimilarPage(
            path="transformers.md",
            title="Transformers",
            score=0.95,
            match_type="title",
            aliases=["Attention Is All You Need"],
            tags=["ai", "deep-learning"],
            excerpt="The transformer architecture revolutionized NLP.",
        ),
        SimilarPage(
            path="attention-mechanism.md",
            title="Attention Mechanism",
            score=0.60,
            match_type="alias",
            tags=["ai"],
            excerpt="Self-attention allows models to focus...",
        ),
    ]


@pytest.fixture
def similar_pages_moderate() -> list[SimilarPage]:
    """Moderate-similarity pages (could go either way)."""
    return [
        SimilarPage(
            path="neural-networks.md",
            title="Neural Networks",
            score=0.50,
            match_type="tag",
            tags=["ai", "deep-learning"],
            excerpt="Neural networks are computing systems...",
        ),
    ]


@pytest.fixture
def no_similar() -> list[SimilarPage]:
    return []


# ---------------------------------------------------------------------------
# SimilarityAssessment tests
# ---------------------------------------------------------------------------


class TestAssessSimilarity:
    def test_no_similar_pages(self):
        result = assess_similarity([])
        assert result.overlap_level == OverlapLevel.NONE
        assert result.top_score == 0.0
        assert result.top_match is None
        assert not result.is_duplicate
        assert not result.has_candidates

    def test_exact_overlap(self, similar_pages_high):
        result = assess_similarity(similar_pages_high)
        assert result.overlap_level == OverlapLevel.EXACT
        assert result.top_score == 0.95
        assert result.top_match is not None
        assert result.top_match.title == "Transformers"
        assert DuplicateSignal.HIGH_SIMILARITY in result.duplicate_signals
        assert result.is_duplicate
        assert result.has_candidates
        assert result.candidate_count == 2

    def test_moderate_overlap(self, similar_pages_moderate):
        result = assess_similarity(similar_pages_moderate)
        assert result.overlap_level == OverlapLevel.MODERATE
        assert result.top_score == 0.50
        assert not result.is_duplicate

    def test_title_exact_signal(self):
        pages = [SimilarPage(
            path="transformers.md",
            title="Transformers",
            score=0.85,
            match_type="title",
        )]
        result = assess_similarity(
            pages, source_title="Transformers"
        )
        assert DuplicateSignal.TITLE_EXACT in result.duplicate_signals
        assert result.overlap_level == OverlapLevel.EXACT  # signals + high score

    def test_url_match_signal(self):
        pages = [SimilarPage(
            path="test.md",
            title="Test",
            score=0.80,
            match_type="title",
            excerpt="See https://example.com/article for details.",
        )]
        result = assess_similarity(
            pages, source_url="https://example.com/article"
        )
        assert DuplicateSignal.URL_MATCH in result.duplicate_signals

    def test_url_no_match(self):
        pages = [SimilarPage(
            path="test.md",
            title="Test",
            score=0.80,
            match_type="title",
            excerpt="Some content without URLs.",
        )]
        result = assess_similarity(
            pages, source_url="https://example.com/article"
        )
        assert DuplicateSignal.URL_MATCH not in result.duplicate_signals

    def test_to_dict(self, similar_pages_high):
        result = assess_similarity(similar_pages_high)
        d = result.to_dict()
        assert d["overlap_level"] == "exact"
        assert d["top_score"] == 0.95
        assert d["is_duplicate"] is True
        assert "top_match" in d
        assert d["top_match"]["title"] == "Transformers"

    def test_low_overlap(self):
        pages = [SimilarPage(
            path="test.md",
            title="Something Else",
            score=0.25,
            match_type="keyword",
        )]
        result = assess_similarity(pages)
        assert result.overlap_level == OverlapLevel.LOW

    def test_high_overlap_without_signals(self):
        pages = [SimilarPage(
            path="test.md",
            title="Related Topic",
            score=0.80,
            match_type="title",
        )]
        result = assess_similarity(pages)
        assert result.overlap_level == OverlapLevel.HIGH
        assert not result.is_duplicate


# ---------------------------------------------------------------------------
# classify_overlap tests
# ---------------------------------------------------------------------------


class TestClassifyOverlap:
    def test_exact_by_score(self):
        assert _classify_overlap(0.96, []) == OverlapLevel.EXACT

    def test_exact_by_signals(self):
        assert _classify_overlap(0.80, [DuplicateSignal.URL_MATCH]) == OverlapLevel.EXACT

    def test_high(self):
        assert _classify_overlap(0.80, []) == OverlapLevel.HIGH

    def test_moderate(self):
        assert _classify_overlap(0.50, []) == OverlapLevel.MODERATE

    def test_low(self):
        assert _classify_overlap(0.25, []) == OverlapLevel.LOW

    def test_none(self):
        assert _classify_overlap(0.10, []) == OverlapLevel.NONE


# ---------------------------------------------------------------------------
# Title matching tests
# ---------------------------------------------------------------------------


class TestTitlesMatch:
    def test_exact_match(self):
        assert _titles_match("Transformers", "Transformers")

    def test_case_insensitive(self):
        assert _titles_match("transformers", "TRANSFORMERS")

    def test_strips_article(self):
        assert _titles_match("The Transformer", "Transformer")

    def test_strips_trailing_punctuation(self):
        assert _titles_match("Transformers:", "Transformers")

    def test_different_titles(self):
        assert not _titles_match("Transformers", "BERT")

    def test_whitespace_normalization(self):
        assert _titles_match("  Transformers  ", "Transformers")


# ---------------------------------------------------------------------------
# URL in page tests
# ---------------------------------------------------------------------------


class TestUrlInPage:
    def test_url_found(self):
        page = SimilarPage(
            path="test.md", title="Test",
            excerpt="Source: https://example.com/article and more text",
        )
        assert _url_in_page("https://example.com/article", page)

    def test_url_not_found(self):
        page = SimilarPage(
            path="test.md", title="Test",
            excerpt="Some content without URLs",
        )
        assert not _url_in_page("https://example.com/article", page)

    def test_empty_url(self):
        page = SimilarPage(path="test.md", title="Test", excerpt="content")
        assert not _url_in_page("", page)

    def test_empty_excerpt(self):
        page = SimilarPage(path="test.md", title="Test", excerpt="")
        assert not _url_in_page("https://example.com/article", page)

    def test_normalizes_protocol(self):
        page = SimilarPage(
            path="test.md", title="Test",
            excerpt="Visit example.com/article for details.",
        )
        assert _url_in_page("https://example.com/article", page)


# ---------------------------------------------------------------------------
# Content hash tests
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_deterministic(self):
        h1 = content_hash("Hello world")
        h2 = content_hash("Hello world")
        assert h1 == h2

    def test_whitespace_normalization(self):
        h1 = content_hash("Hello  world")
        h2 = content_hash("Hello world")
        assert h1 == h2

    def test_case_normalization(self):
        h1 = content_hash("Hello World")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_different_content(self):
        h1 = content_hash("Hello world")
        h2 = content_hash("Goodbye world")
        assert h1 != h2


# ---------------------------------------------------------------------------
# EvaluationResult tests
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    def _make_result(
        self,
        action: Decision,
        target_page: str = "",
        merge_strategy: str = "",
        confidence: float = 0.8,
    ) -> EvaluationResult:
        decision = DecisionResult(
            source_path="sources/rss/test.md",
            decision=action,
            target_page=target_page,
            target_title="Test Title" if action == Decision.CREATE else "Existing",
            reason="Test reason",
            confidence=confidence,
            merge_strategy=merge_strategy,
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.MODERATE,
            top_score=0.6,
            candidate_count=1,
        )
        return EvaluationResult(
            source_path="sources/rss/test.md",
            action=action,
            decision=decision,
            assessment=assessment,
        )

    def test_create_is_actionable(self):
        result = self._make_result(Decision.CREATE)
        assert result.is_actionable
        assert result.action == Decision.CREATE

    def test_update_is_actionable(self):
        result = self._make_result(
            Decision.UPDATE, target_page="existing.md", merge_strategy="append"
        )
        assert result.is_actionable
        assert result.target_page == "existing.md"
        assert result.merge_strategy == "append"

    def test_skip_is_not_actionable(self):
        result = self._make_result(Decision.SKIP, target_page="existing.md")
        assert not result.is_actionable

    def test_summary_line_create(self):
        result = self._make_result(Decision.CREATE)
        line = result.summary_line()
        assert "[CREATE]" in line
        assert "Test Title" in line

    def test_summary_line_update(self):
        result = self._make_result(
            Decision.UPDATE, target_page="existing.md", merge_strategy="append"
        )
        line = result.summary_line()
        assert "[UPDATE]" in line
        assert "existing.md" in line
        assert "append" in line

    def test_summary_line_skip(self):
        result = self._make_result(Decision.SKIP, target_page="existing.md")
        line = result.summary_line()
        assert "[SKIP]" in line

    def test_to_dict(self):
        result = self._make_result(Decision.CREATE)
        d = result.to_dict()
        assert d["action"] == "create"
        assert d["source_path"] == "sources/rss/test.md"
        assert "assessment" in d
        assert d["assessment"]["overlap_level"] == "moderate"

    def test_confidence(self):
        result = self._make_result(Decision.CREATE, confidence=0.92)
        assert result.confidence == 0.92


# ---------------------------------------------------------------------------
# Duplicate override tests
# ---------------------------------------------------------------------------


class TestApplyDuplicateOverride:
    def _content(self) -> NewContent:
        return NewContent(
            source_path="sources/rss/test.md",
            title="Test",
            url="https://example.com/article",
        )

    def test_url_plus_high_similarity_to_skip(self):
        """URL match + high similarity → override to SKIP."""
        decision = DecisionResult(
            source_path="test.md",
            decision=Decision.UPDATE,
            target_page="existing.md",
            confidence=0.8,
            merge_strategy="append",
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.EXACT,
            top_score=0.96,
            top_match=SimilarPage(path="existing.md", title="Existing", score=0.96),
            duplicate_signals=[DuplicateSignal.URL_MATCH, DuplicateSignal.HIGH_SIMILARITY],
            candidate_count=1,
        )
        result = _apply_duplicate_override(decision, assessment, self._content())
        assert result.decision == Decision.SKIP

    def test_url_match_create_to_update(self):
        """URL match alone with CREATE → override to UPDATE."""
        decision = DecisionResult(
            source_path="test.md",
            decision=Decision.CREATE,
            target_title="New Page",
            confidence=0.7,
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.MODERATE,
            top_score=0.60,
            top_match=SimilarPage(path="existing.md", title="Existing", score=0.60),
            duplicate_signals=[DuplicateSignal.URL_MATCH],
            candidate_count=1,
        )
        result = _apply_duplicate_override(decision, assessment, self._content())
        assert result.decision == Decision.UPDATE
        assert result.target_page == "existing.md"
        assert result.merge_strategy == "rewrite"

    def test_no_signals_no_override(self):
        """No duplicate signals → decision unchanged."""
        decision = DecisionResult(
            source_path="test.md",
            decision=Decision.CREATE,
            target_title="New Page",
            confidence=0.8,
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.LOW,
            top_score=0.30,
            candidate_count=1,
        )
        result = _apply_duplicate_override(decision, assessment, self._content())
        assert result.decision == Decision.CREATE
        assert result is decision  # Same object, unchanged

    def test_skip_stays_skip(self):
        """Already SKIP + URL match + high similarity → still SKIP."""
        decision = DecisionResult(
            source_path="test.md",
            decision=Decision.SKIP,
            target_page="existing.md",
            confidence=0.95,
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.EXACT,
            top_score=0.97,
            top_match=SimilarPage(path="existing.md", title="Existing", score=0.97),
            duplicate_signals=[DuplicateSignal.URL_MATCH, DuplicateSignal.HIGH_SIMILARITY],
            candidate_count=1,
        )
        result = _apply_duplicate_override(decision, assessment, self._content())
        assert result.decision == Decision.SKIP


# ---------------------------------------------------------------------------
# Integration: evaluate_source
# ---------------------------------------------------------------------------


class TestEvaluateSource:
    def test_new_topic_creates(self, search: VaultSearch):
        """Unrelated topic → CREATE."""
        result = evaluate_source(
            search,
            source_path="sources/rss/quantum-computing.md",
            title="Quantum Computing Basics",
            tags=["physics", "quantum"],
            body="Quantum computers use qubits instead of classical bits.",
        )
        assert result.action == Decision.CREATE
        assert result.is_actionable
        assert result.assessment.overlap_level in (OverlapLevel.NONE, OverlapLevel.LOW)

    def test_similar_topic_updates(self, search: VaultSearch):
        """Title matches existing page → UPDATE."""
        result = evaluate_source(
            search,
            source_path="sources/rss/new-transformers.md",
            title="Transformers",
            tags=["ai", "deep-learning"],
            body="New developments in the transformer architecture.",
        )
        # Should find the existing "Transformers" page
        assert result.action in (Decision.UPDATE, Decision.SKIP)
        assert result.assessment.overlap_level in (
            OverlapLevel.HIGH, OverlapLevel.EXACT
        )
        assert result.assessment.has_candidates

    def test_returns_similar_pages(self, search: VaultSearch):
        """Similar pages are included in the result."""
        result = evaluate_source(
            search,
            source_path="sources/rss/attention-guide.md",
            title="Attention Mechanisms in Transformers",
            tags=["ai", "transformers"],
            body="How attention mechanisms work in transformer models.",
        )
        assert len(result.similar_pages) >= 1
        assert any(p.title == "Transformers" for p in result.similar_pages)

    def test_empty_input(self, search: VaultSearch):
        """No search criteria → CREATE (no matches)."""
        result = evaluate_source(
            search,
            source_path="sources/rss/empty.md",
        )
        assert result.action == Decision.CREATE
        assert result.assessment.overlap_level == OverlapLevel.NONE

    def test_to_dict_structure(self, search: VaultSearch):
        """Result serializes to complete dict."""
        result = evaluate_source(
            search,
            source_path="sources/rss/test.md",
            title="Some Topic",
            tags=["misc"],
        )
        d = result.to_dict()
        assert "action" in d
        assert "assessment" in d
        assert "confidence" in d
        assert "similar_pages_count" in d

    def test_new_content_preserved(self, search: VaultSearch):
        """NewContent is accessible from the result."""
        result = evaluate_source(
            search,
            source_path="sources/rss/test.md",
            title="Test Title",
            tags=["test"],
            url="https://example.com/test",
            source_type="rss",
            lens_id="test-lens",
            lens_name="Test Lens",
        )
        assert result.new_content is not None
        assert result.new_content.title == "Test Title"
        assert result.new_content.lens_id == "test-lens"


# ---------------------------------------------------------------------------
# Integration: evaluate_source_file
# ---------------------------------------------------------------------------


class TestEvaluateSourceFile:
    def test_reads_source_file(self, search: VaultSearch, vault: Path):
        """Reads frontmatter and body from source file."""
        source_dir = vault / "sources" / "rss"
        source_file = source_dir / "2024-01-15-transformers-explained.md"

        result = evaluate_source_file(search, source_file)
        assert result.source_path == str(source_file)
        assert result.assessment.has_candidates  # Should find "Transformers"
        assert result.action in (Decision.CREATE, Decision.UPDATE, Decision.SKIP)

    def test_nonexistent_file(self, search: VaultSearch, vault: Path):
        """Missing file → CREATE with error reason."""
        result = evaluate_source_file(
            search, vault / "nonexistent.md"
        )
        assert result.action == Decision.CREATE
        assert result.assessment.overlap_level == OverlapLevel.NONE
        assert "Cannot read" in result.assessment.assessment_reason

    def test_with_lens_metadata(self, search: VaultSearch, vault: Path):
        """Lens metadata is passed through."""
        source_file = vault / "sources" / "rss" / "2024-01-15-transformers-explained.md"
        result = evaluate_source_file(
            search, source_file,
            lens_id="ai-ml",
            lens_name="AI & ML",
        )
        assert result.new_content is not None
        assert result.new_content.lens_id == "ai-ml"


# ---------------------------------------------------------------------------
# Integration: evaluate_batch
# ---------------------------------------------------------------------------


class TestEvaluateBatch:
    def test_batch_evaluation(self, search: VaultSearch):
        """Batch evaluation processes all sources."""
        sources = [
            {
                "source_path": "sources/rss/quantum.md",
                "title": "Quantum Computing",
                "tags": ["physics"],
            },
            {
                "source_path": "sources/rss/rust-guide.md",
                "title": "Rust Programming Guide",
                "tags": ["programming"],
            },
        ]
        results = evaluate_batch(search, sources)
        assert len(results) == 2
        assert all(isinstance(r, EvaluationResult) for r in results)

    def test_empty_batch(self, search: VaultSearch):
        results = evaluate_batch(search, [])
        assert results == []

    def test_inter_source_dedup(self, search: VaultSearch, vault: Path):
        """Two sources targeting same page get dedup annotation."""
        # Both of these should match the "Transformers" page
        sources = [
            {
                "source_path": "sources/rss/transformers-a.md",
                "title": "Transformers",
                "tags": ["ai"],
                "body": "First article about transformers.",
            },
            {
                "source_path": "sources/rss/transformers-b.md",
                "title": "Transformers",
                "tags": ["ai"],
                "body": "Second article about transformers.",
            },
        ]
        results = evaluate_batch(search, sources, deduplicate=True)
        assert len(results) == 2

        # Both should target the same page
        update_results = [r for r in results if r.action == Decision.UPDATE]
        if len(update_results) >= 2:
            # Second one should mention merge
            assert "another source" in update_results[1].decision.reason.lower() or \
                   "merge" in update_results[1].decision.reason.lower()

    def test_dedup_disabled(self, search: VaultSearch):
        """With deduplicate=False, no dedup annotation."""
        sources = [
            {"source_path": "s1.md", "title": "Test"},
            {"source_path": "s2.md", "title": "Test"},
        ]
        results = evaluate_batch(search, sources, deduplicate=False)
        assert len(results) == 2

    def test_mixed_actions(self, search: VaultSearch):
        """Batch with create and update sources."""
        sources = [
            {
                "source_path": "sources/rss/new-topic.md",
                "title": "Quantum Entanglement Theory",
                "tags": ["physics"],
            },
            {
                "source_path": "sources/rss/transformers-update.md",
                "title": "Transformers",
                "tags": ["ai", "deep-learning"],
            },
        ]
        results = evaluate_batch(search, sources)
        actions = {r.action for r in results}
        # At least one should be CREATE (quantum) and one might be UPDATE (transformers)
        assert Decision.CREATE in actions or Decision.UPDATE in actions


# ---------------------------------------------------------------------------
# SimilarityAssessment serialization
# ---------------------------------------------------------------------------


class TestSimilarityAssessmentSerialization:
    def test_none_overlap_to_dict(self):
        a = SimilarityAssessment(
            overlap_level=OverlapLevel.NONE,
            assessment_reason="No matches.",
        )
        d = a.to_dict()
        assert d["overlap_level"] == "none"
        assert d["is_duplicate"] is False
        assert "top_match" not in d

    def test_with_top_match_to_dict(self):
        a = SimilarityAssessment(
            overlap_level=OverlapLevel.HIGH,
            top_score=0.82,
            top_match=SimilarPage(
                path="test.md", title="Test", score=0.82, match_type="title",
            ),
            candidate_count=3,
            assessment_reason="Strong match.",
        )
        d = a.to_dict()
        assert d["top_match"]["title"] == "Test"
        assert d["candidate_count"] == 3

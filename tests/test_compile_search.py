"""Tests for compile_search module: vault search → SimilarPage bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.compile_decision import SimilarPage
from llm_wiki.compile_search import (
    _Candidate,
    _compute_score,
    _extract_excerpt,
    _extract_title_keywords,
    _SCORE_WEIGHTS,
    extract_keywords,
    find_related_pages,
    find_related_pages_for_source,
)
from llm_wiki.vault_search import VaultSearch, WikiPage, SearchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_page(path: Path, fm: dict, body: str) -> None:
    """Helper to write a markdown page with YAML frontmatter."""
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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a vault with wiki pages for testing."""
    _write_page(tmp_path / "transformers.md", {
        "title": "Transformers",
        "aliases": ["Attention Is All You Need", "AIAYN"],
        "tags": ["ai", "deep-learning", "nlp"],
    }, "# Transformers\n\nThe transformer architecture revolutionized NLP. "
       "It uses self-attention mechanisms to process sequences in parallel.")

    _write_page(tmp_path / "prompt-engineering.md", {
        "title": "Prompt Engineering",
        "aliases": ["Prompting"],
        "tags": ["ai", "llm", "techniques"],
    }, "# Prompt Engineering\n\nTechniques for crafting effective prompts "
       "to guide LLM behavior and output quality.")

    _write_page(tmp_path / "rust-language.md", {
        "title": "Rust",
        "aliases": ["Rust Programming Language", "rustlang"],
        "tags": ["programming", "systems"],
    }, "# Rust\n\nA systems programming language focused on safety and concurrency.")

    ai_dir = tmp_path / "ai-research"
    ai_dir.mkdir()
    _write_page(ai_dir / "rag.md", {
        "title": "Retrieval-Augmented Generation",
        "aliases": ["RAG"],
        "tags": ["ai", "llm", "rag"],
    }, "# RAG\n\nCombining retrieval with generation for grounded outputs.")

    _write_page(tmp_path / "gpt-4.md", {
        "title": "GPT-4",
        "aliases": ["GPT4"],
        "tags": ["ai/llm", "ai/openai", "model"],
    }, "# GPT-4\n\nOpenAI's multimodal large language model released in 2023.")

    # Source files (should be excluded from wiki search)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(sources_dir / "2024-01-15-transformer-news.md", {
        "title": "New Transformer Variant",
        "tags": ["ai"],
        "source_type": "rss",
    }, "# New Transformer Variant\n\nA breakthrough in transformer design.")

    return tmp_path


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def search(config: WikiConfig) -> VaultSearch:
    return VaultSearch(config)


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_basic_extraction(self):
        text = "Transformers are a neural network architecture for NLP tasks."
        keywords = extract_keywords(text)
        assert "transformers" in keywords
        assert "neural" in keywords
        assert "architecture" in keywords

    def test_filters_stopwords(self):
        text = "The quick brown fox jumps over the lazy dog."
        keywords = extract_keywords(text)
        assert "the" not in keywords
        assert "over" not in keywords

    def test_filters_short_words(self):
        text = "AI is the new ML in CS and IT."
        keywords = extract_keywords(text)
        # "ai", "is", "ml", "cs", "it" are all < 3 chars or stopwords
        assert "new" not in keywords  # "new" is a stopword

    def test_strips_markdown(self):
        text = "**bold** text with [[wikilinks]] and `code`"
        keywords = extract_keywords(text)
        assert "bold" in keywords
        assert "wikilinks" in keywords
        assert "code" in keywords  # 4 chars, passes MIN_KEYWORD_LENGTH=3
        assert "text" in keywords

    def test_strips_urls(self):
        text = "Visit https://example.com for more info about transformers."
        keywords = extract_keywords(text)
        assert "https" not in keywords
        assert "example" not in keywords
        assert "transformers" in keywords

    def test_frequency_ordering(self):
        text = "transformer transformer transformer attention attention model"
        keywords = extract_keywords(text)
        assert keywords[0] == "transformer"
        assert keywords[1] == "attention"

    def test_max_keywords(self):
        text = " ".join(f"keyword{i}" for i in range(20))
        keywords = extract_keywords(text, max_keywords=5)
        assert len(keywords) <= 5

    def test_empty_text(self):
        assert extract_keywords("") == []

    def test_only_stopwords(self):
        assert extract_keywords("the and or but") == []


# ---------------------------------------------------------------------------
# _extract_title_keywords
# ---------------------------------------------------------------------------


class TestExtractTitleKeywords:
    def test_splits_on_separators(self):
        kws = _extract_title_keywords("Prompt Engineering: A Guide")
        assert "prompt" in kws
        assert "engineering" in kws
        assert "guide" in kws

    def test_filters_short_words(self):
        kws = _extract_title_keywords("AI in NLP")
        # "ai" (2 chars) and "in" (2 chars, also stopword) should be filtered
        assert "nlp" in kws
        assert "ai" not in kws

    def test_filters_stopwords(self):
        kws = _extract_title_keywords("The Art of Programming")
        assert "the" not in kws
        assert "programming" in kws


# ---------------------------------------------------------------------------
# find_related_pages — integration tests
# ---------------------------------------------------------------------------


class TestFindRelatedPages:
    def test_finds_by_title(self, search: VaultSearch):
        results = find_related_pages(search, title="Transformers")
        assert len(results) >= 1
        assert any(p.title == "Transformers" for p in results)
        assert all(isinstance(p, SimilarPage) for p in results)

    def test_finds_by_tags(self, search: VaultSearch):
        results = find_related_pages(search, tags=["ai", "llm"])
        assert len(results) >= 1
        # Should find pages tagged with "ai" or "llm"
        found_titles = {p.title for p in results}
        assert "Transformers" in found_titles or "Prompt Engineering" in found_titles

    def test_finds_by_body_keywords(self, search: VaultSearch):
        results = find_related_pages(
            search,
            body="The attention mechanism is a key component of transformer models."
        )
        assert len(results) >= 1

    def test_combined_search(self, search: VaultSearch):
        results = find_related_pages(
            search,
            title="New Transformer Architecture",
            tags=["ai", "deep-learning"],
            body="Self-attention mechanism for parallel processing of sequences.",
        )
        assert len(results) >= 1
        # Transformers page should be among results
        found_titles = {p.title for p in results}
        assert "Transformers" in found_titles

    def test_deduplication(self, search: VaultSearch):
        """Same page found via title and tag should appear only once."""
        results = find_related_pages(
            search,
            title="Transformers",
            tags=["ai"],  # also matches transformers.md
        )
        paths = [p.path for p in results]
        assert len(paths) == len(set(paths)), "Duplicate paths in results"

    def test_max_results(self, search: VaultSearch):
        results = find_related_pages(
            search,
            tags=["ai"],  # matches many pages
            max_results=2,
        )
        assert len(results) <= 2

    def test_sorted_by_score_desc(self, search: VaultSearch):
        results = find_related_pages(
            search,
            title="Transformers",
            tags=["ai"],
        )
        if len(results) >= 2:
            scores = [p.score for p in results]
            assert scores == sorted(scores, reverse=True)

    def test_similar_page_fields(self, search: VaultSearch):
        results = find_related_pages(search, title="Transformers")
        page = results[0]
        assert page.path  # relative path
        assert page.title == "Transformers"
        assert page.score > 0
        assert page.match_type in ("title", "alias", "tag", "keyword", "combined")
        assert isinstance(page.aliases, list)
        assert isinstance(page.tags, list)
        # Excerpt should be populated
        assert page.excerpt  # non-empty

    def test_excludes_source_files(self, search: VaultSearch):
        """Source files in sources/ directory should not appear."""
        results = find_related_pages(
            search, title="New Transformer Variant"
        )
        for p in results:
            assert not p.path.startswith("sources/")

    def test_no_results(self, search: VaultSearch):
        results = find_related_pages(
            search, title="Quantum Entanglement in Biology"
        )
        # May return empty or partial matches
        for p in results:
            assert isinstance(p, SimilarPage)

    def test_empty_input(self, search: VaultSearch):
        results = find_related_pages(search)
        assert results == []

    def test_combined_match_type(self, search: VaultSearch):
        """Page matched via multiple methods should show 'combined' match_type."""
        results = find_related_pages(
            search,
            title="Transformers",
            tags=["ai"],
        )
        # Transformers should match both by title and by tag → combined
        top = results[0]
        assert top.title == "Transformers"
        assert top.match_type == "combined"

    def test_title_exact_match_highest_score(self, search: VaultSearch):
        """Exact title match should have the highest score."""
        results = find_related_pages(search, title="Transformers")
        if results:
            assert results[0].score >= _SCORE_WEIGHTS["title_exact"] - 0.01


# ---------------------------------------------------------------------------
# find_related_pages_for_source
# ---------------------------------------------------------------------------


class TestFindRelatedPagesForSource:
    def test_reads_source_file(self, search: VaultSearch, vault: Path):
        source_dir = vault / "sources" / "rss"
        source_file = source_dir / "2024-02-01-attention-guide.md"
        _write_page(source_file, {
            "title": "Complete Guide to Attention Mechanisms",
            "tags": ["ai", "deep-learning"],
            "source_type": "rss",
        }, "# Attention Guide\n\nThis article explains how attention "
           "mechanisms work in transformer models.")

        results = find_related_pages_for_source(search, source_file)
        assert len(results) >= 1
        assert any(p.title == "Transformers" for p in results)

    def test_fallback_title_from_filename(self, search: VaultSearch, vault: Path):
        source_dir = vault / "sources" / "rss"
        source_file = source_dir / "2024-01-20-rust-safety.md"
        # No title in frontmatter
        _write_page(source_file, {
            "tags": ["programming"],
        }, "# Rust Safety\n\nRust's ownership model ensures memory safety.")

        results = find_related_pages_for_source(search, source_file)
        assert len(results) >= 1

    def test_nonexistent_file(self, search: VaultSearch, vault: Path):
        results = find_related_pages_for_source(
            search, vault / "nonexistent.md"
        )
        assert results == []


# ---------------------------------------------------------------------------
# _extract_excerpt
# ---------------------------------------------------------------------------


class TestExtractExcerpt:
    def test_short_body(self, tmp_path: Path):
        page = tmp_path / "test.md"
        _write_page(page, {"title": "Test"}, "Short body.")
        excerpt = _extract_excerpt(page)
        assert excerpt == "Short body."

    def test_strips_heading(self, tmp_path: Path):
        page = tmp_path / "test.md"
        _write_page(page, {"title": "Test"}, "# Heading\n\nActual content here.")
        excerpt = _extract_excerpt(page)
        assert "# Heading" not in excerpt
        assert "Actual content" in excerpt

    def test_truncation_at_sentence(self, tmp_path: Path):
        page = tmp_path / "test.md"
        body = "First sentence here. Second sentence here. " + "x" * 500
        _write_page(page, {"title": "Test"}, body)
        excerpt = _extract_excerpt(page, max_chars=60)
        assert excerpt.endswith(".")
        assert len(excerpt) <= 60

    def test_truncation_with_ellipsis(self, tmp_path: Path):
        page = tmp_path / "test.md"
        body = "a" * 600  # no sentence boundary
        _write_page(page, {"title": "Test"}, body)
        excerpt = _extract_excerpt(page, max_chars=100)
        assert excerpt.endswith("...")

    def test_nonexistent_file(self, tmp_path: Path):
        excerpt = _extract_excerpt(tmp_path / "nonexistent.md")
        assert excerpt == ""


# ---------------------------------------------------------------------------
# _compute_score
# ---------------------------------------------------------------------------


class TestComputeScore:
    def test_exact_title_match(self):
        page = WikiPage(
            path=Path("/v/transformers.md"),
            relative_path=Path("transformers.md"),
            title="Transformers",
        )
        result = SearchResult(page=page, match_type="title", match_value="Transformers")
        score = _compute_score(result, "title", "transformers")
        assert score == _SCORE_WEIGHTS["title_exact"]

    def test_prefix_title_match(self):
        page = WikiPage(
            path=Path("/v/transformers.md"),
            relative_path=Path("transformers.md"),
            title="Transformers Architecture",
        )
        result = SearchResult(page=page, match_type="title", match_value="Transformers Architecture")
        score = _compute_score(result, "title", "transformer")
        assert score == _SCORE_WEIGHTS["title_prefix"]

    def test_substring_title_match(self):
        page = WikiPage(
            path=Path("/v/test.md"),
            relative_path=Path("test.md"),
            title="Deep Transformer Networks",
        )
        result = SearchResult(page=page, match_type="title", match_value="Deep Transformer Networks")
        score = _compute_score(result, "title", "transformer")
        assert score == _SCORE_WEIGHTS["title_substring"]

    def test_tag_match(self):
        page = WikiPage(
            path=Path("/v/test.md"),
            relative_path=Path("test.md"),
            title="Test",
            tags=["ai"],
        )
        result = SearchResult(page=page, match_type="tag", match_value="ai")
        score = _compute_score(result, "tag", "ai")
        assert score == _SCORE_WEIGHTS["tag"]

    def test_keyword_match(self):
        page = WikiPage(
            path=Path("/v/test.md"),
            relative_path=Path("test.md"),
            title="Test",
        )
        result = SearchResult(page=page, match_type="combined", match_value="test")
        score = _compute_score(result, "keyword", "test")
        assert score == _SCORE_WEIGHTS["keyword"]


# ---------------------------------------------------------------------------
# _Candidate merging
# ---------------------------------------------------------------------------


class TestCandidate:
    def test_keeps_highest_score(self):
        page = WikiPage(
            path=Path("/v/test.md"),
            relative_path=Path("test.md"),
            title="Test",
        )
        c = _Candidate(page=page)
        c.update(0.5, "tag", "ai")
        c.update(0.9, "title", "test")
        c.update(0.3, "keyword", "something")

        assert c.best_score == 0.9
        assert c.best_match_type == "title"
        assert c.match_types == {"tag", "title", "keyword"}

    def test_initial_state(self):
        page = WikiPage(
            path=Path("/v/test.md"),
            relative_path=Path("test.md"),
            title="Test",
        )
        c = _Candidate(page=page)
        assert c.best_score == 0.0
        assert c.match_types == set()

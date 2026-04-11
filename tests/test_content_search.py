"""Tests for content_search module — markdown content reading and keyword matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.content_search import (
    ContentMatch,
    ContentSearch,
    MatchContext,
    read_body,
    read_markdown,
    extract_title,
    tokenize,
    tokenize_unique,
    _token_matches,
    _build_excerpt,
    _find_regex_matches,
    _find_keyword_matches,
)
import re


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a vault with test markdown pages."""
    # Page with frontmatter and rich body
    _write_page(
        tmp_path / "transformers.md",
        {"title": "Transformers", "aliases": ["AIAYN"], "tags": ["ai", "nlp"]},
        (
            "# Transformers\n\n"
            "The transformer architecture was introduced in the paper\n"
            "\"Attention Is All You Need\" by Vaswani et al.\n\n"
            "## Key Concepts\n\n"
            "- Self-attention mechanism allows the model to attend\n"
            "  to different positions of the input sequence.\n"
            "- Multi-head attention extends this by running multiple\n"
            "  attention operations in parallel.\n"
        ),
    )

    # Page about RAG
    ai_dir = tmp_path / "ai-research"
    ai_dir.mkdir()
    _write_page(
        ai_dir / "rag.md",
        {"title": "Retrieval-Augmented Generation", "aliases": ["RAG"], "tags": ["ai", "llm"]},
        (
            "# RAG\n\n"
            "Retrieval-Augmented Generation combines a retrieval system\n"
            "with a language model to ground responses in factual data.\n\n"
            "The retriever fetches relevant documents from a knowledge base,\n"
            "and the generator produces answers conditioned on retrieved context.\n"
        ),
    )

    # Page with no frontmatter
    (tmp_path / "quick-note.md").write_text(
        "# Quick Note\n\nJust a quick note about Python programming.\n"
        "Python is great for data science and machine learning.\n"
    )

    # Empty page
    (tmp_path / "empty.md").write_text("---\ntitle: Empty\ntags:\n  - draft\n---\n")

    # Page in sources/ (should be excluded by default)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(
        sources_dir / "article.md",
        {"title": "Source Article", "source_type": "rss"},
        "# Source Article\n\nThis is from an RSS feed about transformer models.\n",
    )

    # .obsidian dir (always excluded)
    obs_dir = tmp_path / ".obsidian"
    obs_dir.mkdir()
    (obs_dir / "config.md").write_text("# Config\nObsidian config data.\n")

    return tmp_path


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def search(config: WikiConfig) -> ContentSearch:
    return ContentSearch(config)


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
# read_markdown / read_body / extract_title
# ---------------------------------------------------------------------------


class TestReadMarkdown:
    def test_reads_frontmatter_and_body(self, vault: Path):
        fm, body = read_markdown(vault / "transformers.md")
        assert fm["title"] == "Transformers"
        assert "Attention Is All You Need" in body

    def test_no_frontmatter(self, vault: Path):
        fm, body = read_markdown(vault / "quick-note.md")
        assert fm == {}
        assert "Quick Note" in body

    def test_nonexistent_file(self, tmp_path: Path):
        fm, body = read_markdown(tmp_path / "nonexistent.md")
        assert fm == {}
        assert body == ""


class TestReadBody:
    def test_returns_body_only(self, vault: Path):
        body = read_body(vault / "transformers.md")
        assert "Attention Is All You Need" in body
        # frontmatter markers should not appear
        assert "---" not in body.split("\n")[0]

    def test_empty_body(self, vault: Path):
        body = read_body(vault / "empty.md")
        assert body.strip() == ""


class TestExtractTitle:
    def test_from_frontmatter(self, vault: Path):
        title = extract_title(vault / "transformers.md")
        assert title == "Transformers"

    def test_from_filename(self, vault: Path):
        title = extract_title(vault / "quick-note.md")
        assert title == "quick note"

    def test_with_preloaded_frontmatter(self, vault: Path):
        title = extract_title(vault / "transformers.md", {"title": "Override"})
        assert title == "Override"

    def test_strips_date_prefix(self, tmp_path: Path):
        path = tmp_path / "2024-01-15-my-article.md"
        path.write_text("# My Article\n\nContent here.\n")
        title = extract_title(path)
        assert title == "my article"


# ---------------------------------------------------------------------------
# tokenize / tokenize_unique
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic_tokenization(self):
        tokens = tokenize("The Transformer architecture")
        assert "the" in tokens
        assert "transformer" in tokens
        assert "architecture" in tokens

    def test_strips_markdown(self):
        tokens = tokenize("**bold** and [[wikilink]] and `code`")
        assert "bold" in tokens
        assert "wikilink" in tokens
        assert "code" in tokens
        assert "**" not in tokens
        assert "[[" not in tokens

    def test_strips_urls(self):
        tokens = tokenize("Visit https://example.com for more info.")
        assert "example" not in tokens  # URL removed entirely
        assert "visit" in tokens
        assert "for" in tokens
        assert "more" in tokens
        assert "info" in tokens

    def test_strips_punctuation(self):
        tokens = tokenize("Hello, world! How's it going?")
        assert "hello" in tokens
        assert "world" in tokens

    def test_filters_short_tokens(self):
        tokens = tokenize("I am a big cat")
        assert "am" in tokens  # length >= 2
        assert "big" in tokens
        assert "cat" in tokens

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_unicode(self):
        tokens = tokenize("Transformer 모델은 자연어 처리에 사용됩니다")
        assert "transformer" in tokens


class TestTokenizeUnique:
    def test_deduplicates(self):
        unique = tokenize_unique("the cat sat on the cat")
        assert "cat" in unique
        assert "the" in unique
        assert len(unique) < 6  # fewer than total tokens


# ---------------------------------------------------------------------------
# _token_matches
# ---------------------------------------------------------------------------


class TestTokenMatches:
    def test_exact_match(self):
        tokens = {"transformer", "model", "architecture"}
        assert _token_matches("transformer", tokens)

    def test_substring_match(self):
        tokens = {"transformer", "model"}
        assert _token_matches("transform", tokens)

    def test_no_match(self):
        tokens = {"transformer", "model"}
        assert not _token_matches("quantum", tokens)

    def test_multi_word_keyword(self):
        tokens = {"language", "model", "large"}
        assert _token_matches("language model", tokens)

    def test_multi_word_partial_miss(self):
        tokens = {"language", "processing"}
        assert not _token_matches("language model", tokens)


# ---------------------------------------------------------------------------
# _build_excerpt
# ---------------------------------------------------------------------------


class TestBuildExcerpt:
    def test_with_context(self):
        lines = ["line 0", "line 1", "MATCH line", "line 3", "line 4"]
        excerpt = _build_excerpt(lines, center_line=2, context_lines=1)
        assert "line 1" in excerpt
        assert "MATCH line" in excerpt
        assert "line 3" in excerpt
        assert "line 0" not in excerpt

    def test_at_beginning(self):
        lines = ["MATCH line", "line 1", "line 2"]
        excerpt = _build_excerpt(lines, center_line=0, context_lines=2)
        assert "MATCH line" in excerpt
        assert "line 1" in excerpt

    def test_at_end(self):
        lines = ["line 0", "line 1", "MATCH line"]
        excerpt = _build_excerpt(lines, center_line=2, context_lines=2)
        assert "line 0" in excerpt
        assert "MATCH line" in excerpt

    def test_truncates_long_excerpts(self):
        lines = ["x" * 100 for _ in range(10)]
        excerpt = _build_excerpt(lines, center_line=5, context_lines=5)
        assert len(excerpt) <= 303  # MAX_EXCERPT_CHARS + "..."


# ---------------------------------------------------------------------------
# _find_regex_matches
# ---------------------------------------------------------------------------


class TestFindRegexMatches:
    def test_finds_matches(self):
        body = "Line one\nAttention is the key\nLine three\n"
        compiled = re.compile(r"attention", re.IGNORECASE)
        matches = _find_regex_matches(body, compiled)
        assert len(matches) == 1
        assert matches[0].line_number == 2
        assert "Attention" in matches[0].line_text

    def test_multiple_matches(self):
        body = "The model uses attention.\nAnother attention layer.\nNo match here.\n"
        compiled = re.compile(r"attention", re.IGNORECASE)
        matches = _find_regex_matches(body, compiled)
        assert len(matches) == 2

    def test_no_matches(self):
        body = "Nothing relevant here.\n"
        compiled = re.compile(r"quantum", re.IGNORECASE)
        matches = _find_regex_matches(body, compiled)
        assert len(matches) == 0

    def test_match_context_has_excerpt(self):
        body = "Before line\nMatch line here\nAfter line\n"
        compiled = re.compile(r"match", re.IGNORECASE)
        matches = _find_regex_matches(body, compiled, context_lines=1)
        assert len(matches) == 1
        assert "Before line" in matches[0].excerpt
        assert "After line" in matches[0].excerpt


# ---------------------------------------------------------------------------
# _find_keyword_matches
# ---------------------------------------------------------------------------


class TestFindKeywordMatches:
    def test_finds_keyword(self):
        body = "The transformer model is powerful.\nIt uses attention.\n"
        matches = _find_keyword_matches(body, ["transformer"])
        assert len(matches) == 1
        assert matches[0].line_number == 1

    def test_multiple_keywords(self):
        body = "Transformers use attention.\nRAG uses retrieval.\n"
        matches = _find_keyword_matches(body, ["transformer", "retrieval"])
        assert len(matches) == 2

    def test_case_insensitive(self):
        body = "TRANSFORMER architecture\n"
        matches = _find_keyword_matches(body, ["transformer"])
        assert len(matches) == 1

    def test_no_match(self):
        body = "Nothing here.\n"
        matches = _find_keyword_matches(body, ["quantum"])
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# ContentSearch.search_regex
# ---------------------------------------------------------------------------


class TestSearchRegex:
    def test_finds_matching_files(self, search: ContentSearch):
        results = search.search_regex(r"attention")
        assert len(results) >= 1
        titles = [r.title for r in results]
        assert "Transformers" in titles

    def test_regex_pattern(self, search: ContentSearch):
        results = search.search_regex(r"attention\s+mechanism")
        assert len(results) >= 1
        assert results[0].title == "Transformers"

    def test_no_match(self, search: ContentSearch):
        results = search.search_regex(r"quantum\s+computing")
        assert len(results) == 0

    def test_excludes_sources(self, search: ContentSearch):
        results = search.search_regex(r"RSS feed")
        assert len(results) == 0

    def test_includes_sources_when_requested(self, search: ContentSearch):
        results = search.search_regex(r"RSS feed", include_sources=True)
        assert len(results) >= 1
        titles = [r.title for r in results]
        assert "Source Article" in titles

    def test_match_count(self, search: ContentSearch):
        results = search.search_regex(r"attention")
        transformer_result = [r for r in results if r.title == "Transformers"]
        assert len(transformer_result) == 1
        # "attention" appears multiple times in transformers.md
        assert transformer_result[0].match_count >= 2

    def test_sorted_by_score(self, search: ContentSearch):
        results = search.search_regex(r"the")
        if len(results) >= 2:
            assert results[0].score >= results[1].score

    def test_max_results(self, search: ContentSearch):
        results = search.search_regex(r".", max_results=2)
        assert len(results) <= 2

    def test_invalid_regex_raises(self, search: ContentSearch):
        with pytest.raises(Exception):  # re.error
            search.search_regex(r"[invalid")

    def test_result_has_excerpts(self, search: ContentSearch):
        results = search.search_regex(r"attention")
        assert results[0].first_excerpt != ""
        assert "attention" in results[0].first_excerpt.lower()

    def test_result_has_wikilink(self, search: ContentSearch):
        results = search.search_regex(r"attention")
        transformer_result = [r for r in results if r.title == "Transformers"][0]
        assert transformer_result.wikilink == "[[transformers]]"


# ---------------------------------------------------------------------------
# ContentSearch.search_keywords
# ---------------------------------------------------------------------------


class TestSearchKeywords:
    def test_single_keyword(self, search: ContentSearch):
        results = search.search_keywords(["transformer"])
        assert len(results) >= 1
        titles = [r.title for r in results]
        assert "Transformers" in titles

    def test_multiple_keywords_or(self, search: ContentSearch):
        results = search.search_keywords(["transformer", "retrieval"])
        assert len(results) >= 2

    def test_multiple_keywords_and(self, search: ContentSearch):
        results = search.search_keywords(
            ["transformer", "attention"], match_all=True
        )
        assert len(results) >= 1
        titles = [r.title for r in results]
        assert "Transformers" in titles
        # RAG page should NOT match (doesn't have "transformer")
        assert "Retrieval-Augmented Generation" not in titles

    def test_no_match(self, search: ContentSearch):
        results = search.search_keywords(["quantum", "entanglement"])
        assert len(results) == 0

    def test_empty_keywords(self, search: ContentSearch):
        results = search.search_keywords([])
        assert len(results) == 0

    def test_excludes_sources(self, search: ContentSearch):
        results = search.search_keywords(["RSS"])
        assert len(results) == 0

    def test_includes_sources_when_requested(self, search: ContentSearch):
        results = search.search_keywords(["RSS"], include_sources=True)
        assert len(results) >= 1

    def test_score_reflects_keyword_coverage(self, search: ContentSearch):
        results = search.search_keywords(
            ["attention", "nonexistent_word_xyz"],
        )
        # Only one of two keywords matched → score = 0.5
        for r in results:
            assert r.score == pytest.approx(0.5)

    def test_all_keywords_matched_gives_full_score(self, search: ContentSearch):
        results = search.search_keywords(
            ["attention", "transformer"], match_all=True,
        )
        for r in results:
            assert r.score == pytest.approx(1.0)

    def test_case_insensitive(self, search: ContentSearch):
        results = search.search_keywords(["TRANSFORMER"])
        assert len(results) >= 1

    def test_finds_page_without_frontmatter(self, search: ContentSearch):
        results = search.search_keywords(["python"])
        assert len(results) >= 1
        titles = [r.title for r in results]
        assert "quick note" in titles


# ---------------------------------------------------------------------------
# ContentSearch.read_content
# ---------------------------------------------------------------------------


class TestReadContent:
    def test_reads_file(self, search: ContentSearch, vault: Path):
        fm, body = search.read_content(vault / "transformers.md")
        assert fm["title"] == "Transformers"
        assert "Attention" in body

    def test_nonexistent(self, search: ContentSearch, vault: Path):
        fm, body = search.read_content(vault / "nope.md")
        assert fm == {}
        assert body == ""


# ---------------------------------------------------------------------------
# ContentMatch dataclass
# ---------------------------------------------------------------------------


class TestContentMatch:
    def test_name(self):
        cm = ContentMatch(
            path=Path("/vault/my-page.md"),
            relative_path=Path("my-page.md"),
            title="My Page",
        )
        assert cm.name == "my-page"

    def test_wikilink(self):
        cm = ContentMatch(
            path=Path("/vault/my-page.md"),
            relative_path=Path("my-page.md"),
            title="My Page",
        )
        assert cm.wikilink == "[[my-page]]"

    def test_first_excerpt_empty(self):
        cm = ContentMatch(
            path=Path("/vault/x.md"),
            relative_path=Path("x.md"),
            title="X",
        )
        assert cm.first_excerpt == ""

    def test_first_excerpt(self):
        cm = ContentMatch(
            path=Path("/vault/x.md"),
            relative_path=Path("x.md"),
            title="X",
            matches=[MatchContext(line_number=1, line_text="hello", excerpt="hello world")],
        )
        assert cm.first_excerpt == "hello world"

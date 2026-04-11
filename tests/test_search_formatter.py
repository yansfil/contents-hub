"""Tests for search_formatter module — search result formatting and user output."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.search_formatter import (
    SearchFormatter,
    highlight_term,
    format_snippet,
    _make_colors,
    _format_score,
    _format_line_number,
    _match_type_label,
    _truncate,
)
from llm_wiki.vault_search import SearchResult, WikiPage
from llm_wiki.content_search import ContentMatch, MatchContext
from llm_wiki.search_ranking import RankedResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plain_colors():
    """No-color mode for deterministic string assertions."""
    return _make_colors(False)


@pytest.fixture
def ansi_colors():
    """ANSI color mode."""
    return _make_colors(True)


@pytest.fixture
def formatter():
    """Formatter with colors disabled for predictable output."""
    return SearchFormatter(vault_path="/test/vault", force_color=False)


@pytest.fixture
def color_formatter():
    """Formatter with colors enabled."""
    return SearchFormatter(vault_path="/test/vault", force_color=True)


@pytest.fixture
def sample_wiki_page():
    return WikiPage(
        path=Path("/vault/transformers.md"),
        relative_path=Path("transformers.md"),
        title="Transformers",
        aliases=["AIAYN", "Attention Is All You Need"],
        tags=["ai", "nlp", "deep-learning"],
    )


@pytest.fixture
def sample_search_results(sample_wiki_page):
    page2 = WikiPage(
        path=Path("/vault/rag.md"),
        relative_path=Path("ai/rag.md"),
        title="RAG Overview",
        aliases=["Retrieval-Augmented Generation"],
        tags=["ai", "llm"],
    )
    return [
        SearchResult(page=sample_wiki_page, match_type="title", match_value="Transformers"),
        SearchResult(page=page2, match_type="tag", match_value="ai"),
    ]


@pytest.fixture
def sample_content_matches():
    return [
        ContentMatch(
            path=Path("/vault/transformers.md"),
            relative_path=Path("transformers.md"),
            title="Transformers",
            matches=[
                MatchContext(
                    line_number=5,
                    line_text="The transformer architecture uses self-attention mechanisms.",
                    match_start=4,
                    match_end=15,
                    excerpt=(
                        "# Transformers\n"
                        "\n"
                        "The transformer architecture uses self-attention mechanisms.\n"
                        "Multi-head attention allows the model to attend."
                    ),
                ),
                MatchContext(
                    line_number=10,
                    line_text="Transformers have revolutionized NLP.",
                    match_start=0,
                    match_end=12,
                    excerpt="Transformers have revolutionized NLP.",
                ),
            ],
            match_count=2,
            score=2.0,
        ),
    ]


@pytest.fixture
def sample_ranked_results():
    return [
        RankedResult(
            doc_id="transformers",
            score=3.45,
            title="Transformers",
            excerpt="...the transformer architecture uses self-attention mechanisms...",
            path="transformers.md",
            metadata={"tags": ["ai", "nlp"], "aliases": ["AIAYN"]},
        ),
        RankedResult(
            doc_id="rag",
            score=1.23,
            title="RAG Overview",
            excerpt="...transformer model retrieves relevant documents...",
            path="ai/rag.md",
            metadata={"tags": ["ai", "llm"], "aliases": []},
        ),
    ]


# ---------------------------------------------------------------------------
# highlight_term
# ---------------------------------------------------------------------------


class TestHighlightTerm:
    def test_plain_highlight(self, plain_colors):
        result = highlight_term("The Transformer model", "transformer", plain_colors)
        assert result == "The **Transformer** model"

    def test_multiple_occurrences(self, plain_colors):
        result = highlight_term(
            "transformer uses transformer layers",
            "transformer",
            plain_colors,
        )
        assert result == "**transformer** uses **transformer** layers"

    def test_multi_word_query(self, plain_colors):
        result = highlight_term(
            "The transformer uses attention mechanisms",
            "transformer attention",
            plain_colors,
        )
        assert "**transformer**" in result
        assert "**attention**" in result

    def test_case_insensitive(self, plain_colors):
        result = highlight_term("TRANSFORMER Model", "transformer", plain_colors)
        assert result == "**TRANSFORMER** Model"

    def test_no_match(self, plain_colors):
        result = highlight_term("no match here", "xyz", plain_colors)
        assert result == "no match here"

    def test_empty_query(self, plain_colors):
        result = highlight_term("some text", "", plain_colors)
        assert result == "some text"

    def test_empty_text(self, plain_colors):
        result = highlight_term("", "query", plain_colors)
        assert result == ""

    def test_ansi_highlight(self, ansi_colors):
        result = highlight_term("The Transformer", "transformer", ansi_colors)
        assert "\033[1m" in result  # bold
        assert "\033[33m" in result  # yellow
        assert "\033[0m" in result  # reset
        assert "Transformer" in result


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_no_truncation(self):
        assert _truncate("short", 10) == "short"

    def test_truncation(self):
        assert _truncate("a long string here", 10) == "a long ..."

    def test_exact_length(self):
        assert _truncate("exact", 5) == "exact"


# ---------------------------------------------------------------------------
# _format_score
# ---------------------------------------------------------------------------


class TestFormatScore:
    def test_small_score(self):
        assert _format_score(1.23) == "1.23"

    def test_large_score(self):
        assert _format_score(12.5) == "12.5"

    def test_zero(self):
        assert _format_score(0.0) == "0.00"


# ---------------------------------------------------------------------------
# _format_line_number
# ---------------------------------------------------------------------------


class TestFormatLineNumber:
    def test_single_digit(self):
        assert _format_line_number(5) == "   5"

    def test_multi_digit(self):
        assert _format_line_number(123) == " 123"


# ---------------------------------------------------------------------------
# _match_type_label
# ---------------------------------------------------------------------------


class TestMatchTypeLabel:
    def test_title_label(self, plain_colors):
        assert "[TITLE]" in _match_type_label("title", plain_colors)

    def test_alias_label(self, plain_colors):
        assert "[ALIAS]" in _match_type_label("alias", plain_colors)

    def test_tag_label(self, plain_colors):
        assert "[TAG]" in _match_type_label("tag", plain_colors)

    def test_unknown_label(self, plain_colors):
        assert "[CUSTOM]" in _match_type_label("custom", plain_colors)

    def test_colored_label(self, ansi_colors):
        result = _match_type_label("title", ansi_colors)
        assert "\033[32m" in result  # green


# ---------------------------------------------------------------------------
# format_snippet
# ---------------------------------------------------------------------------


class TestFormatSnippet:
    def test_basic_snippet(self, plain_colors):
        match = MatchContext(
            line_number=5,
            line_text="This line has the keyword match.",
            match_start=18,
            match_end=25,
            excerpt=(
                "Context line before.\n"
                "This line has the keyword match.\n"
                "Context line after."
            ),
        )
        result = format_snippet(match, "keyword", plain_colors)
        assert "**keyword**" in result
        assert "Context line before" in result
        assert "Context line after" in result

    def test_snippet_without_excerpt(self, plain_colors):
        match = MatchContext(
            line_number=10,
            line_text="Just a single matched line.",
            match_start=0,
            match_end=4,
            excerpt="",
        )
        result = format_snippet(match, "Just", plain_colors)
        assert "**Just**" in result
        assert "10" in result

    def test_snippet_with_line_numbers(self, plain_colors):
        match = MatchContext(
            line_number=5,
            line_text="matched line",
            match_start=0,
            match_end=7,
            excerpt="before\nmatched line\nafter",
        )
        result = format_snippet(match, "matched", plain_colors)
        # Should contain line numbers
        assert "│" in result


# ---------------------------------------------------------------------------
# SearchFormatter — metadata results
# ---------------------------------------------------------------------------


class TestFormatMetadataResults:
    def test_empty_results(self, formatter):
        output = formatter.format_metadata_results([], "test")
        assert "No results" in output

    def test_basic_output(self, formatter, sample_search_results):
        output = formatter.format_metadata_results(sample_search_results, "transformer")
        assert "2 result(s)" in output
        assert "**Transformer**s" in output
        assert "transformers.md" in output
        assert "[TITLE]" in output
        assert "[TAG]" in output

    def test_shows_tags(self, formatter, sample_search_results):
        output = formatter.format_metadata_results(sample_search_results, "transformer")
        assert "#ai" in output
        assert "#nlp" in output

    def test_shows_aliases(self, formatter, sample_search_results):
        output = formatter.format_metadata_results(sample_search_results, "transformer")
        assert "AIAYN" in output

    def test_shows_path(self, formatter, sample_search_results):
        output = formatter.format_metadata_results(sample_search_results, "ai")
        assert "ai/rag.md" in output

    def test_shows_match_value_for_non_title(self, formatter, sample_search_results):
        output = formatter.format_metadata_results(sample_search_results, "ai")
        # Tag match should show the matched value
        assert "matched:" in output


# ---------------------------------------------------------------------------
# SearchFormatter — content results
# ---------------------------------------------------------------------------


class TestFormatContentResults:
    def test_empty_results(self, formatter):
        output = formatter.format_content_results([], "test")
        assert "No content matches" in output

    def test_basic_output(self, formatter, sample_content_matches):
        output = formatter.format_content_results(sample_content_matches, "transformer")
        assert "1 file(s)" in output
        assert "**Transformer**s" in output
        assert "transformers.md" in output

    def test_shows_match_count(self, formatter, sample_content_matches):
        output = formatter.format_content_results(sample_content_matches, "transformer")
        assert "2 matches" in output

    def test_shows_snippets(self, formatter, sample_content_matches):
        output = formatter.format_content_results(sample_content_matches, "transformer")
        assert "self-attention" in output

    def test_max_snippets(self, formatter, sample_content_matches):
        output = formatter.format_content_results(
            sample_content_matches, "transformer", max_snippets_per_file=1,
        )
        # Should show "and 1 more match"
        assert "more match" in output


# ---------------------------------------------------------------------------
# SearchFormatter — ranked results
# ---------------------------------------------------------------------------


class TestFormatRankedResults:
    def test_empty_results(self, formatter):
        output = formatter.format_ranked_results([], "test")
        assert "No results" in output

    def test_basic_output(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "2 result(s)" in output
        assert "**Transformer**s" in output

    def test_shows_score(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "3.45" in output
        assert "1.23" in output

    def test_shows_path(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "transformers.md" in output
        assert "ai/rag.md" in output

    def test_shows_wikilink(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "[[transformers]]" in output
        assert "[[rag]]" in output

    def test_shows_excerpt(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "self-attention" in output

    def test_shows_tags(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "#ai" in output
        assert "#nlp" in output

    def test_shows_aliases(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "AIAYN" in output


# ---------------------------------------------------------------------------
# SearchFormatter — tags
# ---------------------------------------------------------------------------


class TestFormatTags:
    def test_empty_tags(self, formatter):
        output = formatter.format_tags({})
        assert "No tags" in output

    def test_basic_tags(self, formatter):
        tags = {"ai": 5, "nlp": 3, "devops": 1}
        output = formatter.format_tags(tags)
        assert "3 unique" in output
        assert "#ai" in output
        assert "#nlp" in output
        assert "#devops" in output
        assert "5" in output
        assert "█" in output


# ---------------------------------------------------------------------------
# SearchFormatter — JSON output
# ---------------------------------------------------------------------------


class TestFormatJson:
    def test_metadata_json(self, formatter, sample_search_results):
        output = formatter.format_metadata_json(sample_search_results)
        import json
        data = json.loads(output)
        assert len(data) == 2
        assert data[0]["title"] == "Transformers"
        assert data[0]["match_type"] == "title"
        assert data[0]["wikilink"] == "[[transformers]]"
        assert "ai" in data[0]["tags"]
        assert "AIAYN" in data[0]["aliases"]
        assert data[0]["path"] == "transformers.md"

    def test_content_json(self, formatter, sample_content_matches):
        output = formatter.format_content_json(sample_content_matches)
        import json
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["match_count"] == 2
        assert len(data[0]["matches"]) == 2
        assert data[0]["matches"][0]["line_number"] == 5

    def test_ranked_json(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_json(sample_ranked_results)
        import json
        data = json.loads(output)
        assert len(data) == 2
        assert data[0]["score"] == 3.45
        assert data[0]["doc_id"] == "transformers"
        assert data[0]["wikilink"] == "[[transformers]]"

    def test_tags_json(self, formatter):
        tags = {"ai": 5, "nlp": 3}
        output = formatter.format_tags_json(tags)
        import json
        data = json.loads(output)
        assert len(data) == 2
        assert data[0]["tag"] == "ai"
        assert data[0]["count"] == 5


# ---------------------------------------------------------------------------
# SearchFormatter — color mode
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SearchFormatter — semantic search results
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_semantic_results():
    from llm_wiki.search_formatter import SemanticResult
    return [
        SemanticResult(
            doc_id="transformers",
            score=0.87,
            title="Transformers",
            path="transformers.md",
            tags=["ai", "nlp"],
            aliases=["AIAYN"],
            excerpt="The transformer architecture uses self-attention mechanisms...",
            chunk_count=5,
            best_chunk_index=2,
        ),
        SemanticResult(
            doc_id="rag",
            score=0.52,
            title="Retrieval-Augmented Generation",
            path="ai/rag.md",
            tags=["ai", "llm"],
            aliases=["RAG"],
            excerpt="RAG combines retrieval with language models...",
            chunk_count=3,
            best_chunk_index=0,
        ),
        SemanticResult(
            doc_id="misc",
            score=0.25,
            title="Miscellaneous Notes",
            path="misc.md",
            tags=[],
            aliases=[],
            excerpt="Some loosely related text...",
            chunk_count=1,
            best_chunk_index=0,
        ),
    ]


class TestFormatSemanticResults:
    def test_empty_results(self, formatter):
        output = formatter.format_semantic_results([], "test")
        assert "No semantic results" in output

    def test_basic_output(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "3 result(s)" in output
        assert "Semantic search" in output
        assert "cosine similarity" in output

    def test_shows_score_percent(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "87.0%" in output
        assert "52.0%" in output

    def test_shows_relevance_labels(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "Very High" in output  # 0.87
        assert "Medium" in output     # 0.52
        assert "Marginal" in output   # 0.25

    def test_shows_path(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "transformers.md" in output
        assert "ai/rag.md" in output

    def test_shows_wikilink(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "[[transformers]]" in output
        assert "[[rag]]" in output

    def test_shows_chunk_info(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "5 chunks indexed" in output
        assert "chunk #2" in output

    def test_shows_tags(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "#ai" in output
        assert "#nlp" in output

    def test_shows_excerpt(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_results(sample_semantic_results, "transformer")
        assert "self-attention" in output


class TestFormatSemanticJson:
    def test_basic_json(self, formatter, sample_semantic_results):
        output = formatter.format_semantic_json(sample_semantic_results)
        import json
        data = json.loads(output)
        assert len(data) == 3
        assert data[0]["doc_id"] == "transformers"
        assert data[0]["score"] == 0.87
        assert data[0]["relevance"] == "Very High"
        assert data[0]["score_percent"] == "87.0%"
        assert data[0]["wikilink"] == "[[transformers]]"
        assert data[0]["tags"] == ["ai", "nlp"]
        assert data[0]["chunk_count"] == 5
        assert data[0]["best_chunk_index"] == 2

    def test_json_has_no_ansi(self, color_formatter, sample_semantic_results):
        output = color_formatter.format_semantic_json(sample_semantic_results)
        assert "\033[" not in output


class TestSemanticResultRelevanceLabel:
    def test_very_high(self):
        from llm_wiki.search_formatter import SemanticResult
        r = SemanticResult(doc_id="a", score=0.90)
        assert r.relevance_label == "Very High"

    def test_high(self):
        from llm_wiki.search_formatter import SemanticResult
        r = SemanticResult(doc_id="a", score=0.72)
        assert r.relevance_label == "High"

    def test_medium(self):
        from llm_wiki.search_formatter import SemanticResult
        r = SemanticResult(doc_id="a", score=0.55)
        assert r.relevance_label == "Medium"

    def test_low(self):
        from llm_wiki.search_formatter import SemanticResult
        r = SemanticResult(doc_id="a", score=0.35)
        assert r.relevance_label == "Low"

    def test_marginal(self):
        from llm_wiki.search_formatter import SemanticResult
        r = SemanticResult(doc_id="a", score=0.15)
        assert r.relevance_label == "Marginal"


class TestColorMode:
    def test_no_color_mode(self, formatter, sample_ranked_results):
        output = formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "\033[" not in output  # no ANSI escape codes

    def test_color_mode(self, color_formatter, sample_ranked_results):
        output = color_formatter.format_ranked_results(sample_ranked_results, "transformer")
        assert "\033[" in output  # has ANSI escape codes

    def test_json_has_no_ansi(self, color_formatter, sample_ranked_results):
        """JSON output should never contain ANSI codes (it's data, not display)."""
        output = color_formatter.format_ranked_json(sample_ranked_results)
        assert "\033[" not in output

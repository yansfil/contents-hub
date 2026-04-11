"""Tests for compile result preview formatting."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm_wiki.preview import (
    CompileAction,
    CompileResult,
    SourceReference,
    _Style,
    _action_label,
    _format_source_table,
    _format_yaml_preview,
    _highlight_line,
    preview_compile_batch,
    preview_compile_result,
    preview_routing_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TIME = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_sources() -> list[SourceReference]:
    return [
        SourceReference(
            path="sources/rss/2024-06-15-transformers-explained.md",
            title="Transformers Explained",
            source_type="rss",
            url="https://blog.example.com/transformers",
            score=0.92,
        ),
        SourceReference(
            path="sources/youtube/2024-06-14-attention-is-all-you-need.md",
            title="Attention Is All You Need - Paper Review",
            source_type="youtube",
            url="https://youtube.com/watch?v=abc123",
            score=0.85,
        ),
    ]


@pytest.fixture
def sample_result(sample_sources: list[SourceReference]) -> CompileResult:
    return CompileResult(
        title="Transformer Architecture",
        body=(
            "# Transformer Architecture\n"
            "\n"
            "The transformer architecture revolutionized NLP by introducing\n"
            "the self-attention mechanism. See [[Self-Attention]] for details.\n"
            "\n"
            "## Key Components\n"
            "\n"
            "- Multi-head attention\n"
            "- Positional encoding\n"
            "- Feed-forward networks\n"
            "\n"
            "> The original paper proposed the encoder-decoder structure.\n"
            "\n"
            "Related: [[BERT]], [[GPT]], [[Vision Transformers]]\n"
            "\n"
            "#ai #transformers #deep-learning\n"
        ),
        frontmatter={
            "title": "Transformer Architecture",
            "type": "wiki",
            "lens": "ai-research",
            "tags": ["ai", "transformers", "deep-learning"],
            "sources": 2,
            "compiled_at": "2024-06-15T10:30:00+00:00",
        },
        lens_id="ai-research",
        lens_name="AI Research",
        directory="topics/ai-research",
        action=CompileAction.CREATE,
        sources=sample_sources,
        wikilinks=["Self-Attention", "BERT", "GPT", "Vision Transformers"],
        tags=["ai", "transformers", "deep-learning"],
        compile_strategy="merge",
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def update_result() -> CompileResult:
    return CompileResult(
        title="Prompt Engineering",
        body=(
            "# Prompt Engineering\n"
            "\n"
            "Updated with new chain-of-thought techniques.\n"
        ),
        frontmatter={"title": "Prompt Engineering", "type": "wiki"},
        lens_id="ai-research",
        lens_name="AI Research",
        directory="topics/ai-research",
        action=CompileAction.UPDATE,
        sources=[
            SourceReference(
                path="sources/rss/2024-06-15-cot.md",
                title="Chain of Thought Prompting",
                source_type="rss",
                score=0.78,
            ),
        ],
        tags=["ai", "prompting"],
        existing_path="topics/ai-research/prompt-engineering.md",
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def skip_result() -> CompileResult:
    return CompileResult(
        title="Neural Networks Basics",
        body="# Neural Networks Basics\n\nNo changes needed.\n",
        action=CompileAction.SKIP,
        compiled_at=FIXED_TIME,
    )


# ---------------------------------------------------------------------------
# CompileResult model tests
# ---------------------------------------------------------------------------


class TestCompileResult:
    def test_word_count_auto(self):
        r = CompileResult(title="Test", body="one two three four five")
        assert r.word_count == 5

    def test_word_count_explicit(self):
        r = CompileResult(title="Test", body="one two three", word_count=100)
        assert r.word_count == 100

    def test_compiled_at_default(self):
        r = CompileResult(title="Test", body="")
        assert r.compiled_at is not None

    def test_target_path_with_directory(self, sample_result: CompileResult):
        assert sample_result.target_path == "topics/ai-research/transformer-architecture.md"

    def test_target_path_without_directory(self):
        r = CompileResult(title="Test Page", body="content")
        assert r.target_path == "test-page.md"


# ---------------------------------------------------------------------------
# Style tests
# ---------------------------------------------------------------------------


class TestStyle:
    def test_enabled_wraps(self):
        s = _Style(enabled=True)
        result = s.bold("hello")
        assert "\033[1m" in result
        assert "hello" in result
        assert "\033[0m" in result

    def test_disabled_passthrough(self):
        s = _Style(enabled=False)
        assert s.bold("hello") == "hello"
        assert s.green("hello") == "hello"
        assert s.dim("hello") == "hello"


# ---------------------------------------------------------------------------
# Single preview tests
# ---------------------------------------------------------------------------


class TestPreviewCompileResult:
    def test_contains_title(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "Transformer Architecture" in output

    def test_contains_action_label(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "[NEW]" in output

    def test_update_action_label(self, update_result: CompileResult):
        output = preview_compile_result(update_result, color=False)
        assert "[UPD]" in output

    def test_contains_target_path(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "topics/ai-research/transformer-architecture.md" in output

    def test_contains_lens_info(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "AI Research" in output
        assert "ai-research" in output

    def test_contains_strategy(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "merge" in output

    def test_contains_word_count(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "Words:" in output

    def test_contains_frontmatter(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "---" in output
        assert "title: Transformer Architecture" in output
        assert "type: wiki" in output

    def test_hide_frontmatter(self, sample_result: CompileResult):
        output = preview_compile_result(
            sample_result, color=False, show_frontmatter=False
        )
        assert "type: wiki" not in output

    def test_contains_body(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "self-attention mechanism" in output

    def test_body_truncation(self, sample_result: CompileResult):
        output = preview_compile_result(
            sample_result, color=False, max_body_lines=3
        )
        assert "more lines" in output

    def test_no_truncation_when_unlimited(self, sample_result: CompileResult):
        output = preview_compile_result(
            sample_result, color=False, max_body_lines=0
        )
        assert "more lines" not in output

    def test_contains_tags(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "#ai" in output
        assert "#transformers" in output

    def test_contains_wikilinks(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "[[Self-Attention]]" in output
        assert "[[BERT]]" in output

    def test_contains_source_table(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "Transformers Explained" in output
        assert "0.92" in output
        assert "rss" in output

    def test_hide_sources(self, sample_result: CompileResult):
        output = preview_compile_result(
            sample_result, color=False, show_sources=False
        )
        assert "Sources:" not in output

    def test_color_output(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=True)
        assert "\033[" in output  # Contains ANSI codes

    def test_no_color_output(self, sample_result: CompileResult):
        output = preview_compile_result(sample_result, color=False)
        assert "\033[" not in output


# ---------------------------------------------------------------------------
# Batch preview tests
# ---------------------------------------------------------------------------


class TestPreviewCompileBatch:
    def test_empty_batch(self):
        output = preview_compile_batch([], color=False)
        assert "No compile results" in output

    def test_batch_header(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = preview_compile_batch(
            [sample_result, update_result], color=False
        )
        assert "COMPILE PREVIEW" in output
        assert "2 total" in output

    def test_batch_counts(
        self,
        sample_result: CompileResult,
        update_result: CompileResult,
        skip_result: CompileResult,
    ):
        output = preview_compile_batch(
            [sample_result, update_result, skip_result], color=False
        )
        assert "1 new" in output
        assert "1 updated" in output
        assert "1 skipped" in output

    def test_batch_total_words(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = preview_compile_batch(
            [sample_result, update_result], color=False
        )
        assert "Words:" in output

    def test_batch_skips_skip_details(self, skip_result: CompileResult):
        output = preview_compile_batch([skip_result], color=False)
        assert "[SKIP]" in output
        assert "no changes" in output

    def test_batch_lens_list(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = preview_compile_batch(
            [sample_result, update_result], color=False
        )
        assert "Lenses:" in output
        assert "ai-research" in output

    def test_batch_includes_individual_previews(
        self, sample_result: CompileResult
    ):
        output = preview_compile_batch([sample_result], color=False)
        assert "Transformer Architecture" in output
        assert "[NEW]" in output


# ---------------------------------------------------------------------------
# Routing summary tests
# ---------------------------------------------------------------------------


class TestPreviewRoutingSummary:
    def test_header(self, sample_result: CompileResult):
        output = preview_routing_summary([sample_result], color=False)
        assert "Routing Summary" in output

    def test_table_format(self, sample_result: CompileResult):
        output = preview_routing_summary([sample_result], color=False)
        assert "| Lens |" in output
        assert "| ai-research |" in output
        assert "| Transformer Architecture |" in output

    def test_action_in_table(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = preview_routing_summary(
            [sample_result, update_result], color=False
        )
        assert "[NEW]" in output
        assert "[UPD]" in output


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatYamlPreview:
    def test_simple_string(self):
        assert _format_yaml_preview("title", "Hello") == "title: Hello"

    def test_integer(self):
        assert _format_yaml_preview("count", 42) == "count: 42"

    def test_boolean(self):
        assert _format_yaml_preview("enabled", True) == "enabled: true"
        assert _format_yaml_preview("enabled", False) == "enabled: false"

    def test_list(self):
        result = _format_yaml_preview("tags", ["ai", "ml"])
        assert "tags:" in result
        assert "  - ai" in result
        assert "  - ml" in result

    def test_empty_list(self):
        assert _format_yaml_preview("tags", []) == "tags: []"


class TestHighlightLine:
    def test_heading(self):
        s = _Style(enabled=True)
        result = _highlight_line("# Hello", s)
        assert "\033[1m" in result

    def test_blockquote(self):
        s = _Style(enabled=True)
        result = _highlight_line("> A quote", s)
        assert "\033[2m" in result

    def test_wikilink(self):
        s = _Style(enabled=True)
        result = _highlight_line("See [[Page Name]] for details", s)
        assert "\033[34m" in result  # blue

    def test_tag_highlight(self):
        s = _Style(enabled=True)
        result = _highlight_line("Topics: #ai #ml", s)
        assert "\033[36m" in result  # cyan

    def test_no_highlight_plain(self):
        s = _Style(enabled=False)
        result = _highlight_line("plain text", s)
        assert result == "plain text"


class TestActionLabel:
    def test_create(self):
        s = _Style(enabled=False)
        assert _action_label(CompileAction.CREATE, s) == "[NEW]"

    def test_update(self):
        s = _Style(enabled=False)
        assert _action_label(CompileAction.UPDATE, s) == "[UPD]"

    def test_skip(self):
        s = _Style(enabled=False)
        assert _action_label(CompileAction.SKIP, s) == "[SKIP]"


class TestSourceTable:
    def test_format(self):
        s = _Style(enabled=False)
        sources = [
            SourceReference(
                path="sources/rss/post.md",
                title="A Blog Post",
                source_type="rss",
                score=0.75,
            ),
        ]
        output = _format_source_table(sources, s)
        assert "A Blog Post" in output
        assert "0.75" in output
        assert "rss" in output

    def test_long_title_truncated(self):
        s = _Style(enabled=False)
        sources = [
            SourceReference(
                path="sources/rss/post.md",
                title="A" * 50,
                source_type="rss",
                score=0.5,
            ),
        ]
        output = _format_source_table(sources, s)
        assert "..." in output

    def test_zero_score_shows_dash(self):
        s = _Style(enabled=False)
        sources = [
            SourceReference(
                path="sources/browser/page.md",
                title="Manual Page",
                source_type="browser",
                score=0.0,
            ),
        ]
        output = _format_source_table(sources, s)
        assert "—" in output

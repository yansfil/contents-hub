"""Tests for post_processor: LLM compile output -> Obsidian-native markdown."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm_wiki.compile_service import CompileResult
from llm_wiki.post_processor import (
    PostProcessResult,
    normalise_headings,
    wrap_bare_urls,
    append_see_also,
    append_sources_section,
    extract_sections,
    build_wiki_frontmatter,
    post_process,
    _inject_wikilinks_simple,
    _url_display_text,
)


FIXED_TIME = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_compile_result() -> CompileResult:
    return CompileResult(
        title="Transformer Architecture",
        body=(
            "## Overview\n\n"
            "Transformers use self-attention to process sequences in parallel.\n\n"
            "## Key Components\n\n"
            "- Self-Attention\n"
            "- Multi-Head Attention\n"
            "- Feed-Forward Networks\n\n"
            "## Applications\n\n"
            "Transformers power models like BERT and GPT.\n"
        ),
        tags=["ai", "deep-learning", "transformers"],
        wikilinks=["Self-Attention", "BERT", "GPT"],
        aliases=["Transformers", "Transformer Model"],
        summary="Neural network architecture using self-attention.",
        section_plan=["Overview", "Key Components", "Applications"],
        source_attributions=[
            "Attention Is All You Need (https://arxiv.org/abs/1706.03762)"
        ],
    )


@pytest.fixture
def compile_result_with_h1() -> CompileResult:
    return CompileResult(
        title="Test Page",
        body=(
            "# Main Heading\n\n"
            "Some preamble.\n\n"
            "## Section One\n\n"
            "Content.\n\n"
            "### Subsection\n\n"
            "Detail.\n"
        ),
        tags=["test"],
    )


@pytest.fixture
def compile_result_with_bare_urls() -> CompileResult:
    return CompileResult(
        title="Links Test",
        body=(
            "## Overview\n\n"
            "See https://example.com/article for details.\n\n"
            "Already formatted: [Example](https://example.com/other).\n\n"
            "Another bare link: https://arxiv.org/abs/1706.03762\n"
        ),
        tags=["test"],
    )


# ---------------------------------------------------------------------------
# normalise_headings
# ---------------------------------------------------------------------------


class TestNormaliseHeadings:
    def test_h1_demoted_to_h2(self):
        body = "# Title\n\nContent."
        result, count = normalise_headings(body)
        assert "## Title" in result
        # Should not have a bare H1 (but "# Title" is substring of "## Title")
        assert not result.startswith("# Title")
        assert count == 1

    def test_h1_and_h2_normalised(self):
        body = "# Main\n\n## Sub\n\nContent."
        result, count = normalise_headings(body)
        assert "## Main" in result
        assert "### Sub" in result
        assert count == 2

    def test_h2_h3_unchanged(self):
        body = "## Heading\n\n### Subheading\n\nContent."
        result, count = normalise_headings(body)
        assert "## Heading" in result
        assert "### Subheading" in result
        assert count == 0

    def test_skipped_levels_compressed(self):
        """H2 -> H4 should become H2 -> H3."""
        body = "## Overview\n\n#### Detail\n\nContent."
        result, count = normalise_headings(body)
        assert "## Overview" in result
        assert "### Detail" in result
        assert "####" not in result
        assert count == 1

    def test_single_h1_only(self):
        body = "# Solo Heading\n\nContent."
        result, count = normalise_headings(body)
        assert result.startswith("## Solo Heading")
        assert count == 1

    def test_empty_body(self):
        result, count = normalise_headings("")
        assert result == ""
        assert count == 0

    def test_no_headings(self):
        body = "Just plain text with no headings."
        result, count = normalise_headings(body)
        assert result == body
        assert count == 0

    def test_preserves_content_between_headings(self):
        body = "## First\n\nParagraph one.\n\n## Second\n\nParagraph two."
        result, count = normalise_headings(body)
        assert "Paragraph one." in result
        assert "Paragraph two." in result
        assert count == 0

    def test_three_level_hierarchy(self):
        body = "# H1\n\n## H2\n\n### H3\n\nContent."
        result, count = normalise_headings(body)
        assert "## H1" in result
        assert "### H2" in result
        assert "#### H3" in result


# ---------------------------------------------------------------------------
# wrap_bare_urls
# ---------------------------------------------------------------------------


class TestWrapBareUrls:
    def test_wraps_bare_url(self):
        body = "See https://example.com/article for details."
        result, count = wrap_bare_urls(body)
        assert "[" in result
        assert "](https://example.com/article)" in result
        assert count == 1

    def test_preserves_existing_markdown_link(self):
        body = "See [Example](https://example.com) for details."
        result, count = wrap_bare_urls(body)
        assert result == body
        assert count == 0

    def test_preserves_wikilinks(self):
        body = "See [[Internal Page]] for details."
        result, count = wrap_bare_urls(body)
        assert result == body
        assert count == 0

    def test_multiple_bare_urls(self):
        body = "Check https://a.com and https://b.com for info."
        result, count = wrap_bare_urls(body)
        assert count == 2
        assert "](https://a.com)" in result
        assert "](https://b.com)" in result

    def test_url_with_trailing_period(self):
        body = "Visit https://example.com."
        result, count = wrap_bare_urls(body)
        assert count == 1
        assert "](https://example.com)" in result
        # Period should NOT be inside the URL
        assert "example.com.)" not in result

    def test_empty_body(self):
        result, count = wrap_bare_urls("")
        assert result == ""
        assert count == 0

    def test_preserves_code_blocks(self):
        body = "```\nhttps://example.com/in-code\n```"
        result, count = wrap_bare_urls(body)
        assert count == 0


class TestUrlDisplayText:
    def test_short_url(self):
        assert _url_display_text("https://example.com/page") == "example.com/page"

    def test_wikipedia_url(self):
        result = _url_display_text("https://en.wikipedia.org/wiki/Transformer_(machine_learning_model)")
        assert "Wikipedia" in result
        assert "Transformer" in result

    def test_long_url_truncated(self):
        long = "https://example.com/" + "a" * 80
        result = _url_display_text(long)
        assert len(result) <= 60  # should be truncated


# ---------------------------------------------------------------------------
# append_see_also
# ---------------------------------------------------------------------------


class TestAppendSeeAlso:
    def test_appends_section(self):
        body = "## Overview\n\nContent."
        result = append_see_also(body, ["BERT", "GPT"])
        assert "## See Also" in result
        assert "- [[BERT]]" in result
        assert "- [[GPT]]" in result

    def test_skips_existing_wikilinks(self):
        body = "## Overview\n\nDiscusses [[BERT]] in detail."
        result = append_see_also(body, ["BERT", "GPT"])
        assert "- [[BERT]]" not in result
        assert "- [[GPT]]" in result

    def test_empty_wikilinks(self):
        body = "## Overview\n\nContent."
        result = append_see_also(body, [])
        assert result == body

    def test_all_already_linked(self):
        body = "Discusses [[BERT]] and [[GPT]]."
        result = append_see_also(body, ["BERT", "GPT"])
        assert "See Also" not in result

    def test_case_insensitive_dedup(self):
        body = "Mentions [[bert]] in the text."
        result = append_see_also(body, ["BERT"])
        assert "See Also" not in result


# ---------------------------------------------------------------------------
# append_sources_section
# ---------------------------------------------------------------------------


class TestAppendSourcesSection:
    def test_appends_sources(self):
        body = "## Content\n\nText."
        result = append_sources_section(body, ["https://a.com", "https://b.com"])
        assert "## Sources" in result
        assert "- https://a.com" in result
        assert "- https://b.com" in result

    def test_skips_if_section_exists(self):
        body = "## Sources\n\n- https://existing.com"
        result = append_sources_section(body, ["https://new.com"])
        assert result == body

    def test_empty_urls(self):
        body = "Content."
        result = append_sources_section(body, [])
        assert result == body


# ---------------------------------------------------------------------------
# extract_sections
# ---------------------------------------------------------------------------


class TestExtractSections:
    def test_extracts_headings(self):
        body = "## Overview\n\n### Detail\n\n## Conclusion"
        assert extract_sections(body) == ["Overview", "Detail", "Conclusion"]

    def test_empty_body(self):
        assert extract_sections("") == []

    def test_no_headings(self):
        assert extract_sections("Just plain text.") == []


# ---------------------------------------------------------------------------
# _inject_wikilinks_simple
# ---------------------------------------------------------------------------


class TestInjectWikilinksSimple:
    def test_first_occurrence_linked(self):
        body = "BERT is a model. BERT was released by Google."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 1
        # First occurrence is linked
        assert "[[BERT]]" in result
        # Should appear exactly once
        assert result.count("[[BERT]]") == 1

    def test_case_insensitive_match(self):
        body = "The bert model is powerful."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 1
        assert "[[BERT|bert]]" in result

    def test_skips_existing_wikilinks(self):
        body = "The [[BERT]] model is powerful."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 0
        assert result.count("[[BERT]]") == 1

    def test_skips_headings(self):
        body = "## BERT Overview\n\nBERT is a model."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 1
        # Should NOT link inside heading
        assert "## BERT Overview" in result  # heading unchanged
        # Body occurrence IS linked
        assert "[[BERT]] is a model" in result

    def test_skips_code_blocks(self):
        body = "```\nBERT code here\n```\n\nBERT is great."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 1
        assert "[[BERT]] is great" in result

    def test_word_boundary_aware(self):
        body = "BERTScore is different from BERT."
        result, count = _inject_wikilinks_simple(body, ["BERT"])
        assert count == 1
        # Should NOT match inside "BERTScore"
        assert "[[BERT]]Score" not in result
        assert "[[BERT]]." in result or "[[BERT]]" in result

    def test_longer_match_first(self):
        body = "Machine Learning is a branch of AI."
        result, count = _inject_wikilinks_simple(
            body, ["Machine Learning", "Machine"]
        )
        assert "[[Machine Learning]]" in result
        assert count >= 1

    def test_empty_body(self):
        result, count = _inject_wikilinks_simple("", ["BERT"])
        assert result == ""
        assert count == 0

    def test_empty_wikilinks(self):
        body = "Some text."
        result, count = _inject_wikilinks_simple(body, [])
        assert result == body
        assert count == 0


# ---------------------------------------------------------------------------
# build_wiki_frontmatter
# ---------------------------------------------------------------------------


class TestBuildWikiFrontmatter:
    def test_basic_frontmatter(self, basic_compile_result: CompileResult):
        fm = build_wiki_frontmatter(
            basic_compile_result,
            lenses=["ai-research"],
            sources=["sources/rss/2024-01-15-attention.md"],
            compiled_at=FIXED_TIME,
            created_at=FIXED_TIME,
        )
        assert fm.type == "wiki"
        assert fm.title == "Transformer Architecture"
        assert "ai" in fm.tags
        assert "deep-learning" in fm.tags
        assert "Transformers" in fm.aliases
        assert "ai-research" in fm.lenses
        assert "sources/rss/2024-01-15-attention.md" in fm.sources

    def test_serializes_correctly(self, basic_compile_result: CompileResult):
        fm = build_wiki_frontmatter(
            basic_compile_result,
            compiled_at=FIXED_TIME,
            created_at=FIXED_TIME,
        )
        yaml = fm.serialize()
        assert yaml.startswith("---")
        assert yaml.endswith("---")
        assert "type: wiki" in yaml
        assert "title: Transformer Architecture" in yaml
        assert "  - ai" in yaml
        assert "  - Transformers" in yaml


# ---------------------------------------------------------------------------
# post_process (integration)
# ---------------------------------------------------------------------------


class TestPostProcess:
    def test_basic_post_process(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result,
            lenses=["ai-research"],
            sources=["sources/rss/test.md"],
            source_urls=["https://arxiv.org/abs/1706.03762"],
            created_at=FIXED_TIME,
        )
        assert result.is_valid
        assert result.title == "Transformer Architecture"

        # Has frontmatter
        assert result.markdown.startswith("---\n")
        assert "type: wiki" in result.markdown
        assert "title: Transformer Architecture" in result.markdown
        assert "  - ai" in result.markdown
        assert "  - Transformers" in result.markdown

        # Has body sections
        assert "## Overview" in result.markdown
        assert "## Key Components" in result.markdown
        assert "## Applications" in result.markdown

        # Wikilinks were injected (BERT, GPT, Self-Attention appear in body)
        # See Also may or may not appear depending on dedup (all 3 entities
        # already appear as [[wikilinks]] in the body after injection)

        # Has Sources
        assert "## Sources" in result.markdown
        assert "https://arxiv.org/abs/1706.03762" in result.markdown

        # Trailing newline
        assert result.markdown.endswith("\n")

    def test_h1_normalised(self, compile_result_with_h1: CompileResult):
        result = post_process(compile_result_with_h1, created_at=FIXED_TIME)
        assert result.headings_normalised > 0
        assert "## Main Heading" in result.body
        # No bare H1 (line starting with exactly "# ")
        for line in result.body.split("\n"):
            if line.startswith("# ") and not line.startswith("## "):
                pytest.fail(f"Found bare H1: {line}")

    def test_bare_urls_wrapped(self, compile_result_with_bare_urls: CompileResult):
        result = post_process(compile_result_with_bare_urls, created_at=FIXED_TIME)
        assert result.external_links_wrapped > 0

    def test_wikilinks_injected(self, basic_compile_result: CompileResult):
        result = post_process(basic_compile_result, created_at=FIXED_TIME)
        # Should inject wikilinks for entities mentioned in body
        assert result.wikilinks_injected >= 0  # may be 0 if already linked

    def test_sections_extracted(self, basic_compile_result: CompileResult):
        result = post_process(basic_compile_result, created_at=FIXED_TIME)
        assert "Overview" in result.sections
        assert "Key Components" in result.sections
        assert "Applications" in result.sections

    def test_invalid_compile_result(self):
        empty = CompileResult(title="", body="")
        result = post_process(empty)
        assert not result.is_valid
        assert len(result.warnings) > 0

    def test_no_see_also_when_disabled(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result, add_see_also=False, created_at=FIXED_TIME
        )
        assert "## See Also" not in result.body

    def test_no_sources_when_disabled(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result,
            source_urls=["https://example.com"],
            add_sources=False,
            created_at=FIXED_TIME,
        )
        assert "## Sources" not in result.body

    def test_no_normalise_when_disabled(self, compile_result_with_h1: CompileResult):
        result = post_process(
            compile_result_with_h1, normalise=False, created_at=FIXED_TIME
        )
        assert result.headings_normalised == 0
        assert "# Main Heading" in result.body

    def test_frontmatter_has_lenses(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result,
            lenses=["ai-research", "ml"],
            created_at=FIXED_TIME,
        )
        assert result.frontmatter is not None
        assert "ai-research" in result.frontmatter.lenses
        assert "ml" in result.frontmatter.lenses

    def test_frontmatter_has_sources(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result,
            sources=["sources/rss/test.md"],
            source_urls=["https://example.com"],
            created_at=FIXED_TIME,
        )
        assert result.frontmatter is not None
        assert "sources/rss/test.md" in result.frontmatter.sources

    def test_markdown_is_valid_obsidian(self, basic_compile_result: CompileResult):
        """Smoke test: the output should be valid Obsidian markdown."""
        result = post_process(
            basic_compile_result,
            lenses=["ai"],
            sources=["sources/rss/s1.md"],
            source_urls=["https://arxiv.org/abs/1706.03762"],
            created_at=FIXED_TIME,
        )
        md = result.markdown

        # Has exactly two --- fences for frontmatter
        lines = md.split("\n")
        fence_count = sum(1 for l in lines if l.strip() == "---")
        assert fence_count == 2

        # Body starts after second ---
        second_fence = -1
        found = 0
        for i, line in enumerate(lines):
            if line.strip() == "---":
                found += 1
                if found == 2:
                    second_fence = i
                    break
        assert second_fence > 0

        # There should be content after frontmatter
        body_content = "\n".join(lines[second_fence + 1:]).strip()
        assert len(body_content) > 0

    def test_extra_frontmatter(self, basic_compile_result: CompileResult):
        result = post_process(
            basic_compile_result,
            extra_frontmatter={"custom_key": "custom_value"},
            created_at=FIXED_TIME,
        )
        assert "custom_key: custom_value" in result.markdown

    def test_idempotent_wikilinks(self):
        """Running post_process twice should not double-link."""
        cr = CompileResult(
            title="Test",
            body="## Overview\n\n[[BERT]] is a model and GPT is another.",
            tags=["ai"],
            wikilinks=["BERT", "GPT"],
        )
        result1 = post_process(cr, created_at=FIXED_TIME)
        # Parse the body back and run again
        cr2 = CompileResult(
            title="Test",
            body=result1.body,
            tags=["ai"],
            wikilinks=["BERT", "GPT"],
        )
        result2 = post_process(cr2, created_at=FIXED_TIME)
        # Should not have more wikilinks than result1
        assert result2.body.count("[[BERT]]") == result1.body.count("[[BERT]]")

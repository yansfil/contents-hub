"""Tests for compile_service module: LLM compiler core interface and prompt templates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from llm_wiki.compile_service import (
    BatchCompileResult,
    CompileMode,
    CompileParseError,
    CompileRequest,
    CompileResult,
    CompileService,
    ExistingPageContext,
    SourceGroup,
    SourceNote,
    build_compile_prompt,
    parse_compile_response,
    _split_body_and_metadata,
    _merge_tags,
    _merge_unique,
    _extract_headings,
    COMPILE_SYSTEM_PROMPT,
    MAX_SOURCE_BODY_CHARS,
)
from llm_wiki.compile_decision import Decision, DecisionResult, NewContent, SimilarPage
from llm_wiki.compile_evaluate import (
    EvaluationResult,
    SimilarityAssessment,
    OverlapLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def source_note() -> SourceNote:
    return SourceNote(
        path="sources/rss/2024-01-15-transformers-explained.md",
        title="Transformers Explained: A Visual Guide",
        url="https://blog.example.com/transformers-explained",
        source_type="rss",
        author="Jane Doe",
        published_at="2024-01-15T10:00:00Z",
        tags=["ai", "transformers", "deep-learning"],
        body="Transformers are a type of neural network architecture that uses "
        "self-attention mechanisms to process sequences in parallel. "
        "Unlike RNNs, transformers can attend to all positions simultaneously.\n\n"
        "## Key Components\n\n"
        "1. Self-Attention: Computes relationships between all pairs of tokens.\n"
        "2. Multi-Head Attention: Runs multiple attention operations in parallel.\n"
        "3. Feed-Forward Networks: Processes attention output through dense layers.\n",
        summary="A guide to understanding transformer architecture.",
    )


@pytest.fixture
def source_group(source_note: SourceNote) -> SourceGroup:
    return SourceGroup(
        sources=[source_note],
        primary_title="Transformer Architecture",
        primary_source_path=source_note.path,
        lens_id="ai-ml",
        lens_name="AI & Machine Learning",
    )


@pytest.fixture
def similar_pages() -> list[SimilarPage]:
    return [
        SimilarPage(
            path="ai-ml/attention-mechanism.md",
            title="Attention Mechanism",
            score=0.65,
            match_type="tag",
            tags=["ai", "attention"],
            excerpt="The attention mechanism allows models to focus...",
        ),
        SimilarPage(
            path="ai-ml/bert.md",
            title="BERT",
            score=0.55,
            match_type="combined",
            tags=["ai", "nlp", "bert"],
            excerpt="BERT is a transformer-based language model...",
        ),
    ]


@pytest.fixture
def create_request(
    source_group: SourceGroup,
    similar_pages: list[SimilarPage],
) -> CompileRequest:
    return CompileRequest(
        source_group=source_group,
        mode=CompileMode.CREATE,
        related_pages=similar_pages,
        suggested_tags=["ai", "transformers", "architecture"],
        suggested_wikilinks=["Attention Mechanism", "BERT"],
    )


@pytest.fixture
def update_request(
    source_group: SourceGroup,
    similar_pages: list[SimilarPage],
) -> CompileRequest:
    existing = ExistingPageContext(
        path="ai-ml/transformer-architecture.md",
        title="Transformer Architecture",
        body="## Overview\n\nTransformers use self-attention.\n\n"
        "## Applications\n\nUsed in NLP and computer vision.\n",
        tags=["ai", "transformers"],
        section_headings=["Overview", "Applications"],
    )
    return CompileRequest(
        source_group=source_group,
        mode=CompileMode.UPDATE_APPEND,
        existing_page=existing,
        related_pages=similar_pages,
        suggested_tags=["ai", "transformers"],
        suggested_wikilinks=["Attention Mechanism"],
    )


@pytest.fixture
def wiki_config(tmp_path):
    """Create a WikiConfig with a temporary vault path."""
    from llm_wiki.config import WikiConfig
    return WikiConfig(vault_path=tmp_path)


@pytest.fixture
def service(wiki_config) -> CompileService:
    return CompileService(wiki_config)


# ---------------------------------------------------------------------------
# SourceNote tests
# ---------------------------------------------------------------------------


class TestSourceNote:
    def test_body_truncated_short(self, source_note: SourceNote):
        """Short body should not be truncated."""
        assert source_note.body_truncated == source_note.body

    def test_body_truncated_long(self):
        """Long body should be truncated with marker."""
        long_body = "x" * (MAX_SOURCE_BODY_CHARS + 100)
        note = SourceNote(body=long_body)
        truncated = note.body_truncated
        assert len(truncated) < len(long_body)
        assert truncated.endswith("[...truncated]")
        assert truncated.startswith("x" * 100)


class TestSourceGroup:
    def test_source_count(self, source_group: SourceGroup):
        assert source_group.source_count == 1

    def test_all_tags(self):
        """Tags should be deduplicated across sources."""
        group = SourceGroup(
            sources=[
                SourceNote(tags=["ai", "ml"]),
                SourceNote(tags=["ml", "nlp"]),
            ]
        )
        assert group.all_tags == ["ai", "ml", "nlp"]

    def test_all_urls(self):
        group = SourceGroup(
            sources=[
                SourceNote(url="https://a.com"),
                SourceNote(url=""),
                SourceNote(url="https://b.com"),
            ]
        )
        assert group.all_urls == ["https://a.com", "https://b.com"]


# ---------------------------------------------------------------------------
# CompileRequest tests
# ---------------------------------------------------------------------------


class TestCompileRequest:
    def test_title(self, create_request: CompileRequest):
        assert create_request.title == "Transformer Architecture"

    def test_is_update_create(self, create_request: CompileRequest):
        assert not create_request.is_update

    def test_is_update_update(self, update_request: CompileRequest):
        assert update_request.is_update


# ---------------------------------------------------------------------------
# CompileResult tests
# ---------------------------------------------------------------------------


class TestCompileResult:
    def test_is_valid(self):
        result = CompileResult(title="Test", body="Some content here.")
        assert result.is_valid

    def test_is_invalid_no_title(self):
        result = CompileResult(title="", body="Some content.")
        assert not result.is_valid

    def test_is_invalid_no_body(self):
        result = CompileResult(title="Test", body="  ")
        assert not result.is_valid

    def test_word_count(self):
        result = CompileResult(title="Test", body="one two three four five")
        assert result.word_count == 5


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


class TestBuildCompilePrompt:
    def test_create_prompt_contains_title(self, create_request: CompileRequest):
        prompt = build_compile_prompt(create_request)
        assert "Transformer Architecture" in prompt

    def test_create_prompt_contains_source_content(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "Transformers are a type of neural network" in prompt
        assert "Self-Attention" in prompt

    def test_create_prompt_contains_source_metadata(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "Jane Doe" in prompt
        assert "https://blog.example.com/transformers-explained" in prompt

    def test_create_prompt_contains_related_pages(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "[[Attention Mechanism]]" in prompt
        assert "[[BERT]]" in prompt

    def test_create_prompt_contains_suggested_tags(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "#ai" in prompt
        assert "#transformers" in prompt

    def test_create_prompt_contains_rss_hints(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "RSS/blog article" in prompt

    def test_create_prompt_contains_response_format(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "Response Format" in prompt
        assert '"tags"' in prompt
        assert '"wikilinks"' in prompt

    def test_create_prompt_contains_lens_context(
        self, create_request: CompileRequest
    ):
        prompt = build_compile_prompt(create_request)
        assert "AI & Machine Learning" in prompt

    def test_update_prompt_contains_existing_page(
        self, update_request: CompileRequest
    ):
        prompt = build_compile_prompt(update_request)
        assert "Update Existing Wiki Page" in prompt
        assert "Transformers use self-attention" in prompt
        assert "Update strategy" in prompt

    def test_update_append_instructions(self, update_request: CompileRequest):
        prompt = build_compile_prompt(update_request)
        assert "Add a new section" in prompt
        assert "Do NOT repeat" in prompt

    def test_update_rewrite_instructions(
        self,
        source_group: SourceGroup,
        similar_pages: list[SimilarPage],
    ):
        existing = ExistingPageContext(
            path="test.md",
            title="Test",
            body="Old content.",
        )
        request = CompileRequest(
            source_group=source_group,
            mode=CompileMode.UPDATE_REWRITE,
            existing_page=existing,
        )
        prompt = build_compile_prompt(request)
        assert "Rewrite the page" in prompt

    def test_update_section_instructions(
        self,
        source_group: SourceGroup,
    ):
        existing = ExistingPageContext(
            path="test.md",
            title="Test",
            body="Old content.",
        )
        request = CompileRequest(
            source_group=source_group,
            mode=CompileMode.UPDATE_SECTION,
            existing_page=existing,
        )
        prompt = build_compile_prompt(request)
        assert "Update the specific section" in prompt

    def test_multi_source_prompt(self, similar_pages: list[SimilarPage]):
        """Multiple sources should all appear in the prompt."""
        group = SourceGroup(
            sources=[
                SourceNote(
                    title="Article A",
                    body="Content of article A.",
                    source_type="rss",
                ),
                SourceNote(
                    title="Article B",
                    body="Content of article B.",
                    source_type="youtube",
                ),
            ],
            primary_title="Combined Topic",
        )
        request = CompileRequest(
            source_group=group,
            mode=CompileMode.CREATE,
            related_pages=similar_pages,
        )
        prompt = build_compile_prompt(request)
        assert "Article A" in prompt
        assert "Article B" in prompt
        assert "Content of article A" in prompt
        assert "Content of article B" in prompt
        assert "2 sources" in prompt

    def test_no_related_pages(self, source_group: SourceGroup):
        """Prompt should work without related pages."""
        request = CompileRequest(
            source_group=source_group,
            mode=CompileMode.CREATE,
        )
        prompt = build_compile_prompt(request)
        assert "Transformer Architecture" in prompt
        # Should not have related pages section
        assert "Related Pages in the Wiki" not in prompt

    def test_youtube_source_hints(self):
        group = SourceGroup(
            sources=[SourceNote(source_type="youtube", body="Video content.")],
            primary_title="YouTube Test",
        )
        request = CompileRequest(source_group=group, mode=CompileMode.CREATE)
        prompt = build_compile_prompt(request)
        assert "YouTube video" in prompt

    def test_twitter_source_hints(self):
        group = SourceGroup(
            sources=[SourceNote(source_type="twitter", body="Tweet content.")],
            primary_title="Twitter Test",
        )
        request = CompileRequest(source_group=group, mode=CompileMode.CREATE)
        prompt = build_compile_prompt(request)
        assert "Twitter/X thread" in prompt


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_mentions_obsidian(self):
        assert "Obsidian" in COMPILE_SYSTEM_PROMPT

    def test_system_prompt_mentions_wikilinks(self):
        assert "[[wikilinks]]" in COMPILE_SYSTEM_PROMPT

    def test_system_prompt_mentions_no_frontmatter(self):
        assert "frontmatter" in COMPILE_SYSTEM_PROMPT

    def test_system_prompt_mentions_no_h1(self):
        assert "H1" in COMPILE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestSplitBodyAndMetadata:
    def test_fenced_json(self):
        text = (
            "## Overview\n\nSome wiki content.\n\n"
            "```json\n"
            '{"tags": ["ai"], "wikilinks": ["BERT"]}\n'
            "```"
        )
        body, meta = _split_body_and_metadata(text)
        assert "## Overview" in body
        assert "Some wiki content" in body
        assert meta["tags"] == ["ai"]
        assert meta["wikilinks"] == ["BERT"]

    def test_bare_json(self):
        text = (
            "## Overview\n\nContent here.\n\n"
            '{"tags": ["ml"], "summary": "A page about ML"}'
        )
        body, meta = _split_body_and_metadata(text)
        assert "Content here" in body
        assert meta["tags"] == ["ml"]
        assert meta["summary"] == "A page about ML"

    def test_no_json(self):
        text = "## Just Content\n\nNo metadata block here."
        body, meta = _split_body_and_metadata(text)
        assert "Just Content" in body
        assert meta == {}

    def test_malformed_json_fallback(self):
        text = "Content.\n\n```json\n{invalid json}\n```"
        body, meta = _split_body_and_metadata(text)
        # Should fall back to treating everything as body
        assert "Content" in body


class TestMergeTags:
    def test_basic_merge(self):
        result = _merge_tags(["ai", "ml"], ["ml", "nlp"])
        assert result == ["ai", "ml", "nlp"]

    def test_strips_hash(self):
        result = _merge_tags(["#ai", "#ml"], [])
        assert result == ["ai", "ml"]

    def test_case_insensitive_dedup(self):
        result = _merge_tags(["AI", "ml"], ["ai", "ML"])
        assert len(result) == 2

    def test_empty(self):
        result = _merge_tags([], [])
        assert result == []


class TestMergeUnique:
    def test_basic_merge(self):
        result = _merge_unique(["BERT", "GPT"], ["GPT", "Attention"])
        assert result == ["BERT", "GPT", "Attention"]

    def test_case_insensitive(self):
        result = _merge_unique(["bert"], ["BERT"])
        assert len(result) == 1


class TestExtractHeadings:
    def test_extracts_h2_h3(self):
        body = "## Overview\n\nContent.\n\n### Details\n\nMore.\n\n## Conclusion"
        headings = _extract_headings(body)
        assert headings == ["Overview", "Details", "Conclusion"]

    def test_ignores_h1(self):
        body = "# Title\n\n## Section"
        headings = _extract_headings(body)
        assert headings == ["Section"]

    def test_empty_body(self):
        assert _extract_headings("") == []


class TestParseCompileResponse:
    def test_valid_response_with_json_block(self, create_request: CompileRequest):
        response = (
            "## Overview\n\n"
            "Transformers use self-attention to process sequences in parallel. "
            "The architecture was introduced in the landmark paper "
            '"Attention Is All You Need" by [[Vaswani et al.]].\n\n'
            "## Key Components\n\n"
            "- **[[Self-Attention]]**: Computes relationships between tokens.\n"
            "- **Multi-Head Attention**: Multiple attention heads in parallel.\n"
            "- **Feed-Forward Networks**: Dense layers after attention.\n\n"
            "## Applications\n\n"
            "Transformers power modern NLP models like [[BERT]] and [[GPT]].\n\n"
            "```json\n"
            "{\n"
            '  "title": "Transformer Architecture",\n'
            '  "tags": ["ai", "transformers", "deep-learning", "attention"],\n'
            '  "wikilinks": ["Self-Attention", "BERT", "GPT", "Vaswani et al."],\n'
            '  "aliases": ["Transformers"],\n'
            '  "summary": "Neural network architecture using self-attention"\n'
            "}\n"
            "```"
        )
        result = parse_compile_response(response, create_request)

        assert result.is_valid
        assert result.title == "Transformer Architecture"
        assert "self-attention" in result.body
        assert "## Key Components" in result.body
        assert "ai" in result.tags
        assert "deep-learning" in result.tags
        assert "Self-Attention" in result.wikilinks
        assert "BERT" in result.wikilinks
        assert "Transformers" in result.aliases
        assert result.summary == "Neural network architecture using self-attention"
        assert result.section_plan == ["Overview", "Key Components", "Applications"]

    def test_response_without_json_block(self, create_request: CompileRequest):
        response = "## Overview\n\nJust content, no metadata."
        result = parse_compile_response(response, create_request)

        assert result.is_valid
        assert result.title == "Transformer Architecture"  # Falls back to request
        assert "Just content" in result.body
        # Tags fall back to suggested
        assert "ai" in result.tags

    def test_empty_response(self, create_request: CompileRequest):
        result = parse_compile_response("", create_request)
        assert not result.is_valid
        assert result.title == "Transformer Architecture"

    def test_source_attributions(self, create_request: CompileRequest):
        response = "## Content\n\nSome text."
        result = parse_compile_response(response, create_request)
        assert len(result.source_attributions) == 1
        assert "Transformers Explained" in result.source_attributions[0]

    def test_tags_merge_with_suggested(self, create_request: CompileRequest):
        """LLM tags + suggested tags should be merged."""
        response = (
            "Content.\n\n"
            '```json\n{"tags": ["neural-networks"]}\n```'
        )
        result = parse_compile_response(response, create_request)
        # "neural-networks" from LLM + suggested tags
        assert "neural-networks" in result.tags
        assert "ai" in result.tags  # from suggested

    def test_word_count(self, create_request: CompileRequest):
        response = "one two three four five six seven eight nine ten"
        result = parse_compile_response(response, create_request)
        assert result.word_count == 10


# ---------------------------------------------------------------------------
# CompileService tests
# ---------------------------------------------------------------------------


class TestCompileService:
    def test_system_prompt(self, service: CompileService):
        assert "Obsidian" in service.system_prompt

    def test_build_prompt(
        self, service: CompileService, create_request: CompileRequest
    ):
        prompt = service.build_prompt(create_request)
        assert "Transformer Architecture" in prompt

    def test_parse_response(
        self, service: CompileService, create_request: CompileRequest
    ):
        response = (
            "## Overview\n\nContent about transformers.\n\n"
            '```json\n{"tags": ["ai"], "title": "Transformers"}\n```'
        )
        result = service.parse_response(create_request, response)
        assert result.is_valid
        assert result.title == "Transformers"

    def test_compile(
        self, service: CompileService, create_request: CompileRequest
    ):
        response = "## Overview\n\nContent."
        result = service.compile(create_request, response)
        assert result.is_valid

    def test_build_request_from_evaluation(self, service: CompileService):
        """build_request should convert EvaluationResult to CompileRequest."""
        new_content = NewContent(
            source_path="sources/rss/test.md",
            title="Test Article",
            source_type="rss",
            url="https://example.com/test",
            tags=["test"],
            body="Article body content.",
            lens_id="tech",
            lens_name="Technology",
        )
        decision = DecisionResult(
            source_path="sources/rss/test.md",
            decision=Decision.CREATE,
            target_title="Test Article",
            suggested_tags=["test", "tech"],
            suggested_wikilinks=["Related Topic"],
        )
        assessment = SimilarityAssessment(
            overlap_level=OverlapLevel.NONE,
        )
        evaluation = EvaluationResult(
            source_path="sources/rss/test.md",
            action=Decision.CREATE,
            decision=decision,
            assessment=assessment,
            similar_pages=[],
            new_content=new_content,
        )

        request = service.build_request(evaluation)

        assert request.title == "Test Article"
        assert request.mode == CompileMode.CREATE
        assert not request.is_update
        assert request.source_group.source_count == 1
        assert request.source_group.sources[0].source_type == "rss"
        assert "test" in request.suggested_tags

    def test_build_request_update_mode(self, service: CompileService):
        """build_request should resolve UPDATE mode from merge_strategy."""
        new_content = NewContent(
            source_path="sources/rss/test.md",
            title="Test",
            body="Content.",
        )
        decision = DecisionResult(
            source_path="sources/rss/test.md",
            decision=Decision.UPDATE,
            target_page="existing.md",
            target_title="Existing Page",
            merge_strategy="rewrite",
        )
        assessment = SimilarityAssessment(overlap_level=OverlapLevel.HIGH)
        evaluation = EvaluationResult(
            source_path="sources/rss/test.md",
            action=Decision.UPDATE,
            decision=decision,
            assessment=assessment,
            new_content=new_content,
        )

        request = service.build_request(
            evaluation,
            existing_page_body="Old content here.",
        )

        assert request.mode == CompileMode.UPDATE_REWRITE
        assert request.is_update
        assert request.existing_page is not None
        assert request.existing_page.body == "Old content here."

    def test_build_request_from_group(self, service: CompileService):
        """build_request_from_group should accept raw source dicts."""
        sources = [
            {
                "path": "sources/rss/a.md",
                "title": "Article A",
                "url": "https://a.com",
                "source_type": "rss",
                "body": "Content A.",
                "tags": ["tag1"],
            },
            {
                "path": "sources/youtube/b.md",
                "title": "Video B",
                "url": "https://youtube.com/b",
                "source_type": "youtube",
                "body": "Transcript B.",
                "tags": ["tag2"],
            },
        ]
        request = service.build_request_from_group(
            sources,
            title="Combined Topic",
            lens_id="tech",
            suggested_tags=["combined"],
        )

        assert request.title == "Combined Topic"
        assert request.source_group.source_count == 2
        assert request.source_group.all_tags == ["tag1", "tag2"]
        assert "combined" in request.suggested_tags


# ---------------------------------------------------------------------------
# BatchCompileResult tests
# ---------------------------------------------------------------------------


class TestBatchCompileResult:
    def test_summary(self):
        batch = BatchCompileResult(
            results=[
                (
                    CompileRequest(
                        source_group=SourceGroup(primary_title="Page 1"),
                    ),
                    CompileResult(
                        title="Page 1",
                        body="Content 1.",
                        tags=["a"],
                        wikilinks=["B"],
                    ),
                ),
            ],
            total=1,
            successful=1,
            failed=0,
        )
        summary = batch.summary()
        assert "1 ok" in summary
        assert "Page 1" in summary


# ---------------------------------------------------------------------------
# Integration: prompt → parse round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_create_round_trip(
        self,
        service: CompileService,
        create_request: CompileRequest,
    ):
        """Prompt should be parseable back through parse_response."""
        prompt = service.build_prompt(create_request)

        # Verify prompt structure
        assert "## Task: Create New Wiki Page" in prompt
        assert "## Source Content" in prompt
        assert "## Response Format" in prompt

        # Simulate LLM response
        simulated_response = (
            "## Overview\n\n"
            "The [[Transformer Architecture]] is a neural network design that uses "
            "[[Self-Attention]] to process sequences in parallel.\n\n"
            "## Key Components\n\n"
            "1. **Self-Attention**: Computes token relationships.\n"
            "2. **Multi-Head Attention**: Parallel attention operations.\n\n"
            "## Impact\n\n"
            "Transformers power [[BERT]], [[GPT]], and modern LLMs.\n\n"
            "```json\n"
            "{\n"
            '  "title": "Transformer Architecture",\n'
            '  "tags": ["ai", "deep-learning", "attention", "architecture"],\n'
            '  "wikilinks": ["Self-Attention", "BERT", "GPT", "Multi-Head Attention"],\n'
            '  "aliases": ["Transformers", "Transformer Model"],\n'
            '  "summary": "Neural network architecture using self-attention for parallel sequence processing"\n'
            "}\n"
            "```"
        )

        result = service.parse_response(create_request, simulated_response)

        assert result.is_valid
        assert result.title == "Transformer Architecture"
        assert "self-attention" in result.body.lower()
        assert "ai" in result.tags
        assert "Self-Attention" in result.wikilinks
        assert "Transformers" in result.aliases
        assert result.section_plan == ["Overview", "Key Components", "Impact"]
        assert result.word_count > 0

    def test_update_round_trip(
        self,
        service: CompileService,
        update_request: CompileRequest,
    ):
        """Update prompt should produce valid parseable result."""
        prompt = service.build_prompt(update_request)

        assert "## Task: Update Existing Wiki Page" in prompt
        assert "Existing Page Content" in prompt

        simulated_response = (
            "## New Findings\n\n"
            "Recent research shows that transformers can also be applied "
            "to computer vision via [[Vision Transformer]].\n\n"
            "```json\n"
            '{"tags": ["ai", "computer-vision"], '
            '"wikilinks": ["Vision Transformer"]}\n'
            "```"
        )

        result = service.parse_response(update_request, simulated_response)

        assert result.is_valid
        assert "Vision Transformer" in result.wikilinks
        assert "computer-vision" in result.tags

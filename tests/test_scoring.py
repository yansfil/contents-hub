"""Tests for LLM prompt-based relevance scoring."""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

from llm_wiki.lens import Lens, RelevanceResult, RelevanceScore
from llm_wiki.scoring import (
    KeywordMatch,
    MAX_BODY_CHARS,
    ScoringParseError,
    SourceContent,
    build_batch_scoring_prompt,
    build_scoring_prompt,
    extract_source_content,
    keyword_prefilter,
    parse_batch_scoring_response,
    parse_scoring_response,
    score_source,
    score_source_with_prefilter,
    _split_frontmatter,
    _strip_code_fences,
    _extract_json_array,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ai_lens() -> Lens:
    return Lens(
        id="ai-ml",
        name="AI/ML",
        description="Artificial intelligence and machine learning",
        keywords=["transformer", "LLM", "neural network", "GPT"],
    )


@pytest.fixture
def devops_lens() -> Lens:
    return Lens(
        id="devops",
        name="DevOps",
        description="Infrastructure, CI/CD, and deployment",
        keywords=["kubernetes", "docker", "ci/cd", "terraform"],
    )


@pytest.fixture
def rust_lens() -> Lens:
    return Lens(
        id="rust-ecosystem",
        name="Rust Ecosystem",
        description="Rust programming language and ecosystem",
        keywords=["rust", "cargo", "tokio", "async"],
    )


@pytest.fixture
def sample_lenses(ai_lens, devops_lens, rust_lens) -> list[Lens]:
    return [ai_lens, devops_lens, rust_lens]


@pytest.fixture
def ai_source() -> SourceContent:
    return SourceContent(
        path="sources/rss/20240101-transformer-paper-abc12345.md",
        title="Attention Is All You Need: A Retrospective",
        source_type="rss",
        url="https://arxiv.org/abs/1706.03762",
        tags=["ai", "research"],
        body=(
            "The transformer architecture revolutionized natural language "
            "processing. This paper introduces self-attention mechanisms that "
            "replaced recurrent neural networks. GPT and other LLM models "
            "are built on this foundation."
        ),
    )


@pytest.fixture
def devops_source() -> SourceContent:
    return SourceContent(
        path="sources/rss/20240102-k8s-deploy-def45678.md",
        title="Deploying ML Models on Kubernetes",
        source_type="rss",
        url="https://blog.example.com/k8s-ml-deploy",
        tags=["devops", "ml"],
        body=(
            "This guide covers deploying machine learning models using "
            "Kubernetes and Docker containers. We use terraform for "
            "infrastructure provisioning and set up CI/CD pipelines."
        ),
    )


@pytest.fixture
def valid_llm_response() -> str:
    """A well-formed LLM response for 3 lenses."""
    return json.dumps([
        {
            "lens": "ai-ml",
            "score": 0.95,
            "reason": "Directly about transformer architecture and LLMs",
            "matched_keywords": ["transformer", "LLM", "GPT"],
        },
        {
            "lens": "devops",
            "score": 0.1,
            "reason": "No DevOps content",
            "matched_keywords": [],
        },
        {
            "lens": "rust-ecosystem",
            "score": 0.05,
            "reason": "No Rust content",
            "matched_keywords": [],
        },
    ])


# ---------------------------------------------------------------------------
# SourceContent
# ---------------------------------------------------------------------------


class TestSourceContent:
    def test_summary_for_prompt(self, ai_source: SourceContent):
        summary = ai_source.summary_for_prompt
        assert "Attention Is All You Need" in summary
        assert "rss" in summary
        assert "arxiv.org" in summary
        assert "Content:" in summary

    def test_summary_truncation(self):
        long_body = "x" * (MAX_BODY_CHARS + 500)
        src = SourceContent(path="x", body=long_body)
        summary = src.summary_for_prompt
        assert "[...truncated]" in summary
        # Body in prompt should be at most MAX_BODY_CHARS + truncation marker
        content_part = summary.split("Content:\n")[1]
        assert len(content_part) < MAX_BODY_CHARS + 50

    def test_summary_empty_fields(self):
        src = SourceContent(path="x")
        summary = src.summary_for_prompt
        # Should not contain labels for empty fields
        assert "Title:" not in summary
        assert "Tags:" not in summary


# ---------------------------------------------------------------------------
# extract_source_content
# ---------------------------------------------------------------------------


class TestExtractSourceContent:
    def test_basic_extraction(self):
        md = """\
---
type: rss
url: "https://example.com/article"
title: Test Article
collected_at: 2024-01-01T00:00:00+00:00
status: pending
tags:
  - ai
  - research
---

# Test Article

This is the article body about transformers and LLMs."""

        source = extract_source_content(md, "sources/rss/test.md")
        assert source.title == "Test Article"
        assert source.source_type == "rss"
        assert source.url == "https://example.com/article"
        assert source.tags == ["ai", "research"]
        assert "transformers and LLMs" in source.body
        assert source.path == "sources/rss/test.md"

    def test_no_frontmatter(self):
        md = "# Just a heading\n\nSome body text."
        source = extract_source_content(md, "sources/test.md")
        assert source.title == ""
        assert source.body == "# Just a heading\n\nSome body text."

    def test_empty_file(self):
        source = extract_source_content("", "sources/empty.md")
        assert source.title == ""
        assert source.body == ""


# ---------------------------------------------------------------------------
# Keyword pre-filter
# ---------------------------------------------------------------------------


class TestKeywordPrefilter:
    def test_keyword_hits(self, ai_source, sample_lenses):
        results = keyword_prefilter(ai_source, sample_lenses)

        ai_match = results["ai-ml"]
        assert ai_match.has_hits
        assert "transformer" in ai_match.matched_keywords
        assert "LLM" in ai_match.matched_keywords
        assert "GPT" in ai_match.matched_keywords

        devops_match = results["devops"]
        assert not devops_match.has_hits

    def test_cross_topic_source(self, devops_source, sample_lenses):
        results = keyword_prefilter(devops_source, sample_lenses)

        devops_match = results["devops"]
        assert devops_match.has_hits
        assert "kubernetes" in devops_match.matched_keywords
        assert "docker" in devops_match.matched_keywords
        assert "terraform" in devops_match.matched_keywords

        # ML content should not match AI lens keywords specifically
        ai_match = results["ai-ml"]
        assert not ai_match.has_hits

    def test_word_boundary_matching(self):
        """Keywords should match on word boundaries, not substrings."""
        source = SourceContent(
            path="x",
            body="rusty containers for dockerized apps",
        )
        lenses = [
            Lens(id="rust", name="Rust", keywords=["rust"]),
            Lens(id="docker", name="Docker", keywords=["docker"]),
        ]
        results = keyword_prefilter(source, lenses)
        # "rusty" should NOT match "rust" (word boundary)
        assert not results["rust"].has_hits
        # "dockerized" should NOT match "docker" (word boundary)
        assert not results["docker"].has_hits

    def test_case_insensitive(self):
        source = SourceContent(path="x", body="Using KUBERNETES and Docker")
        lenses = [Lens(id="devops", name="DevOps", keywords=["kubernetes", "docker"])]
        results = keyword_prefilter(source, lenses)
        assert results["devops"].has_hits
        assert results["devops"].hit_count == 2

    def test_empty_keywords(self):
        source = SourceContent(path="x", body="some content")
        lenses = [Lens(id="empty", name="Empty", keywords=[])]
        results = keyword_prefilter(source, lenses)
        assert not results["empty"].has_hits


# ---------------------------------------------------------------------------
# build_scoring_prompt
# ---------------------------------------------------------------------------


class TestBuildScoringPrompt:
    def test_contains_lens_info(self, ai_source, sample_lenses):
        prompt = build_scoring_prompt(ai_source, sample_lenses)
        assert "AI/ML" in prompt
        assert "ai-ml" in prompt
        assert "DevOps" in prompt
        assert "Rust Ecosystem" in prompt
        assert "transformer" in prompt

    def test_contains_source_content(self, ai_source, sample_lenses):
        prompt = build_scoring_prompt(ai_source, sample_lenses)
        assert "Attention Is All You Need" in prompt
        assert "arxiv.org" in prompt

    def test_contains_scoring_guidelines(self, ai_source, sample_lenses):
        prompt = build_scoring_prompt(ai_source, sample_lenses)
        assert "0.0" in prompt
        assert "1.0" in prompt
        assert "JSON" in prompt

    def test_with_keyword_matches(self, ai_source, sample_lenses):
        km = keyword_prefilter(ai_source, sample_lenses)
        prompt = build_scoring_prompt(ai_source, sample_lenses, keyword_matches=km)
        assert "Pre-filter hits:" in prompt
        assert "transformer" in prompt

    def test_response_format_instruction(self, ai_source, sample_lenses):
        prompt = build_scoring_prompt(ai_source, sample_lenses)
        assert '"lens"' in prompt
        assert '"score"' in prompt
        assert '"reason"' in prompt


# ---------------------------------------------------------------------------
# parse_scoring_response
# ---------------------------------------------------------------------------


class TestParseScoringResponse:
    def test_valid_json_array(self, valid_llm_response):
        result = parse_scoring_response(valid_llm_response, "sources/test.md")
        assert len(result.scores) == 3
        ai_score = result.for_lens("ai-ml")
        assert ai_score is not None
        assert ai_score.score == 0.95
        assert "transformer" in ai_score.matched_keywords

    def test_json_in_code_fence(self):
        response = '```json\n[{"lens": "ai-ml", "score": 0.8, "reason": "test"}]\n```'
        result = parse_scoring_response(response, "test.md")
        assert len(result.scores) == 1
        assert result.scores[0].score == 0.8

    def test_json_with_surrounding_text(self):
        response = (
            'Here are the scores:\n'
            '[{"lens": "ai-ml", "score": 0.7, "reason": "relevant"}]\n'
            'Let me know if you need more.'
        )
        result = parse_scoring_response(response, "test.md")
        assert len(result.scores) == 1
        assert result.scores[0].score == 0.7

    def test_score_clamping(self):
        response = json.dumps([
            {"lens": "ai-ml", "score": 1.5, "reason": "over"},
            {"lens": "devops", "score": -0.3, "reason": "under"},
        ])
        result = parse_scoring_response(response, "test.md")
        assert result.for_lens("ai-ml").score == 1.0
        assert result.for_lens("devops").score == 0.0

    def test_valid_slugs_filter(self):
        response = json.dumps([
            {"lens": "ai-ml", "score": 0.9, "reason": "good"},
            {"lens": "unknown-lens", "score": 0.5, "reason": "??"},
        ])
        result = parse_scoring_response(
            response, "test.md", valid_slugs={"ai-ml"}
        )
        assert len(result.scores) == 1
        assert result.scores[0].lens_id == "ai-ml"

    def test_invalid_json_raises(self):
        with pytest.raises(ScoringParseError):
            parse_scoring_response("not json at all", "test.md")

    def test_non_array_raises(self):
        with pytest.raises(ScoringParseError):
            parse_scoring_response('{"not": "array"}', "test.md")

    def test_missing_fields_graceful(self):
        response = json.dumps([{"lens": "ai-ml"}])
        result = parse_scoring_response(response, "test.md")
        assert result.scores[0].score == 0.0
        assert result.scores[0].reason == ""

    def test_assessed_at_set(self, valid_llm_response):
        result = parse_scoring_response(valid_llm_response, "test.md")
        assert result.assessed_at is not None
        assert result.assessed_at.tzinfo is not None


# ---------------------------------------------------------------------------
# score_source (high-level API)
# ---------------------------------------------------------------------------


class TestScoreSource:
    def test_end_to_end(self, ai_source, sample_lenses, valid_llm_response):
        result = score_source(ai_source, sample_lenses, valid_llm_response)
        assert result.source_path == ai_source.path
        assert len(result.scores) == 3
        assert result.top_lens().lens_id == "ai-ml"

    def test_filters_invalid_slugs(self, ai_source, sample_lenses):
        response = json.dumps([
            {"lens": "ai-ml", "score": 0.9, "reason": "good"},
            {"lens": "nonexistent", "score": 0.5, "reason": "?"},
        ])
        result = score_source(ai_source, sample_lenses, response)
        assert len(result.scores) == 1


# ---------------------------------------------------------------------------
# score_source_with_prefilter
# ---------------------------------------------------------------------------


class TestScoreSourceWithPrefilter:
    def test_keyword_enrichment(self, ai_source, sample_lenses):
        # LLM response doesn't include "GPT" in matched_keywords
        response = json.dumps([
            {
                "lens": "ai-ml",
                "score": 0.95,
                "reason": "About transformers",
                "matched_keywords": ["transformer"],
            },
            {"lens": "devops", "score": 0.1, "reason": "No DevOps"},
            {"lens": "rust-ecosystem", "score": 0.05, "reason": "No Rust"},
        ])

        result = score_source_with_prefilter(
            ai_source, sample_lenses, response
        )
        ai_score = result.for_lens("ai-ml")
        # Pre-filter should have added "LLM" and "GPT" to matched_keywords
        assert "transformer" in ai_score.matched_keywords
        assert "LLM" in ai_score.matched_keywords
        assert "GPT" in ai_score.matched_keywords


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------


class TestBatchScoring:
    def test_build_batch_prompt(self, ai_source, devops_source, sample_lenses):
        prompt = build_batch_scoring_prompt(
            [ai_source, devops_source], sample_lenses
        )
        assert "Source 1" in prompt
        assert "Source 2" in prompt
        assert ai_source.path in prompt
        assert devops_source.path in prompt
        assert "AI/ML" in prompt

    def test_parse_batch_response(self):
        response = json.dumps({
            "sources/test1.md": [
                {"lens": "ai-ml", "score": 0.9, "reason": "AI content"},
            ],
            "sources/test2.md": [
                {"lens": "devops", "score": 0.8, "reason": "DevOps content"},
            ],
        })
        results = parse_batch_scoring_response(response)
        assert "sources/test1.md" in results
        assert "sources/test2.md" in results
        assert results["sources/test1.md"].for_lens("ai-ml").score == 0.9
        assert results["sources/test2.md"].for_lens("devops").score == 0.8

    def test_parse_batch_with_code_fence(self):
        inner = json.dumps({
            "sources/test.md": [
                {"lens": "ai-ml", "score": 0.7, "reason": "test"},
            ]
        })
        response = f"```json\n{inner}\n```"
        results = parse_batch_scoring_response(response)
        assert "sources/test.md" in results

    def test_parse_batch_invalid_raises(self):
        with pytest.raises(ScoringParseError):
            parse_batch_scoring_response("not json")

    def test_parse_batch_filters_slugs(self):
        response = json.dumps({
            "sources/test.md": [
                {"lens": "ai-ml", "score": 0.9, "reason": "ok"},
                {"lens": "unknown", "score": 0.5, "reason": "?"},
            ]
        })
        results = parse_batch_scoring_response(
            response, valid_slugs={"ai-ml"}
        )
        assert len(results["sources/test.md"].scores) == 1


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_split_frontmatter(self):
        text = "---\ntitle: Test\ntype: rss\n---\n\nBody text"
        fm, body = _split_frontmatter(text)
        assert fm["title"] == "Test"
        assert fm["type"] == "rss"
        assert body == "Body text"

    def test_split_frontmatter_with_list(self):
        text = "---\ntags:\n  - ai\n  - ml\ntitle: Test\n---\n\nBody"
        fm, body = _split_frontmatter(text)
        assert fm["tags"] == ["ai", "ml"]
        assert fm["title"] == "Test"

    def test_split_frontmatter_no_fm(self):
        text = "Just body text"
        fm, body = _split_frontmatter(text)
        assert fm == {}
        assert body == "Just body text"

    def test_strip_code_fences(self):
        assert _strip_code_fences('```json\n[1,2]\n```') == "[1,2]"
        assert _strip_code_fences('```\n[1,2]\n```') == "[1,2]"
        assert _strip_code_fences("[1,2]") == "[1,2]"

    def test_extract_json_array(self):
        result = _extract_json_array('text before [1, 2, 3] text after')
        assert result == [1, 2, 3]

    def test_extract_json_array_none(self):
        assert _extract_json_array("no array here") is None

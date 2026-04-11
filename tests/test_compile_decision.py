"""Tests for compile_decision module: LLM create/update/skip decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.compile_decision import (
    Decision,
    DecidePageActionError,
    DecisionParseError,
    DecisionResult,
    NewContent,
    SimilarPage,
    _validate_decision_result,
    build_batch_decision_prompt,
    build_decision_prompt,
    decide_batch,
    decide_page_action,
    decide_page_action_batch,
    decide_single,
    decide_without_llm,
    decidePageAction,
    decidePageActionBatch,
    parse_batch_decision_response,
    parse_decision_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def new_content() -> NewContent:
    return NewContent(
        source_path="sources/rss/2024-01-15-transformers-explained.md",
        title="Transformers Explained: A Visual Guide",
        source_type="rss",
        url="https://blog.example.com/transformers-explained",
        tags=["ai", "transformers", "deep-learning"],
        body="Transformers are a type of neural network architecture...",
        lens_id="ai-ml",
        lens_name="AI & Machine Learning",
    )


@pytest.fixture
def similar_pages() -> list[SimilarPage]:
    return [
        SimilarPage(
            path="ai-ml/transformers.md",
            title="Transformers",
            score=0.85,
            match_type="title",
            aliases=["Attention Mechanism", "Transformer Architecture"],
            tags=["ai", "deep-learning", "nlp"],
            excerpt="Transformers are the dominant architecture for NLP tasks...",
        ),
        SimilarPage(
            path="ai-ml/attention-mechanism.md",
            title="Attention Mechanism",
            score=0.62,
            match_type="alias",
            tags=["ai", "deep-learning"],
            excerpt="The attention mechanism allows models to focus on relevant parts...",
        ),
    ]


@pytest.fixture
def no_similar_pages() -> list[SimilarPage]:
    return []


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


class TestBuildDecisionPrompt:
    def test_includes_new_content(self, new_content, similar_pages):
        prompt = build_decision_prompt(new_content, similar_pages)
        assert "Transformers Explained" in prompt
        assert "rss" in prompt
        assert "ai-ml" in prompt

    def test_includes_similar_pages(self, new_content, similar_pages):
        prompt = build_decision_prompt(new_content, similar_pages)
        assert "Candidate 1" in prompt
        assert "Candidate 2" in prompt
        assert "Transformers" in prompt
        assert "0.85" in prompt
        assert "Attention Mechanism" in prompt

    def test_no_similar_pages(self, new_content, no_similar_pages):
        prompt = build_decision_prompt(new_content, no_similar_pages)
        assert "No similar pages found" in prompt

    def test_includes_decision_guidelines(self, new_content, similar_pages):
        prompt = build_decision_prompt(new_content, similar_pages)
        assert "CREATE" in prompt
        assert "UPDATE" in prompt
        assert "SKIP" in prompt

    def test_includes_response_format(self, new_content, similar_pages):
        prompt = build_decision_prompt(new_content, similar_pages)
        assert '"decision"' in prompt
        assert '"target_page"' in prompt
        assert '"reason"' in prompt
        assert '"confidence"' in prompt

    def test_includes_merge_strategies(self, new_content, similar_pages):
        prompt = build_decision_prompt(new_content, similar_pages)
        assert "append" in prompt
        assert "rewrite" in prompt
        assert "section" in prompt

    def test_body_truncation(self, similar_pages):
        long_body = "x" * 5000
        content = NewContent(
            source_path="sources/rss/test.md",
            title="Test",
            body=long_body,
        )
        prompt = build_decision_prompt(content, similar_pages)
        assert "[...truncated]" in prompt


class TestBuildBatchDecisionPrompt:
    def test_includes_all_items(self, new_content, similar_pages):
        content2 = NewContent(
            source_path="sources/youtube/2024-01-16-gpt-overview.md",
            title="GPT Architecture Overview",
            source_type="youtube",
        )
        items = [
            (new_content, similar_pages),
            (content2, []),
        ]
        prompt = build_batch_decision_prompt(items)
        assert "Item 1" in prompt
        assert "Item 2" in prompt
        assert "Transformers Explained" in prompt
        assert "GPT Architecture Overview" in prompt
        assert "No similar pages found" in prompt

    def test_includes_count(self, new_content, similar_pages):
        items = [(new_content, similar_pages)]
        prompt = build_batch_decision_prompt(items)
        assert "1 new content items" in prompt


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestParseDecisionResponse:
    def test_parse_create_response(self):
        response = json.dumps({
            "decision": "create",
            "target_page": "",
            "target_title": "Transformers Explained",
            "reason": "No existing page covers visual guides specifically.",
            "confidence": 0.85,
            "merge_strategy": "",
            "suggested_tags": ["ai", "visual-guide"],
            "suggested_wikilinks": ["Transformers", "Attention Mechanism"],
        })
        result = parse_decision_response(response, "sources/rss/test.md")

        assert result.decision == Decision.CREATE
        assert result.target_title == "Transformers Explained"
        assert result.confidence == 0.85
        assert result.target_page == ""
        assert "ai" in result.suggested_tags
        assert "Transformers" in result.suggested_wikilinks
        assert result.is_actionable

    def test_parse_update_response(self):
        response = json.dumps({
            "decision": "update",
            "target_page": "ai-ml/transformers.md",
            "target_title": "Transformers",
            "reason": "Existing page covers the same topic.",
            "confidence": 0.9,
            "merge_strategy": "append",
            "suggested_tags": ["visual-guide"],
            "suggested_wikilinks": [],
        })
        result = parse_decision_response(response, "sources/rss/test.md")

        assert result.decision == Decision.UPDATE
        assert result.target_page == "ai-ml/transformers.md"
        assert result.merge_strategy == "append"
        assert result.is_actionable

    def test_parse_skip_response(self):
        response = json.dumps({
            "decision": "skip",
            "target_page": "ai-ml/transformers.md",
            "target_title": "Transformers",
            "reason": "Content already fully covered.",
            "confidence": 0.95,
        })
        result = parse_decision_response(response, "sources/rss/test.md")

        assert result.decision == Decision.SKIP
        assert not result.is_actionable

    def test_parse_with_code_fences(self):
        response = '```json\n{"decision": "create", "target_title": "Test", "reason": "New topic", "confidence": 0.8}\n```'
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.decision == Decision.CREATE

    def test_parse_with_surrounding_text(self):
        response = 'Here is my analysis:\n\n{"decision": "update", "target_page": "ai/test.md", "target_title": "Test", "reason": "Exists", "confidence": 0.7, "merge_strategy": "append"}\n\nLet me know if you need more details.'
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.decision == Decision.UPDATE

    def test_parse_unknown_decision_defaults_to_create(self):
        response = json.dumps({
            "decision": "unknown_value",
            "target_title": "Test",
            "reason": "Test",
            "confidence": 0.5,
        })
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.decision == Decision.CREATE

    def test_parse_invalid_json_raises(self):
        with pytest.raises(DecisionParseError):
            parse_decision_response("not json at all!", "sources/rss/test.md")

    def test_confidence_clamped(self):
        response = json.dumps({
            "decision": "create",
            "target_title": "Test",
            "reason": "Test",
            "confidence": 1.5,
        })
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.confidence == 1.0

    def test_merge_strategy_cleared_for_non_update(self):
        response = json.dumps({
            "decision": "create",
            "target_title": "Test",
            "reason": "Test",
            "confidence": 0.8,
            "merge_strategy": "append",
        })
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.merge_strategy == ""

    def test_missing_optional_fields(self):
        response = json.dumps({
            "decision": "create",
        })
        result = parse_decision_response(response, "sources/rss/test.md")
        assert result.decision == Decision.CREATE
        assert result.target_title == ""
        assert result.reason == ""
        assert result.confidence == 0.5  # default
        assert result.suggested_tags == []
        assert result.suggested_wikilinks == []


class TestParseBatchDecisionResponse:
    def test_parse_batch(self):
        response = json.dumps({
            "sources/rss/a.md": {
                "decision": "create",
                "target_title": "New Topic",
                "reason": "Novel content",
                "confidence": 0.9,
            },
            "sources/rss/b.md": {
                "decision": "update",
                "target_page": "ai/existing.md",
                "target_title": "Existing Topic",
                "reason": "Adds new info",
                "confidence": 0.8,
                "merge_strategy": "section",
            },
        })
        results = parse_batch_decision_response(response)

        assert len(results) == 2
        assert results["sources/rss/a.md"].decision == Decision.CREATE
        assert results["sources/rss/b.md"].decision == Decision.UPDATE
        assert results["sources/rss/b.md"].merge_strategy == "section"

    def test_parse_batch_invalid_json(self):
        with pytest.raises(DecisionParseError):
            parse_batch_decision_response("not json")


# ---------------------------------------------------------------------------
# High-level API tests
# ---------------------------------------------------------------------------


class TestDecideSingle:
    def test_decide_single(self, new_content, similar_pages):
        response = json.dumps({
            "decision": "update",
            "target_page": "ai-ml/transformers.md",
            "target_title": "Transformers",
            "reason": "Existing page covers transformers.",
            "confidence": 0.88,
            "merge_strategy": "append",
            "suggested_tags": ["visual-guide"],
            "suggested_wikilinks": ["Attention Mechanism"],
        })
        result = decide_single(new_content, similar_pages, response)

        assert result.decision == Decision.UPDATE
        assert result.source_path == new_content.source_path
        assert result.target_page == "ai-ml/transformers.md"


class TestDecideBatch:
    def test_decide_batch(self, new_content, similar_pages):
        content2 = NewContent(
            source_path="sources/youtube/test.md",
            title="New Topic",
        )
        items = [(new_content, similar_pages), (content2, [])]
        response = json.dumps({
            new_content.source_path: {
                "decision": "update",
                "target_page": "ai-ml/transformers.md",
                "target_title": "Transformers",
                "reason": "Merge",
                "confidence": 0.8,
                "merge_strategy": "append",
            },
            content2.source_path: {
                "decision": "create",
                "target_title": "New Topic",
                "reason": "Novel",
                "confidence": 0.9,
            },
        })
        results = decide_batch(items, response)

        assert len(results) == 2
        assert results[new_content.source_path].decision == Decision.UPDATE
        assert results[content2.source_path].decision == Decision.CREATE


# ---------------------------------------------------------------------------
# Rule-based fallback tests
# ---------------------------------------------------------------------------


class TestDecideWithoutLLM:
    def test_no_similar_pages_creates(self, new_content):
        result = decide_without_llm(new_content, [])
        assert result.decision == Decision.CREATE
        assert result.confidence >= 0.8

    def test_very_high_similarity_skips(self, new_content):
        pages = [
            SimilarPage(
                path="ai/transformers.md",
                title="Transformers",
                score=0.97,
                match_type="title",
            )
        ]
        result = decide_without_llm(new_content, pages)
        assert result.decision == Decision.SKIP
        assert result.target_page == "ai/transformers.md"

    def test_high_similarity_updates(self, new_content):
        pages = [
            SimilarPage(
                path="ai/transformers.md",
                title="Transformers",
                score=0.75,
                match_type="title",
            )
        ]
        result = decide_without_llm(new_content, pages)
        assert result.decision == Decision.UPDATE
        assert result.merge_strategy == "append"

    def test_moderate_similarity_creates(self, new_content):
        pages = [
            SimilarPage(
                path="ai/neural-networks.md",
                title="Neural Networks",
                score=0.45,
                match_type="tag",
            )
        ]
        result = decide_without_llm(new_content, pages)
        assert result.decision == Decision.CREATE
        assert "Neural Networks" in result.suggested_wikilinks

    def test_low_similarity_creates(self, new_content):
        pages = [
            SimilarPage(
                path="devops/kubernetes.md",
                title="Kubernetes",
                score=0.15,
                match_type="tag",
            )
        ]
        result = decide_without_llm(new_content, pages)
        assert result.decision == Decision.CREATE

    def test_custom_thresholds(self, new_content):
        pages = [
            SimilarPage(
                path="ai/transformers.md",
                title="Transformers",
                score=0.55,
                match_type="title",
            )
        ]
        # Lower update threshold so 0.55 triggers UPDATE
        result = decide_without_llm(
            new_content, pages, update_threshold=0.5
        )
        assert result.decision == Decision.UPDATE

    def test_uses_source_title(self, new_content):
        result = decide_without_llm(new_content, [])
        assert result.target_title == new_content.title

    def test_fallback_title_from_path(self):
        content = NewContent(
            source_path="sources/rss/2024-01-15-my-article.md",
            title="",
        )
        result = decide_without_llm(content, [])
        assert result.target_title == "My Article"


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------


class TestSimilarPage:
    def test_summary_for_prompt(self, similar_pages):
        page = similar_pages[0]
        summary = page.summary_for_prompt
        assert "Transformers" in summary
        assert "0.85" in summary
        assert "#ai" in summary
        assert "Attention Mechanism" in summary  # alias

    def test_summary_minimal(self):
        page = SimilarPage(path="test.md", title="Test")
        summary = page.summary_for_prompt
        assert "Test" in summary
        assert "0.00" in summary


class TestNewContent:
    def test_summary_for_prompt(self, new_content):
        summary = new_content.summary_for_prompt
        assert "Transformers Explained" in summary
        assert "rss" in summary
        assert "AI & Machine Learning" in summary
        assert "#ai" in summary

    def test_body_truncation_in_summary(self):
        content = NewContent(
            source_path="test.md",
            title="Test",
            body="x" * 5000,
        )
        summary = content.summary_for_prompt
        assert "[...truncated]" in summary


class TestDecisionResult:
    def test_is_actionable(self):
        assert DecisionResult(
            source_path="a", decision=Decision.CREATE
        ).is_actionable
        assert DecisionResult(
            source_path="a", decision=Decision.UPDATE
        ).is_actionable
        assert not DecisionResult(
            source_path="a", decision=Decision.SKIP
        ).is_actionable


# ---------------------------------------------------------------------------
# Mock LLM client for decidePageAction tests
# ---------------------------------------------------------------------------


@dataclass
class _MockTextBlock:
    text: str


@dataclass
class _MockMessage:
    content: list[_MockTextBlock] = field(default_factory=list)


class _MockMessages:
    """Mock for client.messages that returns configurable responses."""

    def __init__(self, response_data: dict | str | None = None, *, raise_on_call: Exception | None = None):
        self._response_data = response_data
        self._raise_on_call = raise_on_call
        self.call_count = 0
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs) -> _MockMessage:
        self.call_count += 1
        self.last_kwargs = kwargs
        if self._raise_on_call is not None:
            raise self._raise_on_call
        if self._response_data is None:
            return _MockMessage(content=[])
        if isinstance(self._response_data, str):
            return _MockMessage(content=[_MockTextBlock(text=self._response_data)])
        return _MockMessage(
            content=[_MockTextBlock(text=json.dumps(self._response_data))]
        )


class _MockClient:
    """Mock Anthropic client."""

    def __init__(self, messages: _MockMessages):
        self.messages = messages


def _make_client(response: dict | str | None = None, *, raise_on_call: Exception | None = None) -> _MockClient:
    return _MockClient(_MockMessages(response, raise_on_call=raise_on_call))


# ---------------------------------------------------------------------------
# decidePageAction tests
# ---------------------------------------------------------------------------


class TestDecidePageAction:
    """Tests for the end-to-end decide_page_action function."""

    def test_create_decision(self, new_content, similar_pages):
        """LLM returns CREATE — result is parsed correctly."""
        client = _make_client({
            "decision": "create",
            "target_page": "",
            "target_title": "Transformers Explained: A Visual Guide",
            "reason": "Existing page covers general transformers, not visual guides.",
            "confidence": 0.85,
            "merge_strategy": "",
            "suggested_tags": ["ai", "visual-guide", "transformers"],
            "suggested_wikilinks": ["Transformers", "Attention Mechanism"],
        })
        result = decide_page_action(new_content, similar_pages, client=client)

        assert result.decision == Decision.CREATE
        assert result.target_title == "Transformers Explained: A Visual Guide"
        assert result.confidence == 0.85
        assert result.source_path == new_content.source_path
        assert "ai" in result.suggested_tags
        assert "Transformers" in result.suggested_wikilinks
        assert result.is_actionable

    def test_update_decision(self, new_content, similar_pages):
        """LLM returns UPDATE — target_page and merge_strategy preserved."""
        client = _make_client({
            "decision": "update",
            "target_page": "ai-ml/transformers.md",
            "target_title": "Transformers",
            "reason": "New content adds visual explanations to existing page.",
            "confidence": 0.92,
            "merge_strategy": "append",
            "suggested_tags": ["visual-guide"],
            "suggested_wikilinks": [],
        })
        result = decide_page_action(new_content, similar_pages, client=client)

        assert result.decision == Decision.UPDATE
        assert result.target_page == "ai-ml/transformers.md"
        assert result.merge_strategy == "append"
        assert result.confidence == 0.92

    def test_skip_decision(self, new_content, similar_pages):
        """LLM returns SKIP — result is not actionable."""
        client = _make_client({
            "decision": "skip",
            "target_page": "ai-ml/transformers.md",
            "target_title": "Transformers",
            "reason": "Content already fully represented.",
            "confidence": 0.95,
        })
        result = decide_page_action(new_content, similar_pages, client=client)

        assert result.decision == Decision.SKIP
        assert not result.is_actionable

    def test_sends_correct_prompt(self, new_content, similar_pages):
        """Verifies the LLM receives the correct system + user prompt."""
        client = _make_client({
            "decision": "create",
            "target_title": "Test",
            "reason": "New",
            "confidence": 0.8,
        })
        decide_page_action(new_content, similar_pages, client=client)

        kwargs = client.messages.last_kwargs
        assert kwargs["model"] == "claude-sonnet-4-20250514"
        assert "wiki curator" in kwargs["system"].lower()
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0]["role"] == "user"
        assert "Transformers Explained" in kwargs["messages"][0]["content"]

    def test_custom_model(self, new_content, similar_pages):
        """Custom model is passed to the LLM call."""
        client = _make_client({
            "decision": "create",
            "target_title": "Test",
            "reason": "New",
            "confidence": 0.8,
        })
        decide_page_action(
            new_content, similar_pages,
            client=client, model="claude-3-5-haiku-20241022",
        )
        assert client.messages.last_kwargs["model"] == "claude-3-5-haiku-20241022"

    def test_fallback_on_llm_error(self, new_content, similar_pages):
        """Falls back to rule-based when LLM call raises an exception."""
        client = _make_client(raise_on_call=ConnectionError("network timeout"))
        result = decide_page_action(
            new_content, similar_pages,
            client=client, fallback_on_error=True,
        )

        # Rule-based: top similar page score=0.85 → UPDATE
        assert result.decision == Decision.UPDATE
        assert result.source_path == new_content.source_path

    def test_fallback_on_empty_response(self, new_content, similar_pages):
        """Falls back to rule-based when LLM returns empty response."""
        client = _make_client(None)  # empty response
        result = decide_page_action(
            new_content, similar_pages,
            client=client, fallback_on_error=True,
        )
        # Rule-based fallback
        assert isinstance(result.decision, Decision)

    def test_fallback_on_parse_error(self, new_content, similar_pages):
        """Falls back to rule-based when LLM returns unparseable text."""
        client = _make_client("This is not JSON at all, just random text.")
        result = decide_page_action(
            new_content, similar_pages,
            client=client, fallback_on_error=True,
        )
        assert isinstance(result.decision, Decision)

    def test_raises_when_fallback_disabled(self, new_content, similar_pages):
        """Raises DecidePageActionError when fallback_on_error=False."""
        client = _make_client(raise_on_call=RuntimeError("API down"))
        with pytest.raises(DecidePageActionError, match="API down"):
            decide_page_action(
                new_content, similar_pages,
                client=client, fallback_on_error=False,
            )

    def test_raises_on_parse_error_no_fallback(self, new_content, similar_pages):
        """Raises DecidePageActionError when parse fails and no fallback."""
        client = _make_client("not json")
        with pytest.raises(DecidePageActionError, match="parse"):
            decide_page_action(
                new_content, similar_pages,
                client=client, fallback_on_error=False,
            )

    def test_retries_on_transient_error(self, new_content, similar_pages):
        """Retries up to MAX_RETRIES times on transient errors."""
        mock_messages = _MockMessages(raise_on_call=ConnectionError("transient"))
        client = _MockClient(mock_messages)
        decide_page_action(
            new_content, similar_pages,
            client=client, fallback_on_error=True,
        )
        # Should have tried MAX_RETRIES + 1 times (3 total)
        assert mock_messages.call_count == 3

    def test_no_similar_pages(self, new_content):
        """Works correctly with no similar pages → CREATE."""
        client = _make_client({
            "decision": "create",
            "target_title": "Transformers Explained: A Visual Guide",
            "reason": "No existing pages cover this topic.",
            "confidence": 0.95,
            "suggested_tags": ["ai", "transformers"],
            "suggested_wikilinks": [],
        })
        result = decide_page_action(new_content, [], client=client)

        assert result.decision == Decision.CREATE
        assert result.confidence == 0.95

    def test_handles_code_fenced_response(self, new_content, similar_pages):
        """Handles LLM response wrapped in markdown code fences."""
        response = '```json\n{"decision": "update", "target_page": "ai/test.md", "target_title": "Test", "reason": "Merge", "confidence": 0.8, "merge_strategy": "append"}\n```'
        client = _make_client(response)
        result = decide_page_action(new_content, similar_pages, client=client)
        assert result.decision == Decision.UPDATE

    def test_alias_camel_case(self, new_content, similar_pages):
        """decidePageAction alias works identically."""
        client = _make_client({
            "decision": "create",
            "target_title": "Test",
            "reason": "New",
            "confidence": 0.8,
        })
        result = decidePageAction(new_content, similar_pages, client=client)
        assert result.decision == Decision.CREATE


# ---------------------------------------------------------------------------
# decidePageActionBatch tests
# ---------------------------------------------------------------------------


class TestDecidePageActionBatch:
    """Tests for the batch version of decide_page_action."""

    def test_batch_decisions(self, new_content, similar_pages):
        """Multiple items are decided in a single LLM call."""
        content2 = NewContent(
            source_path="sources/youtube/gpt-overview.md",
            title="GPT Architecture Overview",
            source_type="youtube",
        )
        items = [(new_content, similar_pages), (content2, [])]

        client = _make_client({
            new_content.source_path: {
                "decision": "update",
                "target_page": "ai-ml/transformers.md",
                "target_title": "Transformers",
                "reason": "Merge visual guide content.",
                "confidence": 0.88,
                "merge_strategy": "append",
            },
            content2.source_path: {
                "decision": "create",
                "target_title": "GPT Architecture Overview",
                "reason": "No existing page for GPT.",
                "confidence": 0.92,
            },
        })
        results = decide_page_action_batch(items, client=client)

        assert len(results) == 2
        assert results[new_content.source_path].decision == Decision.UPDATE
        assert results[content2.source_path].decision == Decision.CREATE

    def test_empty_items(self):
        """Empty items list returns empty dict without LLM call."""
        result = decide_page_action_batch([])
        assert result == {}

    def test_batch_fallback_on_error(self, new_content, similar_pages):
        """Falls back to per-item rule-based on LLM failure."""
        items = [(new_content, similar_pages)]
        client = _make_client(raise_on_call=RuntimeError("batch fail"))
        results = decide_page_action_batch(
            items, client=client, fallback_on_error=True,
        )
        assert len(results) == 1
        assert isinstance(results[new_content.source_path].decision, Decision)

    def test_batch_fills_missing_items(self, new_content, similar_pages):
        """Items missing from LLM response get rule-based fallback."""
        content2 = NewContent(
            source_path="sources/rss/missing.md",
            title="Missing Article",
        )
        items = [(new_content, similar_pages), (content2, [])]

        # LLM only returns decision for first item
        client = _make_client({
            new_content.source_path: {
                "decision": "update",
                "target_page": "ai-ml/transformers.md",
                "target_title": "Transformers",
                "reason": "Merge",
                "confidence": 0.8,
                "merge_strategy": "section",
            },
        })
        results = decide_page_action_batch(items, client=client)

        assert len(results) == 2
        assert results[new_content.source_path].decision == Decision.UPDATE
        # Missing item → rule-based CREATE (no similar pages)
        assert results[content2.source_path].decision == Decision.CREATE

    def test_batch_raises_when_fallback_disabled(self, new_content, similar_pages):
        """Raises when LLM fails and fallback_on_error=False."""
        items = [(new_content, similar_pages)]
        client = _make_client(raise_on_call=RuntimeError("fail"))
        with pytest.raises(DecidePageActionError):
            decide_page_action_batch(
                items, client=client, fallback_on_error=False,
            )

    def test_alias_camel_case(self, new_content, similar_pages):
        """decidePageActionBatch alias works."""
        client = _make_client({
            new_content.source_path: {
                "decision": "create",
                "target_title": "Test",
                "reason": "New",
                "confidence": 0.8,
            },
        })
        results = decidePageActionBatch(
            [(new_content, similar_pages)], client=client,
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Type validation tests
# ---------------------------------------------------------------------------


class TestValidateDecisionResult:
    """Tests for _validate_decision_result."""

    def test_valid_create(self):
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.CREATE,
            target_title="Test",
            reason="New topic",
            confidence=0.8,
        )
        assert _validate_decision_result(result) is None

    def test_valid_update(self):
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.UPDATE,
            target_page="existing.md",
            target_title="Existing",
            reason="Merge",
            confidence=0.7,
            merge_strategy="append",
        )
        assert _validate_decision_result(result) is None

    def test_valid_skip(self):
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.SKIP,
            target_page="existing.md",
            confidence=0.9,
        )
        assert _validate_decision_result(result) is None

    def test_update_missing_target_page(self):
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.UPDATE,
            target_page="",
            merge_strategy="append",
            confidence=0.8,
        )
        err = _validate_decision_result(result)
        assert err is not None
        assert "target_page" in err

    def test_invalid_merge_strategy(self):
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.UPDATE,
            target_page="existing.md",
            merge_strategy="invalid_strategy",
            confidence=0.8,
        )
        err = _validate_decision_result(result)
        assert err is not None
        assert "merge_strategy" in err

    def test_confidence_out_of_range(self):
        # Note: _parse_single_decision clamps confidence, so this tests
        # a result constructed directly (e.g., from external code)
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.CREATE,
            confidence=1.5,
        )
        err = _validate_decision_result(result)
        assert err is not None
        assert "confidence" in err

    def test_non_update_with_merge_strategy(self):
        """CREATE/SKIP with merge_strategy is invalid."""
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.CREATE,
            merge_strategy="append",
            confidence=0.8,
        )
        err = _validate_decision_result(result)
        assert err is not None
        assert "merge_strategy" in err

    def test_valid_update_strategies(self):
        """All valid merge strategies pass validation."""
        for strategy in ("append", "rewrite", "section"):
            result = DecisionResult(
                source_path="a.md",
                decision=Decision.UPDATE,
                target_page="existing.md",
                merge_strategy=strategy,
                confidence=0.8,
            )
            assert _validate_decision_result(result) is None

    def test_update_with_empty_strategy_is_valid(self):
        """UPDATE with empty merge_strategy is allowed (LLM may omit)."""
        result = DecisionResult(
            source_path="a.md",
            decision=Decision.UPDATE,
            target_page="existing.md",
            merge_strategy="",
            confidence=0.8,
        )
        assert _validate_decision_result(result) is None

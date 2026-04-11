"""
Tests for llm_wiki.lens_converter — interview answers → Lens YAML conversion.

Tests cover:
- User prompt building from interview answers
- JSON response parsing (clean, markdown-fenced, invalid)
- Lens validation from raw LLM output (valid, partial, invalid)
- LLM-based conversion with mocked Claude API
- Fallback rule-based conversion
- Safe conversion (auto-fallback)
- ConversionResult properties
- Edge cases (empty answers, duplicates, max_lenses cap)
- Cross-phase enrichment (language, compile strategy)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.lens import CompileStrategy, Lens
from llm_wiki.lens_converter import (
    ConversionResult,
    _build_user_prompt,
    _fallback_convert,
    _format_answer,
    _parse_json_response,
    _validate_and_convert_lens,
    convert_answers_to_lenses,
    convert_answers_to_lenses_safe,
    convert_answers_to_lenses_sync,
)


# ────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ────────────────────────────────────────────────────────────────────────────


FULL_ANSWERS = {
    "interests": "AI research and LLMs, React/frontend ecosystem",
    "interest_depth": "AI: deep technical with papers. Frontend: moderate practical.",
    "source_types": ["RSS feeds", "YouTube channels"],
    "initial_sources": (
        "https://simonwillison.net/atom/everything/\n"
        "YouTube: @3Blue1Brown"
    ),
    "compile_strategy": "merge",
    "wiki_language": "English",
    "vault_path": "~/Documents/MyWiki",
    "existing_vault": "Fresh/empty vault",
    "schedule_preference": "Every 30 minutes",
}

MINIMAL_ANSWERS = {
    "interests": "AI",
}

MOCK_LLM_LENSES = [
    {
        "id": "ai-research",
        "name": "AI Research",
        "description": "Latest developments in artificial intelligence, LLM architectures, and training.",
        "keywords": ["ai", "machine learning", "llm", "transformer", "deep learning"],
        "default_tags": ["ai", "research"],
        "wiki_directory": "topics/ai-research",
        "compile_strategy": "merge",
        "compile_instructions": (
            "Write detailed technical analysis. Include paper references. "
            "Link to [[Frontend Development]] when relevant. Write in English."
        ),
        "priority": 0,
        "enabled": True,
    },
    {
        "id": "frontend-dev",
        "name": "Frontend Development",
        "description": "Modern web frontend with React, Next.js, and CSS.",
        "keywords": ["react", "nextjs", "css", "javascript", "frontend", "web"],
        "default_tags": ["frontend", "web"],
        "wiki_directory": "topics/frontend-dev",
        "compile_strategy": "merge",
        "compile_instructions": (
            "Focus on practical examples and code snippets. "
            "Link to [[AI Research]] for AI-powered frontend tools. Write in English."
        ),
        "priority": 0,
        "enabled": True,
    },
]


def _mock_claude_response(lenses: list[dict]) -> MagicMock:
    """Create a mock Claude API response."""
    text_block = MagicMock()
    text_block.text = json.dumps(lenses)
    message = MagicMock()
    message.content = [text_block]
    return message


# ────────────────────────────────────────────────────────────────────────────
# _format_answer
# ────────────────────────────────────────────────────────────────────────────


class TestFormatAnswer:
    def test_none(self):
        assert _format_answer(None) == "(not provided)"

    def test_empty_string(self):
        assert _format_answer("") == "(not provided)"

    def test_string(self):
        assert _format_answer("AI research") == "AI research"

    def test_list(self):
        assert _format_answer(["RSS", "YouTube"]) == "RSS, YouTube"

    def test_empty_list(self):
        assert _format_answer([]) == "(none selected)"

    def test_bool_true(self):
        assert _format_answer(True) == "Yes"

    def test_bool_false(self):
        assert _format_answer(False) == "No"

    def test_whitespace_stripped(self):
        assert _format_answer("  AI  ") == "AI"


# ────────────────────────────────────────────────────────────────────────────
# _build_user_prompt
# ────────────────────────────────────────────────────────────────────────────


class TestBuildUserPrompt:
    def test_full_answers(self):
        prompt = _build_user_prompt(FULL_ANSWERS)
        assert "AI research and LLMs" in prompt
        assert "deep technical" in prompt
        assert "RSS feeds, YouTube channels" in prompt
        assert "simonwillison" in prompt
        assert "merge" in prompt
        assert "English" in prompt
        assert "~/Documents/MyWiki" in prompt
        assert "Fresh/empty vault" in prompt
        assert "Every 30 minutes" in prompt

    def test_minimal_answers(self):
        prompt = _build_user_prompt(MINIMAL_ANSWERS)
        assert "AI" in prompt
        # Missing keys should show "(not provided)"
        assert "(not provided)" in prompt

    def test_empty_answers(self):
        prompt = _build_user_prompt({})
        # All fields should be "(not provided)"
        assert prompt.count("(not provided)") >= 5


# ────────────────────────────────────────────────────────────────────────────
# _parse_json_response
# ────────────────────────────────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_valid_array(self):
        result = _parse_json_response('[{"id": "test"}]')
        assert len(result) == 1

    def test_with_markdown_fences(self):
        result = _parse_json_response('```json\n[{"id": "test"}]\n```')
        assert len(result) == 1

    def test_non_array_raises(self):
        with pytest.raises(ValueError, match="Expected JSON array"):
            _parse_json_response('{"id": "test"}')

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not json")

    def test_empty_array(self):
        result = _parse_json_response("[]")
        assert result == []


# ────────────────────────────────────────────────────────────────────────────
# _validate_and_convert_lens
# ────────────────────────────────────────────────────────────────────────────


class TestValidateAndConvertLens:
    def test_valid_full(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens(MOCK_LLM_LENSES[0], warnings)
        assert lens is not None
        assert lens.id == "ai-research"
        assert lens.name == "AI Research"
        assert lens.compile_strategy == CompileStrategy.MERGE
        assert "ai" in lens.keywords
        assert "ai" in lens.default_tags
        assert lens.wiki_directory == "topics/ai-research"
        assert lens.enabled is True
        assert warnings == []

    def test_missing_id_returns_none(self):
        warnings: list[str] = []
        result = _validate_and_convert_lens({"name": "Test"}, warnings)
        assert result is None
        assert len(warnings) == 1

    def test_empty_id_returns_none(self):
        warnings: list[str] = []
        result = _validate_and_convert_lens({"id": "", "name": "Test"}, warnings)
        assert result is None

    def test_derives_name_from_id(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens({"id": "ai-research"}, warnings)
        assert lens is not None
        assert lens.name == "Ai Research"
        assert any("Derived name" in w for w in warnings)

    def test_normalizes_id(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens({"id": "AI Research", "name": "AI"}, warnings)
        assert lens is not None
        assert lens.id == "ai-research"

    def test_default_wiki_directory(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens({"id": "test", "name": "Test"}, warnings)
        assert lens is not None
        assert lens.wiki_directory == "topics/test"

    def test_invalid_strategy_defaults_to_merge(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens(
            {"id": "test", "name": "Test", "compile_strategy": "invalid"},
            warnings,
        )
        assert lens is not None
        assert lens.compile_strategy == CompileStrategy.MERGE
        assert any("Invalid compile_strategy" in w for w in warnings)

    def test_handles_camelCase_keys(self):
        """LLM might output camelCase field names."""
        warnings: list[str] = []
        lens = _validate_and_convert_lens(
            {
                "id": "test",
                "name": "Test",
                "defaultTags": ["tag1"],
                "compileInstructions": "Write well",
                "wikiDirectory": "topics/custom",
                "compileStrategy": "per-source",
            },
            warnings,
        )
        assert lens is not None
        assert lens.default_tags == ["tag1"]
        assert lens.compile_instructions == "Write well"
        assert lens.wiki_directory == "topics/custom"
        assert lens.compile_strategy == CompileStrategy.PER_SOURCE

    def test_strips_hash_from_tags(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens(
            {"id": "test", "name": "Test", "default_tags": ["#ai", "#ml"]},
            warnings,
        )
        assert lens is not None
        assert lens.default_tags == ["ai", "ml"]

    def test_negative_priority_defaults_to_zero(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens(
            {"id": "test", "name": "Test", "priority": -1},
            warnings,
        )
        assert lens is not None
        assert lens.priority == 0

    def test_non_list_keywords_handled(self):
        warnings: list[str] = []
        lens = _validate_and_convert_lens(
            {"id": "test", "name": "Test", "keywords": "not a list"},
            warnings,
        )
        assert lens is not None
        assert lens.keywords == []


# ────────────────────────────────────────────────────────────────────────────
# ConversionResult
# ────────────────────────────────────────────────────────────────────────────


class TestConversionResult:
    def test_lens_count(self):
        lenses = [Lens(id="a", name="A"), Lens(id="b", name="B")]
        result = ConversionResult(lenses=lenses)
        assert result.lens_count == 2

    def test_lens_ids(self):
        lenses = [Lens(id="ai", name="AI"), Lens(id="web", name="Web")]
        result = ConversionResult(lenses=lenses)
        assert result.lens_ids == ["ai", "web"]

    def test_empty(self):
        result = ConversionResult(lenses=[])
        assert result.lens_count == 0
        assert result.lens_ids == []


# ────────────────────────────────────────────────────────────────────────────
# convert_answers_to_lenses (LLM-based, mocked)
# ────────────────────────────────────────────────────────────────────────────


class TestConvertAnswersToLenses:
    @pytest.mark.asyncio
    async def test_success_full_answers(self):
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS)

        assert result.method == "llm"
        assert result.lens_count == 2
        assert result.lenses[0].id == "ai-research"
        assert result.lenses[1].id == "frontend-dev"
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_prompt_includes_all_answers(self):
        """Verify the Claude API call includes all interview context."""
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            await convert_answers_to_lenses(FULL_ANSWERS)

        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]

        # All key answers should be in the prompt
        assert "AI research and LLMs" in user_msg
        assert "deep technical" in user_msg
        assert "RSS feeds, YouTube channels" in user_msg
        assert "English" in user_msg
        assert "merge" in user_msg

    @pytest.mark.asyncio
    async def test_deduplicates_by_id(self):
        duplicate_lenses = [
            {"id": "ai", "name": "AI"},
            {"id": "ai", "name": "AI duplicate"},
        ]
        mock_message = _mock_claude_response(duplicate_lenses)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS)

        assert result.lens_count == 1

    @pytest.mark.asyncio
    async def test_caps_at_max_lenses(self):
        many_lenses = [{"id": f"topic-{i}", "name": f"Topic {i}"} for i in range(15)]
        mock_message = _mock_claude_response(many_lenses)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS, max_lenses=5)

        assert result.lens_count <= 5

    @pytest.mark.asyncio
    async def test_empty_interests_returns_empty(self):
        """No interests → no lenses, no API call."""
        result = await convert_answers_to_lenses({"interests": ""})
        assert result.lens_count == 0
        assert any("No interests" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_none_interests_returns_empty(self):
        result = await convert_answers_to_lenses({})
        assert result.lens_count == 0

    @pytest.mark.asyncio
    async def test_empty_llm_response(self):
        text_block = MagicMock()
        text_block.text = ""
        message = MagicMock()
        message.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS)

        assert result.lens_count == 0
        assert any("Empty response" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_lenses_are_valid_lens_objects(self):
        """Ensure returned lenses can be serialized to YAML."""
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS)

        for lens in result.lenses:
            # Should serialize without errors
            yaml_str = lens.to_yaml()
            assert f"id: {lens.id}" in yaml_str
            # Should round-trip
            restored = Lens.from_yaml(yaml_str)
            assert restored.id == lens.id

    @pytest.mark.asyncio
    async def test_max_tokens_set(self):
        """Verify sufficient max_tokens for complex output."""
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            await convert_answers_to_lenses(FULL_ANSWERS)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] >= 4096

    @pytest.mark.asyncio
    async def test_system_prompt_included(self):
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            await convert_answers_to_lenses(FULL_ANSWERS)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert "Lens" in call_kwargs["system"]
        assert "llm-wiki" in call_kwargs["system"]


# ────────────────────────────────────────────────────────────────────────────
# convert_answers_to_lenses_sync
# ────────────────────────────────────────────────────────────────────────────


class TestConvertSync:
    def test_sync_wrapper(self):
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = convert_answers_to_lenses_sync(FULL_ANSWERS)

        assert result.method == "llm"
        assert result.lens_count == 2


# ────────────────────────────────────────────────────────────────────────────
# _fallback_convert (rule-based)
# ────────────────────────────────────────────────────────────────────────────


class TestFallbackConvert:
    def test_basic(self):
        result = _fallback_convert(FULL_ANSWERS)
        assert result.method == "fallback"
        assert result.lens_count >= 1
        # Should have at least one lens for each distinct interest
        ids = result.lens_ids
        assert any("ai" in lid for lid in ids)

    def test_applies_compile_strategy(self):
        answers = {**FULL_ANSWERS, "compile_strategy": "per-source"}
        result = _fallback_convert(answers)
        for lens in result.lenses:
            assert lens.compile_strategy == CompileStrategy.PER_SOURCE

    def test_applies_language(self):
        answers = {**FULL_ANSWERS, "wiki_language": "Korean"}
        result = _fallback_convert(answers)
        for lens in result.lenses:
            assert "Korean" in lens.compile_instructions

    def test_empty_interests(self):
        result = _fallback_convert({"interests": ""})
        assert result.lens_count == 0
        assert any("No interests" in w for w in result.warnings)

    def test_invalid_strategy_defaults(self):
        answers = {**FULL_ANSWERS, "compile_strategy": "invalid"}
        result = _fallback_convert(answers)
        for lens in result.lenses:
            assert lens.compile_strategy == CompileStrategy.MERGE

    def test_produces_valid_lens_objects(self):
        result = _fallback_convert(FULL_ANSWERS)
        for lens in result.lenses:
            yaml_str = lens.to_yaml()
            restored = Lens.from_yaml(yaml_str)
            assert restored.id == lens.id


# ────────────────────────────────────────────────────────────────────────────
# convert_answers_to_lenses_safe (auto-fallback)
# ────────────────────────────────────────────────────────────────────────────


class TestConvertSafe:
    @pytest.mark.asyncio
    async def test_llm_success(self):
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses_safe(FULL_ANSWERS)

        assert result.method == "llm"
        assert result.lens_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_import_error(self):
        with patch(
            "llm_wiki.lens_converter._get_anthropic_client",
            side_effect=ImportError("no anthropic"),
        ):
            result = await convert_answers_to_lenses_safe(FULL_ANSWERS)

        assert result.method == "fallback"
        assert result.lens_count >= 1

    @pytest.mark.asyncio
    async def test_fallback_on_env_error(self):
        with patch(
            "llm_wiki.lens_converter._get_anthropic_client",
            side_effect=EnvironmentError("ANTHROPIC_API_KEY required"),
        ):
            result = await convert_answers_to_lenses_safe(FULL_ANSWERS)

        assert result.method == "fallback"
        assert result.lens_count >= 1

    @pytest.mark.asyncio
    async def test_fallback_on_api_error(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ConnectionError("network down")

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses_safe(FULL_ANSWERS)

        assert result.method == "fallback"
        assert result.lens_count >= 1

    @pytest.mark.asyncio
    async def test_never_raises(self):
        """Safe variant should never raise, even on unexpected errors."""
        with patch(
            "llm_wiki.lens_converter._get_anthropic_client",
            side_effect=RuntimeError("unexpected"),
        ):
            result = await convert_answers_to_lenses_safe(FULL_ANSWERS)

        # Should fallback gracefully
        assert isinstance(result, ConversionResult)


# ────────────────────────────────────────────────────────────────────────────
# Integration: end-to-end lens quality
# ────────────────────────────────────────────────────────────────────────────


class TestLensQuality:
    """Verify that produced lenses meet Obsidian-native requirements."""

    @pytest.mark.asyncio
    async def test_lens_yaml_is_obsidian_friendly(self):
        mock_message = _mock_claude_response(MOCK_LLM_LENSES)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.lens_converter._get_anthropic_client", return_value=mock_client):
            result = await convert_answers_to_lenses(FULL_ANSWERS)

        for lens in result.lenses:
            # ID is a valid slug
            assert lens.id.islower()
            assert " " not in lens.id

            # Tags don't have # prefix
            for tag in lens.default_tags:
                assert not tag.startswith("#")
                assert tag.islower()

            # Wiki directory is set
            assert lens.wiki_directory

            # Keywords are lowercase
            for kw in lens.keywords:
                assert kw == kw.lower()

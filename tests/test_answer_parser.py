"""
Tests for llm_wiki.answer_parser — interest extraction from natural language.

Tests cover:
- Text splitting (commas, newlines, numbered lists, semicolons, "and")
- Slug generation (_to_slug)
- Depth inference from text cues
- Keyword expansion from known topics
- Tag generation
- Rule-based extraction (full pipeline)
- LLM-based extraction with mocked API
- Fallback from LLM to rule-based on errors
- ParseResult properties
- ParsedInterest.to_lens_dict() output
- Edge cases (empty input, single interest, unicode, duplicates)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.answer_parser import (
    DepthLevel,
    ParsedInterest,
    ParseResult,
    _depth_to_instructions,
    _expand_keywords,
    _generate_tags,
    _infer_depth,
    _parse_depth_preferences,
    _parse_json_response,
    _split_interests_text,
    _to_slug,
    _validate_interest,
    extract_interests_llm,
    extract_interests_rule_based,
    parse_interests,
    parse_interests_sync,
)


# ────────────────────────────────────────────────────────────────────────────
# _to_slug
# ────────────────────────────────────────────────────────────────────────────


class TestToSlug:
    def test_simple(self):
        assert _to_slug("AI Research") == "ai-research"

    def test_with_slashes(self):
        assert _to_slug("React/Next.js") == "react-nextjs"

    def test_with_ampersand(self):
        assert _to_slug("AI & ML") == "ai-ml"

    def test_already_slug(self):
        assert _to_slug("frontend-dev") == "frontend-dev"

    def test_mixed_separators(self):
        assert _to_slug("AI, ML & Deep Learning") == "ai-ml-deep-learning"

    def test_empty_string(self):
        assert _to_slug("") == ""

    def test_special_chars_stripped(self):
        assert _to_slug("C++ Programming") == "c-programming"

    def test_multiple_hyphens_collapsed(self):
        assert _to_slug("AI  --  Research") == "ai-research"

    def test_leading_trailing_hyphens_stripped(self):
        assert _to_slug("---test---") == "test"


# ────────────────────────────────────────────────────────────────────────────
# _split_interests_text
# ────────────────────────────────────────────────────────────────────────────


class TestSplitInterestsText:
    def test_comma_separated(self):
        result = _split_interests_text("AI, frontend, startups")
        assert result == ["AI", "frontend", "startups"]

    def test_comma_with_and(self):
        result = _split_interests_text("AI, frontend, and startups")
        assert result == ["AI", "frontend", "startups"]

    def test_newline_separated(self):
        result = _split_interests_text("AI\nfrontend\nstartups")
        assert result == ["AI", "frontend", "startups"]

    def test_numbered_list(self):
        result = _split_interests_text("1. AI\n2. frontend\n3. startups")
        assert result == ["AI", "frontend", "startups"]

    def test_bullet_list(self):
        result = _split_interests_text("- AI\n- frontend\n- startups")
        assert result == ["AI", "frontend", "startups"]

    def test_semicolons(self):
        result = _split_interests_text("AI; frontend; startups")
        assert result == ["AI", "frontend", "startups"]

    def test_and_separator(self):
        result = _split_interests_text("AI and frontend and startups")
        assert result == ["AI", "frontend", "startups"]

    def test_single_interest(self):
        result = _split_interests_text("machine learning")
        assert result == ["machine learning"]

    def test_empty_string(self):
        result = _split_interests_text("")
        assert result == []

    def test_whitespace_only(self):
        result = _split_interests_text("   \n  ")
        assert result == []

    def test_complex_comma_list(self):
        result = _split_interests_text(
            "AI research and LLMs, React/frontend ecosystem, startup growth strategies"
        )
        assert len(result) == 3
        assert result[0] == "AI research and LLMs"
        assert result[1] == "React/frontend ecosystem"
        assert result[2] == "startup growth strategies"

    def test_numbered_with_parentheses(self):
        result = _split_interests_text("1) AI\n2) frontend")
        assert result == ["AI", "frontend"]


# ────────────────────────────────────────────────────────────────────────────
# _infer_depth
# ────────────────────────────────────────────────────────────────────────────


class TestInferDepth:
    def test_deep_signals(self):
        assert _infer_depth("deep technical analysis with papers") == "deep"

    def test_surface_signals(self):
        assert _infer_depth("quick news overview") == "surface"

    def test_neutral_defaults_moderate(self):
        assert _infer_depth("some general interest") == "moderate"

    def test_empty_string(self):
        assert _infer_depth("") == "moderate"

    def test_mixed_favors_majority(self):
        # "deep" and "research" = 2 deep vs "news" = 1 surface
        assert _infer_depth("deep research news") == "deep"


# ────────────────────────────────────────────────────────────────────────────
# _expand_keywords
# ────────────────────────────────────────────────────────────────────────────


class TestExpandKeywords:
    def test_known_topic(self):
        kws = _expand_keywords("AI Research", "ai-research")
        assert "ai research" in kws
        assert any("machine learning" in k for k in kws)

    def test_unknown_topic(self):
        kws = _expand_keywords("Cooking recipes", "cooking-recipes")
        assert "cooking recipes" in kws
        assert "cooking" in kws
        assert "recipes" in kws

    def test_max_10_keywords(self):
        kws = _expand_keywords("AI ML LLM frontend React Python", "ai-ml-llm-frontend-react-python")
        assert len(kws) <= 10

    def test_no_duplicate_keywords(self):
        kws = _expand_keywords("frontend development", "frontend")
        # Should not have duplicates (case-insensitive)
        lower_kws = [k.lower() for k in kws]
        assert len(lower_kws) == len(set(lower_kws))


# ────────────────────────────────────────────────────────────────────────────
# _generate_tags
# ────────────────────────────────────────────────────────────────────────────


class TestGenerateTags:
    def test_basic(self):
        tags = _generate_tags("AI Research", "ai-research")
        assert "ai" in tags
        assert "research" in tags

    def test_max_4(self):
        tags = _generate_tags("a b c d e f", "a-b-c-d-e-f")
        assert len(tags) <= 4

    def test_skips_single_char(self):
        tags = _generate_tags("A Research", "a-research")
        assert "a" not in tags
        assert "research" in tags


# ────────────────────────────────────────────────────────────────────────────
# _parse_depth_preferences
# ────────────────────────────────────────────────────────────────────────────


class TestParseDepthPreferences:
    def test_specific_mapping(self):
        result = _parse_depth_preferences(
            "AI: deep technical with papers. Frontend: quick practical tips.",
            ["AI", "Frontend"],
        )
        assert result.get("ai") == "deep"
        assert result.get("frontend") == "surface"

    def test_global_fallback(self):
        result = _parse_depth_preferences(
            "I want deep technical analysis for everything",
            ["AI", "Frontend"],
        )
        assert result.get("ai") == "deep"
        assert result.get("frontend") == "deep"

    def test_empty_depth_answer(self):
        result = _parse_depth_preferences("", ["AI"])
        assert result == {}

    def test_dash_separator(self):
        result = _parse_depth_preferences(
            "AI - deep research\nstartups - surface news",
            ["AI", "Startups"],
        )
        assert result.get("ai") == "deep"
        assert result.get("startups") == "surface"


# ────────────────────────────────────────────────────────────────────────────
# _depth_to_instructions
# ────────────────────────────────────────────────────────────────────────────


class TestDepthToInstructions:
    def test_deep(self):
        inst = _depth_to_instructions("deep", "AI Research")
        assert "detailed" in inst.lower() or "technical" in inst.lower()
        assert "AI Research" in inst
        assert "[[wikilinks]]" in inst

    def test_surface(self):
        inst = _depth_to_instructions("surface", "Startups")
        assert "concise" in inst.lower() or "brief" in inst.lower()
        assert "Startups" in inst

    def test_moderate(self):
        inst = _depth_to_instructions("moderate", "Frontend")
        assert "Frontend" in inst
        assert "[[wikilinks]]" in inst


# ────────────────────────────────────────────────────────────────────────────
# _validate_interest
# ────────────────────────────────────────────────────────────────────────────


class TestValidateInterest:
    def test_valid(self):
        raw = {
            "id": "ai-research",
            "name": "AI Research",
            "description": "Latest AI developments",
            "keywords": ["AI", "machine learning"],
            "default_tags": ["ai", "research"],
            "depth": "deep",
            "compile_instructions": "Write detailed analysis",
        }
        result = _validate_interest(raw)
        assert result is not None
        assert result.id == "ai-research"
        assert result.name == "AI Research"
        assert result.depth == "deep"

    def test_missing_id(self):
        assert _validate_interest({"name": "Test"}) is None

    def test_empty_id(self):
        assert _validate_interest({"id": "", "name": "Test"}) is None

    def test_derives_name_from_id(self):
        result = _validate_interest({"id": "ai-research"})
        assert result is not None
        assert result.name == "Ai Research"

    def test_invalid_depth_defaults(self):
        result = _validate_interest({"id": "test", "depth": "extreme"})
        assert result is not None
        assert result.depth == "moderate"

    def test_handles_camelCase_keys(self):
        """LLM might output camelCase field names."""
        result = _validate_interest({
            "id": "test",
            "name": "Test",
            "defaultTags": ["tag1"],
            "compileInstructions": "Write well",
            "wikiDirectory": "topics/test",
        })
        assert result is not None
        assert result.default_tags == ["tag1"]
        assert result.compile_instructions == "Write well"
        assert result.wiki_directory == "topics/test"

    def test_strips_hash_from_tags(self):
        result = _validate_interest({
            "id": "test",
            "default_tags": ["#ai", "#research"],
        })
        assert result is not None
        assert result.default_tags == ["ai", "research"]

    def test_non_list_keywords_handled(self):
        result = _validate_interest({"id": "test", "keywords": "not a list"})
        assert result is not None
        assert result.keywords == []


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


# ────────────────────────────────────────────────────────────────────────────
# ParsedInterest
# ────────────────────────────────────────────────────────────────────────────


class TestParsedInterest:
    def test_frozen(self):
        interest = ParsedInterest(
            id="test", name="Test", description="Desc",
        )
        with pytest.raises(AttributeError):
            interest.id = "changed"  # type: ignore[misc]

    def test_to_lens_dict(self):
        interest = ParsedInterest(
            id="ai-research",
            name="AI Research",
            description="Latest AI developments",
            keywords=["AI", "ML"],
            default_tags=["ai", "research"],
            depth="deep",
            compile_instructions="Write detailed analysis",
            wiki_directory="topics/ai-research",
        )
        d = interest.to_lens_dict()
        assert d["id"] == "ai-research"
        assert d["name"] == "AI Research"
        assert d["description"] == "Latest AI developments"
        assert d["keywords"] == ["AI", "ML"]
        assert d["defaultTags"] == ["ai", "research"]
        assert d["wikiDirectory"] == "topics/ai-research"
        assert d["compileStrategy"] == "merge"
        assert d["compileInstructions"] == "Write detailed analysis"
        assert d["enabled"] is True

    def test_to_lens_dict_default_directory(self):
        interest = ParsedInterest(
            id="frontend", name="Frontend", description="",
        )
        d = interest.to_lens_dict()
        assert d["wikiDirectory"] == "topics/frontend"

    def test_to_lens_dict_omits_empty(self):
        interest = ParsedInterest(
            id="test", name="Test", description="",
        )
        d = interest.to_lens_dict()
        assert "description" not in d
        assert "keywords" not in d
        assert "compileInstructions" not in d


# ────────────────────────────────────────────────────────────────────────────
# ParseResult
# ────────────────────────────────────────────────────────────────────────────


class TestParseResult:
    def test_interest_count(self):
        result = ParseResult(
            interests=[
                ParsedInterest(id="a", name="A", description=""),
                ParsedInterest(id="b", name="B", description=""),
            ],
            raw_answer="A, B",
            method="rule-based",
        )
        assert result.interest_count == 2

    def test_all_keywords_deduped(self):
        result = ParseResult(
            interests=[
                ParsedInterest(id="a", name="A", description="", keywords=["ai", "ML"]),
                ParsedInterest(id="b", name="B", description="", keywords=["AI", "data"]),
            ],
            raw_answer="",
            method="rule-based",
        )
        kws = result.all_keywords
        lower_kws = [k.lower() for k in kws]
        # "ai" appears in both but should only appear once
        assert lower_kws.count("ai") == 1
        assert "ml" in lower_kws
        assert "data" in lower_kws

    def test_empty_interests(self):
        result = ParseResult(interests=[], raw_answer="", method="rule-based")
        assert result.interest_count == 0
        assert result.all_keywords == []


# ────────────────────────────────────────────────────────────────────────────
# extract_interests_rule_based
# ────────────────────────────────────────────────────────────────────────────


class TestExtractInterestsRuleBased:
    def test_basic_comma_list(self):
        result = extract_interests_rule_based("AI, frontend, startups")
        assert result.method == "rule-based"
        assert result.interest_count == 3
        assert result.confidence == 0.5

        ids = [i.id for i in result.interests]
        assert "ai" in ids
        assert "frontend" in ids
        assert "startups" in ids

    def test_with_depth(self):
        result = extract_interests_rule_based(
            "AI research, frontend",
            "AI research: deep technical. Frontend: surface news.",
        )
        ai = next(i for i in result.interests if "ai" in i.id)
        frontend = next(i for i in result.interests if "frontend" in i.id)
        assert ai.depth == "deep"
        assert frontend.depth == "surface"

    def test_empty_input(self):
        result = extract_interests_rule_based("")
        assert result.interest_count == 0
        assert result.confidence == 0.0

    def test_single_interest(self):
        result = extract_interests_rule_based("machine learning")
        assert result.interest_count == 1
        assert result.interests[0].id == "machine-learning"
        assert result.interests[0].name == "Machine Learning"

    def test_deduplicates(self):
        result = extract_interests_rule_based("AI, ai, AI Research")
        ids = [i.id for i in result.interests]
        # "ai" should appear only once
        assert ids.count("ai") == 1

    def test_keywords_populated(self):
        result = extract_interests_rule_based("AI research")
        interest = result.interests[0]
        assert len(interest.keywords) > 0
        assert any("ai" in k.lower() for k in interest.keywords)

    def test_tags_populated(self):
        result = extract_interests_rule_based("AI research")
        interest = result.interests[0]
        assert len(interest.default_tags) > 0

    def test_compile_instructions_populated(self):
        result = extract_interests_rule_based("AI research")
        interest = result.interests[0]
        assert interest.compile_instructions != ""
        assert "[[wikilinks]]" in interest.compile_instructions

    def test_wiki_directory_set(self):
        result = extract_interests_rule_based("frontend")
        interest = result.interests[0]
        assert interest.wiki_directory == "topics/frontend"

    def test_complex_input(self):
        result = extract_interests_rule_based(
            "AI research and LLMs, React/frontend ecosystem, "
            "startup growth strategies, Korean tech industry"
        )
        assert result.interest_count == 4
        # Each interest should have all required fields
        for interest in result.interests:
            assert interest.id
            assert interest.name
            assert interest.description
            assert interest.keywords
            assert interest.default_tags
            assert interest.compile_instructions
            assert interest.wiki_directory


# ────────────────────────────────────────────────────────────────────────────
# extract_interests_llm (mocked)
# ────────────────────────────────────────────────────────────────────────────


def _mock_claude_response(interests: list[dict]) -> MagicMock:
    text_block = MagicMock()
    text_block.text = json.dumps(interests)
    message = MagicMock()
    message.content = [text_block]
    return message


class TestExtractInterestsLLM:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_interests = [
            {
                "id": "ai-research",
                "name": "AI Research",
                "description": "Latest developments in AI",
                "keywords": ["AI", "machine learning", "deep learning"],
                "default_tags": ["ai", "research"],
                "depth": "deep",
                "compile_instructions": "Write detailed analysis",
            },
            {
                "id": "frontend-dev",
                "name": "Frontend Development",
                "description": "Modern web frontend",
                "keywords": ["React", "CSS", "JavaScript"],
                "default_tags": ["frontend", "web"],
                "depth": "moderate",
                "compile_instructions": "Focus on practical examples",
            },
        ]

        mock_message = _mock_claude_response(mock_interests)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await extract_interests_llm("AI and frontend development")

        assert result.method == "llm"
        assert result.interest_count == 2
        assert result.confidence == 0.9
        assert result.interests[0].id == "ai-research"
        assert result.interests[1].id == "frontend-dev"

    @pytest.mark.asyncio
    async def test_deduplicates_by_id(self):
        mock_interests = [
            {"id": "ai", "name": "AI"},
            {"id": "ai", "name": "AI duplicate"},
        ]
        mock_message = _mock_claude_response(mock_interests)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await extract_interests_llm("AI")

        assert result.interest_count == 1

    @pytest.mark.asyncio
    async def test_caps_at_10(self):
        mock_interests = [
            {"id": f"topic-{i}", "name": f"Topic {i}"} for i in range(15)
        ]
        mock_message = _mock_claude_response(mock_interests)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await extract_interests_llm("many topics")

        assert result.interest_count <= 10

    @pytest.mark.asyncio
    async def test_empty_response(self):
        text_block = MagicMock()
        text_block.text = ""
        message = MagicMock()
        message.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await extract_interests_llm("test")

        assert result.interest_count == 0
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_with_depth_answer(self):
        mock_interests = [
            {"id": "ai", "name": "AI", "depth": "deep"},
        ]
        mock_message = _mock_claude_response(mock_interests)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await extract_interests_llm(
                "AI research", depth_answer="deep technical analysis"
            )

        # Verify the depth answer was included in the prompt
        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "deep technical analysis" in user_msg


# ────────────────────────────────────────────────────────────────────────────
# parse_interests (unified API with fallback)
# ────────────────────────────────────────────────────────────────────────────


class TestParseInterests:
    @pytest.mark.asyncio
    async def test_force_rule_based(self):
        result = await parse_interests("AI, frontend", force_rule_based=True)
        assert result.method == "rule-based"
        assert result.interest_count == 2

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await parse_interests("")
        assert result.interest_count == 0

    @pytest.mark.asyncio
    async def test_fallback_on_import_error(self):
        with patch(
            "llm_wiki.answer_parser._get_anthropic_client",
            side_effect=ImportError("no anthropic"),
        ):
            result = await parse_interests("AI, frontend")

        assert result.method == "rule-based"
        assert result.interest_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_env_error(self):
        with patch(
            "llm_wiki.answer_parser._get_anthropic_client",
            side_effect=EnvironmentError("ANTHROPIC_API_KEY required"),
        ):
            result = await parse_interests("AI, frontend")

        assert result.method == "rule-based"
        assert result.interest_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_api_error(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ConnectionError("network down")

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await parse_interests("AI, frontend")

        assert result.method == "rule-based"
        assert result.interest_count == 2

    @pytest.mark.asyncio
    async def test_llm_success_path(self):
        mock_interests = [
            {"id": "ai", "name": "AI", "description": "Artificial Intelligence"},
        ]
        mock_message = _mock_claude_response(mock_interests)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.answer_parser._get_anthropic_client", return_value=mock_client):
            result = await parse_interests("AI research")

        assert result.method == "llm"


class TestParseInterestsSync:
    def test_sync_wrapper(self):
        result = parse_interests_sync("AI, frontend", force_rule_based=True)
        assert result.method == "rule-based"
        assert result.interest_count == 2

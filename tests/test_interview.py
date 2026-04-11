"""
Tests for llm_wiki.interview — LLM-based dynamic question generation.

Tests cover:
- InterviewContext creation and summary formatting
- Prompt template rendering
- LLM response parsing (valid JSON, markdown fences, malformed)
- Question validation and dedup
- generate_follow_up_questions with mocked Claude API
- Error handling (missing API key, network errors, bad JSON)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.interview import (
    FollowUpQuestion,
    InterviewContext,
    _parse_llm_response,
    _validate_question,
    generate_follow_up_questions,
    generate_follow_up_questions_sync,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    PHASE_HINTS,
)


# ────────────────────────────────────────────────────────────────────────────
# InterviewContext
# ────────────────────────────────────────────────────────────────────────────


class TestInterviewContext:
    def test_summary_empty(self):
        ctx = InterviewContext(answers={}, current_phase="interests")
        assert ctx.summary() == "(no answers yet)"

    def test_summary_with_answers(self):
        ctx = InterviewContext(
            answers={
                "interests": "AI and ML",
                "source_types": ["RSS feeds", "YouTube"],
                "existing_vault": True,
            },
            current_phase="sources",
        )
        summary = ctx.summary()
        assert "interests: AI and ML" in summary
        assert "source_types: RSS feeds, YouTube" in summary
        assert "existing_vault: Yes" in summary

    def test_summary_bool_false(self):
        ctx = InterviewContext(
            answers={"confirm": False},
            current_phase="vault",
        )
        assert "confirm: No" in ctx.summary()


# ────────────────────────────────────────────────────────────────────────────
# _parse_llm_response
# ────────────────────────────────────────────────────────────────────────────


class TestParseLLMResponse:
    def test_valid_json_array(self):
        raw = json.dumps([{"key": "test", "question": "Q?"}])
        result = _parse_llm_response(raw)
        assert len(result) == 1
        assert result[0]["key"] == "test"

    def test_json_with_markdown_fences(self):
        raw = '```json\n[{"key": "test"}]\n```'
        result = _parse_llm_response(raw)
        assert len(result) == 1

    def test_json_with_plain_fences(self):
        raw = '```\n[{"key": "test"}]\n```'
        result = _parse_llm_response(raw)
        assert len(result) == 1

    def test_non_array_raises(self):
        raw = json.dumps({"key": "test"})
        with pytest.raises(ValueError, match="Expected JSON array"):
            _parse_llm_response(raw)

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response("not json at all")

    def test_whitespace_handling(self):
        raw = '  \n  [{"key": "test"}]  \n  '
        result = _parse_llm_response(raw)
        assert len(result) == 1


# ────────────────────────────────────────────────────────────────────────────
# _validate_question
# ────────────────────────────────────────────────────────────────────────────


class TestValidateQuestion:
    def test_valid_question(self):
        raw = {
            "key": "ai_subfields",
            "phase": "interests",
            "question": "Which AI subfields?",
            "description": "Clarify your AI interest",
            "answer_type": "free-text",
            "options": [],
            "example": "NLP, CV, RL",
            "reason": "Your interest in AI is broad",
        }
        q = _validate_question(raw, asked_keys=[])
        assert q is not None
        assert q.key == "ai_subfields"
        assert q.phase == "interests"
        assert q.answer_type == "free-text"

    def test_skips_already_asked(self):
        raw = {"key": "interests", "question": "Q?"}
        q = _validate_question(raw, asked_keys=["interests"])
        assert q is None

    def test_missing_key(self):
        raw = {"question": "Q?"}
        q = _validate_question(raw, asked_keys=[])
        assert q is None

    def test_invalid_phase_defaults(self):
        raw = {"key": "test", "phase": "invalid", "question": "Q?"}
        q = _validate_question(raw, asked_keys=[])
        assert q is not None
        assert q.phase == "interests"

    def test_invalid_answer_type_defaults(self):
        raw = {"key": "test", "answer_type": "slider", "question": "Q?"}
        q = _validate_question(raw, asked_keys=[])
        assert q is not None
        assert q.answer_type == "free-text"

    def test_options_non_list(self):
        raw = {"key": "test", "options": "not a list", "question": "Q?"}
        q = _validate_question(raw, asked_keys=[])
        assert q is not None
        assert q.options == []

    def test_options_coerced_to_strings(self):
        raw = {"key": "test", "options": [1, 2, 3], "question": "Q?"}
        q = _validate_question(raw, asked_keys=[])
        assert q is not None
        assert q.options == ["1", "2", "3"]


# ────────────────────────────────────────────────────────────────────────────
# Prompt templates
# ────────────────────────────────────────────────────────────────────────────


class TestPromptTemplates:
    def test_system_prompt_has_key_instructions(self):
        assert "1-3 follow-up questions" in SYSTEM_PROMPT
        assert "JSON array" in SYSTEM_PROMPT
        assert "asked_keys" in SYSTEM_PROMPT

    def test_user_prompt_renders(self):
        rendered = USER_PROMPT_TEMPLATE.format(
            phase="interests",
            answers_summary="- interests: AI and ML",
            asked_keys="interests",
        )
        assert "interests" in rendered
        assert "AI and ML" in rendered

    def test_phase_hints_cover_all_phases(self):
        for phase in ("interests", "sources", "output", "vault"):
            assert phase in PHASE_HINTS


# ────────────────────────────────────────────────────────────────────────────
# generate_follow_up_questions (mocked API)
# ────────────────────────────────────────────────────────────────────────────


def _mock_claude_response(questions: list[dict]) -> MagicMock:
    """Create a mock Anthropic message response."""
    text_block = MagicMock()
    text_block.text = json.dumps(questions)
    message = MagicMock()
    message.content = [text_block]
    return message


class TestGenerateFollowUpQuestions:
    @pytest.fixture
    def context(self):
        return InterviewContext(
            answers={"interests": "AI and machine learning, frontend development"},
            current_phase="interests",
            asked_keys=["interests"],
        )

    @pytest.mark.asyncio
    async def test_success(self, context):
        mock_questions = [
            {
                "key": "ai_subfields",
                "phase": "interests",
                "question": "Which specific areas of AI interest you most?",
                "description": "Help us focus your AI lens",
                "answer_type": "multi-select",
                "options": ["NLP", "Computer Vision", "RL", "LLM/Foundation Models"],
                "example": "NLP, LLM/Foundation Models",
                "reason": "AI is broad; narrowing helps create focused lenses",
            },
            {
                "key": "frontend_framework",
                "phase": "interests",
                "question": "Which frontend frameworks/tools do you primarily use?",
                "description": "Helps find relevant sources",
                "answer_type": "free-text",
                "options": [],
                "example": "React, Next.js, Tailwind CSS",
                "reason": "Frontend is broad; knowing your stack helps find better sources",
            },
        ]

        mock_message = _mock_claude_response(mock_questions)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context)

        assert len(questions) == 2
        assert questions[0].key == "ai_subfields"
        assert questions[1].key == "frontend_framework"
        assert questions[0].answer_type == "multi-select"

        # Verify API was called with correct parameters
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == SYSTEM_PROMPT
        assert call_kwargs["max_tokens"] == 1024
        assert len(call_kwargs["messages"]) == 1

    @pytest.mark.asyncio
    async def test_max_questions_cap(self, context):
        mock_questions = [
            {"key": f"q{i}", "phase": "interests", "question": f"Q{i}?", "description": "", "answer_type": "free-text"}
            for i in range(5)
        ]
        mock_message = _mock_claude_response(mock_questions)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context, max_questions=2)

        assert len(questions) == 2

    @pytest.mark.asyncio
    async def test_filters_already_asked(self, context):
        mock_questions = [
            {"key": "interests", "phase": "interests", "question": "Repeat?", "description": ""},
            {"key": "new_question", "phase": "interests", "question": "New?", "description": "", "answer_type": "free-text"},
        ]
        mock_message = _mock_claude_response(mock_questions)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context)

        assert len(questions) == 1
        assert questions[0].key == "new_question"

    @pytest.mark.asyncio
    async def test_empty_response(self, context):
        text_block = MagicMock()
        text_block.text = ""
        message = MagicMock()
        message.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context)

        assert questions == []

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self, context):
        text_block = MagicMock()
        text_block.text = "not valid json"
        message = MagicMock()
        message.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context)

        assert questions == []

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self, context):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ConnectionError("network down")

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = await generate_follow_up_questions(context)

        assert questions == []

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self, context):
        with patch("llm_wiki.interview._get_anthropic_client", side_effect=EnvironmentError("ANTHROPIC_API_KEY environment variable is required")):
            with pytest.raises(EnvironmentError):
                await generate_follow_up_questions(context)

    @pytest.mark.asyncio
    async def test_missing_anthropic_package_raises(self, context):
        with patch("llm_wiki.interview._get_anthropic_client", side_effect=ImportError("no anthropic")):
            with pytest.raises(ImportError):
                await generate_follow_up_questions(context)


class TestGenerateFollowUpSync:
    def test_sync_wrapper(self):
        ctx = InterviewContext(
            answers={"interests": "Testing"},
            current_phase="interests",
            asked_keys=["interests"],
        )

        mock_questions = [
            {"key": "test_q", "phase": "interests", "question": "Follow up?", "description": "", "answer_type": "free-text"},
        ]
        mock_message = _mock_claude_response(mock_questions)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("llm_wiki.interview._get_anthropic_client", return_value=mock_client):
            questions = generate_follow_up_questions_sync(ctx)

        assert len(questions) == 1
        assert questions[0].key == "test_q"


# ────────────────────────────────────────────────────────────────────────────
# FollowUpQuestion data class
# ────────────────────────────────────────────────────────────────────────────


class TestFollowUpQuestion:
    def test_frozen(self):
        q = FollowUpQuestion(
            key="test",
            phase="interests",
            question="Q?",
            description="D",
            answer_type="free-text",
        )
        with pytest.raises(AttributeError):
            q.key = "changed"  # type: ignore[misc]

    def test_defaults(self):
        q = FollowUpQuestion(
            key="test",
            phase="interests",
            question="Q?",
            description="D",
            answer_type="free-text",
        )
        assert q.options == []
        assert q.example == ""
        assert q.reason == ""

"""Tests for compile confirmation prompt and user choice parsing."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from llm_wiki.confirmation import (
    ConfirmationChoice,
    ConfirmationState,
    confirmation_prompt_json,
    deserialize_results,
    format_batch_confirmation_prompt,
    format_confirmation_prompt,
    load_pending_confirmation,
    parse_user_choice,
    resolve_confirmation,
    save_confirmation,
    serialize_results,
)
from llm_wiki.preview import (
    CompileAction,
    CompileResult,
    SourceReference,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TIME = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_result() -> CompileResult:
    return CompileResult(
        title="Transformer Architecture",
        body="# Transformer Architecture\n\nThe transformer...\n",
        frontmatter={"title": "Transformer Architecture", "type": "wiki"},
        lens_id="ai",
        lens_name="AI Research",
        directory="ai",
        action=CompileAction.CREATE,
        sources=[
            SourceReference(
                path="sources/rss/transformers.md",
                title="Transformers Explained",
                source_type="rss",
                url="https://example.com/transformers",
                score=0.9,
            ),
        ],
        wikilinks=["Self-Attention", "BERT"],
        tags=["ai", "transformers"],
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def update_result() -> CompileResult:
    return CompileResult(
        title="Prompt Engineering",
        body="# Prompt Engineering\n\nUpdated...\n",
        frontmatter={"title": "Prompt Engineering", "type": "wiki"},
        lens_id="ai",
        lens_name="AI Research",
        directory="ai",
        action=CompileAction.UPDATE,
        existing_path="ai/prompt-engineering.md",
        tags=["ai", "prompting"],
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def skip_result() -> CompileResult:
    return CompileResult(
        title="Neural Networks",
        body="# Neural Networks\n\nNo changes.\n",
        action=CompileAction.SKIP,
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Single confirmation prompt tests
# ---------------------------------------------------------------------------


class TestFormatConfirmationPrompt:
    def test_contains_preview(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False)
        assert "Transformer Architecture" in output
        assert "[NEW]" in output

    def test_contains_choices(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False)
        assert "Approve" in output
        assert "Reject" in output
        assert "Modify" in output

    def test_contains_write_summary(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False)
        assert "1 new page" in output

    def test_attempt_indicator(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False, attempt=2)
        assert "attempt #2" in output

    def test_no_attempt_indicator_on_first(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False, attempt=1)
        assert "attempt" not in output

    def test_no_partial_for_single(self, sample_result: CompileResult):
        output = format_confirmation_prompt(sample_result, color=False)
        assert "Partial" not in output


# ---------------------------------------------------------------------------
# Batch confirmation prompt tests
# ---------------------------------------------------------------------------


class TestFormatBatchConfirmationPrompt:
    def test_empty_results(self):
        output = format_batch_confirmation_prompt([], color=False)
        assert "No compile results" in output

    def test_contains_batch_preview(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = format_batch_confirmation_prompt(
            [sample_result, update_result], color=False
        )
        assert "COMPILE PREVIEW" in output
        assert "Transformer Architecture" in output
        assert "Prompt Engineering" in output

    def test_contains_choices(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = format_batch_confirmation_prompt(
            [sample_result, update_result], color=False
        )
        assert "Approve" in output
        assert "Reject" in output
        assert "Modify" in output

    def test_partial_option_for_multi(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = format_batch_confirmation_prompt(
            [sample_result, update_result], color=False
        )
        assert "Partial" in output

    def test_skip_only_shows_no_changes(self, skip_result: CompileResult):
        output = format_batch_confirmation_prompt([skip_result], color=False)
        assert "No changes to write" in output

    def test_page_list_in_batch(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = format_batch_confirmation_prompt(
            [sample_result, update_result], color=False
        )
        assert "Transformer Architecture" in output
        assert "Prompt Engineering" in output


# ---------------------------------------------------------------------------
# User choice parsing tests
# ---------------------------------------------------------------------------


class TestParseUserChoice:
    # --- Approve ---
    @pytest.mark.parametrize("response", [
        "1", "yes", "y", "approve", "ok", "lgtm", "ship", "write",
        "승인", "확인", "좋아", "ㅇㅇ", "네",
        "Yes, go ahead",
        "APPROVE",
    ])
    def test_approve(self, response: str):
        choice, feedback, indices = parse_user_choice(response)
        assert choice == ConfirmationChoice.APPROVE
        assert feedback == ""
        assert indices == []

    # --- Reject ---
    @pytest.mark.parametrize("response", [
        "2", "no", "n", "reject", "discard", "cancel",
        "거절", "취소", "아니", "ㄴㄴ",
    ])
    def test_reject(self, response: str):
        choice, feedback, indices = parse_user_choice(response)
        assert choice == ConfirmationChoice.REJECT

    # --- Modify ---
    @pytest.mark.parametrize("response", [
        "3", "modify", "edit", "revise", "수정",
    ])
    def test_modify_keyword_only(self, response: str):
        choice, feedback, indices = parse_user_choice(response)
        assert choice == ConfirmationChoice.MODIFY

    def test_modify_with_feedback(self):
        choice, feedback, indices = parse_user_choice(
            "modify: add more detail about attention mechanism"
        )
        assert choice == ConfirmationChoice.MODIFY
        assert "attention mechanism" in feedback

    def test_modify_with_dash_feedback(self):
        choice, feedback, indices = parse_user_choice(
            "edit - please shorten the intro"
        )
        assert choice == ConfirmationChoice.MODIFY
        assert "shorten" in feedback

    def test_long_response_as_modify(self):
        long_feedback = "I think the section about transformers needs more detail about positional encoding"
        choice, feedback, indices = parse_user_choice(long_feedback)
        assert choice == ConfirmationChoice.MODIFY
        assert "positional encoding" in feedback

    # --- Partial ---
    def test_partial_keyword_with_indices(self):
        choice, feedback, indices = parse_user_choice(
            "partial 1,3", num_results=4
        )
        assert choice == ConfirmationChoice.APPROVE_PARTIAL
        assert indices == [0, 2]  # 0-based

    def test_partial_with_spaces(self):
        choice, feedback, indices = parse_user_choice(
            "select 1 3 5", num_results=5
        )
        assert choice == ConfirmationChoice.APPROVE_PARTIAL
        assert indices == [0, 2, 4]

    def test_number_list_as_partial(self):
        choice, feedback, indices = parse_user_choice(
            "1,3", num_results=4
        )
        assert choice == ConfirmationChoice.APPROVE_PARTIAL
        assert indices == [0, 2]

    def test_range_pattern(self):
        choice, feedback, indices = parse_user_choice(
            "partial 1-3", num_results=5
        )
        assert choice == ConfirmationChoice.APPROVE_PARTIAL
        assert indices == [0, 1, 2]

    def test_out_of_range_indices_filtered(self):
        choice, feedback, indices = parse_user_choice(
            "partial 1,10,3", num_results=4
        )
        assert choice == ConfirmationChoice.APPROVE_PARTIAL
        assert indices == [0, 2]  # 10 is out of range

    # --- Edge cases ---
    def test_empty_response(self):
        choice, feedback, indices = parse_user_choice("")
        assert choice == ConfirmationChoice.PENDING

    def test_ambiguous_short_response(self):
        choice, feedback, indices = parse_user_choice("hmm")
        assert choice == ConfirmationChoice.PENDING


# ---------------------------------------------------------------------------
# Confirmation state tests
# ---------------------------------------------------------------------------


class TestConfirmationState:
    def test_default_values(self):
        state = ConfirmationState()
        assert state.choice == ConfirmationChoice.PENDING
        assert not state.is_resolved
        assert not state.is_approved
        assert state.attempt == 1
        assert state.id.startswith("confirm-")

    def test_is_resolved(self):
        state = ConfirmationState(choice=ConfirmationChoice.APPROVE)
        assert state.is_resolved

    def test_is_approved(self):
        assert ConfirmationState(choice=ConfirmationChoice.APPROVE).is_approved
        assert ConfirmationState(choice=ConfirmationChoice.APPROVE_PARTIAL).is_approved
        assert not ConfirmationState(choice=ConfirmationChoice.REJECT).is_approved
        assert not ConfirmationState(choice=ConfirmationChoice.MODIFY).is_approved


# ---------------------------------------------------------------------------
# SQLite persistence tests
# ---------------------------------------------------------------------------


class TestConfirmationPersistence:
    def test_save_and_load(self, in_memory_db: sqlite3.Connection):
        state = ConfirmationState(
            results_json='[{"title":"Test"}]',
            attempt=1,
        )
        save_confirmation(state, in_memory_db)

        loaded = load_pending_confirmation(in_memory_db)
        assert loaded is not None
        assert loaded.id == state.id
        assert loaded.results_json == '[{"title":"Test"}]'
        assert loaded.choice == ConfirmationChoice.PENDING

    def test_resolve_confirmation(self, in_memory_db: sqlite3.Connection):
        state = ConfirmationState(results_json="[]")
        save_confirmation(state, in_memory_db)

        resolved = resolve_confirmation(
            state,
            ConfirmationChoice.APPROVE,
            in_memory_db,
        )
        assert resolved.choice == ConfirmationChoice.APPROVE
        assert resolved.resolved_at is not None

        # Should not appear as pending anymore
        pending = load_pending_confirmation(in_memory_db)
        assert pending is None

    def test_resolve_with_feedback(self, in_memory_db: sqlite3.Connection):
        state = ConfirmationState(results_json="[]")
        save_confirmation(state, in_memory_db)

        resolved = resolve_confirmation(
            state,
            ConfirmationChoice.MODIFY,
            in_memory_db,
            feedback="Add more details",
        )
        assert resolved.feedback == "Add more details"

    def test_resolve_with_indices(self, in_memory_db: sqlite3.Connection):
        state = ConfirmationState(results_json="[]")
        save_confirmation(state, in_memory_db)

        resolved = resolve_confirmation(
            state,
            ConfirmationChoice.APPROVE_PARTIAL,
            in_memory_db,
            approved_indices=[0, 2],
        )
        assert resolved.approved_indices == [0, 2]

    def test_no_pending(self, in_memory_db: sqlite3.Connection):
        result = load_pending_confirmation(in_memory_db)
        assert result is None


# ---------------------------------------------------------------------------
# JSON prompt tests
# ---------------------------------------------------------------------------


class TestConfirmationPromptJson:
    def test_structure(
        self, sample_result: CompileResult, update_result: CompileResult
    ):
        output = confirmation_prompt_json([sample_result, update_result])
        data = json.loads(output)

        assert data["status"] == "confirm"
        assert data["actionable_count"] == 2
        assert data["creates"] == 1
        assert data["updates"] == 1
        assert len(data["pages"]) == 2
        assert len(data["choices"]) == 4  # includes partial for multi

    def test_single_result_no_partial(self, sample_result: CompileResult):
        output = confirmation_prompt_json([sample_result])
        data = json.loads(output)

        assert len(data["choices"]) == 3  # no partial

    def test_skip_not_in_pages(self, skip_result: CompileResult):
        output = confirmation_prompt_json([skip_result])
        data = json.loads(output)

        assert data["actionable_count"] == 0
        assert len(data["pages"]) == 0

    def test_page_fields(self, sample_result: CompileResult):
        output = confirmation_prompt_json([sample_result])
        data = json.loads(output)
        page = data["pages"][0]

        assert page["title"] == "Transformer Architecture"
        assert page["action"] == "create"
        assert page["lens"] == "AI Research"
        assert "ai" in page["tags"]


# ---------------------------------------------------------------------------
# Serialization roundtrip tests
# ---------------------------------------------------------------------------


class TestResultSerialization:
    def test_roundtrip(self, sample_result: CompileResult):
        serialized = serialize_results([sample_result])
        deserialized = deserialize_results(serialized)

        assert len(deserialized) == 1
        r = deserialized[0]
        assert r.title == "Transformer Architecture"
        assert r.action == CompileAction.CREATE
        assert r.lens_id == "ai"
        assert r.tags == ["ai", "transformers"]
        assert len(r.sources) == 1
        assert r.sources[0].title == "Transformers Explained"
        assert r.sources[0].score == 0.9

    def test_roundtrip_multiple(
        self,
        sample_result: CompileResult,
        update_result: CompileResult,
        skip_result: CompileResult,
    ):
        results = [sample_result, update_result, skip_result]
        serialized = serialize_results(results)
        deserialized = deserialize_results(serialized)

        assert len(deserialized) == 3
        assert deserialized[0].title == "Transformer Architecture"
        assert deserialized[1].title == "Prompt Engineering"
        assert deserialized[2].title == "Neural Networks"

    def test_empty_roundtrip(self):
        serialized = serialize_results([])
        deserialized = deserialize_results(serialized)
        assert deserialized == []

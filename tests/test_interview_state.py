"""
Tests for llm_wiki.interview_state — conversation state machine.

Tests cover:
- InterviewPhase enum ordering and navigation
- QuestionDef registry and lookups
- InterviewState tracking and serialization
- InterviewStateMachine transitions (answer, skip, next, back, restart, complete)
- Auto-advance when all required questions are answered
- Error cases (skip required, back at first phase, etc.)
- TransitionEvent unified handler
"""

from __future__ import annotations

import pytest

from llm_wiki.interview_state import (
    InterviewPhase,
    InterviewState,
    InterviewStateMachine,
    QuestionDef,
    TransitionEvent,
    TransitionResult,
    PHASE_QUESTIONS,
    get_all_keys,
    get_question,
    get_required_keys,
)


# ────────────────────────────────────────────────────────────────────────────
# InterviewPhase enum
# ────────────────────────────────────────────────────────────────────────────


class TestInterviewPhase:
    def test_ordered_excludes_complete(self):
        ordered = InterviewPhase.ordered()
        assert InterviewPhase.COMPLETE not in ordered
        assert len(ordered) == 4

    def test_ordered_sequence(self):
        ordered = InterviewPhase.ordered()
        assert ordered == [
            InterviewPhase.INTERESTS,
            InterviewPhase.SOURCES,
            InterviewPhase.OUTPUT,
            InterviewPhase.VAULT,
        ]

    def test_next_phase(self):
        assert InterviewPhase.INTERESTS.next_phase() == InterviewPhase.SOURCES
        assert InterviewPhase.SOURCES.next_phase() == InterviewPhase.OUTPUT
        assert InterviewPhase.OUTPUT.next_phase() == InterviewPhase.VAULT
        assert InterviewPhase.VAULT.next_phase() == InterviewPhase.COMPLETE
        assert InterviewPhase.COMPLETE.next_phase() == InterviewPhase.COMPLETE

    def test_prev_phase(self):
        assert InterviewPhase.INTERESTS.prev_phase() is None
        assert InterviewPhase.SOURCES.prev_phase() == InterviewPhase.INTERESTS
        assert InterviewPhase.OUTPUT.prev_phase() == InterviewPhase.SOURCES
        assert InterviewPhase.VAULT.prev_phase() == InterviewPhase.OUTPUT
        assert InterviewPhase.COMPLETE.prev_phase() is None

    def test_is_terminal(self):
        assert InterviewPhase.COMPLETE.is_terminal is True
        assert InterviewPhase.INTERESTS.is_terminal is False

    def test_string_value(self):
        assert InterviewPhase.INTERESTS.value == "interests"
        assert str(InterviewPhase.INTERESTS) == "InterviewPhase.INTERESTS"


# ────────────────────────────────────────────────────────────────────────────
# Question registry
# ────────────────────────────────────────────────────────────────────────────


class TestQuestionRegistry:
    def test_all_phases_have_questions(self):
        for phase in InterviewPhase.ordered():
            questions = PHASE_QUESTIONS[phase]
            assert len(questions) > 0, f"Phase {phase.value} has no questions"

    def test_get_required_keys(self):
        keys = get_required_keys(InterviewPhase.INTERESTS)
        assert "interests" in keys
        assert "interest_depth" in keys

    def test_get_all_keys(self):
        keys = get_all_keys(InterviewPhase.SOURCES)
        assert "source_types" in keys
        assert "initial_sources" in keys

    def test_get_question_found(self):
        q = get_question("interests")
        assert q is not None
        assert q.phase == InterviewPhase.INTERESTS
        assert q.required is True

    def test_get_question_not_found(self):
        assert get_question("nonexistent") is None

    def test_no_duplicate_keys(self):
        all_keys: list[str] = []
        for questions in PHASE_QUESTIONS.values():
            for q in questions:
                all_keys.append(q.key)
        assert len(all_keys) == len(set(all_keys)), "Duplicate question keys found"

    def test_questions_reference_correct_phase(self):
        for phase, questions in PHASE_QUESTIONS.items():
            for q in questions:
                assert q.phase == phase, (
                    f"Question '{q.key}' has phase {q.phase} "
                    f"but is registered under {phase}"
                )


# ────────────────────────────────────────────────────────────────────────────
# InterviewState
# ────────────────────────────────────────────────────────────────────────────


class TestInterviewState:
    def test_default_state(self):
        state = InterviewState()
        assert state.phase == InterviewPhase.INTERESTS
        assert state.answered_keys == set()
        assert state.is_complete is False

    def test_phase_progress_empty(self):
        state = InterviewState()
        answered, total = state.phase_progress()
        assert answered == 0
        assert total == len(get_required_keys(InterviewPhase.INTERESTS))

    def test_phase_progress_partial(self):
        state = InterviewState(answered_keys={"interests"})
        answered, total = state.phase_progress()
        assert answered == 1

    def test_phase_is_satisfied(self):
        required = get_required_keys(InterviewPhase.INTERESTS)
        state = InterviewState(answered_keys=required)
        assert state.phase_is_satisfied() is True

    def test_phase_not_satisfied(self):
        state = InterviewState(answered_keys={"interests"})
        # interests phase requires both "interests" and "interest_depth"
        assert state.phase_is_satisfied() is False

    def test_pending_questions(self):
        state = InterviewState(answered_keys={"interests"})
        pending = state.pending_questions()
        keys = [q.key for q in pending]
        assert "interest_depth" in keys
        assert "interests" not in keys

    def test_next_question(self):
        state = InterviewState()
        q = state.next_question()
        assert q is not None
        assert q.key == "interests"  # first question in interests phase

    def test_next_question_none_when_satisfied(self):
        required = get_required_keys(InterviewPhase.INTERESTS)
        state = InterviewState(answered_keys=required)
        assert state.next_question() is None

    def test_overall_progress(self):
        state = InterviewState()
        answered, total = state.overall_progress()
        assert answered == 0
        assert total > 0  # sum of all required questions

    def test_serialization_roundtrip(self):
        state = InterviewState(
            phase=InterviewPhase.SOURCES,
            answered_keys={"interests", "interest_depth"},
            skipped_keys={"optional_q"},
            dynamic_keys={"followup_1"},
        )
        data = state.to_dict()
        restored = InterviewState.from_dict(data)

        assert restored.phase == state.phase
        assert restored.answered_keys == state.answered_keys
        assert restored.skipped_keys == state.skipped_keys
        assert restored.dynamic_keys == state.dynamic_keys

    def test_serialization_defaults(self):
        state = InterviewState.from_dict({})
        assert state.phase == InterviewPhase.INTERESTS
        assert state.answered_keys == set()


# ────────────────────────────────────────────────────────────────────────────
# InterviewStateMachine — basic operations
# ────────────────────────────────────────────────────────────────────────────


class TestStateMachineBasic:
    def test_initial_state(self):
        machine = InterviewStateMachine()
        assert machine.state.phase == InterviewPhase.INTERESTS
        assert machine.state.is_complete is False

    def test_current_question(self):
        machine = InterviewStateMachine()
        q = machine.current_question()
        assert q is not None
        assert q.key == "interests"

    def test_current_phase_questions(self):
        machine = InterviewStateMachine()
        questions = machine.current_phase_questions()
        assert len(questions) == len(PHASE_QUESTIONS[InterviewPhase.INTERESTS])

    def test_custom_initial_state(self):
        state = InterviewState(
            phase=InterviewPhase.SOURCES,
            answered_keys={"interests", "interest_depth"},
        )
        machine = InterviewStateMachine(state)
        assert machine.state.phase == InterviewPhase.SOURCES


# ────────────────────────────────────────────────────────────────────────────
# InterviewStateMachine — answer flow
# ────────────────────────────────────────────────────────────────────────────


class TestStateMachineAnswer:
    def test_answer_records_key(self):
        machine = InterviewStateMachine()
        machine.answer("interests", "AI and ML")
        assert "interests" in machine.state.answered_keys

    def test_answer_auto_advance_on_phase_complete(self):
        machine = InterviewStateMachine()
        machine.answer("interests", "AI")
        result = machine.answer("interest_depth", "deep")

        assert result.changed is True
        assert result.previous_phase == InterviewPhase.INTERESTS
        assert result.new_phase == InterviewPhase.SOURCES

    def test_answer_no_advance_when_incomplete(self):
        machine = InterviewStateMachine()
        result = machine.answer("interests", "AI")

        assert result.changed is False
        assert result.new_phase == InterviewPhase.INTERESTS
        assert result.next_question is not None
        assert result.next_question.key == "interest_depth"

    def test_full_interview_flow(self):
        """Walk through the entire interview and verify completion."""
        machine = InterviewStateMachine()

        # Phase 1: Interests
        machine.answer("interests", "AI and ML")
        result = machine.answer("interest_depth", "deep")
        assert machine.state.phase == InterviewPhase.SOURCES

        # Phase 2: Sources
        machine.answer("source_types", ["RSS", "YouTube"])
        result = machine.answer("initial_sources", "https://example.com/feed")
        assert machine.state.phase == InterviewPhase.OUTPUT

        # Phase 3: Output
        machine.answer("compile_strategy", "merge")
        result = machine.answer("wiki_language", "English")
        assert machine.state.phase == InterviewPhase.VAULT

        # Phase 4: Vault
        machine.answer("vault_path", "~/wiki")
        machine.answer("existing_vault", "Fresh/empty vault")
        result = machine.answer("schedule_preference", "Every 30 minutes")
        assert machine.state.phase == InterviewPhase.COMPLETE
        assert machine.state.is_complete is True
        assert machine.current_question() is None

    def test_answer_returns_message(self):
        machine = InterviewStateMachine()
        result = machine.answer("interests", "AI")
        assert isinstance(result.message, str)
        assert len(result.message) > 0


# ────────────────────────────────────────────────────────────────────────────
# InterviewStateMachine — navigation
# ────────────────────────────────────────────────────────────────────────────


class TestStateMachineNavigation:
    def _at_sources(self) -> InterviewStateMachine:
        """Helper: create a machine at the sources phase."""
        state = InterviewState(
            phase=InterviewPhase.SOURCES,
            answered_keys={"interests", "interest_depth"},
        )
        return InterviewStateMachine(state)

    def test_go_back(self):
        machine = self._at_sources()
        result = machine.go_back()
        assert result.changed is True
        assert result.new_phase == InterviewPhase.INTERESTS

    def test_go_back_at_first_raises(self):
        machine = InterviewStateMachine()
        with pytest.raises(ValueError, match="cannot go back"):
            machine.go_back()

    def test_next_phase_explicit(self):
        # Answer all interests questions first
        machine = InterviewStateMachine()
        machine.answer("interests", "AI")
        machine.answer("interest_depth", "deep")
        # Already auto-advanced to SOURCES
        assert machine.state.phase == InterviewPhase.SOURCES

    def test_next_phase_with_unanswered_raises(self):
        machine = InterviewStateMachine()
        # Only answered one of two required questions
        machine.state.answered_keys.add("interests")
        machine.state.phase = InterviewPhase.INTERESTS  # prevent auto-advance
        with pytest.raises(ValueError, match="required questions unanswered"):
            machine.next_phase()

    def test_next_phase_when_complete(self):
        state = InterviewState(phase=InterviewPhase.COMPLETE)
        machine = InterviewStateMachine(state)
        result = machine.next_phase()
        assert result.changed is False
        assert result.message == "Interview is already complete."

    def test_restart(self):
        machine = self._at_sources()
        result = machine.restart()
        assert result.new_phase == InterviewPhase.INTERESTS
        assert machine.state.answered_keys == set()
        assert result.changed is True

    def test_force_complete(self):
        machine = InterviewStateMachine()
        result = machine.force_complete()
        assert result.new_phase == InterviewPhase.COMPLETE
        assert machine.state.is_complete is True
        assert result.changed is True


# ────────────────────────────────────────────────────────────────────────────
# InterviewStateMachine — skip
# ────────────────────────────────────────────────────────────────────────────


class TestStateMachineSkip:
    def test_skip_required_raises(self):
        machine = InterviewStateMachine()
        with pytest.raises(ValueError, match="Cannot skip required"):
            machine.skip("interests")

    def test_skip_unknown_key_no_error(self):
        """Skipping a key not in the registry should work (for dynamic questions)."""
        machine = InterviewStateMachine()
        result = machine.skip("dynamic_followup_1")
        assert "dynamic_followup_1" in machine.state.skipped_keys


# ────────────────────────────────────────────────────────────────────────────
# TransitionEvent handler
# ────────────────────────────────────────────────────────────────────────────


class TestTransitionEvent:
    def test_answer_event(self):
        machine = InterviewStateMachine()
        result = machine.handle_event(TransitionEvent.ANSWER, key="interests", value="AI")
        assert "interests" in machine.state.answered_keys

    def test_skip_event(self):
        machine = InterviewStateMachine()
        result = machine.handle_event(TransitionEvent.SKIP, key="dynamic_q")
        assert "dynamic_q" in machine.state.skipped_keys

    def test_next_event(self):
        state = InterviewState(
            phase=InterviewPhase.INTERESTS,
            answered_keys=get_required_keys(InterviewPhase.INTERESTS),
        )
        machine = InterviewStateMachine(state)
        result = machine.handle_event(TransitionEvent.NEXT)
        assert result.new_phase == InterviewPhase.SOURCES

    def test_back_event(self):
        state = InterviewState(
            phase=InterviewPhase.SOURCES,
            answered_keys={"interests", "interest_depth"},
        )
        machine = InterviewStateMachine(state)
        result = machine.handle_event(TransitionEvent.BACK)
        assert result.new_phase == InterviewPhase.INTERESTS

    def test_restart_event(self):
        machine = InterviewStateMachine()
        machine.answer("interests", "AI")
        result = machine.handle_event(TransitionEvent.RESTART)
        assert machine.state.answered_keys == set()

    def test_complete_event(self):
        machine = InterviewStateMachine()
        result = machine.handle_event(TransitionEvent.COMPLETE)
        assert machine.state.is_complete is True

    def test_answer_event_requires_key(self):
        machine = InterviewStateMachine()
        with pytest.raises(ValueError, match="requires a question key"):
            machine.handle_event(TransitionEvent.ANSWER)

    def test_skip_event_requires_key(self):
        machine = InterviewStateMachine()
        with pytest.raises(ValueError, match="requires a question key"):
            machine.handle_event(TransitionEvent.SKIP)


# ────────────────────────────────────────────────────────────────────────────
# Dynamic questions
# ────────────────────────────────────────────────────────────────────────────


class TestDynamicQuestions:
    def test_register_dynamic_question(self):
        machine = InterviewStateMachine()
        machine.register_dynamic_question("followup_ai_depth")
        assert "followup_ai_depth" in machine.state.dynamic_keys

    def test_dynamic_questions_dont_block_advance(self):
        """Dynamic questions are optional — they shouldn't prevent phase completion."""
        machine = InterviewStateMachine()
        machine.register_dynamic_question("followup_1")
        machine.answer("interests", "AI")
        result = machine.answer("interest_depth", "deep")
        # Should auto-advance even though dynamic question is unanswered
        assert result.new_phase == InterviewPhase.SOURCES

    def test_dynamic_question_can_be_answered(self):
        machine = InterviewStateMachine()
        machine.register_dynamic_question("followup_1")
        machine.answer("followup_1", "some answer")
        assert "followup_1" in machine.state.answered_keys


# ────────────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_answer_same_key_twice(self):
        """Answering the same key again should be idempotent for state."""
        machine = InterviewStateMachine()
        machine.answer("interests", "AI")
        machine.answer("interests", "AI and ML")  # re-answer
        assert "interests" in machine.state.answered_keys

    def test_answer_removes_from_skipped(self):
        """If a key was skipped then answered, it should no longer be skipped."""
        machine = InterviewStateMachine()
        machine.state.skipped_keys.add("dynamic_q")
        machine.answer("dynamic_q", "value")
        assert "dynamic_q" not in machine.state.skipped_keys
        assert "dynamic_q" in machine.state.answered_keys

    def test_overall_progress_increases(self):
        machine = InterviewStateMachine()
        before = machine.state.overall_progress()
        machine.answer("interests", "AI")
        after = machine.state.overall_progress()
        assert after[0] > before[0]
        assert after[1] == before[1]  # total unchanged

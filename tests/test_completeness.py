"""
Tests for llm_wiki.completeness — interview completeness checker.

Tests cover:
- Rule-based completeness checking across all three dimensions
- Gap detection (blocking, degraded, cosmetic)
- Content quality heuristics (vague interests, actionable sources, path validation)
- Configuration viability checks
- Overall readiness determination
- should_end_interview convenience function
- LLM-assisted checking with mocked API
- Edge cases (empty answers, partial answers, all answered)
- State inference from answers dict
- Gap deduplication
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.completeness import (
    CompletenessResult,
    DimensionScore,
    Gap,
    GapSeverity,
    Readiness,
    check_completeness,
    check_completeness_llm,
    should_end_interview,
    _check_content_quality,
    _check_structural,
    _check_viability,
    _count_segments,
    _has_actionable_source,
    _is_empty,
    _is_vague_interest,
    _looks_like_path,
    _infer_state,
    _deduplicate_gaps,
    _merge_results,
    READY_THRESHOLD,
    ACCEPTABLE_THRESHOLD,
)
from llm_wiki.interview_state import InterviewPhase, InterviewState


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def complete_answers() -> dict[str, object]:
    """A fully answered interview with high-quality answers."""
    return {
        "interests": "AI/ML research, Rust ecosystem, startup strategy",
        "interest_depth": "AI: deep technical papers, Rust: moderate, Startups: surface news",
        "source_types": ["RSS feeds", "YouTube channels", "Twitter/X accounts"],
        "initial_sources": (
            "https://simonwillison.net/atom/everything/\n"
            "YouTube: @3Blue1Brown\n"
            "Twitter: @karpathy"
        ),
        "compile_strategy": "merge",
        "wiki_language": "English",
        "vault_path": "~/Documents/MyWiki",
        "existing_vault": "Fresh/empty vault",
        "schedule_preference": "Every 30 minutes",
    }


@pytest.fixture
def minimal_answers() -> dict[str, object]:
    """Minimum viable answers — just enough to generate a config."""
    return {
        "interests": "AI research",
        "interest_depth": "deep",
        "source_types": ["RSS feeds"],
        "initial_sources": "https://example.com/feed",
        "compile_strategy": "merge",
        "wiki_language": "English",
        "vault_path": "~/wiki",
        "existing_vault": "Fresh/empty vault",
        "schedule_preference": "Daily",
    }


@pytest.fixture
def empty_answers() -> dict[str, object]:
    """Empty/missing answers."""
    return {}


@pytest.fixture
def partial_answers() -> dict[str, object]:
    """Only interests phase answered."""
    return {
        "interests": "AI and machine learning",
        "interest_depth": "deep technical analysis",
    }


# ────────────────────────────────────────────────────────────────────────────
# Helper function tests
# ────────────────────────────────────────────────────────────────────────────


class TestIsEmpty:
    def test_none(self):
        assert _is_empty(None) is True

    def test_empty_string(self):
        assert _is_empty("") is True

    def test_whitespace_string(self):
        assert _is_empty("   ") is True

    def test_non_empty_string(self):
        assert _is_empty("hello") is False

    def test_empty_list(self):
        assert _is_empty([]) is True

    def test_non_empty_list(self):
        assert _is_empty(["a"]) is False

    def test_bool_false_not_empty(self):
        assert _is_empty(False) is False

    def test_bool_true_not_empty(self):
        assert _is_empty(True) is False

    def test_zero_not_empty(self):
        assert _is_empty(0) is False


class TestCountSegments:
    def test_comma_separated(self):
        assert _count_segments("AI, frontend, startups") == 3

    def test_newline_separated(self):
        assert _count_segments("AI\nfrontend\nstartups") == 3

    def test_semicolons(self):
        assert _count_segments("AI; frontend; startups") == 3

    def test_single_item(self):
        assert _count_segments("AI research") == 1

    def test_empty(self):
        assert _count_segments("") == 0

    def test_single_char_filtered(self):
        assert _count_segments("AI, , x") == 1  # "x" is only 1 char


class TestIsVagueInterest:
    def test_everything(self):
        assert _is_vague_interest("everything") is True

    def test_anything(self):
        assert _is_vague_interest("Anything") is True

    def test_specific(self):
        assert _is_vague_interest("AI research and machine learning") is False

    def test_all(self):
        assert _is_vague_interest("all") is True

    def test_empty(self):
        assert _is_vague_interest("") is False


class TestHasActionableSource:
    def test_url(self):
        assert _has_actionable_source("https://example.com/feed") is True

    def test_at_handle(self):
        assert _has_actionable_source("@karpathy") is True

    def test_youtube_reference(self):
        assert _has_actionable_source("YouTube: 3Blue1Brown") is True

    def test_twitter_reference(self):
        assert _has_actionable_source("Twitter: elonmusk") is True

    def test_domain(self):
        assert _has_actionable_source("simonwillison.net") is True

    def test_vague_text(self):
        assert _has_actionable_source("some tech blogs") is False

    def test_channel_reference(self):
        assert _has_actionable_source("channel: myFavorite") is True


class TestLooksLikePath:
    def test_home_tilde(self):
        assert _looks_like_path("~/Documents/wiki") is True

    def test_absolute_unix(self):
        assert _looks_like_path("/home/user/wiki") is True

    def test_relative_dot(self):
        assert _looks_like_path("./wiki") is True

    def test_relative_dotdot(self):
        assert _looks_like_path("../wiki") is True

    def test_windows_drive(self):
        assert _looks_like_path("C:\\Users\\wiki") is True

    def test_not_a_path(self):
        assert _looks_like_path("my wiki") is False

    def test_empty(self):
        assert _looks_like_path("") is False


# ────────────────────────────────────────────────────────────────────────────
# Dimension 1: Structural completeness
# ────────────────────────────────────────────────────────────────────────────


class TestStructuralCheck:
    def test_all_answered(self, complete_answers):
        state = _infer_state(complete_answers)
        score, gaps = _check_structural(complete_answers, state)
        assert score.score == 1.0
        assert len(gaps) == 0

    def test_empty_answers(self, empty_answers):
        state = InterviewState()
        score, gaps = _check_structural(empty_answers, state)
        assert score.score == 0.0
        assert len(gaps) > 0
        assert all(g.severity == GapSeverity.BLOCKING for g in gaps)

    def test_partial_answers(self, partial_answers):
        state = _infer_state(partial_answers)
        score, gaps = _check_structural(partial_answers, state)
        assert 0.0 < score.score < 1.0
        blocking_keys = {g.key for g in gaps}
        assert "source_types" in blocking_keys
        assert "vault_path" in blocking_keys


# ────────────────────────────────────────────────────────────────────────────
# Dimension 2: Content quality
# ────────────────────────────────────────────────────────────────────────────


class TestContentQualityCheck:
    def test_high_quality(self, complete_answers):
        score, gaps = _check_content_quality(complete_answers)
        assert score.score >= 0.8
        assert not any(g.severity == GapSeverity.BLOCKING for g in gaps)

    def test_vague_interests(self):
        answers = {
            "interests": "everything",
            "interest_depth": "moderate",
            "source_types": ["RSS feeds"],
            "initial_sources": "https://example.com",
            "vault_path": "~/wiki",
        }
        score, gaps = _check_content_quality(answers)
        degraded_keys = [g.key for g in gaps if g.severity == GapSeverity.DEGRADED]
        assert "interests" in degraded_keys

    def test_no_actionable_sources(self):
        answers = {
            "interests": "AI, frontend",
            "interest_depth": "deep",
            "source_types": ["RSS feeds"],
            "initial_sources": "some tech blogs about stuff",
            "vault_path": "~/wiki",
        }
        score, gaps = _check_content_quality(answers)
        degraded_keys = [g.key for g in gaps if g.severity == GapSeverity.DEGRADED]
        assert "initial_sources" in degraded_keys

    def test_invalid_vault_path(self):
        answers = {
            "interests": "AI, frontend",
            "interest_depth": "deep",
            "source_types": ["RSS feeds"],
            "initial_sources": "https://example.com",
            "vault_path": "my wiki folder",
        }
        score, gaps = _check_content_quality(answers)
        degraded_keys = [g.key for g in gaps if g.severity == GapSeverity.DEGRADED]
        assert "vault_path" in degraded_keys

    def test_empty_answers_no_crash(self, empty_answers):
        score, gaps = _check_content_quality(empty_answers)
        assert score.score is not None  # should not crash


# ────────────────────────────────────────────────────────────────────────────
# Dimension 3: Viability
# ────────────────────────────────────────────────────────────────────────────


class TestViabilityCheck:
    def test_viable(self, complete_answers):
        score, gaps = _check_viability(complete_answers)
        assert score.score == 1.0
        assert len(gaps) == 0

    def test_no_interests(self):
        answers = {
            "source_types": ["RSS feeds"],
            "vault_path": "~/wiki",
        }
        score, gaps = _check_viability(answers)
        blocking_keys = [g.key for g in gaps if g.severity == GapSeverity.BLOCKING]
        assert "interests" in blocking_keys

    def test_no_source_types(self):
        answers = {
            "interests": "AI research",
            "vault_path": "~/wiki",
        }
        score, gaps = _check_viability(answers)
        blocking_keys = [g.key for g in gaps if g.severity == GapSeverity.BLOCKING]
        assert "source_types" in blocking_keys

    def test_no_vault_path(self):
        answers = {
            "interests": "AI research",
            "source_types": ["RSS feeds"],
        }
        score, gaps = _check_viability(answers)
        blocking_keys = [g.key for g in gaps if g.severity == GapSeverity.BLOCKING]
        assert "vault_path" in blocking_keys

    def test_vague_interests_not_viable(self):
        answers = {
            "interests": "everything",
            "source_types": ["RSS feeds"],
            "vault_path": "~/wiki",
        }
        score, gaps = _check_viability(answers)
        blocking_keys = [g.key for g in gaps if g.severity == GapSeverity.BLOCKING]
        assert "interests" in blocking_keys

    def test_list_interests_viable(self):
        answers: dict[str, object] = {
            "interests": ["AI", "Frontend"],
            "source_types": ["RSS feeds"],
            "vault_path": "~/wiki",
        }
        score, gaps = _check_viability(answers)
        assert score.score == 1.0


# ────────────────────────────────────────────────────────────────────────────
# Full check_completeness
# ────────────────────────────────────────────────────────────────────────────


class TestCheckCompleteness:
    def test_complete_answers_ready(self, complete_answers):
        result = check_completeness(complete_answers)
        assert result.readiness == "ready"
        assert result.can_generate_config is True
        assert result.overall_score >= READY_THRESHOLD
        assert len(result.blocking_gaps) == 0

    def test_minimal_answers_acceptable_or_ready(self, minimal_answers):
        result = check_completeness(minimal_answers)
        assert result.readiness in ("ready", "acceptable")
        assert result.can_generate_config is True

    def test_empty_answers_insufficient(self, empty_answers):
        result = check_completeness(empty_answers)
        assert result.readiness == "insufficient"
        assert result.can_generate_config is False
        assert len(result.blocking_gaps) > 0

    def test_partial_answers_insufficient(self, partial_answers):
        result = check_completeness(partial_answers)
        assert result.readiness == "insufficient"
        assert result.can_generate_config is False

    def test_result_has_three_dimensions(self, complete_answers):
        result = check_completeness(complete_answers)
        assert len(result.dimensions) == 3
        dims = {d.dimension for d in result.dimensions}
        assert dims == {"structural", "content_quality", "viability"}

    def test_summary_is_nonempty(self, complete_answers):
        result = check_completeness(complete_answers)
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0

    def test_with_explicit_state(self, complete_answers):
        state = InterviewState(
            phase=InterviewPhase.COMPLETE,
            answered_keys=set(complete_answers.keys()),
        )
        result = check_completeness(complete_answers, state)
        assert result.readiness == "ready"

    def test_gaps_are_deduplicated(self):
        """The same key+severity should appear only once."""
        answers: dict[str, object] = {
            "interests": "everything",  # vague → appears in quality AND viability
            "interest_depth": "yes",
            "source_types": ["RSS feeds"],
            "initial_sources": "https://example.com",
            "compile_strategy": "merge",
            "wiki_language": "English",
            "vault_path": "~/wiki",
            "existing_vault": "Fresh",
            "schedule_preference": "Daily",
        }
        result = check_completeness(answers)
        # Count gaps per key+severity
        seen = set()
        for g in result.gaps:
            ident = (g.key, g.severity.value)
            assert ident not in seen, f"Duplicate gap: {g.key} / {g.severity}"
            seen.add(ident)


# ────────────────────────────────────────────────────────────────────────────
# should_end_interview
# ────────────────────────────────────────────────────────────────────────────


class TestShouldEndInterview:
    def test_complete_answers_ends(self, complete_answers):
        should_end, result = should_end_interview(complete_answers)
        assert should_end is True
        assert result.can_generate_config is True

    def test_empty_answers_does_not_end(self, empty_answers):
        should_end, result = should_end_interview(empty_answers)
        assert should_end is False

    def test_require_ready_stricter(self, minimal_answers):
        # With require_ready=False, minimal answers should pass
        should_end_lenient, _ = should_end_interview(minimal_answers, require_ready=False)

        # With require_ready=True, might not pass
        should_end_strict, result = should_end_interview(minimal_answers, require_ready=True)

        # Lenient should be at least as permissive as strict
        if should_end_strict:
            assert should_end_lenient is True

    def test_returns_result(self, complete_answers):
        should_end, result = should_end_interview(complete_answers)
        assert isinstance(result, CompletenessResult)
        assert result.readiness in ("ready", "acceptable", "insufficient")


# ────────────────────────────────────────────────────────────────────────────
# State inference
# ────────────────────────────────────────────────────────────────────────────


class TestInferState:
    def test_empty_answers(self):
        state = _infer_state({})
        assert state.phase == InterviewPhase.INTERESTS

    def test_interests_complete(self):
        answers = {
            "interests": "AI",
            "interest_depth": "deep",
        }
        state = _infer_state(answers)
        assert state.phase == InterviewPhase.SOURCES

    def test_all_complete(self, complete_answers):
        state = _infer_state(complete_answers)
        assert state.phase == InterviewPhase.COMPLETE

    def test_answered_keys_populated(self, partial_answers):
        state = _infer_state(partial_answers)
        assert "interests" in state.answered_keys
        assert "interest_depth" in state.answered_keys


# ────────────────────────────────────────────────────────────────────────────
# Gap deduplication
# ────────────────────────────────────────────────────────────────────────────


class TestDeduplication:
    def test_removes_duplicates(self):
        gaps = [
            Gap(key="interests", severity=GapSeverity.BLOCKING, message="A"),
            Gap(key="interests", severity=GapSeverity.BLOCKING, message="B"),
        ]
        result = _deduplicate_gaps(gaps)
        assert len(result) == 1
        assert result[0].message == "A"  # keeps first

    def test_keeps_different_severities(self):
        gaps = [
            Gap(key="interests", severity=GapSeverity.BLOCKING, message="A"),
            Gap(key="interests", severity=GapSeverity.DEGRADED, message="B"),
        ]
        result = _deduplicate_gaps(gaps)
        assert len(result) == 2

    def test_keeps_different_keys(self):
        gaps = [
            Gap(key="interests", severity=GapSeverity.BLOCKING, message="A"),
            Gap(key="vault_path", severity=GapSeverity.BLOCKING, message="B"),
        ]
        result = _deduplicate_gaps(gaps)
        assert len(result) == 2


# ────────────────────────────────────────────────────────────────────────────
# DimensionScore
# ────────────────────────────────────────────────────────────────────────────


class TestDimensionScore:
    def test_normalized_full(self):
        score = DimensionScore(dimension="structural", score=1.0)
        assert score.normalized == 100.0

    def test_normalized_half(self):
        score = DimensionScore(dimension="structural", score=0.5)
        assert score.normalized == 50.0

    def test_normalized_zero_max(self):
        score = DimensionScore(dimension="structural", score=0.0, max_score=0.0)
        assert score.normalized == 0.0


# ────────────────────────────────────────────────────────────────────────────
# CompletenessResult properties
# ────────────────────────────────────────────────────────────────────────────


class TestCompletenessResult:
    def test_blocking_gaps_filter(self):
        result = CompletenessResult(
            readiness="insufficient",
            overall_score=0.3,
            dimensions=[],
            gaps=[
                Gap(key="a", severity=GapSeverity.BLOCKING, message="block"),
                Gap(key="b", severity=GapSeverity.DEGRADED, message="degrade"),
                Gap(key="c", severity=GapSeverity.COSMETIC, message="cosmetic"),
            ],
            summary="test",
            can_generate_config=False,
        )
        assert len(result.blocking_gaps) == 1
        assert len(result.degraded_gaps) == 1
        assert result.gap_count == 3


# ────────────────────────────────────────────────────────────────────────────
# LLM-assisted completeness (mocked)
# ────────────────────────────────────────────────────────────────────────────


class TestCheckCompletenessLLM:
    @pytest.fixture
    def llm_response_data(self):
        return {
            "overall_quality": 0.85,
            "interest_clarity": 0.9,
            "source_actionability": 0.8,
            "config_readiness": 0.85,
            "gaps": [
                {
                    "key": "initial_sources",
                    "severity": "degraded",
                    "message": "Sources could be more diverse",
                    "suggestion": "Add sources from different platforms",
                }
            ],
            "recommendation": "ready",
            "reasoning": "Good coverage of interests and sources",
        }

    @pytest.mark.asyncio
    async def test_llm_enhances_rule_result(self, complete_answers, llm_response_data):
        text_block = MagicMock()
        text_block.text = json.dumps(llm_response_data)
        message = MagicMock()
        message.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = message

        with patch("llm_wiki.completeness.anthropic", create=True), \
             patch("llm_wiki.completeness.os.environ.get", return_value="fake-key"), \
             patch("llm_wiki.completeness._call_llm_completeness", return_value=llm_response_data):
            result = await check_completeness_llm(complete_answers)

        assert result.readiness == "ready"
        assert result.can_generate_config is True

    @pytest.mark.asyncio
    async def test_llm_fallback_on_error(self, complete_answers):
        with patch("llm_wiki.completeness._call_llm_completeness", side_effect=RuntimeError("network error")):
            result = await check_completeness_llm(complete_answers)

        # Should fall back to rule-based
        assert result.readiness == "ready"
        assert result.can_generate_config is True

    @pytest.mark.asyncio
    async def test_llm_skips_when_structurally_insufficient(self, empty_answers):
        # LLM should not be called when structural checks fail
        with patch("llm_wiki.completeness._call_llm_completeness") as mock_llm:
            result = await check_completeness_llm(empty_answers)

        mock_llm.assert_not_called()
        assert result.readiness == "insufficient"

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self, complete_answers):
        with patch("llm_wiki.completeness._call_llm_completeness",
                    side_effect=EnvironmentError("ANTHROPIC_API_KEY required")):
            with pytest.raises(EnvironmentError):
                await check_completeness_llm(complete_answers)


# ────────────────────────────────────────────────────────────────────────────
# Merge results
# ────────────────────────────────────────────────────────────────────────────


class TestMergeResults:
    def test_empty_llm_data_returns_rule(self, complete_answers):
        rule_result = check_completeness(complete_answers)
        merged = _merge_results(rule_result, {})
        assert merged.readiness == rule_result.readiness
        assert merged.overall_score == rule_result.overall_score

    def test_llm_can_lower_readiness(self):
        rule_result = CompletenessResult(
            readiness="ready",
            overall_score=0.9,
            dimensions=[],
            gaps=[],
            summary="All good",
            can_generate_config=True,
        )
        llm_data = {
            "overall_quality": 0.3,
            "recommendation": "insufficient",
            "gaps": [],
        }
        merged = _merge_results(rule_result, llm_data)
        # Average of 0.9 and 0.3 = 0.6 → acceptable, but LLM says insufficient
        assert merged.readiness == "insufficient"

    def test_llm_adds_gaps(self):
        rule_result = CompletenessResult(
            readiness="ready",
            overall_score=0.9,
            dimensions=[],
            gaps=[],
            summary="All good",
            can_generate_config=True,
        )
        llm_data = {
            "overall_quality": 0.8,
            "recommendation": "ready",
            "gaps": [
                {"key": "interests", "severity": "cosmetic", "message": "Could be more specific", "suggestion": "Add subfields"},
            ],
        }
        merged = _merge_results(rule_result, llm_data)
        assert len(merged.gaps) == 1
        assert merged.gaps[0].key == "interests"

    def test_llm_reasoning_in_summary(self):
        rule_result = CompletenessResult(
            readiness="ready",
            overall_score=0.9,
            dimensions=[],
            gaps=[],
            summary="All good",
            can_generate_config=True,
        )
        llm_data = {
            "overall_quality": 0.9,
            "recommendation": "ready",
            "gaps": [],
            "reasoning": "Excellent interview coverage",
        }
        merged = _merge_results(rule_result, llm_data)
        assert "Excellent interview coverage" in merged.summary


# ────────────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_answers_with_extra_keys(self, complete_answers):
        """Extra keys (e.g., from dynamic follow-ups) should not cause errors."""
        answers = {**complete_answers, "dynamic_followup_1": "some answer"}
        result = check_completeness(answers)
        assert result.readiness == "ready"

    def test_answers_with_none_values(self):
        answers: dict[str, object] = {
            "interests": None,
            "interest_depth": None,
        }
        result = check_completeness(answers)
        assert result.readiness == "insufficient"

    def test_list_type_interests(self):
        """Interests as a list (from multi-select) should be supported."""
        answers: dict[str, object] = {
            "interests": ["AI Research", "Frontend Dev"],
            "interest_depth": "moderate for all",
            "source_types": ["RSS feeds"],
            "initial_sources": "https://example.com",
            "compile_strategy": "merge",
            "wiki_language": "English",
            "vault_path": "~/wiki",
            "existing_vault": "Fresh",
            "schedule_preference": "Daily",
        }
        result = check_completeness(answers)
        assert result.can_generate_config is True

    def test_source_types_as_string(self):
        """source_types as a comma-separated string should work."""
        answers: dict[str, object] = {
            "interests": "AI research",
            "interest_depth": "deep",
            "source_types": "RSS feeds, YouTube channels",
            "initial_sources": "https://example.com",
            "compile_strategy": "merge",
            "wiki_language": "English",
            "vault_path": "~/wiki",
            "existing_vault": "Fresh",
            "schedule_preference": "Daily",
        }
        result = check_completeness(answers)
        assert result.can_generate_config is True

    def test_bool_answers(self):
        """Boolean answers should be handled correctly."""
        answers: dict[str, object] = {
            "interests": "AI",
            "interest_depth": "deep",
            "source_types": True,  # unusual but possible
            "initial_sources": "https://example.com",
            "vault_path": "~/wiki",
        }
        # Should not crash
        result = check_completeness(answers)
        assert isinstance(result, CompletenessResult)

    def test_gap_severity_enum(self):
        """GapSeverity should have correct values."""
        assert GapSeverity.BLOCKING.value == "blocking"
        assert GapSeverity.DEGRADED.value == "degraded"
        assert GapSeverity.COSMETIC.value == "cosmetic"

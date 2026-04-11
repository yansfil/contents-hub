"""
Tests for llm_wiki.branching — answer-based conditional branching.

Tests cover:
- Predicate helpers (contains_any, equals, matches_pattern, etc.)
- Predicate combinators (combined_and, combined_or, negate)
- BranchRule evaluation
- BranchOutcome merging and properties
- BranchEvaluator with default rules
- Source-type branching (YouTube, Twitter, Webpage)
- Vault-type branching (existing vs fresh)
- Compile strategy branching (merge vs per-source)
- Depth-based branching
- Custom rule addition/removal
- State persistence (to_dict / restore_state)
- Edge cases (re-fire prevention, error handling, empty answers)
"""

from __future__ import annotations

import pytest

from llm_wiki.branching import (
    BranchEvaluator,
    BranchOutcome,
    BranchRule,
    BROWSER_SELECTOR_Q,
    CITATION_STYLE_Q,
    DAILY_NOTES_Q,
    MERGE_CONFLICT_Q,
    TWITTER_THREAD_Q,
    VAULT_STRUCTURE_Q,
    YOUTUBE_TRANSCRIPT_Q,
    answer_exists,
    combined_and,
    combined_or,
    contains_any,
    equals,
    get_default_rules,
    matches_pattern,
    negate,
)
from llm_wiki.interview_state import InterviewPhase, QuestionDef


# ────────────────────────────────────────────────────────────────────────────
# Predicate helpers
# ────────────────────────────────────────────────────────────────────────────


class TestContainsAny:
    def test_string_match(self):
        pred = contains_any(["youtube", "twitter"])
        assert pred("I watch YouTube daily", {}) is True

    def test_string_no_match(self):
        pred = contains_any(["youtube"])
        assert pred("I read RSS feeds", {}) is False

    def test_case_insensitive(self):
        pred = contains_any(["YouTube"])
        assert pred("youtube channels", {}) is True

    def test_list_match(self):
        pred = contains_any(["youtube"])
        assert pred(["RSS feeds", "YouTube channels"], {}) is True

    def test_list_no_match(self):
        pred = contains_any(["twitter"])
        assert pred(["RSS feeds", "YouTube channels"], {}) is False

    def test_non_string_value(self):
        pred = contains_any(["yes"])
        assert pred(42, {}) is False
        assert pred(None, {}) is False

    def test_empty_needles(self):
        pred = contains_any([])
        assert pred("anything", {}) is False

    def test_partial_match_in_list(self):
        """Contains checks substring, not exact match."""
        pred = contains_any(["tube"])
        assert pred(["YouTube channels"], {}) is True


class TestEquals:
    def test_string_match(self):
        pred = equals("merge")
        assert pred("merge", {}) is True

    def test_string_case_insensitive(self):
        pred = equals("Merge")
        assert pred("merge", {}) is True

    def test_string_with_whitespace(self):
        pred = equals("merge")
        assert pred("  merge  ", {}) is True

    def test_string_no_match(self):
        pred = equals("merge")
        assert pred("per-source", {}) is False

    def test_non_string_equality(self):
        pred = equals(True)
        assert pred(True, {}) is True
        assert pred(False, {}) is False

    def test_list_equality(self):
        pred = equals(["a", "b"])
        assert pred(["a", "b"], {}) is True
        assert pred(["a"], {}) is False


class TestMatchesPattern:
    def test_simple_match(self):
        pred = matches_pattern(r"deep|technical")
        assert pred("I want deep analysis", {}) is True

    def test_no_match(self):
        pred = matches_pattern(r"deep|technical")
        assert pred("just news updates", {}) is False

    def test_case_insensitive(self):
        pred = matches_pattern(r"Deep")
        assert pred("deep dive", {}) is True

    def test_non_string(self):
        pred = matches_pattern(r"test")
        assert pred(42, {}) is False

    def test_complex_pattern(self):
        pred = matches_pattern(r"https?://.*youtube\.com")
        assert pred("https://www.youtube.com/channel", {}) is True
        assert pred("https://twitter.com", {}) is False


class TestAnswerExists:
    def test_exists(self):
        pred = answer_exists("interests")
        assert pred("anything", {"interests": "AI"}) is True

    def test_not_exists(self):
        pred = answer_exists("interests")
        assert pred("anything", {}) is False

    def test_exists_but_none(self):
        pred = answer_exists("interests")
        assert pred("anything", {"interests": None}) is False


# ────────────────────────────────────────────────────────────────────────────
# Predicate combinators
# ────────────────────────────────────────────────────────────────────────────


class TestPredicateCombinators:
    def test_combined_and_all_true(self):
        pred = combined_and(
            contains_any(["youtube"]),
            contains_any(["channel"]),
        )
        assert pred("YouTube channels", {}) is True

    def test_combined_and_one_false(self):
        pred = combined_and(
            contains_any(["youtube"]),
            contains_any(["twitter"]),
        )
        assert pred("YouTube channels", {}) is False

    def test_combined_or_one_true(self):
        pred = combined_or(
            contains_any(["youtube"]),
            contains_any(["twitter"]),
        )
        assert pred("YouTube channels", {}) is True

    def test_combined_or_none_true(self):
        pred = combined_or(
            contains_any(["youtube"]),
            contains_any(["twitter"]),
        )
        assert pred("RSS feeds", {}) is False

    def test_negate(self):
        pred = negate(contains_any(["youtube"]))
        assert pred("RSS feeds", {}) is True
        assert pred("YouTube", {}) is False

    def test_nested_combinators(self):
        """Test complex nested predicate logic."""
        pred = combined_or(
            combined_and(
                contains_any(["deep"]),
                answer_exists("interests"),
            ),
            equals("technical"),
        )
        assert pred("technical", {}) is True
        assert pred("deep analysis", {"interests": "AI"}) is True
        assert pred("deep analysis", {}) is False  # interests doesn't exist


# ────────────────────────────────────────────────────────────────────────────
# BranchOutcome
# ────────────────────────────────────────────────────────────────────────────


class TestBranchOutcome:
    def test_empty_outcome(self):
        outcome = BranchOutcome()
        assert outcome.has_changes is False
        assert outcome.triggered_rules == []
        assert outcome.questions_to_inject == []
        assert outcome.keys_to_skip == []

    def test_has_changes(self):
        outcome = BranchOutcome(triggered_rules=["rule_1"])
        assert outcome.has_changes is True

    def test_merge_combines_rules(self):
        o1 = BranchOutcome(triggered_rules=["r1"])
        o2 = BranchOutcome(triggered_rules=["r2"])
        merged = o1.merge(o2)
        assert merged.triggered_rules == ["r1", "r2"]

    def test_merge_deduplicates_questions(self):
        q1 = QuestionDef(
            key="q1", phase=InterviewPhase.SOURCES,
            question="Q1?", description="", answer_type="free-text",
        )
        q2 = QuestionDef(
            key="q2", phase=InterviewPhase.SOURCES,
            question="Q2?", description="", answer_type="free-text",
        )
        o1 = BranchOutcome(triggered_rules=["r1"], questions_to_inject=[q1, q2])
        o2 = BranchOutcome(triggered_rules=["r2"], questions_to_inject=[q1])  # duplicate
        merged = o1.merge(o2)
        assert len(merged.questions_to_inject) == 2  # q1 not duplicated

    def test_merge_deduplicates_skip_keys(self):
        o1 = BranchOutcome(keys_to_skip=["k1", "k2"])
        o2 = BranchOutcome(keys_to_skip=["k2", "k3"])
        merged = o1.merge(o2)
        assert sorted(merged.keys_to_skip) == ["k1", "k2", "k3"]


# ────────────────────────────────────────────────────────────────────────────
# BranchRule
# ────────────────────────────────────────────────────────────────────────────


class TestBranchRule:
    def test_frozen(self):
        rule = BranchRule(
            id="test", trigger_key="source_types",
            predicate=contains_any(["youtube"]),
        )
        with pytest.raises(AttributeError):
            rule.id = "changed"  # type: ignore[misc]

    def test_defaults(self):
        rule = BranchRule(
            id="test", trigger_key="key",
            predicate=contains_any(["x"]),
        )
        assert rule.inject == []
        assert rule.skip_keys == []
        assert rule.description == ""


# ────────────────────────────────────────────────────────────────────────────
# Default rules
# ────────────────────────────────────────────────────────────────────────────


class TestDefaultRules:
    def test_default_rules_exist(self):
        rules = get_default_rules()
        assert len(rules) > 0

    def test_all_rules_have_unique_ids(self):
        rules = get_default_rules()
        ids = [r.id for r in rules]
        assert len(ids) == len(set(ids)), f"Duplicate rule IDs: {ids}"

    def test_all_rules_have_descriptions(self):
        rules = get_default_rules()
        for rule in rules:
            assert rule.description, f"Rule '{rule.id}' missing description"

    def test_conditional_questions_are_optional(self):
        """All injected questions should be optional (required=False)."""
        rules = get_default_rules()
        for rule in rules:
            for q in rule.inject:
                assert q.required is False, (
                    f"Rule '{rule.id}' injects required question '{q.key}'"
                )


# ────────────────────────────────────────────────────────────────────────────
# BranchEvaluator — source type branching
# ────────────────────────────────────────────────────────────────────────────


class TestSourceTypeBranching:
    @pytest.fixture
    def evaluator(self):
        return BranchEvaluator()

    def test_youtube_selected(self, evaluator):
        outcome = evaluator.evaluate(
            "source_types",
            ["RSS feeds", "YouTube channels"],
            {"source_types": ["RSS feeds", "YouTube channels"]},
        )
        assert outcome.has_changes
        assert "youtube_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "youtube_transcript" in injected_keys

    def test_twitter_selected(self, evaluator):
        outcome = evaluator.evaluate(
            "source_types",
            ["Twitter/X accounts"],
            {"source_types": ["Twitter/X accounts"]},
        )
        assert "twitter_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "twitter_thread_unroll" in injected_keys

    def test_webpage_selected(self, evaluator):
        outcome = evaluator.evaluate(
            "source_types",
            ["Webpages"],
            {"source_types": ["Webpages"]},
        )
        assert "webpage_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "browser_css_selector" in injected_keys

    def test_rss_only_no_branching(self, evaluator):
        outcome = evaluator.evaluate(
            "source_types",
            ["RSS feeds"],
            {"source_types": ["RSS feeds"]},
        )
        # RSS doesn't trigger any source-type-specific rules
        assert "youtube_selected" not in outcome.triggered_rules
        assert "twitter_selected" not in outcome.triggered_rules

    def test_multiple_source_types(self, evaluator):
        """Selecting YouTube + Twitter should trigger both rules."""
        outcome = evaluator.evaluate(
            "source_types",
            ["YouTube channels", "Twitter/X accounts"],
            {"source_types": ["YouTube channels", "Twitter/X accounts"]},
        )
        assert "youtube_selected" in outcome.triggered_rules
        assert "twitter_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "youtube_transcript" in injected_keys
        assert "twitter_thread_unroll" in injected_keys


# ────────────────────────────────────────────────────────────────────────────
# BranchEvaluator — vault branching
# ────────────────────────────────────────────────────────────────────────────


class TestVaultBranching:
    @pytest.fixture
    def evaluator(self):
        return BranchEvaluator()

    def test_existing_vault(self, evaluator):
        outcome = evaluator.evaluate(
            "existing_vault",
            "Existing vault",
            {"existing_vault": "Existing vault"},
        )
        assert "existing_vault_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "vault_folder_structure" in injected_keys
        assert "daily_notes_integration" in injected_keys

    def test_fresh_vault(self, evaluator):
        outcome = evaluator.evaluate(
            "existing_vault",
            "Fresh/empty vault",
            {"existing_vault": "Fresh/empty vault"},
        )
        assert "fresh_vault_skip_structure" in outcome.triggered_rules
        assert "vault_folder_structure" in outcome.keys_to_skip


# ────────────────────────────────────────────────────────────────────────────
# BranchEvaluator — compile strategy branching
# ────────────────────────────────────────────────────────────────────────────


class TestCompileStrategyBranching:
    @pytest.fixture
    def evaluator(self):
        return BranchEvaluator()

    def test_merge_strategy(self, evaluator):
        outcome = evaluator.evaluate(
            "compile_strategy",
            "merge",
            {"compile_strategy": "merge"},
        )
        assert "merge_strategy_selected" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "merge_conflict_strategy" in injected_keys

    def test_per_source_strategy(self, evaluator):
        outcome = evaluator.evaluate(
            "compile_strategy",
            "per-source",
            {"compile_strategy": "per-source"},
        )
        assert "per_source_skip_merge" in outcome.triggered_rules
        assert "merge_conflict_strategy" in outcome.keys_to_skip

    def test_append_strategy(self, evaluator):
        outcome = evaluator.evaluate(
            "compile_strategy",
            "append",
            {"compile_strategy": "append"},
        )
        assert "per_source_skip_merge" in outcome.triggered_rules
        assert "merge_conflict_strategy" in outcome.keys_to_skip


# ────────────────────────────────────────────────────────────────────────────
# BranchEvaluator — depth branching
# ────────────────────────────────────────────────────────────────────────────


class TestDepthBranching:
    @pytest.fixture
    def evaluator(self):
        return BranchEvaluator()

    def test_deep_interest(self, evaluator):
        outcome = evaluator.evaluate(
            "interest_depth",
            "AI: deep technical, Startups: surface",
            {"interest_depth": "AI: deep technical, Startups: surface"},
        )
        assert "deep_interests" in outcome.triggered_rules
        injected_keys = [q.key for q in outcome.questions_to_inject]
        assert "citation_style" in injected_keys

    def test_surface_interest(self, evaluator):
        outcome = evaluator.evaluate(
            "interest_depth",
            "just news and updates",
            {"interest_depth": "just news and updates"},
        )
        assert "deep_interests" not in outcome.triggered_rules


# ────────────────────────────────────────────────────────────────────────────
# BranchEvaluator — core behavior
# ────────────────────────────────────────────────────────────────────────────


class TestBranchEvaluatorCore:
    def test_no_rules_for_key(self):
        evaluator = BranchEvaluator()
        outcome = evaluator.evaluate(
            "nonexistent_key",
            "some value",
            {"nonexistent_key": "some value"},
        )
        assert outcome.has_changes is False

    def test_rule_does_not_refire(self):
        """Same rule should not fire twice for the same answer."""
        evaluator = BranchEvaluator()
        outcome1 = evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        assert "youtube_selected" in outcome1.triggered_rules

        # Re-evaluate same key — rule should not fire again
        outcome2 = evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        assert "youtube_selected" not in outcome2.triggered_rules
        assert outcome2.has_changes is False

    def test_reset_allows_refire(self):
        evaluator = BranchEvaluator()
        evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        assert "youtube_selected" in evaluator.fired_rules

        evaluator.reset()
        assert len(evaluator.fired_rules) == 0

        outcome = evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        assert "youtube_selected" in outcome.triggered_rules

    def test_evaluate_all(self):
        """evaluate_all should process all answers and combine outcomes."""
        evaluator = BranchEvaluator()
        answers = {
            "source_types": ["YouTube channels", "Twitter/X accounts"],
            "compile_strategy": "merge",
            "interest_depth": "deep technical analysis",
        }
        outcome = evaluator.evaluate_all(answers)

        assert "youtube_selected" in outcome.triggered_rules
        assert "twitter_selected" in outcome.triggered_rules
        assert "merge_strategy_selected" in outcome.triggered_rules
        assert "deep_interests" in outcome.triggered_rules

    def test_predicate_error_handled(self):
        """Rule with a failing predicate should not crash the evaluator."""

        def bad_predicate(value: object, all_answers: dict) -> bool:
            raise RuntimeError("boom")

        evaluator = BranchEvaluator(rules=[
            BranchRule(
                id="bad_rule",
                trigger_key="test_key",
                predicate=bad_predicate,
            ),
        ])
        # Should not raise
        outcome = evaluator.evaluate("test_key", "value", {"test_key": "value"})
        assert outcome.has_changes is False

    def test_custom_rule(self):
        """Add a custom rule and verify it fires."""
        custom_q = QuestionDef(
            key="custom_follow_up",
            phase=InterviewPhase.INTERESTS,
            question="Custom follow-up?",
            description="",
            answer_type="free-text",
            required=False,
        )
        custom_rule = BranchRule(
            id="custom_rule",
            trigger_key="interests",
            predicate=contains_any(["cooking"]),
            inject=[custom_q],
            description="Ask about cooking preferences",
        )

        evaluator = BranchEvaluator(rules=[custom_rule])
        outcome = evaluator.evaluate(
            "interests", "cooking and baking",
            {"interests": "cooking and baking"},
        )
        assert "custom_rule" in outcome.triggered_rules
        assert outcome.questions_to_inject[0].key == "custom_follow_up"

    def test_add_rule(self):
        evaluator = BranchEvaluator(rules=[])
        assert len(evaluator.rules) == 0

        rule = BranchRule(
            id="new_rule", trigger_key="test",
            predicate=equals("yes"),
        )
        evaluator.add_rule(rule)
        assert len(evaluator.rules) == 1

    def test_remove_rule(self):
        rule = BranchRule(
            id="removable", trigger_key="test",
            predicate=equals("yes"),
        )
        evaluator = BranchEvaluator(rules=[rule])
        assert evaluator.remove_rule("removable") is True
        assert len(evaluator.rules) == 0

    def test_remove_nonexistent_rule(self):
        evaluator = BranchEvaluator(rules=[])
        assert evaluator.remove_rule("nonexistent") is False

    def test_get_rules_for_key(self):
        evaluator = BranchEvaluator()
        source_rules = evaluator.get_rules_for_key("source_types")
        assert len(source_rules) >= 3  # youtube, twitter, webpage

        vault_rules = evaluator.get_rules_for_key("existing_vault")
        assert len(vault_rules) >= 2  # existing, fresh


# ────────────────────────────────────────────────────────────────────────────
# State persistence
# ────────────────────────────────────────────────────────────────────────────


class TestBranchEvaluatorPersistence:
    def test_to_dict(self):
        evaluator = BranchEvaluator()
        evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        data = evaluator.to_dict()
        assert "youtube_selected" in data["fired_rules"]

    def test_restore_state(self):
        evaluator = BranchEvaluator()
        evaluator.restore_state({"fired_rules": ["youtube_selected", "twitter_selected"]})
        assert "youtube_selected" in evaluator.fired_rules
        assert "twitter_selected" in evaluator.fired_rules

    def test_restore_empty(self):
        evaluator = BranchEvaluator()
        evaluator.restore_state({})
        assert len(evaluator.fired_rules) == 0

    def test_roundtrip(self):
        evaluator = BranchEvaluator()
        evaluator.evaluate(
            "source_types",
            ["YouTube channels"],
            {"source_types": ["YouTube channels"]},
        )
        evaluator.evaluate(
            "compile_strategy",
            "merge",
            {"compile_strategy": "merge"},
        )
        data = evaluator.to_dict()

        evaluator2 = BranchEvaluator()
        evaluator2.restore_state(data)
        assert evaluator2.fired_rules == evaluator.fired_rules


# ────────────────────────────────────────────────────────────────────────────
# Built-in question definitions
# ────────────────────────────────────────────────────────────────────────────


class TestBuiltInQuestions:
    """Verify all built-in conditional questions are well-formed."""

    @pytest.mark.parametrize("question", [
        YOUTUBE_TRANSCRIPT_Q,
        TWITTER_THREAD_Q,
        VAULT_STRUCTURE_Q,
        DAILY_NOTES_Q,
        MERGE_CONFLICT_Q,
        BROWSER_SELECTOR_Q,
        CITATION_STYLE_Q,
    ])
    def test_question_has_key(self, question):
        assert question.key
        assert isinstance(question.key, str)

    @pytest.mark.parametrize("question", [
        YOUTUBE_TRANSCRIPT_Q,
        TWITTER_THREAD_Q,
        VAULT_STRUCTURE_Q,
        DAILY_NOTES_Q,
        MERGE_CONFLICT_Q,
        BROWSER_SELECTOR_Q,
        CITATION_STYLE_Q,
    ])
    def test_question_is_optional(self, question):
        assert question.required is False

    @pytest.mark.parametrize("question", [
        YOUTUBE_TRANSCRIPT_Q,
        TWITTER_THREAD_Q,
        VAULT_STRUCTURE_Q,
        DAILY_NOTES_Q,
        MERGE_CONFLICT_Q,
        BROWSER_SELECTOR_Q,
        CITATION_STYLE_Q,
    ])
    def test_question_has_description(self, question):
        assert question.description

    def test_no_duplicate_keys(self):
        all_questions = [
            YOUTUBE_TRANSCRIPT_Q,
            TWITTER_THREAD_Q,
            VAULT_STRUCTURE_Q,
            DAILY_NOTES_Q,
            MERGE_CONFLICT_Q,
            BROWSER_SELECTOR_Q,
            CITATION_STYLE_Q,
        ]
        keys = [q.key for q in all_questions]
        assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"


# ────────────────────────────────────────────────────────────────────────────
# Integration: evaluator + state machine compatibility
# ────────────────────────────────────────────────────────────────────────────


class TestIntegrationWithStateMachine:
    """Test that BranchEvaluator outcomes are compatible with InterviewStateMachine."""

    def test_injected_questions_are_questiondef(self):
        """All injected questions must be QuestionDef instances."""
        evaluator = BranchEvaluator()
        outcome = evaluator.evaluate(
            "source_types",
            ["YouTube channels", "Twitter/X accounts", "Webpages"],
            {"source_types": ["YouTube channels", "Twitter/X accounts", "Webpages"]},
        )
        for q in outcome.questions_to_inject:
            assert isinstance(q, QuestionDef)

    def test_full_interview_branching_scenario(self):
        """Simulate a full interview with branching at each step."""
        evaluator = BranchEvaluator()
        all_answers: dict[str, object] = {}

        # Step 1: interests (no branching rules)
        all_answers["interests"] = "AI and web development"
        outcome = evaluator.evaluate("interests", all_answers["interests"], all_answers)
        assert outcome.has_changes is False

        # Step 2: interest_depth with "deep" keyword
        all_answers["interest_depth"] = "AI: deep technical, web: moderate"
        outcome = evaluator.evaluate("interest_depth", all_answers["interest_depth"], all_answers)
        assert "deep_interests" in outcome.triggered_rules

        # Step 3: source_types with YouTube + Twitter
        all_answers["source_types"] = ["YouTube channels", "Twitter/X accounts"]
        outcome = evaluator.evaluate("source_types", all_answers["source_types"], all_answers)
        assert "youtube_selected" in outcome.triggered_rules
        assert "twitter_selected" in outcome.triggered_rules

        # Step 4: compile_strategy = merge
        all_answers["compile_strategy"] = "merge"
        outcome = evaluator.evaluate("compile_strategy", all_answers["compile_strategy"], all_answers)
        assert "merge_strategy_selected" in outcome.triggered_rules

        # Step 5: existing vault
        all_answers["existing_vault"] = "Existing vault"
        outcome = evaluator.evaluate("existing_vault", all_answers["existing_vault"], all_answers)
        assert "existing_vault_selected" in outcome.triggered_rules

        # Verify total fired rules
        assert len(evaluator.fired_rules) == 5

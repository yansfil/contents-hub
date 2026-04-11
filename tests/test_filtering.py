"""Tests for relevance score threshold-based filtering."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from llm_wiki.lens import RelevanceResult, RelevanceScore
from llm_wiki.filtering import (
    DEFAULT_THRESHOLD,
    FilterConfig,
    FilterStrategy,
    FilterVerdict,
    LensVerdict,
    batch_filter,
    filter_by_relevance,
    get_top_results,
    partition_by_relevance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(
    path: str, scores: list[tuple[str, float, str]]
) -> RelevanceResult:
    """Helper: create a RelevanceResult from (lens_id, score, reason) tuples."""
    return RelevanceResult(
        source_path=path,
        scores=[
            RelevanceScore(lens_id=lid, score=s, reason=r, matched_keywords=[])
            for lid, s, r in scores
        ],
        assessed_at=datetime.now(timezone.utc),
    )


def _make_result_with_keywords(
    path: str,
    scores: list[tuple[str, float, str, list[str]]],
) -> RelevanceResult:
    """Helper: create a RelevanceResult with keyword matches."""
    return RelevanceResult(
        source_path=path,
        scores=[
            RelevanceScore(lens_id=lid, score=s, reason=r, matched_keywords=kw)
            for lid, s, r, kw in scores
        ],
        assessed_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def high_relevance_result() -> RelevanceResult:
    """Source highly relevant to AI lens, moderately to DevOps."""
    return _make_result(
        "sources/rss/transformer-paper.md",
        [
            ("ai-ml", 0.95, "Directly about transformers"),
            ("devops", 0.3, "Mentions deployment tangentially"),
            ("rust", 0.05, "No Rust content"),
        ],
    )


@pytest.fixture
def medium_relevance_result() -> RelevanceResult:
    """Source moderately relevant to multiple lenses."""
    return _make_result(
        "sources/rss/ml-deploy.md",
        [
            ("ai-ml", 0.55, "Discusses ML models"),
            ("devops", 0.65, "About Kubernetes deployment"),
            ("rust", 0.1, "No Rust content"),
        ],
    )


@pytest.fixture
def low_relevance_result() -> RelevanceResult:
    """Source below default threshold for all lenses."""
    return _make_result(
        "sources/rss/cooking-recipe.md",
        [
            ("ai-ml", 0.1, "No AI content"),
            ("devops", 0.05, "No DevOps content"),
            ("rust", 0.02, "No Rust content"),
        ],
    )


@pytest.fixture
def no_scores_result() -> RelevanceResult:
    """Source with no scores at all."""
    return RelevanceResult(
        source_path="sources/rss/empty.md",
        scores=[],
        assessed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# FilterConfig
# ---------------------------------------------------------------------------


class TestFilterConfig:
    def test_defaults(self):
        config = FilterConfig()
        assert config.default_threshold == DEFAULT_THRESHOLD
        assert config.strategy == FilterStrategy.ANY
        assert config.per_lens_thresholds == {}
        assert config.min_keywords == 0

    def test_threshold_for_lens_default(self):
        config = FilterConfig(default_threshold=0.6)
        assert config.threshold_for_lens("ai-ml") == 0.6

    def test_threshold_for_lens_override(self):
        config = FilterConfig(
            default_threshold=0.5,
            per_lens_thresholds={"ai-ml": 0.3, "news": 0.7},
        )
        assert config.threshold_for_lens("ai-ml") == 0.3
        assert config.threshold_for_lens("news") == 0.7
        assert config.threshold_for_lens("other") == 0.5

    def test_invalid_default_threshold(self):
        with pytest.raises(ValueError, match="default_threshold"):
            FilterConfig(default_threshold=1.5)
        with pytest.raises(ValueError, match="default_threshold"):
            FilterConfig(default_threshold=-0.1)

    def test_invalid_per_lens_threshold(self):
        with pytest.raises(ValueError, match="Threshold for lens"):
            FilterConfig(per_lens_thresholds={"bad": 2.0})

    def test_invalid_min_keywords(self):
        with pytest.raises(ValueError, match="min_keywords"):
            FilterConfig(min_keywords=-1)

    def test_from_dict(self):
        config = FilterConfig.from_dict({
            "default_threshold": 0.6,
            "per_lens_thresholds": {"ai-ml": 0.3},
            "strategy": "top",
            "min_keywords": 2,
        })
        assert config.default_threshold == 0.6
        assert config.per_lens_thresholds == {"ai-ml": 0.3}
        assert config.strategy == FilterStrategy.TOP
        assert config.min_keywords == 2

    def test_from_dict_defaults(self):
        config = FilterConfig.from_dict({})
        assert config.default_threshold == DEFAULT_THRESHOLD
        assert config.strategy == FilterStrategy.ANY

    def test_from_dict_invalid_strategy(self):
        with pytest.raises(ValueError, match="Invalid filter strategy"):
            FilterConfig.from_dict({"strategy": "nonexistent"})


# ---------------------------------------------------------------------------
# filter_by_relevance — ANY strategy (default)
# ---------------------------------------------------------------------------


class TestFilterByRelevanceAny:
    def test_high_relevance_passes(self, high_relevance_result):
        verdict = filter_by_relevance(high_relevance_result)
        assert verdict.passed is True
        assert verdict.top_score == 0.95
        assert verdict.top_lens_id == "ai-ml"
        assert len(verdict.passing_lenses) >= 1

    def test_low_relevance_filtered(self, low_relevance_result):
        verdict = filter_by_relevance(low_relevance_result)
        assert verdict.passed is False
        assert len(verdict.passing_lenses) == 0
        assert len(verdict.failing_lenses) == 3

    def test_medium_relevance_passes(self, medium_relevance_result):
        """Both ai-ml (0.55) and devops (0.65) exceed default 0.5."""
        verdict = filter_by_relevance(medium_relevance_result)
        assert verdict.passed is True
        assert len(verdict.passing_lenses) == 2

    def test_custom_threshold(self, medium_relevance_result):
        """With threshold 0.6, only devops (0.65) passes."""
        config = FilterConfig(default_threshold=0.6)
        verdict = filter_by_relevance(medium_relevance_result, config)
        assert verdict.passed is True
        assert len(verdict.passing_lenses) == 1
        assert verdict.passing_lenses[0].lens_id == "devops"

    def test_high_threshold_filters_everything(self, high_relevance_result):
        config = FilterConfig(default_threshold=0.99)
        verdict = filter_by_relevance(high_relevance_result, config)
        assert verdict.passed is False

    def test_zero_threshold_passes_everything(self, low_relevance_result):
        config = FilterConfig(default_threshold=0.0)
        verdict = filter_by_relevance(low_relevance_result, config)
        assert verdict.passed is True
        assert len(verdict.passing_lenses) == 3

    def test_no_scores_filtered(self, no_scores_result):
        verdict = filter_by_relevance(no_scores_result)
        assert verdict.passed is False
        assert verdict.summary == "Filtered: no relevance scores available"


# ---------------------------------------------------------------------------
# filter_by_relevance — TOP strategy
# ---------------------------------------------------------------------------


class TestFilterByRelevanceTop:
    def test_top_score_passes(self, high_relevance_result):
        config = FilterConfig(strategy=FilterStrategy.TOP)
        verdict = filter_by_relevance(high_relevance_result, config)
        assert verdict.passed is True
        assert "0.95" in verdict.summary

    def test_top_score_filtered(self, low_relevance_result):
        config = FilterConfig(strategy=FilterStrategy.TOP)
        verdict = filter_by_relevance(low_relevance_result, config)
        assert verdict.passed is False

    def test_top_ignores_per_lens_threshold_except_top(self):
        """TOP strategy uses threshold of the highest-scoring lens."""
        result = _make_result(
            "test.md",
            [("ai-ml", 0.8, "good"), ("news", 0.3, "meh")],
        )
        config = FilterConfig(
            strategy=FilterStrategy.TOP,
            default_threshold=0.5,
            per_lens_thresholds={"ai-ml": 0.9},  # top lens has high bar
        )
        verdict = filter_by_relevance(result, config)
        # ai-ml is top (0.8) but its threshold is 0.9 → fail
        assert verdict.passed is False


# ---------------------------------------------------------------------------
# filter_by_relevance — ALL strategy
# ---------------------------------------------------------------------------


class TestFilterByRelevanceAll:
    def test_all_pass(self):
        result = _make_result(
            "test.md",
            [("a", 0.8, ""), ("b", 0.7, "")],
        )
        config = FilterConfig(strategy=FilterStrategy.ALL)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is True

    def test_one_fails_means_overall_fail(self, high_relevance_result):
        """ai-ml passes, but devops (0.3) and rust (0.05) fail."""
        config = FilterConfig(strategy=FilterStrategy.ALL)
        verdict = filter_by_relevance(high_relevance_result, config)
        assert verdict.passed is False
        assert len(verdict.failing_lenses) == 2


# ---------------------------------------------------------------------------
# Per-lens thresholds
# ---------------------------------------------------------------------------


class TestPerLensThresholds:
    def test_lower_bar_for_niche_topic(self):
        """AI research lens has lower threshold (0.3) — niche sources pass."""
        result = _make_result(
            "test.md",
            [("ai-ml", 0.35, "Some AI mentions"), ("news", 0.2, "Not news")],
        )
        config = FilterConfig(
            default_threshold=0.5,
            per_lens_thresholds={"ai-ml": 0.3},
        )
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is True
        ai_verdict = next(v for v in verdict.lens_verdicts if v.lens_id == "ai-ml")
        assert ai_verdict.passed is True
        assert ai_verdict.threshold == 0.3

    def test_higher_bar_for_noisy_category(self):
        """News lens has higher threshold (0.7) — noisy sources filtered."""
        result = _make_result(
            "test.md",
            [("news", 0.55, "Some news"), ("ai-ml", 0.45, "Tangential AI")],
        )
        config = FilterConfig(
            default_threshold=0.5,
            per_lens_thresholds={"news": 0.7},
        )
        verdict = filter_by_relevance(result, config)
        # news: 0.55 < 0.7 → fail, ai-ml: 0.45 < 0.5 → fail → overall fail
        assert verdict.passed is False


# ---------------------------------------------------------------------------
# Keyword minimum gate
# ---------------------------------------------------------------------------


class TestMinKeywords:
    def test_score_passes_but_keywords_fail(self):
        """High score but no keyword matches → filtered with min_keywords."""
        result = _make_result_with_keywords(
            "test.md",
            [("ai-ml", 0.9, "About AI", [])],  # no keywords matched
        )
        config = FilterConfig(min_keywords=1)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is False
        assert verdict.lens_verdicts[0].keyword_count == 0

    def test_score_and_keywords_pass(self):
        result = _make_result_with_keywords(
            "test.md",
            [("ai-ml", 0.9, "About AI", ["transformer", "LLM"])],
        )
        config = FilterConfig(min_keywords=2)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is True

    def test_zero_min_keywords_no_gate(self):
        """min_keywords=0 means no keyword requirement."""
        result = _make_result_with_keywords(
            "test.md",
            [("ai-ml", 0.9, "About AI", [])],
        )
        config = FilterConfig(min_keywords=0)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is True


# ---------------------------------------------------------------------------
# FilterVerdict properties
# ---------------------------------------------------------------------------


class TestFilterVerdict:
    def test_passing_lenses(self, high_relevance_result):
        verdict = filter_by_relevance(high_relevance_result)
        passing = verdict.passing_lenses
        assert all(v.passed for v in passing)
        assert any(v.lens_id == "ai-ml" for v in passing)

    def test_failing_lenses(self, high_relevance_result):
        verdict = filter_by_relevance(high_relevance_result)
        failing = verdict.failing_lenses
        assert all(not v.passed for v in failing)

    def test_source_path_preserved(self, high_relevance_result):
        verdict = filter_by_relevance(high_relevance_result)
        assert verdict.source_path == "sources/rss/transformer-paper.md"

    def test_summary_present(self, high_relevance_result):
        verdict = filter_by_relevance(high_relevance_result)
        assert verdict.summary != ""
        assert "Passed" in verdict.summary


# ---------------------------------------------------------------------------
# batch_filter
# ---------------------------------------------------------------------------


class TestBatchFilter:
    def test_batch_filter(
        self, high_relevance_result, low_relevance_result
    ):
        verdicts = batch_filter([high_relevance_result, low_relevance_result])
        assert len(verdicts) == 2
        assert verdicts[0].passed is True
        assert verdicts[1].passed is False

    def test_batch_filter_with_config(self, high_relevance_result):
        config = FilterConfig(default_threshold=0.99)
        verdicts = batch_filter([high_relevance_result], config)
        assert verdicts[0].passed is False

    def test_batch_filter_empty_list(self):
        verdicts = batch_filter([])
        assert verdicts == []


# ---------------------------------------------------------------------------
# partition_by_relevance
# ---------------------------------------------------------------------------


class TestPartitionByRelevance:
    def test_partition(
        self,
        high_relevance_result,
        medium_relevance_result,
        low_relevance_result,
    ):
        results = [
            high_relevance_result,
            medium_relevance_result,
            low_relevance_result,
        ]
        passed, filtered_out = partition_by_relevance(results)
        assert len(passed) == 2  # high + medium
        assert len(filtered_out) == 1  # low
        assert filtered_out[0].source_path == "sources/rss/cooking-recipe.md"

    def test_partition_all_pass(self, high_relevance_result):
        config = FilterConfig(default_threshold=0.0)
        passed, filtered_out = partition_by_relevance(
            [high_relevance_result], config
        )
        assert len(passed) == 1
        assert len(filtered_out) == 0

    def test_partition_all_fail(self, low_relevance_result):
        passed, filtered_out = partition_by_relevance([low_relevance_result])
        assert len(passed) == 0
        assert len(filtered_out) == 1

    def test_partition_empty(self):
        passed, filtered_out = partition_by_relevance([])
        assert passed == []
        assert filtered_out == []


# ---------------------------------------------------------------------------
# get_top_results
# ---------------------------------------------------------------------------


class TestGetTopResults:
    def test_top_results_for_lens(
        self,
        high_relevance_result,
        medium_relevance_result,
        low_relevance_result,
    ):
        results = [
            high_relevance_result,
            medium_relevance_result,
            low_relevance_result,
        ]
        top = get_top_results(results, "ai-ml", limit=2)
        assert len(top) == 2
        # Should be sorted descending
        assert top[0].source_path == "sources/rss/transformer-paper.md"  # 0.95
        assert top[1].source_path == "sources/rss/ml-deploy.md"  # 0.55

    def test_top_results_with_min_threshold(
        self,
        high_relevance_result,
        medium_relevance_result,
        low_relevance_result,
    ):
        results = [
            high_relevance_result,
            medium_relevance_result,
            low_relevance_result,
        ]
        top = get_top_results(results, "ai-ml", min_threshold=0.5)
        assert len(top) == 2  # 0.95 and 0.55
        top_strict = get_top_results(results, "ai-ml", min_threshold=0.9)
        assert len(top_strict) == 1  # only 0.95

    def test_top_results_missing_lens(self, high_relevance_result):
        top = get_top_results([high_relevance_result], "nonexistent")
        assert top == []

    def test_top_results_limit(
        self,
        high_relevance_result,
        medium_relevance_result,
    ):
        results = [high_relevance_result, medium_relevance_result]
        top = get_top_results(results, "ai-ml", limit=1)
        assert len(top) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_exact_threshold_passes(self):
        """Score exactly equal to threshold should pass (>=)."""
        result = _make_result("test.md", [("ai-ml", 0.5, "exact")])
        config = FilterConfig(default_threshold=0.5)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is True

    def test_just_below_threshold_fails(self):
        """Score just below threshold should fail."""
        result = _make_result("test.md", [("ai-ml", 0.499, "close")])
        config = FilterConfig(default_threshold=0.5)
        verdict = filter_by_relevance(result, config)
        assert verdict.passed is False

    def test_single_lens_single_score(self):
        result = _make_result("test.md", [("ai-ml", 0.8, "good")])
        verdict = filter_by_relevance(result)
        assert verdict.passed is True
        assert len(verdict.lens_verdicts) == 1

    def test_all_scores_zero(self):
        result = _make_result(
            "test.md",
            [("a", 0.0, ""), ("b", 0.0, "")],
        )
        verdict = filter_by_relevance(result)
        assert verdict.passed is False

    def test_all_scores_one(self):
        result = _make_result(
            "test.md",
            [("a", 1.0, ""), ("b", 1.0, "")],
        )
        verdict = filter_by_relevance(result)
        assert verdict.passed is True

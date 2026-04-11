"""Tests for Lens-based content routing."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from llm_wiki.filtering import (
    FilterConfig,
    FilterStrategy,
    FilterVerdict,
    LensVerdict,
    filter_by_relevance,
)
from llm_wiki.lens import Lens, RelevanceResult, RelevanceScore
from llm_wiki.routing import (
    CATCH_ALL_LENS_ID,
    LensAssignment,
    RoutedSource,
    RoutingConfig,
    RoutingPlan,
    get_primary_lens_for_source,
    get_sources_for_lens,
    route_batch,
    route_from_relevance,
    route_source,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lens(
    lid: str,
    name: str = "",
    keywords: list[str] | None = None,
    priority: int = 0,
) -> Lens:
    """Create a test Lens."""
    return Lens(
        id=lid,
        name=name or lid.replace("-", " ").title(),
        keywords=keywords or [],
        priority=priority,
    )


def _make_relevance_result(
    path: str, scores: list[tuple[str, float, str]]
) -> RelevanceResult:
    """Create a RelevanceResult from (lens_id, score, reason) tuples."""
    return RelevanceResult(
        source_path=path,
        scores=[
            RelevanceScore(lens_id=lid, score=s, reason=r, matched_keywords=[])
            for lid, s, r in scores
        ],
        assessed_at=datetime.now(timezone.utc),
    )


def _make_verdict(
    path: str,
    lens_verdicts: list[tuple[str, float, float, bool]],
    passed: bool = True,
) -> FilterVerdict:
    """Create a FilterVerdict from (lens_id, score, threshold, passed) tuples."""
    lvs = [
        LensVerdict(lens_id=lid, score=s, threshold=t, passed=p, reason="test")
        for lid, s, t, p in lens_verdicts
    ]
    top_score = max((s for _, s, _, _ in lens_verdicts), default=0.0)
    top_lens = next(
        (lid for lid, s, _, _ in lens_verdicts if s == top_score), ""
    )
    return FilterVerdict(
        source_path=path,
        passed=passed,
        strategy=FilterStrategy.ANY,
        lens_verdicts=lvs,
        top_score=top_score,
        top_lens_id=top_lens,
    )


@pytest.fixture
def lenses() -> list[Lens]:
    """Standard test lenses."""
    return [
        _make_lens("ai-ml", "AI/ML Research", ["machine learning", "LLM"], priority=0),
        _make_lens("devops", "DevOps", ["kubernetes", "docker"], priority=1),
        _make_lens("rust", "Rust Programming", ["rust", "cargo"], priority=2),
    ]


@pytest.fixture
def ai_focused_verdict() -> FilterVerdict:
    """Source highly relevant to AI, moderately to DevOps."""
    return _make_verdict(
        "sources/rss/transformer-paper.md",
        [
            ("ai-ml", 0.95, 0.5, True),
            ("devops", 0.3, 0.5, False),
            ("rust", 0.05, 0.5, False),
        ],
    )


@pytest.fixture
def multi_lens_verdict() -> FilterVerdict:
    """Source relevant to both AI and DevOps."""
    return _make_verdict(
        "sources/rss/ml-on-k8s.md",
        [
            ("ai-ml", 0.75, 0.5, True),
            ("devops", 0.65, 0.5, True),
            ("rust", 0.1, 0.5, False),
        ],
    )


@pytest.fixture
def no_match_verdict() -> FilterVerdict:
    """Source that passed filter but no lens exceeds routing threshold."""
    return _make_verdict(
        "sources/rss/cooking.md",
        [
            ("ai-ml", 0.1, 0.5, False),
            ("devops", 0.05, 0.5, False),
            ("rust", 0.02, 0.5, False),
        ],
        passed=True,  # passed filter (e.g., with threshold 0.0) but no lens match
    )


# ---------------------------------------------------------------------------
# RoutingConfig
# ---------------------------------------------------------------------------


class TestRoutingConfig:
    def test_defaults(self):
        config = RoutingConfig()
        assert config.score_threshold == 0.5
        assert config.max_lenses_per_source == 0
        assert config.enable_catch_all is True
        assert config.catch_all_lens_id == CATCH_ALL_LENS_ID
        assert config.prefer_explicit is True

    def test_invalid_score_threshold(self):
        with pytest.raises(ValueError, match="score_threshold"):
            RoutingConfig(score_threshold=1.5)
        with pytest.raises(ValueError, match="score_threshold"):
            RoutingConfig(score_threshold=-0.1)

    def test_invalid_max_lenses(self):
        with pytest.raises(ValueError, match="max_lenses_per_source"):
            RoutingConfig(max_lenses_per_source=-1)

    def test_from_dict(self):
        config = RoutingConfig.from_dict({
            "score_threshold": 0.7,
            "max_lenses_per_source": 3,
            "enable_catch_all": False,
            "catch_all_lens_id": "misc",
            "prefer_explicit": False,
        })
        assert config.score_threshold == 0.7
        assert config.max_lenses_per_source == 3
        assert config.enable_catch_all is False
        assert config.catch_all_lens_id == "misc"
        assert config.prefer_explicit is False

    def test_from_dict_defaults(self):
        config = RoutingConfig.from_dict({})
        assert config.score_threshold == 0.5
        assert config.enable_catch_all is True


# ---------------------------------------------------------------------------
# route_source — basic routing
# ---------------------------------------------------------------------------


class TestRouteSourceBasic:
    def test_single_lens_match(self, ai_focused_verdict, lenses):
        """Source relevant to only AI → routes to ai-ml only."""
        result = route_source(ai_focused_verdict, lenses)
        assert result.is_routed is True
        assert result.lens_ids == ["ai-ml"]
        assert result.primary_lens.lens_id == "ai-ml"
        assert result.primary_lens.source == "score"
        assert result.primary_lens.score == 0.95

    def test_multi_lens_match(self, multi_lens_verdict, lenses):
        """Source relevant to AI and DevOps → routes to both."""
        result = route_source(multi_lens_verdict, lenses)
        assert result.is_routed is True
        assert len(result.assignments) == 2
        # Sorted by priority: ai-ml (0) before devops (1)
        assert result.lens_ids == ["ai-ml", "devops"]

    def test_no_match_catch_all(self, no_match_verdict, lenses):
        """No lens match → routes to catch-all."""
        result = route_source(no_match_verdict, lenses)
        assert result.is_routed is True
        assert result.is_catch_all is True
        assert result.lens_ids == [CATCH_ALL_LENS_ID]
        assert result.assignments[0].source == "catch-all"

    def test_no_match_no_catch_all(self, no_match_verdict, lenses):
        """No lens match + catch-all disabled → unrouted."""
        config = RoutingConfig(enable_catch_all=False)
        result = route_source(no_match_verdict, lenses, config=config)
        assert result.is_routed is False
        assert result.lens_ids == []

    def test_source_path_preserved(self, ai_focused_verdict, lenses):
        result = route_source(ai_focused_verdict, lenses)
        assert result.source_path == "sources/rss/transformer-paper.md"

    def test_routed_at_set(self, ai_focused_verdict, lenses):
        result = route_source(ai_focused_verdict, lenses)
        assert result.routed_at is not None


# ---------------------------------------------------------------------------
# route_source — explicit subscription bindings
# ---------------------------------------------------------------------------


class TestExplicitBindings:
    def test_explicit_binding_always_included(self, ai_focused_verdict, lenses):
        """Explicit subscription lens binding is always included."""
        result = route_source(
            ai_focused_verdict,
            lenses,
            subscription_lenses=["rust"],
        )
        assert "rust" in result.lens_ids
        assert "ai-ml" in result.lens_ids  # still included from score

    def test_explicit_only(self, no_match_verdict, lenses):
        """Explicit binding works even when no score-based match."""
        result = route_source(
            no_match_verdict,
            lenses,
            subscription_lenses=["devops"],
        )
        assert result.is_routed is True
        assert "devops" in result.lens_ids
        assert not result.is_catch_all

    def test_explicit_dedup(self, ai_focused_verdict, lenses):
        """Explicit binding for a lens already matched by score → no duplicate."""
        result = route_source(
            ai_focused_verdict,
            lenses,
            subscription_lenses=["ai-ml"],
        )
        # Should have ai-ml once (from explicit), not twice
        assert result.lens_ids.count("ai-ml") == 1
        assert result.assignments[0].source == "explicit"

    def test_explicit_unknown_lens(self, ai_focused_verdict, lenses):
        """Explicit binding for unknown lens ID → still included."""
        result = route_source(
            ai_focused_verdict,
            lenses,
            subscription_lenses=["nonexistent"],
        )
        assert "nonexistent" in result.lens_ids


# ---------------------------------------------------------------------------
# route_source — max_lenses_per_source cap
# ---------------------------------------------------------------------------


class TestMaxLensesCap:
    def test_cap_applied(self, multi_lens_verdict, lenses):
        """Cap at 1 lens → only highest-priority (ai-ml) remains."""
        config = RoutingConfig(max_lenses_per_source=1)
        result = route_source(multi_lens_verdict, lenses, config=config)
        assert len(result.assignments) == 1

    def test_prefer_explicit_with_cap(self, multi_lens_verdict, lenses):
        """With prefer_explicit and cap, explicit bindings kept first."""
        config = RoutingConfig(max_lenses_per_source=1, prefer_explicit=True)
        result = route_source(
            multi_lens_verdict,
            lenses,
            subscription_lenses=["rust"],
            config=config,
        )
        # rust (explicit) should survive even though it has lower score
        assert "rust" in result.lens_ids
        assert len(result.assignments) == 1

    def test_no_prefer_explicit_with_cap(self, multi_lens_verdict, lenses):
        """Without prefer_explicit, cap picks by score descending."""
        config = RoutingConfig(max_lenses_per_source=1, prefer_explicit=False)
        result = route_source(
            multi_lens_verdict,
            lenses,
            subscription_lenses=["rust"],
            config=config,
        )
        # Highest score (ai-ml 0.75) should win
        assert result.assignments[0].lens_id == "ai-ml"

    def test_cap_zero_means_unlimited(self, multi_lens_verdict, lenses):
        """max_lenses_per_source=0 means no cap."""
        config = RoutingConfig(max_lenses_per_source=0)
        result = route_source(multi_lens_verdict, lenses, config=config)
        assert len(result.assignments) == 2


# ---------------------------------------------------------------------------
# route_source — score threshold
# ---------------------------------------------------------------------------


class TestScoreThreshold:
    def test_higher_routing_threshold(self, multi_lens_verdict, lenses):
        """Routing threshold 0.7 → only ai-ml (0.75) passes, devops (0.65) does not."""
        config = RoutingConfig(score_threshold=0.7)
        result = route_source(multi_lens_verdict, lenses, config=config)
        assert result.lens_ids == ["ai-ml"]

    def test_very_high_threshold_catch_all(self, ai_focused_verdict, lenses):
        """Threshold 0.99 → nothing matches → catch-all."""
        config = RoutingConfig(score_threshold=0.99)
        result = route_source(ai_focused_verdict, lenses, config=config)
        assert result.is_catch_all

    def test_zero_threshold(self, ai_focused_verdict, lenses):
        """Threshold 0.0 → all passing lenses included."""
        config = RoutingConfig(score_threshold=0.0)
        result = route_source(ai_focused_verdict, lenses, config=config)
        # ai-ml passed filter (0.95 >= 0.5), others didn't pass filter
        assert "ai-ml" in result.lens_ids


# ---------------------------------------------------------------------------
# route_source — priority sorting
# ---------------------------------------------------------------------------


class TestPrioritySorting:
    def test_sorted_by_priority(self, multi_lens_verdict, lenses):
        """Assignments should be sorted by lens priority."""
        result = route_source(multi_lens_verdict, lenses)
        assert result.lens_ids == ["ai-ml", "devops"]  # priority 0, 1

    def test_explicit_binding_sorted(self, ai_focused_verdict, lenses):
        """Explicit bindings are also sorted by priority."""
        result = route_source(
            ai_focused_verdict,
            lenses,
            subscription_lenses=["rust", "ai-ml"],
        )
        # ai-ml (priority 0) before rust (priority 2)
        assert result.lens_ids[0] == "ai-ml"


# ---------------------------------------------------------------------------
# route_batch
# ---------------------------------------------------------------------------


class TestRouteBatch:
    def test_batch_routing(self, ai_focused_verdict, multi_lens_verdict, lenses):
        plan = route_batch(
            [ai_focused_verdict, multi_lens_verdict], lenses
        )
        assert len(plan.routed_sources) == 2
        assert plan.total_assignments >= 3  # at least 1 + 2

    def test_batch_with_subscription_map(
        self, ai_focused_verdict, multi_lens_verdict, lenses
    ):
        sub_map = {
            "sources/rss/transformer-paper.md": ["devops"],
        }
        plan = route_batch(
            [ai_focused_verdict, multi_lens_verdict],
            lenses,
            subscription_lenses_map=sub_map,
        )
        # transformer-paper should have devops (explicit) + ai-ml (score)
        paper_source = next(
            rs
            for rs in plan.routed_sources
            if rs.source_path == "sources/rss/transformer-paper.md"
        )
        assert "devops" in paper_source.lens_ids
        assert "ai-ml" in paper_source.lens_ids

    def test_batch_empty(self, lenses):
        plan = route_batch([], lenses)
        assert len(plan.routed_sources) == 0
        assert plan.total_assignments == 0

    def test_plan_timestamp(self, ai_focused_verdict, lenses):
        plan = route_batch([ai_focused_verdict], lenses)
        assert plan.routed_at is not None

    def test_plan_config_stored(self, ai_focused_verdict, lenses):
        config = RoutingConfig(score_threshold=0.8)
        plan = route_batch([ai_focused_verdict], lenses, config=config)
        assert plan.config is not None
        assert plan.config.score_threshold == 0.8


# ---------------------------------------------------------------------------
# RoutingPlan.by_lens
# ---------------------------------------------------------------------------


class TestRoutingPlanByLens:
    def test_by_lens_grouping(self, ai_focused_verdict, multi_lens_verdict, lenses):
        plan = route_batch(
            [ai_focused_verdict, multi_lens_verdict], lenses
        )
        by_lens = plan.by_lens
        assert "ai-ml" in by_lens
        assert len(by_lens["ai-ml"]) == 2  # both sources route to ai-ml

    def test_by_lens_multi_assignment(self, multi_lens_verdict, lenses):
        plan = route_batch([multi_lens_verdict], lenses)
        by_lens = plan.by_lens
        assert "ai-ml" in by_lens
        assert "devops" in by_lens
        # Same source appears in both
        assert (
            by_lens["ai-ml"][0].source_path
            == by_lens["devops"][0].source_path
        )


# ---------------------------------------------------------------------------
# RoutingPlan.summary
# ---------------------------------------------------------------------------


class TestRoutingPlanSummary:
    def test_summary_structure(self, ai_focused_verdict, lenses):
        plan = route_batch([ai_focused_verdict], lenses)
        s = plan.summary()
        assert s["total_sources"] == 1
        assert s["total_assignments"] >= 1
        assert s["lenses_used"] >= 1
        assert "per_lens" in s
        assert isinstance(s["per_lens"], dict)

    def test_summary_with_catch_all(self, no_match_verdict, lenses):
        plan = route_batch([no_match_verdict], lenses)
        s = plan.summary()
        assert s["catch_all"] == 1


# ---------------------------------------------------------------------------
# route_from_relevance (end-to-end: filter + route)
# ---------------------------------------------------------------------------


class TestRouteFromRelevance:
    def test_end_to_end(self, lenses):
        results = [
            _make_relevance_result(
                "sources/rss/ai-paper.md",
                [("ai-ml", 0.9, "AI paper"), ("devops", 0.2, "no"), ("rust", 0.05, "no")],
            ),
            _make_relevance_result(
                "sources/rss/cooking.md",
                [("ai-ml", 0.1, "no"), ("devops", 0.05, "no"), ("rust", 0.02, "no")],
            ),
        ]
        plan = route_from_relevance(results, lenses)
        # cooking should be filtered out, ai-paper should route to ai-ml
        assert len(plan.routed_sources) == 1
        assert plan.routed_sources[0].source_path == "sources/rss/ai-paper.md"
        assert "ai-ml" in plan.routed_sources[0].lens_ids

    def test_end_to_end_with_subscription_lenses(self, lenses):
        results = [
            _make_relevance_result(
                "sources/rss/mixed.md",
                [("ai-ml", 0.6, "ok"), ("devops", 0.55, "ok"), ("rust", 0.1, "no")],
            ),
        ]
        sub_map = {"sources/rss/mixed.md": ["rust"]}
        plan = route_from_relevance(
            results,
            lenses,
            subscription_lenses_map=sub_map,
        )
        rs = plan.routed_sources[0]
        # rust (explicit) + ai-ml (score) + devops (score)
        assert "rust" in rs.lens_ids
        assert "ai-ml" in rs.lens_ids
        assert "devops" in rs.lens_ids

    def test_end_to_end_all_filtered(self, lenses):
        results = [
            _make_relevance_result(
                "sources/rss/junk.md",
                [("ai-ml", 0.01, "no"), ("devops", 0.02, "no"), ("rust", 0.0, "no")],
            ),
        ]
        plan = route_from_relevance(results, lenses)
        assert len(plan.routed_sources) == 0


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestQueryHelpers:
    def test_get_sources_for_lens(self, ai_focused_verdict, multi_lens_verdict, lenses):
        plan = route_batch(
            [ai_focused_verdict, multi_lens_verdict], lenses
        )
        ai_sources = get_sources_for_lens(plan, "ai-ml")
        assert len(ai_sources) == 2
        devops_sources = get_sources_for_lens(plan, "devops")
        assert len(devops_sources) == 1

    def test_get_sources_for_nonexistent_lens(self, ai_focused_verdict, lenses):
        plan = route_batch([ai_focused_verdict], lenses)
        result = get_sources_for_lens(plan, "nonexistent")
        assert result == []

    def test_get_primary_lens(self, ai_focused_verdict, lenses):
        plan = route_batch([ai_focused_verdict], lenses)
        primary = get_primary_lens_for_source(
            plan, "sources/rss/transformer-paper.md"
        )
        assert primary == "ai-ml"

    def test_get_primary_lens_not_found(self, ai_focused_verdict, lenses):
        plan = route_batch([ai_focused_verdict], lenses)
        primary = get_primary_lens_for_source(plan, "nonexistent.md")
        assert primary is None


# ---------------------------------------------------------------------------
# RoutedSource properties
# ---------------------------------------------------------------------------


class TestRoutedSourceProperties:
    def test_is_routed(self, ai_focused_verdict, lenses):
        result = route_source(ai_focused_verdict, lenses)
        assert result.is_routed is True

    def test_not_routed(self, no_match_verdict, lenses):
        config = RoutingConfig(enable_catch_all=False)
        result = route_source(no_match_verdict, lenses, config=config)
        assert result.is_routed is False

    def test_is_catch_all(self, no_match_verdict, lenses):
        result = route_source(no_match_verdict, lenses)
        assert result.is_catch_all is True

    def test_not_catch_all(self, ai_focused_verdict, lenses):
        result = route_source(ai_focused_verdict, lenses)
        assert result.is_catch_all is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_lenses_list(self):
        """No lenses defined + no passing verdicts → catch-all."""
        verdict = FilterVerdict(
            source_path="test.md",
            passed=True,
            strategy=FilterStrategy.ANY,
            lens_verdicts=[],
        )
        result = route_source(verdict, [])
        assert result.is_catch_all

    def test_empty_verdicts_list_in_verdict(self, lenses):
        """Verdict with no lens verdicts → catch-all."""
        verdict = FilterVerdict(
            source_path="test.md",
            passed=True,
            strategy=FilterStrategy.ANY,
            lens_verdicts=[],
        )
        result = route_source(verdict, lenses)
        assert result.is_catch_all

    def test_all_lenses_disabled_still_route(self, ai_focused_verdict):
        """Disabled lenses are still routable (filtering handles enabled state)."""
        disabled_lens = _make_lens("ai-ml")
        disabled_lens.enabled = False
        result = route_source(ai_focused_verdict, [disabled_lens])
        assert "ai-ml" in result.lens_ids

    def test_duplicate_subscription_lenses(self, ai_focused_verdict, lenses):
        """Duplicate lens IDs in subscription_lenses → no duplicates in output."""
        result = route_source(
            ai_focused_verdict,
            lenses,
            subscription_lenses=["rust", "rust", "rust"],
        )
        assert result.lens_ids.count("rust") == 1

    def test_custom_catch_all_id(self, no_match_verdict, lenses):
        """Custom catch-all lens ID."""
        config = RoutingConfig(catch_all_lens_id="misc")
        result = route_source(no_match_verdict, lenses, config=config)
        assert result.lens_ids == ["misc"]

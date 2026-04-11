"""Tests for Lens data model, relevance scoring, and LensStore."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.lens import (
    CompileStrategy,
    Lens,
    LensStore,
    RelevanceScore,
    RelevanceResult,
    get_lens_file_path,
    slugify_name,
    validate_lens_id,
    LENSES_DIR,
    LENS_FILE_EXT,
)


# ---------------------------------------------------------------------------
# validate_lens_id
# ---------------------------------------------------------------------------


class TestValidateLensId:
    def test_valid_ids(self):
        assert validate_lens_id("ai-research") == "ai-research"
        assert validate_lens_id("frontend") == "frontend"
        assert validate_lens_id("x") == "x"
        assert validate_lens_id("a1") == "a1"
        assert validate_lens_id("my-lens-99") == "my-lens-99"

    def test_invalid_ids(self):
        with pytest.raises(ValueError):
            validate_lens_id("")
        with pytest.raises(ValueError):
            validate_lens_id("-starts-with-hyphen")
        with pytest.raises(ValueError):
            validate_lens_id("ends-with-hyphen-")
        with pytest.raises(ValueError):
            validate_lens_id("HAS-UPPERCASE")
        with pytest.raises(ValueError):
            validate_lens_id("has spaces")
        with pytest.raises(ValueError):
            validate_lens_id("has_underscore")


# ---------------------------------------------------------------------------
# slugify_name
# ---------------------------------------------------------------------------


class TestSlugifyName:
    def test_basic(self):
        assert slugify_name("AI/ML") == "ai-ml"
        assert slugify_name("Rust Ecosystem") == "rust-ecosystem"
        assert slugify_name("  Web Dev  ") == "web-dev"

    def test_empty(self):
        assert slugify_name("") == "uncategorized"
        assert slugify_name("///") == "uncategorized"


# ---------------------------------------------------------------------------
# CompileStrategy
# ---------------------------------------------------------------------------


class TestCompileStrategy:
    def test_values(self):
        assert CompileStrategy.MERGE.value == "merge"
        assert CompileStrategy.PER_SOURCE.value == "per-source"
        assert CompileStrategy.APPEND.value == "append"

    def test_from_string(self):
        assert CompileStrategy("merge") == CompileStrategy.MERGE
        assert CompileStrategy("per-source") == CompileStrategy.PER_SOURCE

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            CompileStrategy("invalid")


# ---------------------------------------------------------------------------
# Lens dataclass
# ---------------------------------------------------------------------------


class TestLens:
    def test_create_minimal(self):
        lens = Lens(id="ai-research", name="AI Research")
        assert lens.id == "ai-research"
        assert lens.name == "AI Research"
        assert lens.compile_strategy == CompileStrategy.MERGE
        assert lens.enabled is True
        assert lens.priority == 0
        assert lens.keywords == []
        assert lens.default_tags == []
        assert lens.source_ids == []

    def test_create_full(self):
        lens = Lens(
            id="devops",
            name="DevOps",
            description="Infrastructure and deployment",
            keywords=["kubernetes", "docker", "ci/cd"],
            default_tags=["devops", "infra"],
            wiki_directory="topics/devops",
            compile_strategy=CompileStrategy.PER_SOURCE,
            compile_instructions="Focus on practical examples",
            source_ids=["sub-001", "sub-002"],
            priority=1,
            enabled=True,
        )
        assert lens.id == "devops"
        assert lens.keywords == ["kubernetes", "docker", "ci/cd"]
        assert lens.default_tags == ["devops", "infra"]
        assert lens.wiki_directory == "topics/devops"
        assert lens.compile_strategy == CompileStrategy.PER_SOURCE
        assert lens.source_ids == ["sub-001", "sub-002"]
        assert lens.priority == 1

    def test_invalid_id_raises(self):
        with pytest.raises(ValueError, match="lowercase slug"):
            Lens(id="INVALID", name="Test")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Lens(id="test", name="")

    def test_negative_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be >= 0"):
            Lens(id="test", name="Test", priority=-1)

    def test_compile_strategy_from_string(self):
        """compile_strategy accepts string values (from YAML deserialization)."""
        lens = Lens(id="test", name="Test", compile_strategy="per-source")
        assert lens.compile_strategy == CompileStrategy.PER_SOURCE

    def test_effective_wiki_directory(self):
        lens1 = Lens(id="ai-research", name="AI", wiki_directory="topics/ai")
        assert lens1.effective_wiki_directory == "topics/ai"

        lens2 = Lens(id="ai-research", name="AI")
        assert lens2.effective_wiki_directory == "ai-research"

    def test_roundtrip_dict(self):
        lens = Lens(
            id="ai-research",
            name="AI Research",
            description="Artificial intelligence",
            keywords=["llm", "transformer"],
            default_tags=["ai", "research"],
            wiki_directory="topics/ai",
            compile_strategy=CompileStrategy.MERGE,
            compile_instructions="Write in academic style",
            source_ids=["sub-001"],
            priority=2,
            enabled=True,
        )
        d = lens.to_dict()
        restored = Lens.from_dict(d)
        assert restored.id == lens.id
        assert restored.name == lens.name
        assert restored.description == lens.description
        assert restored.keywords == lens.keywords
        assert restored.default_tags == lens.default_tags
        assert restored.wiki_directory == lens.wiki_directory
        assert restored.compile_strategy == lens.compile_strategy
        assert restored.compile_instructions == lens.compile_instructions
        assert restored.source_ids == lens.source_ids
        assert restored.priority == lens.priority
        assert restored.enabled == lens.enabled

    def test_roundtrip_yaml(self):
        lens = Lens(
            id="frontend",
            name="Frontend",
            keywords=["react", "css"],
            default_tags=["web"],
        )
        yaml_str = lens.to_yaml()
        restored = Lens.from_yaml(yaml_str)
        assert restored.id == "frontend"
        assert restored.keywords == ["react", "css"]

    def test_to_dict_omits_empty_optionals(self):
        """Empty optional fields are omitted for cleaner YAML."""
        lens = Lens(id="minimal", name="Minimal")
        d = lens.to_dict()
        assert "description" not in d
        assert "keywords" not in d
        assert "default_tags" not in d
        assert "wiki_directory" not in d
        assert "compile_instructions" not in d
        assert "source_ids" not in d
        # Always present:
        assert "compile_strategy" in d
        assert "priority" in d
        assert "enabled" in d

    def test_from_dict_legacy_slug_compat(self):
        """Backward compatibility: legacy 'slug' field maps to 'id'."""
        data = {"slug": "old-lens", "name": "Old Lens"}
        lens = Lens.from_dict(data)
        assert lens.id == "old-lens"
        assert lens.name == "Old Lens"

    def test_from_dict_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="Invalid compile_strategy"):
            Lens.from_dict({"id": "x", "name": "X", "compile_strategy": "bad"})

    def test_from_yaml_invalid_raises(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            Lens.from_yaml("just a string")


# ---------------------------------------------------------------------------
# RelevanceScore
# ---------------------------------------------------------------------------


class TestRelevanceScore:
    def test_create(self):
        score = RelevanceScore(lens_id="ai-research", score=0.85, reason="Discusses LLMs")
        assert score.score == 0.85
        assert score.exceeds_threshold() is True

    def test_below_threshold(self):
        score = RelevanceScore(lens_id="devops", score=0.3)
        assert score.exceeds_threshold() is False
        assert score.exceeds_threshold(0.2) is True
        assert score.exceeds_threshold(0.5) is False

    def test_invalid_score_raises(self):
        with pytest.raises(ValueError):
            RelevanceScore(lens_id="x", score=1.5)
        with pytest.raises(ValueError):
            RelevanceScore(lens_id="x", score=-0.1)

    def test_roundtrip_dict(self):
        score = RelevanceScore(
            lens_id="ai-research",
            score=0.85,
            reason="Discusses LLMs",
            matched_keywords=["llm", "transformer"],
        )
        d = score.to_dict()
        restored = RelevanceScore.from_dict(d)
        assert restored.lens_id == "ai-research"
        assert restored.score == 0.85
        assert restored.reason == "Discusses LLMs"
        assert restored.matched_keywords == ["llm", "transformer"]


# ---------------------------------------------------------------------------
# RelevanceResult
# ---------------------------------------------------------------------------


class TestRelevanceResult:
    def test_relevant_lenses(self):
        result = RelevanceResult(
            source_path="sources/rss/article.md",
            scores=[
                RelevanceScore(lens_id="ai-research", score=0.9),
                RelevanceScore(lens_id="devops", score=0.3),
                RelevanceScore(lens_id="rust", score=0.6),
            ],
        )
        relevant = result.relevant_lenses
        assert len(relevant) == 2
        assert {s.lens_id for s in relevant} == {"ai-research", "rust"}

    def test_top_lens(self):
        result = RelevanceResult(
            source_path="sources/rss/article.md",
            scores=[
                RelevanceScore(lens_id="ai-research", score=0.9),
                RelevanceScore(lens_id="devops", score=0.3),
            ],
        )
        assert result.top_lens().lens_id == "ai-research"

    def test_top_lens_empty(self):
        result = RelevanceResult(source_path="x")
        assert result.top_lens() is None

    def test_for_lens(self):
        result = RelevanceResult(
            source_path="x",
            scores=[RelevanceScore(lens_id="ai-research", score=0.9)],
        )
        assert result.for_lens("ai-research").score == 0.9
        assert result.for_lens("devops") is None

    def test_to_frontmatter(self):
        result = RelevanceResult(
            source_path="x",
            scores=[
                RelevanceScore(lens_id="ai-research", score=0.9, reason="LLM content"),
                RelevanceScore(lens_id="devops", score=0.0),  # filtered out
            ],
        )
        fm = result.to_frontmatter()
        assert len(fm["relevance"]) == 1
        assert fm["relevance"][0]["lens"] == "ai-research"


# ---------------------------------------------------------------------------
# File path helpers
# ---------------------------------------------------------------------------


class TestFilePathHelpers:
    def test_get_lens_file_path(self):
        assert get_lens_file_path("ai-research") == "lenses/ai-research.yml"
        assert get_lens_file_path("x") == "lenses/x.yml"


# ---------------------------------------------------------------------------
# LensStore (one-file-per-lens YAML persistence)
# ---------------------------------------------------------------------------


class TestLensStore:
    @pytest.fixture
    def config(self, tmp_path: Path) -> WikiConfig:
        return WikiConfig(vault_path=tmp_path)

    def test_save_and_load(self, config: WikiConfig):
        store = LensStore(config)
        lens = Lens(
            id="ai-research",
            name="AI Research",
            description="AI stuff",
            keywords=["llm"],
            default_tags=["ai"],
        )
        store.save(lens)

        loaded = store.load("ai-research")
        assert loaded is not None
        assert loaded.id == "ai-research"
        assert loaded.name == "AI Research"
        assert loaded.description == "AI stuff"
        assert loaded.keywords == ["llm"]
        assert loaded.default_tags == ["ai"]
        assert loaded.created_at is not None
        assert loaded.updated_at is not None

    def test_save_creates_lenses_dir(self, config: WikiConfig):
        store = LensStore(config)
        lens = Lens(id="test", name="Test")
        store.save(lens)
        assert (config.vault_path / LENSES_DIR).is_dir()

    def test_load_nonexistent_returns_none(self, config: WikiConfig):
        store = LensStore(config)
        assert store.load("nonexistent") is None

    def test_delete(self, config: WikiConfig):
        store = LensStore(config)
        lens = Lens(id="to-delete", name="Delete Me")
        store.save(lens)
        assert store.exists("to-delete")
        assert store.delete("to-delete") is True
        assert store.exists("to-delete") is False

    def test_delete_nonexistent_returns_false(self, config: WikiConfig):
        store = LensStore(config)
        assert store.delete("nonexistent") is False

    def test_list_all_sorted_by_priority_then_name(self, config: WikiConfig):
        store = LensStore(config)
        store.save(Lens(id="z-lens", name="Z Lens", priority=0))
        store.save(Lens(id="a-lens", name="A Lens", priority=0))
        store.save(Lens(id="first", name="First", priority=-1 if False else 0))
        # priority 0, sorted by name: A, First, Z
        # Add one with higher priority
        store.save(Lens(id="urgent", name="Urgent", priority=1))

        lenses = store.list_all()
        assert len(lenses) == 4
        # priority 0 first (A, First, Z), then priority 1 (Urgent)
        assert lenses[0].id == "a-lens"
        assert lenses[-1].id == "urgent"

    def test_list_enabled(self, config: WikiConfig):
        store = LensStore(config)
        store.save(Lens(id="active-lens", name="Active"))
        store.save(Lens(id="disabled-lens", name="Disabled", enabled=False))

        enabled = store.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].id == "active-lens"

    def test_exists(self, config: WikiConfig):
        store = LensStore(config)
        assert store.exists("x") is False
        store.save(Lens(id="x", name="X"))
        assert store.exists("x") is True

    def test_count(self, config: WikiConfig):
        store = LensStore(config)
        assert store.count == 0
        store.save(Lens(id="a", name="A"))
        store.save(Lens(id="b", name="B"))
        assert store.count == 2

    def test_yaml_file_is_human_readable(self, config: WikiConfig):
        """Verify the persisted YAML is clean and human-readable."""
        store = LensStore(config)
        lens = Lens(
            id="ai-research",
            name="AI Research",
            description="Latest AI developments",
            keywords=["llm", "transformer"],
            default_tags=["ai"],
            compile_strategy=CompileStrategy.MERGE,
        )
        path = store.save(lens)
        content = path.read_text()

        assert "id: ai-research" in content
        assert "name: AI Research" in content
        assert "- llm" in content
        assert "compile_strategy: merge" in content
        assert "enabled: true" in content

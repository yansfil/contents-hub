"""Tests for Lens → Obsidian markdown file writer."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.lens import CompileStrategy, Lens, LensStore, LENSES_DIR
from llm_wiki.lens_writer import (
    LENS_MD_EXT,
    delete_lens_complete,
    delete_lens_markdown,
    format_lens_body,
    format_lens_frontmatter,
    format_lens_markdown,
    get_lens_md_path,
    save_lens_complete,
    save_lenses_complete,
    write_lens_markdown,
    write_lenses_batch,
    _find_related_lenses,
)
from llm_wiki.writer import WriteAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> WikiConfig:
    return WikiConfig(vault_path=tmp_path)


@pytest.fixture
def sample_lens() -> Lens:
    return Lens(
        id="ai-research",
        name="AI Research",
        description="Latest developments in artificial intelligence.",
        keywords=["machine learning", "transformer", "llm"],
        default_tags=["ai", "research"],
        wiki_directory="topics/ai-research",
        compile_strategy=CompileStrategy.MERGE,
        compile_instructions="Write in clear technical prose. Link related concepts.",
        source_ids=["sub-001", "sub-002"],
        priority=0,
        enabled=True,
    )


@pytest.fixture
def sibling_lenses() -> list[Lens]:
    return [
        Lens(
            id="frontend",
            name="Frontend",
            keywords=["react", "css", "javascript"],
            default_tags=["web"],
        ),
        Lens(
            id="ml-ops",
            name="ML Ops",
            keywords=["machine learning", "deployment", "kubernetes"],
            default_tags=["mlops"],
            source_ids=["sub-002"],
        ),
    ]


# ---------------------------------------------------------------------------
# get_lens_md_path
# ---------------------------------------------------------------------------


class TestGetLensMdPath:
    def test_basic(self):
        assert get_lens_md_path("ai-research") == "lenses/ai-research.md"
        assert get_lens_md_path("x") == "lenses/x.md"

    def test_distinct_from_yaml(self):
        from llm_wiki.lens import get_lens_file_path

        assert get_lens_md_path("test") != get_lens_file_path("test")
        assert get_lens_md_path("test").endswith(".md")
        assert get_lens_file_path("test").endswith(".yml")


# ---------------------------------------------------------------------------
# format_lens_frontmatter
# ---------------------------------------------------------------------------


class TestFormatLensFrontmatter:
    def test_required_fields(self, sample_lens: Lens):
        fm = format_lens_frontmatter(sample_lens)
        assert fm["type"] == "lens"
        assert fm["id"] == "ai-research"
        assert fm["name"] == "AI Research"
        assert fm["compile_strategy"] == "merge"
        assert fm["priority"] == 0
        assert fm["enabled"] is True

    def test_keywords_as_list(self, sample_lens: Lens):
        fm = format_lens_frontmatter(sample_lens)
        assert fm["keywords"] == ["machine learning", "transformer", "llm"]

    def test_tags_field_uses_default_tags(self, sample_lens: Lens):
        """Obsidian uses 'tags' in frontmatter, not 'default_tags'."""
        fm = format_lens_frontmatter(sample_lens)
        assert fm["tags"] == ["ai", "research"]
        assert "default_tags" not in fm

    def test_wiki_directory(self, sample_lens: Lens):
        fm = format_lens_frontmatter(sample_lens)
        assert fm["wiki_directory"] == "topics/ai-research"

    def test_source_ids(self, sample_lens: Lens):
        fm = format_lens_frontmatter(sample_lens)
        assert fm["source_ids"] == ["sub-001", "sub-002"]

    def test_timestamps(self):
        now = datetime.now(timezone.utc)
        lens = Lens(id="test", name="Test", created_at=now, updated_at=now)
        fm = format_lens_frontmatter(lens)
        assert fm["created_at"] == now.isoformat()
        assert fm["updated_at"] == now.isoformat()

    def test_omits_empty_optionals(self):
        lens = Lens(id="minimal", name="Minimal")
        fm = format_lens_frontmatter(lens)
        assert "keywords" not in fm
        assert "tags" not in fm
        assert "wiki_directory" not in fm
        assert "source_ids" not in fm
        assert "created_at" not in fm
        assert "updated_at" not in fm
        # Always present:
        assert "type" in fm
        assert "id" in fm
        assert "name" in fm
        assert "compile_strategy" in fm
        assert "priority" in fm
        assert "enabled" in fm


# ---------------------------------------------------------------------------
# format_lens_body
# ---------------------------------------------------------------------------


class TestFormatLensBody:
    def test_title(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert body.startswith("# AI Research\n")

    def test_description(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "Latest developments in artificial intelligence." in body

    def test_inline_tags(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "#ai #research" in body

    def test_keywords_section(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "## Keywords" in body
        assert "`machine learning`" in body
        assert "`transformer`" in body
        assert "`llm`" in body

    def test_compile_instructions_section(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "## Compile Instructions" in body
        assert "Write in clear technical prose" in body

    def test_configuration_section(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "## Configuration" in body
        assert "**Strategy**: merge" in body
        assert "**Output**: `topics/ai-research/`" in body
        assert "**Priority**: 0" in body
        assert "**Enabled**: Yes" in body

    def test_sources_section(self, sample_lens: Lens):
        body = format_lens_body(sample_lens)
        assert "## Sources" in body
        assert "`sub-001`" in body
        assert "`sub-002`" in body

    def test_no_description_section_when_empty(self):
        lens = Lens(id="test", name="Test")
        body = format_lens_body(lens)
        # Should not have empty paragraphs after title
        lines = body.split("\n")
        assert lines[0] == "# Test"

    def test_no_keywords_section_when_empty(self):
        lens = Lens(id="test", name="Test")
        body = format_lens_body(lens)
        assert "## Keywords" not in body

    def test_no_instructions_section_when_empty(self):
        lens = Lens(id="test", name="Test")
        body = format_lens_body(lens)
        assert "## Compile Instructions" not in body

    def test_no_sources_section_when_empty(self):
        lens = Lens(id="test", name="Test")
        body = format_lens_body(lens)
        assert "## Sources" not in body

    def test_disabled_lens_shows_no(self):
        lens = Lens(id="test", name="Test", enabled=False)
        body = format_lens_body(lens)
        assert "**Enabled**: No" in body

    def test_related_lenses_wikilinks(
        self, sample_lens: Lens, sibling_lenses: list[Lens]
    ):
        body = format_lens_body(sample_lens, sibling_lenses=sibling_lenses)
        assert "## Related Lenses" in body
        # ml-ops shares source_ids with sample_lens
        assert "[[ML Ops]]" in body

    def test_no_related_lenses_when_no_overlap(self):
        lens = Lens(id="cooking", name="Cooking", keywords=["recipe"])
        siblings = [Lens(id="sports", name="Sports", keywords=["football"])]
        body = format_lens_body(lens, sibling_lenses=siblings)
        assert "## Related Lenses" not in body


# ---------------------------------------------------------------------------
# format_lens_markdown
# ---------------------------------------------------------------------------


class TestFormatLensMarkdown:
    def test_has_frontmatter_delimiters(self, sample_lens: Lens):
        md = format_lens_markdown(sample_lens)
        assert md.startswith("---\n")
        # Second --- after frontmatter
        parts = md.split("---")
        assert len(parts) >= 3  # before, frontmatter, body (may contain ---)

    def test_frontmatter_contains_type_lens(self, sample_lens: Lens):
        md = format_lens_markdown(sample_lens)
        assert "type: lens" in md

    def test_body_contains_title(self, sample_lens: Lens):
        md = format_lens_markdown(sample_lens)
        assert "# AI Research" in md

    def test_ends_with_newline(self, sample_lens: Lens):
        md = format_lens_markdown(sample_lens)
        assert md.endswith("\n")


# ---------------------------------------------------------------------------
# _find_related_lenses
# ---------------------------------------------------------------------------


class TestFindRelatedLenses:
    def test_shared_source_ids(self):
        lens = Lens(id="a", name="A", source_ids=["s1", "s2"])
        siblings = [
            Lens(id="b", name="B", source_ids=["s2", "s3"]),
            Lens(id="c", name="C", source_ids=["s4"]),
        ]
        related = _find_related_lenses(lens, siblings)
        assert len(related) == 1
        assert related[0].id == "b"

    def test_shared_keywords_need_at_least_two(self):
        lens = Lens(id="a", name="A", keywords=["ml", "ai", "python"])
        siblings = [
            Lens(id="b", name="B", keywords=["ml", "ai"]),  # 2 overlap -> related
            Lens(id="c", name="C", keywords=["ml"]),  # 1 overlap -> not related
        ]
        related = _find_related_lenses(lens, siblings)
        assert len(related) == 1
        assert related[0].id == "b"

    def test_empty_siblings(self):
        lens = Lens(id="a", name="A")
        assert _find_related_lenses(lens, []) == []

    def test_no_self_reference(self):
        lens = Lens(id="a", name="A", keywords=["ml", "ai"])
        siblings = [lens]  # same object
        related = _find_related_lenses(lens, siblings)
        assert len(related) == 0

    def test_sorted_by_name(self):
        lens = Lens(id="a", name="A", source_ids=["s1"])
        siblings = [
            Lens(id="z", name="Z Lens", source_ids=["s1"]),
            Lens(id="b", name="B Lens", source_ids=["s1"]),
        ]
        related = _find_related_lenses(lens, siblings)
        assert [r.name for r in related] == ["B Lens", "Z Lens"]


# ---------------------------------------------------------------------------
# write_lens_markdown
# ---------------------------------------------------------------------------


class TestWriteLensMarkdown:
    def test_creates_file(self, config: WikiConfig, sample_lens: Lens):
        result = write_lens_markdown(config, sample_lens)
        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert result.relative_path == Path("lenses/ai-research.md")
        assert result.bytes_written > 0

    def test_file_content_is_valid_obsidian(self, config: WikiConfig, sample_lens: Lens):
        result = write_lens_markdown(config, sample_lens)
        content = result.path.read_text(encoding="utf-8")

        # Valid frontmatter
        assert content.startswith("---\n")
        assert "type: lens" in content
        assert "id: ai-research" in content

        # Valid body
        assert "# AI Research" in content
        assert "#ai #research" in content

    def test_creates_lenses_directory(self, config: WikiConfig, sample_lens: Lens):
        write_lens_markdown(config, sample_lens)
        assert (config.vault_path / LENSES_DIR).is_dir()

    def test_overwrites_by_default(self, config: WikiConfig, sample_lens: Lens):
        result1 = write_lens_markdown(config, sample_lens)
        assert result1.action == WriteAction.CREATED

        result2 = write_lens_markdown(config, sample_lens)
        assert result2.action == WriteAction.UPDATED

    def test_skip_when_overwrite_false(self, config: WikiConfig, sample_lens: Lens):
        write_lens_markdown(config, sample_lens)
        result = write_lens_markdown(config, sample_lens, overwrite=False)
        assert result.action == WriteAction.SKIPPED

    def test_sets_timestamps(self, config: WikiConfig):
        lens = Lens(id="test", name="Test")
        assert lens.created_at is None
        assert lens.updated_at is None

        write_lens_markdown(config, lens)
        assert lens.created_at is not None
        assert lens.updated_at is not None

    def test_preserves_existing_created_at(self, config: WikiConfig):
        original_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        lens = Lens(id="test", name="Test", created_at=original_time)

        write_lens_markdown(config, lens)
        assert lens.created_at == original_time
        assert lens.updated_at is not None
        assert lens.updated_at != original_time


# ---------------------------------------------------------------------------
# write_lenses_batch
# ---------------------------------------------------------------------------


class TestWriteLensesBatch:
    def test_writes_all(self, config: WikiConfig):
        lenses = [
            Lens(id="ai-research", name="AI Research", source_ids=["s1"]),
            Lens(id="frontend", name="Frontend", source_ids=["s2"]),
        ]
        results = write_lenses_batch(config, lenses)
        assert len(results) == 2
        assert all(r.action == WriteAction.CREATED for r in results)

    def test_cross_links_related_lenses(self, config: WikiConfig):
        lenses = [
            Lens(id="ai-research", name="AI Research", source_ids=["shared-sub"]),
            Lens(id="ml-ops", name="ML Ops", source_ids=["shared-sub"]),
            Lens(id="cooking", name="Cooking"),  # unrelated
        ]
        results = write_lenses_batch(config, lenses)

        ai_content = results[0].path.read_text(encoding="utf-8")
        assert "[[ML Ops]]" in ai_content

        mlops_content = results[1].path.read_text(encoding="utf-8")
        assert "[[AI Research]]" in mlops_content

        cooking_content = results[2].path.read_text(encoding="utf-8")
        assert "## Related Lenses" not in cooking_content

    def test_skip_when_overwrite_false(self, config: WikiConfig):
        lenses = [Lens(id="test", name="Test")]
        write_lenses_batch(config, lenses)
        results = write_lenses_batch(config, lenses, overwrite=False)
        assert results[0].action == WriteAction.SKIPPED


# ---------------------------------------------------------------------------
# save_lens_complete
# ---------------------------------------------------------------------------


class TestSaveLensComplete:
    def test_creates_both_yaml_and_md(self, config: WikiConfig, sample_lens: Lens):
        yaml_path, md_result = save_lens_complete(config, sample_lens)

        # YAML file
        assert yaml_path.exists()
        assert yaml_path.suffix == ".yml"
        assert yaml_path.name == "ai-research.yml"

        # MD file
        assert md_result.path.exists()
        assert md_result.path.suffix == ".md"
        assert md_result.relative_path == Path("lenses/ai-research.md")

    def test_yaml_and_md_are_different_files(self, config: WikiConfig, sample_lens: Lens):
        yaml_path, md_result = save_lens_complete(config, sample_lens)
        assert yaml_path != md_result.path

    def test_yaml_is_pure_yaml(self, config: WikiConfig, sample_lens: Lens):
        yaml_path, _ = save_lens_complete(config, sample_lens)
        content = yaml_path.read_text(encoding="utf-8")
        # Pure YAML has no --- delimiters
        assert not content.startswith("---")
        assert "id: ai-research" in content

    def test_md_has_frontmatter(self, config: WikiConfig, sample_lens: Lens):
        _, md_result = save_lens_complete(config, sample_lens)
        content = md_result.path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "type: lens" in content


# ---------------------------------------------------------------------------
# save_lenses_complete
# ---------------------------------------------------------------------------


class TestSaveLensesComplete:
    def test_writes_all_pairs(self, config: WikiConfig):
        lenses = [
            Lens(id="a", name="A"),
            Lens(id="b", name="B"),
        ]
        results = save_lenses_complete(config, lenses)
        assert len(results) == 2

        for yaml_path, md_result in results:
            assert yaml_path.exists()
            assert md_result.path.exists()


# ---------------------------------------------------------------------------
# delete_lens_markdown / delete_lens_complete
# ---------------------------------------------------------------------------


class TestDeleteLens:
    def test_delete_md_only(self, config: WikiConfig, sample_lens: Lens):
        write_lens_markdown(config, sample_lens)
        assert delete_lens_markdown(config, "ai-research") is True
        assert not (config.vault_path / "lenses/ai-research.md").exists()

    def test_delete_nonexistent_returns_false(self, config: WikiConfig):
        assert delete_lens_markdown(config, "nonexistent") is False

    def test_delete_complete(self, config: WikiConfig, sample_lens: Lens):
        save_lens_complete(config, sample_lens)
        yaml_del, md_del = delete_lens_complete(config, "ai-research")
        assert yaml_del is True
        assert md_del is True
        assert not (config.vault_path / "lenses/ai-research.yml").exists()
        assert not (config.vault_path / "lenses/ai-research.md").exists()

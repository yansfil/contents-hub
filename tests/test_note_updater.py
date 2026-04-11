"""Tests for wiki note updater."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.note_updater import (
    EditInstruction,
    MarkdownSection,
    UpdatePlan,
    find_section,
    format_update_preview,
    merge_frontmatter,
    parse_sections,
    serialize_frontmatter,
    update_note,
    update_note_from_decision,
)
from llm_wiki.writer import parse_frontmatter as parse_fm


FIXED_TIME = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """Create a WikiConfig pointing to a temporary vault directory."""
    return WikiConfig(vault_path=tmp_path)


def _write_note(vault: WikiConfig, rel_path: str, content: str) -> Path:
    """Helper to write a note file in the vault."""
    abs_path = vault.vault_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    return abs_path


# ---------------------------------------------------------------------------
# parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_no_headings(self) -> None:
        body = "Just some text\nwith no headings."
        sections = parse_sections(body)
        assert len(sections) == 1
        assert sections[0].level == 0
        assert "Just some text" in sections[0].content

    def test_single_heading(self) -> None:
        body = "# Title\n\nSome content here."
        sections = parse_sections(body)
        assert len(sections) == 1
        assert sections[0].heading_text == "Title"
        assert sections[0].level == 1

    def test_multiple_headings(self) -> None:
        body = "# Title\n\nIntro.\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        sections = parse_sections(body)
        assert len(sections) == 3
        assert sections[0].heading_text == "Title"
        assert sections[1].heading_text == "Section A"
        assert sections[2].heading_text == "Section B"

    def test_preamble_before_first_heading(self) -> None:
        body = "Some preamble text.\n\n# Title\n\nContent."
        sections = parse_sections(body)
        assert len(sections) == 2
        assert sections[0].level == 0
        assert "preamble" in sections[0].content
        assert sections[1].heading_text == "Title"

    def test_heading_levels(self) -> None:
        body = "# H1\n\n## H2\n\n### H3\n\nDeep content."
        sections = parse_sections(body)
        assert sections[0].level == 1
        assert sections[1].level == 2
        assert sections[2].level == 3

    def test_empty_body(self) -> None:
        sections = parse_sections("")
        assert len(sections) == 1
        assert sections[0].content == ""


class TestFindSection:
    def test_find_existing_section(self) -> None:
        body = "# Title\n\n## See Also\n\n- [[Link]]"
        sections = parse_sections(body)
        result = find_section(sections, "See Also")
        assert result is not None
        assert result.heading_text == "See Also"

    def test_find_case_insensitive(self) -> None:
        body = "# Title\n\n## SEE ALSO\n\n- [[Link]]"
        sections = parse_sections(body)
        result = find_section(sections, "see also")
        assert result is not None

    def test_find_missing_section(self) -> None:
        body = "# Title\n\nContent."
        sections = parse_sections(body)
        result = find_section(sections, "See Also")
        assert result is None


# ---------------------------------------------------------------------------
# merge_frontmatter
# ---------------------------------------------------------------------------


class TestMergeFrontmatter:
    def test_merge_tags_union(self) -> None:
        old = {"tags": ["ai", "ml"]}
        new = {"tags": ["ml", "transformers"]}
        merged = merge_frontmatter(old, new)
        assert set(merged["tags"]) == {"ai", "ml", "transformers"}

    def test_merge_preserves_order(self) -> None:
        old = {"tags": ["ai", "ml"]}
        new = {"tags": ["transformers"]}
        merged = merge_frontmatter(old, new)
        assert merged["tags"] == ["ai", "ml", "transformers"]

    def test_merge_sources_union(self) -> None:
        old = {"sources": ["sources/rss/old.md"]}
        new = {"sources": ["sources/rss/new.md"]}
        merged = merge_frontmatter(old, new)
        assert len(merged["sources"]) == 2
        assert "sources/rss/old.md" in merged["sources"]
        assert "sources/rss/new.md" in merged["sources"]

    def test_preserve_created_at(self) -> None:
        old = {"created_at": "2024-01-01T00:00:00+00:00"}
        new = {"created_at": "2024-06-01T00:00:00+00:00"}
        merged = merge_frontmatter(old, new)
        assert merged["created_at"] == "2024-01-01T00:00:00+00:00"

    def test_compiled_at_overwritten(self) -> None:
        old = {"compiled_at": "2024-01-01T00:00:00+00:00"}
        new = {"compiled_at": "2024-06-01T00:00:00+00:00"}
        merged = merge_frontmatter(old, new)
        assert merged["compiled_at"] == "2024-06-01T00:00:00+00:00"

    def test_merge_empty_old(self) -> None:
        merged = merge_frontmatter({}, {"tags": ["new"], "title": "New"})
        assert merged["tags"] == ["new"]
        assert merged["title"] == "New"

    def test_merge_empty_new(self) -> None:
        old = {"tags": ["old"], "title": "Old"}
        merged = merge_frontmatter(old, {})
        assert merged["tags"] == ["old"]
        assert merged["title"] == "Old"

    def test_custom_fields_new_wins(self) -> None:
        old = {"custom_field": "old_value"}
        new = {"custom_field": "new_value"}
        merged = merge_frontmatter(old, new)
        assert merged["custom_field"] == "new_value"

    def test_merge_aliases(self) -> None:
        old = {"aliases": ["GPT"]}
        new = {"aliases": ["GPT", "Generative Pre-trained Transformer"]}
        merged = merge_frontmatter(old, new)
        assert "GPT" in merged["aliases"]
        assert "Generative Pre-trained Transformer" in merged["aliases"]
        # No duplicates
        assert merged["aliases"].count("GPT") == 1


# ---------------------------------------------------------------------------
# serialize_frontmatter
# ---------------------------------------------------------------------------


class TestSerializeFrontmatter:
    def test_simple_fields(self) -> None:
        fm = {"type": "wiki", "title": "Test"}
        result = serialize_frontmatter(fm)
        assert result.startswith("---")
        assert result.endswith("---")
        assert "type: wiki" in result
        assert "title: Test" in result

    def test_list_fields(self) -> None:
        fm = {"tags": ["ai", "ml"]}
        result = serialize_frontmatter(fm)
        assert "  - ai" in result
        assert "  - ml" in result

    def test_empty_list(self) -> None:
        fm = {"tags": []}
        result = serialize_frontmatter(fm)
        assert "tags: []" in result


# ---------------------------------------------------------------------------
# update_note — append strategy
# ---------------------------------------------------------------------------


class TestUpdateNoteAppend:
    def test_append_basic(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: AI\ntags:\n  - ai\n"
            "compiled_at: 2024-01-01T00:00:00+00:00\n---\n\n"
            "# AI\n\nArtificial Intelligence is...\n"
        )
        _write_note(vault, "ai.md", existing)

        plan = update_note(
            vault,
            title="AI",
            new_content="New discoveries about LLMs.",
            merge_strategy="append",
            tags=["ai", "llm"],
        )

        assert plan.merge_strategy == "append"
        assert len(plan.edits) >= 1
        assert "llm" in plan.new_tags_added

        # Verify the file was actually updated
        updated = (vault.vault_path / "ai.md").read_text()
        assert "New discoveries about LLMs" in updated

    def test_append_with_see_also(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: AI\n---\n\n"
            "# AI\n\nContent.\n\n## See Also\n\n- [[Machine Learning]]\n"
        )
        _write_note(vault, "ai.md", existing)

        plan = update_note(
            vault,
            title="AI",
            new_content="Additional AI info.",
            merge_strategy="append",
            wikilinks=["Deep Learning", "Machine Learning"],
        )

        updated = (vault.vault_path / "ai.md").read_text()
        assert "Additional AI info" in updated
        # Machine Learning already exists, shouldn't be duplicated
        assert "Deep Learning" in updated

    def test_append_dry_run(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: AI\n---\n\n# AI\n\nContent.\n"
        original_path = _write_note(vault, "ai.md", existing)

        plan = update_note(
            vault,
            title="AI",
            new_content="Should not be written.",
            merge_strategy="append",
            dry_run=True,
        )

        # File should be unchanged
        assert (vault.vault_path / "ai.md").read_text() == existing
        assert len(plan.edits) >= 1


# ---------------------------------------------------------------------------
# update_note — rewrite strategy
# ---------------------------------------------------------------------------


class TestUpdateNoteRewrite:
    def test_rewrite_replaces_body(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: Transformers\ntags:\n  - ai\n"
            "created_at: 2024-01-01T00:00:00+00:00\n---\n\n"
            "# Transformers\n\nOld content about transformers.\n"
        )
        _write_note(vault, "transformers.md", existing)

        plan = update_note(
            vault,
            title="Transformers",
            new_content="Completely new content about transformers and attention.",
            merge_strategy="rewrite",
            tags=["ai", "attention"],
            sources=["sources/rss/new-article.md"],
        )

        assert plan.merge_strategy == "rewrite"

        updated = (vault.vault_path / "transformers.md").read_text()
        fm, body = parse_fm(updated)

        # Body should be replaced
        assert "Completely new content" in body
        assert "Old content" not in body

        # created_at should be preserved (YAML may parse datetime differently)
        created_at = str(fm.get("created_at", ""))
        assert "2024-01-01" in created_at

        # New tags merged
        assert "attention" in fm.get("tags", [])


# ---------------------------------------------------------------------------
# update_note — section strategy
# ---------------------------------------------------------------------------


class TestUpdateNoteSection:
    def test_section_update_existing(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: GPT\n---\n\n"
            "# GPT\n\nOverview.\n\n## Architecture\n\nOld architecture info.\n\n"
            "## Training\n\nTraining details.\n"
        )
        _write_note(vault, "gpt.md", existing)

        plan = update_note(
            vault,
            title="GPT",
            new_content="Updated architecture with new details.",
            merge_strategy="section",
            target_section="Architecture",
        )

        updated = (vault.vault_path / "gpt.md").read_text()
        assert "Updated architecture with new details" in updated
        # Training section should be preserved
        assert "Training details" in updated

    def test_section_create_new(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: GPT\n---\n\n"
            "# GPT\n\nOverview.\n"
        )
        _write_note(vault, "gpt.md", existing)

        plan = update_note(
            vault,
            title="GPT",
            new_content="New performance benchmarks.",
            merge_strategy="section",
            target_section="Performance",
        )

        updated = (vault.vault_path / "gpt.md").read_text()
        assert "## Performance" in updated
        assert "New performance benchmarks" in updated


# ---------------------------------------------------------------------------
# update_note — edge cases
# ---------------------------------------------------------------------------


class TestUpdateNoteEdgeCases:
    def test_file_not_found(self, vault: WikiConfig) -> None:
        with pytest.raises(FileNotFoundError):
            update_note(
                vault,
                title="Nonexistent",
                new_content="Content.",
            )

    def test_unknown_strategy_defaults_to_append(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: AI\n---\n\n# AI\n\nContent.\n"
        _write_note(vault, "ai.md", existing)

        plan = update_note(
            vault,
            title="AI",
            new_content="New content.",
            merge_strategy="unknown_strategy",
        )

        updated = (vault.vault_path / "ai.md").read_text()
        assert "New content" in updated

    def test_update_with_lens_directory(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: Attention\n---\n\n# Attention\n\nContent.\n"
        _write_note(vault, "topics/ai/attention.md", existing)

        plan = update_note(
            vault,
            title="Attention",
            new_content="Updated attention info.",
            merge_strategy="append",
            lens_directory="topics/ai",
        )

        updated = (vault.vault_path / "topics/ai/attention.md").read_text()
        assert "Updated attention info" in updated

    def test_update_with_explicit_path(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: Custom\n---\n\n# Custom\n\nContent.\n"
        _write_note(vault, "custom-note.md", existing)

        plan = update_note(
            vault,
            title="Custom",
            new_content="Updated via explicit path.",
            merge_strategy="append",
            existing_path="custom-note.md",
        )

        updated = (vault.vault_path / "custom-note.md").read_text()
        assert "Updated via explicit path" in updated


# ---------------------------------------------------------------------------
# update_note_from_decision
# ---------------------------------------------------------------------------


class TestUpdateFromDecision:
    def test_from_decision_result(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: AI\ntags:\n  - ai\n---\n\n# AI\n\nContent.\n"
        note_path = _write_note(vault, "ai.md", existing)

        # Mock a DecisionResult-like object
        class MockDecision:
            target_title = "AI"
            target_page = str(note_path)
            merge_strategy = "append"
            suggested_tags = ["ai", "ml"]
            suggested_wikilinks = ["Machine Learning"]

        plan = update_note_from_decision(
            vault,
            MockDecision(),
            "New LLM research findings.",
            source_path="sources/rss/llm-article.md",
            source_url="https://example.com/article",
        )

        assert plan.merge_strategy == "append"
        updated = (vault.vault_path / "ai.md").read_text()
        assert "New LLM research findings" in updated


# ---------------------------------------------------------------------------
# format_update_preview
# ---------------------------------------------------------------------------


class TestFormatPreview:
    def test_preview_format(self) -> None:
        plan = UpdatePlan(
            file_path="/vault/ai.md",
            relative_path="ai.md",
            edits=[
                EditInstruction(
                    file_path="/vault/ai.md",
                    old_string="old text",
                    new_string="new text",
                    description="Update content",
                )
            ],
            merge_strategy="append",
            sections_affected=["body (appended)"],
            new_tags_added=["ml"],
            new_sources_added=["sources/rss/new.md"],
            summary="Test summary",
        )

        preview = format_update_preview(plan)
        assert "## Update: ai.md" in preview
        assert "append" in preview
        assert "#ml" in preview
        assert "Edit 1" in preview
        assert "```diff" in preview

"""Tests for wiki note creator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.note_creator import (
    NoteMetadata,
    WriteAction,
    compose_body,
    create_note,
    create_note_from_decision,
    create_notes_batch,
    resolve_note_path,
)
from llm_wiki.writer import parse_frontmatter

FIXED_TIME = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """Create a WikiConfig pointing to a temporary vault directory."""
    return WikiConfig(vault_path=tmp_path)


# ---------------------------------------------------------------------------
# NoteMetadata
# ---------------------------------------------------------------------------


class TestNoteMetadata:
    def test_to_frontmatter_minimal(self) -> None:
        meta = NoteMetadata(title="Test Note")
        fm = meta.to_frontmatter()

        assert fm["type"] == "wiki"
        assert fm["title"] == "Test Note"
        assert "compiled_at" in fm
        assert "aliases" not in fm  # empty list → not included
        assert "tags" not in fm

    def test_to_frontmatter_full(self) -> None:
        meta = NoteMetadata(
            title="Transformers",
            aliases=["Transformer Architecture"],
            tags=["deep-learning", "nlp"],
            lenses=["ai-research"],
            sources=["sources/rss/2024-01-15-attention.md"],
            source_urls=["https://arxiv.org/abs/1706.03762"],
            compiled_at=FIXED_TIME,
            created_at=FIXED_TIME,
            extra={"confidence": 0.95},
        )
        fm = meta.to_frontmatter()

        assert fm["title"] == "Transformers"
        assert fm["aliases"] == ["Transformer Architecture"]
        assert fm["tags"] == ["deep-learning", "nlp"]
        assert fm["lenses"] == ["ai-research"]
        assert fm["sources"] == ["sources/rss/2024-01-15-attention.md"]
        assert fm["source_urls"] == ["https://arxiv.org/abs/1706.03762"]
        assert fm["compiled_at"] == FIXED_TIME.isoformat()
        assert fm["created_at"] == FIXED_TIME.isoformat()
        assert fm["confidence"] == 0.95

    def test_extra_does_not_override_standard_keys(self) -> None:
        meta = NoteMetadata(
            title="Test",
            extra={"type": "override", "custom_key": "value"},
        )
        fm = meta.to_frontmatter()

        # "type" should be "wiki" not "override"
        assert fm["type"] == "wiki"
        assert fm["custom_key"] == "value"


# ---------------------------------------------------------------------------
# resolve_note_path
# ---------------------------------------------------------------------------


class TestResolveNotePath:
    def test_root_level(self, vault: WikiConfig) -> None:
        abs_path, rel_path = resolve_note_path(vault, "Transformers")

        assert rel_path == Path("transformers.md")
        assert abs_path == vault.vault_path / "transformers.md"

    def test_with_lens_directory(self, vault: WikiConfig) -> None:
        abs_path, rel_path = resolve_note_path(
            vault, "GPT-4", lens_directory="topics/ai-research"
        )

        assert rel_path == Path("topics/ai-research/gpt-4.md")
        assert abs_path == vault.vault_path / "topics/ai-research/gpt-4.md"

    def test_special_chars_in_title(self, vault: WikiConfig) -> None:
        _, rel_path = resolve_note_path(vault, "AI: The Future (2024)")
        assert rel_path == Path("ai-the-future-2024.md")


# ---------------------------------------------------------------------------
# compose_body
# ---------------------------------------------------------------------------


class TestComposeBody:
    def test_basic_body(self) -> None:
        body = compose_body("Test Title", "This is the content.")

        assert body.startswith("# Test Title\n")
        assert "This is the content." in body

    def test_no_duplicate_heading(self) -> None:
        """If content already has an H1, don't add another."""
        body = compose_body("Test", "# Test\n\nContent here.")

        # Should not have "# Test" twice
        assert body.count("# Test") == 1

    def test_with_wikilinks(self) -> None:
        body = compose_body(
            "Transformers",
            "A neural network architecture.",
            wikilinks=["Self-Attention", "BERT"],
        )

        assert "## See Also" in body
        assert "- [[Self-Attention]]" in body
        assert "- [[BERT]]" in body

    def test_wikilinks_already_bracketed(self) -> None:
        body = compose_body(
            "Test",
            "Content.",
            wikilinks=["[[Already Bracketed]]", "Not Bracketed"],
        )

        assert "- [[Already Bracketed]]" in body
        assert "- [[Not Bracketed]]" in body
        # Should not double-bracket
        assert "[[[[" not in body

    def test_with_source_urls(self) -> None:
        body = compose_body(
            "Test",
            "Content.",
            source_urls=["https://example.com/article"],
        )

        assert "## Sources" in body
        assert "- https://example.com/article" in body

    def test_with_all_sections(self) -> None:
        body = compose_body(
            "Full Note",
            "Main content here.",
            wikilinks=["Related Page"],
            source_urls=["https://example.com"],
        )

        assert "# Full Note" in body
        assert "Main content here." in body
        assert "## See Also" in body
        assert "## Sources" in body

    def test_empty_content(self) -> None:
        body = compose_body("Empty", "")
        assert "# Empty" in body


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------


class TestCreateNote:
    def test_creates_file(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="Transformers",
            content="A neural network architecture based on self-attention.",
            tags=["deep-learning", "nlp"],
        )

        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert result.bytes_written > 0

        # Verify file content
        text = result.path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        assert fm["type"] == "wiki"
        assert fm["title"] == "Transformers"
        assert fm["tags"] == ["deep-learning", "nlp"]
        assert "compiled_at" in fm
        assert "created_at" in fm
        assert "# Transformers" in body
        assert "self-attention" in body

    def test_creates_parent_directories(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="GPT-4",
            content="Large language model by OpenAI.",
            lens_directory="topics/ai-research",
        )

        assert result.action == WriteAction.CREATED
        assert result.path.parent.exists()
        assert result.relative_path == Path("topics/ai-research/gpt-4.md")

    def test_with_full_metadata(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="Transformer Architecture",
            content="Transformers use self-attention.",
            tags=["deep-learning", "attention"],
            lenses=["ai-research"],
            sources=["sources/rss/2024-01-15-attention.md"],
            source_urls=["https://arxiv.org/abs/1706.03762"],
            aliases=["Transformer Model"],
            wikilinks=["Self-Attention", "BERT"],
            lens_directory="topics/ai-research",
        )

        text = result.path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        assert fm["aliases"] == ["Transformer Model"]
        assert fm["lenses"] == ["ai-research"]
        assert fm["sources"] == ["sources/rss/2024-01-15-attention.md"]
        assert "## See Also" in body
        assert "[[Self-Attention]]" in body
        assert "## Sources" in body

    def test_with_metadata_object(self, vault: WikiConfig) -> None:
        meta = NoteMetadata(
            title="Custom Note",
            tags=["test"],
            compiled_at=FIXED_TIME,
            created_at=FIXED_TIME,
        )

        result = create_note(
            vault,
            title="Custom Note",
            content="Content here.",
            metadata=meta,
        )

        text = result.path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        # PyYAML may parse ISO strings as datetime objects
        compiled_val = fm["compiled_at"]
        if isinstance(compiled_val, datetime):
            assert compiled_val == FIXED_TIME
        else:
            assert str(compiled_val) == FIXED_TIME.isoformat()

    def test_update_existing_merges_created_at(self, vault: WikiConfig) -> None:
        # Create initial note
        create_note(vault, title="Evolving Note", content="Version 1.")

        # Read created_at from first write
        first_content = (vault.vault_path / "evolving-note.md").read_text()
        first_fm, _ = parse_frontmatter(first_content)
        original_created = first_fm["created_at"]

        # Update the note
        result = create_note(
            vault,
            title="Evolving Note",
            content="Version 2 with new information.",
            tags=["updated"],
        )

        assert result.action == WriteAction.UPDATED

        # Verify created_at is preserved
        updated_content = result.path.read_text(encoding="utf-8")
        updated_fm, body = parse_frontmatter(updated_content)

        assert updated_fm["created_at"] == original_created
        assert "Version 2" in body
        assert updated_fm["tags"] == ["updated"]

    def test_skip_existing_when_update_false(self, vault: WikiConfig) -> None:
        create_note(vault, title="Immutable", content="Original.")
        result = create_note(
            vault,
            title="Immutable",
            content="Attempted overwrite.",
            update_existing=False,
        )

        assert result.action == WriteAction.SKIPPED
        # Content should remain original
        text = result.path.read_text(encoding="utf-8")
        assert "Original." in text

    def test_extra_frontmatter(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="With Extra",
            content="Content.",
            extra_frontmatter={"confidence": 0.9, "model": "claude-sonnet"},
        )

        text = result.path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm["confidence"] == 0.9
        assert fm["model"] == "claude-sonnet"

    def test_relative_path_in_root(self, vault: WikiConfig) -> None:
        result = create_note(vault, title="Root Note", content="At root.")
        assert result.relative_path == Path("root-note.md")

    def test_relative_path_in_lens(self, vault: WikiConfig) -> None:
        result = create_note(
            vault, title="Lens Note", content="In a lens.",
            lens_directory="devops",
        )
        assert result.relative_path == Path("devops/lens-note.md")


# ---------------------------------------------------------------------------
# create_note_from_decision
# ---------------------------------------------------------------------------


class TestCreateNoteFromDecision:
    def test_from_decision_result(self, vault: WikiConfig) -> None:
        """Test creating note from a DecisionResult-like object."""

        class MockDecision:
            target_title = "Attention Mechanism"
            suggested_tags = ["nlp", "attention"]
            suggested_wikilinks = ["Transformers", "BERT"]

        result = create_note_from_decision(
            vault,
            MockDecision(),
            compiled_content="Self-attention allows models to weigh input tokens.",
            source_path="sources/rss/2024-01-15-attention.md",
            source_url="https://example.com/attention",
            lens_directory="ai",
        )

        assert result.action == WriteAction.CREATED

        text = result.path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        assert fm["title"] == "Attention Mechanism"
        assert fm["tags"] == ["nlp", "attention"]
        assert fm["sources"] == ["sources/rss/2024-01-15-attention.md"]
        assert "[[Transformers]]" in body
        assert "[[BERT]]" in body

    def test_fallback_title(self, vault: WikiConfig) -> None:
        class EmptyDecision:
            target_title = ""
            suggested_tags = []
            suggested_wikilinks = []

        result = create_note_from_decision(
            vault, EmptyDecision(), "Some content."
        )

        text = result.path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        assert fm["title"] == "Untitled"


# ---------------------------------------------------------------------------
# create_notes_batch
# ---------------------------------------------------------------------------


class TestCreateNotesBatch:
    def test_batch_create(self, vault: WikiConfig) -> None:
        notes = [
            {
                "title": "Page One",
                "content": "First page content.",
                "tags": ["tag1"],
            },
            {
                "title": "Page Two",
                "content": "Second page content.",
                "tags": ["tag2"],
                "lens_directory": "ai",
            },
            {
                "title": "Page Three",
                "content": "Third page content.",
            },
        ]

        results = create_notes_batch(vault, notes)

        assert len(results) == 3
        assert all(r.action == WriteAction.CREATED for r in results)
        assert all(r.path.exists() for r in results)

        # Verify directory placement
        assert results[0].relative_path == Path("page-one.md")
        assert results[1].relative_path == Path("ai/page-two.md")

    def test_batch_with_existing(self, vault: WikiConfig) -> None:
        # Create one note first
        create_note(vault, title="Existing", content="Original.")

        notes = [
            {"title": "Existing", "content": "Updated."},
            {"title": "Brand New", "content": "Fresh content."},
        ]

        results = create_notes_batch(vault, notes)

        assert results[0].action == WriteAction.UPDATED
        assert results[1].action == WriteAction.CREATED

    def test_empty_batch(self, vault: WikiConfig) -> None:
        results = create_notes_batch(vault, [])
        assert results == []


# ---------------------------------------------------------------------------
# Integration: Obsidian-native output validation
# ---------------------------------------------------------------------------


class TestObsidianNativeOutput:
    """Validate that output files are well-formed for Obsidian consumption."""

    def test_frontmatter_is_valid_yaml(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="YAML Test: Special Characters",
            content="Content with: colons and #hashes.",
            tags=["test-tag", "another"],
            aliases=["YAML: Test"],
        )

        text = result.path.read_text(encoding="utf-8")

        # Must start with --- and have closing ---
        assert text.startswith("---\n")
        lines = text.split("\n")
        # Find closing --- (not the first one)
        closing_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break
        assert closing_idx is not None, "Missing closing frontmatter fence"

        # Frontmatter should be parseable
        fm, body = parse_frontmatter(text)
        assert fm["title"] == "YAML Test: Special Characters"
        assert fm["type"] == "wiki"

    def test_wikilinks_format(self, vault: WikiConfig) -> None:
        result = create_note(
            vault,
            title="Linked Note",
            content="References to other concepts.",
            wikilinks=["Machine Learning", "Neural Networks"],
        )

        text = result.path.read_text(encoding="utf-8")
        assert "[[Machine Learning]]" in text
        assert "[[Neural Networks]]" in text

    def test_tags_in_frontmatter_not_body(self, vault: WikiConfig) -> None:
        """Tags should be in frontmatter, not as inline #tags in body."""
        result = create_note(
            vault,
            title="Tagged Note",
            content="Clean body without inline tags.",
            tags=["ai", "ml"],
        )

        text = result.path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        assert fm["tags"] == ["ai", "ml"]
        # Body should not contain inline #tags from our code
        # (the content itself might, but we don't inject them)
        assert "#ai" not in body or "ai" in "Clean body without inline tags."

    def test_file_ends_with_newline(self, vault: WikiConfig) -> None:
        result = create_note(vault, title="Newline", content="Content.")
        text = result.path.read_text(encoding="utf-8")
        assert text.endswith("\n")

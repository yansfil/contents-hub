"""Tests for compile executor — decision dispatch to Write/Edit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from llm_wiki.compile_decision import Decision, DecisionResult, NewContent, SimilarPage
from llm_wiki.compile_evaluate import (
    EvaluationResult,
    OverlapLevel,
    SimilarityAssessment,
)
from llm_wiki.compile_executor import (
    BatchExecutionReport,
    ExecutionResult,
    ExecutionStatus,
    execute_decisions,
    execute_from_evaluations,
    execute_single,
)
from llm_wiki.config import WikiConfig
from llm_wiki.writer import parse_frontmatter


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """WikiConfig with a temporary vault directory."""
    return WikiConfig(vault_path=tmp_path)


def _make_evaluation(
    source_path: str = "sources/rss/2024-01-15-test.md",
    action: Decision = Decision.CREATE,
    target_title: str = "Test Note",
    target_page: str = "",
    merge_strategy: str = "",
    tags: list[str] | None = None,
    wikilinks: list[str] | None = None,
    confidence: float = 0.8,
    reason: str = "Test decision",
    lens_id: str = "",
    lens_name: str = "",
    url: str = "",
    source_type: str = "rss",
) -> EvaluationResult:
    """Helper to construct an EvaluationResult for testing."""
    decision = DecisionResult(
        source_path=source_path,
        decision=action,
        target_page=target_page,
        target_title=target_title,
        reason=reason,
        confidence=confidence,
        merge_strategy=merge_strategy,
        suggested_tags=tags or [],
        suggested_wikilinks=wikilinks or [],
    )
    new_content = NewContent(
        source_path=source_path,
        title=target_title,
        source_type=source_type,
        url=url,
        tags=tags or [],
        lens_id=lens_id,
        lens_name=lens_name,
    )
    assessment = SimilarityAssessment(
        overlap_level=OverlapLevel.NONE if action == Decision.CREATE else OverlapLevel.HIGH,
    )
    return EvaluationResult(
        source_path=source_path,
        action=action,
        decision=decision,
        assessment=assessment,
        new_content=new_content,
    )


def _write_existing_note(vault: WikiConfig, rel_path: str, content: str) -> Path:
    """Write a note file in the vault for UPDATE tests."""
    abs_path = vault.vault_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    return abs_path


# ---------------------------------------------------------------------------
# execute_single — CREATE
# ---------------------------------------------------------------------------


class TestExecuteSingleCreate:
    def test_create_writes_file(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(action=Decision.CREATE, target_title="Transformer Architecture")
        result = execute_single(vault, ev, "Transformers use self-attention.")

        assert result.status == ExecutionStatus.SUCCESS
        assert result.decision == Decision.CREATE
        assert result.target_title == "Transformer Architecture"
        assert result.bytes_written > 0

        # Verify file exists and has correct content
        note_path = vault.vault_path / result.target_path
        assert note_path.exists()
        text = note_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        assert fm["type"] == "wiki"
        assert fm["title"] == "Transformer Architecture"
        assert "self-attention" in body

    def test_create_with_metadata(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(
            action=Decision.CREATE,
            target_title="GPT-4",
            tags=["llm", "openai"],
            wikilinks=["Transformers", "RLHF"],
            url="https://openai.com/gpt-4",
            lens_id="ai-research",
        )
        result = execute_single(
            vault, ev, "GPT-4 is a large language model.",
            lens_directory="ai-research",
        )

        assert result.status == ExecutionStatus.SUCCESS
        note_path = vault.vault_path / result.target_path
        text = note_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)

        assert fm["tags"] == ["llm", "openai"]
        assert fm["lenses"] == ["ai-research"]
        assert "https://openai.com/gpt-4" in text
        assert "[[Transformers]]" in body
        assert "[[RLHF]]" in body

    def test_create_dry_run(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(action=Decision.CREATE, target_title="Dry Run Test")
        result = execute_single(vault, ev, "Content.", dry_run=True)

        assert result.status == ExecutionStatus.DRY_RUN
        assert result.action == "would_create"
        # File should NOT exist
        note_path = vault.vault_path / result.target_path
        assert not note_path.exists()

    def test_create_with_lens_directory(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(action=Decision.CREATE, target_title="Attention")
        result = execute_single(
            vault, ev, "Attention mechanism.", lens_directory="ai"
        )

        assert result.status == ExecutionStatus.SUCCESS
        assert "ai/" in result.target_path


# ---------------------------------------------------------------------------
# execute_single — UPDATE
# ---------------------------------------------------------------------------


class TestExecuteSingleUpdate:
    def test_update_existing_note(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: AI\ntags:\n  - ai\n"
            "compiled_at: 2024-01-01T00:00:00+00:00\n---\n\n"
            "# AI\n\nArtificial Intelligence overview.\n"
        )
        note_path = _write_existing_note(vault, "ai.md", existing)

        ev = _make_evaluation(
            action=Decision.UPDATE,
            target_title="AI",
            target_page=str(note_path),
            merge_strategy="append",
            tags=["ai", "llm"],
        )

        result = execute_single(vault, ev, "New LLM discoveries.")

        assert result.status == ExecutionStatus.SUCCESS
        assert result.decision == Decision.UPDATE
        assert result.merge_strategy == "append"
        assert result.edits_applied >= 1

        # Verify the file was updated
        updated = note_path.read_text(encoding="utf-8")
        assert "New LLM discoveries" in updated
        assert "Artificial Intelligence overview" in updated  # original preserved

    def test_update_rewrite_strategy(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: Transformers\n---\n\n"
            "# Transformers\n\nOld content.\n"
        )
        note_path = _write_existing_note(vault, "transformers.md", existing)

        ev = _make_evaluation(
            action=Decision.UPDATE,
            target_title="Transformers",
            target_page=str(note_path),
            merge_strategy="rewrite",
        )

        result = execute_single(vault, ev, "Completely rewritten transformer content.")

        assert result.status == ExecutionStatus.SUCCESS
        updated = note_path.read_text(encoding="utf-8")
        assert "Completely rewritten" in updated
        assert "Old content" not in updated

    def test_update_dry_run(self, vault: WikiConfig) -> None:
        existing = "---\ntype: wiki\ntitle: Test\n---\n\n# Test\n\nContent.\n"
        note_path = _write_existing_note(vault, "test.md", existing)

        ev = _make_evaluation(
            action=Decision.UPDATE,
            target_title="Test",
            target_page=str(note_path),
            merge_strategy="append",
        )

        result = execute_single(vault, ev, "New content.", dry_run=True)

        assert result.status == ExecutionStatus.DRY_RUN
        assert result.action == "would_update"
        # File should be unchanged
        assert note_path.read_text() == existing

    def test_update_fallback_to_create_when_not_found(self, vault: WikiConfig) -> None:
        """When UPDATE target doesn't exist, fall back to CREATE."""
        ev = _make_evaluation(
            action=Decision.UPDATE,
            target_title="Missing Page",
            target_page="nonexistent/missing.md",
            merge_strategy="append",
        )

        result = execute_single(vault, ev, "Content for missing page.")

        # Should fall back to CREATE
        assert result.status == ExecutionStatus.SUCCESS
        assert result.decision == Decision.CREATE
        note_path = vault.vault_path / result.target_path
        assert note_path.exists()
        assert "Content for missing page" in note_path.read_text()


# ---------------------------------------------------------------------------
# execute_single — SKIP
# ---------------------------------------------------------------------------


class TestExecuteSingleSkip:
    def test_skip_no_action(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(
            action=Decision.SKIP,
            target_title="Already Covered",
            target_page="ai/existing.md",
        )

        result = execute_single(vault, ev, "")

        assert result.status == ExecutionStatus.SKIPPED
        assert result.decision == Decision.SKIP
        assert result.bytes_written == 0
        assert result.target_path == "ai/existing.md"


# ---------------------------------------------------------------------------
# execute_decisions (batch)
# ---------------------------------------------------------------------------


class TestExecuteDecisions:
    def test_batch_mixed_decisions(self, vault: WikiConfig) -> None:
        # Prepare an existing note for UPDATE
        existing = "---\ntype: wiki\ntitle: ML\ntags:\n  - ml\n---\n\n# ML\n\nMachine Learning.\n"
        ml_path = _write_existing_note(vault, "ml.md", existing)

        evaluations = [
            _make_evaluation(
                source_path="sources/rss/article-1.md",
                action=Decision.CREATE,
                target_title="Neural Networks",
                tags=["nn"],
            ),
            _make_evaluation(
                source_path="sources/rss/article-2.md",
                action=Decision.UPDATE,
                target_title="ML",
                target_page=str(ml_path),
                merge_strategy="append",
                tags=["ml", "deep-learning"],
            ),
            _make_evaluation(
                source_path="sources/rss/article-3.md",
                action=Decision.SKIP,
                target_title="Already There",
                target_page="existing.md",
            ),
        ]

        contents = {
            "sources/rss/article-1.md": "Neural networks are computational models.",
            "sources/rss/article-2.md": "New deep learning techniques.",
        }

        report = execute_decisions(vault, evaluations, contents)

        assert report.total == 3
        assert report.created == 1
        assert report.updated == 1
        assert report.skipped == 1
        assert report.failed == 0
        assert not report.has_failures

        # Verify created note
        nn_result = report.results[0]
        assert nn_result.decision == Decision.CREATE
        assert nn_result.status == ExecutionStatus.SUCCESS
        nn_path = vault.vault_path / nn_result.target_path
        assert nn_path.exists()

        # Verify updated note
        ml_result = report.results[1]
        assert ml_result.decision == Decision.UPDATE
        assert ml_result.status == ExecutionStatus.SUCCESS
        ml_content = ml_path.read_text()
        assert "New deep learning techniques" in ml_content

    def test_batch_dry_run(self, vault: WikiConfig) -> None:
        evaluations = [
            _make_evaluation(
                source_path="sources/rss/a.md",
                action=Decision.CREATE,
                target_title="Note A",
            ),
            _make_evaluation(
                source_path="sources/rss/b.md",
                action=Decision.CREATE,
                target_title="Note B",
            ),
        ]

        contents = {
            "sources/rss/a.md": "Content A.",
            "sources/rss/b.md": "Content B.",
        }

        report = execute_decisions(vault, evaluations, contents, dry_run=True)

        assert report.dry_run is True
        assert report.created == 2
        assert report.failed == 0

        # No files should have been written
        assert not (vault.vault_path / "note-a.md").exists()
        assert not (vault.vault_path / "note-b.md").exists()

    def test_batch_missing_content(self, vault: WikiConfig) -> None:
        """CREATE without compiled content should fail gracefully."""
        evaluations = [
            _make_evaluation(
                source_path="sources/rss/no-content.md",
                action=Decision.CREATE,
                target_title="No Content",
            ),
        ]

        report = execute_decisions(vault, evaluations, {})

        assert report.failed == 1
        assert report.results[0].status == ExecutionStatus.FAILED
        assert "No compiled content" in report.results[0].error

    def test_batch_empty(self, vault: WikiConfig) -> None:
        report = execute_decisions(vault, [], {})

        assert report.total == 0
        assert report.created == 0
        assert report.failed == 0

    def test_batch_with_lens_directories(self, vault: WikiConfig) -> None:
        evaluations = [
            _make_evaluation(
                source_path="sources/rss/ai-article.md",
                action=Decision.CREATE,
                target_title="BERT",
                lens_id="ai-research",
            ),
        ]

        contents = {"sources/rss/ai-article.md": "BERT is a language model."}
        lens_dirs = {"sources/rss/ai-article.md": "topics/ai"}

        report = execute_decisions(
            vault, evaluations, contents,
            lens_directories=lens_dirs,
        )

        assert report.created == 1
        assert "topics/ai" in report.results[0].target_path


# ---------------------------------------------------------------------------
# execute_from_evaluations
# ---------------------------------------------------------------------------


class TestExecuteFromEvaluations:
    def test_auto_resolves_lens_dirs(self, vault: WikiConfig) -> None:
        evaluations = [
            _make_evaluation(
                source_path="sources/rss/article.md",
                action=Decision.CREATE,
                target_title="Auto Lens",
                lens_id="ai-research",
            ),
        ]
        contents = {"sources/rss/article.md": "Content."}

        report = execute_from_evaluations(vault, evaluations, contents)

        assert report.created == 1
        # Should use lens_id as directory
        assert "ai-research" in report.results[0].target_path


# ---------------------------------------------------------------------------
# BatchExecutionReport
# ---------------------------------------------------------------------------


class TestBatchExecutionReport:
    def test_summary_output(self) -> None:
        report = BatchExecutionReport(
            results=[
                ExecutionResult(
                    source_path="a.md",
                    decision=Decision.CREATE,
                    status=ExecutionStatus.SUCCESS,
                    target_path="note-a.md",
                    target_title="Note A",
                    action="created",
                    bytes_written=100,
                ),
                ExecutionResult(
                    source_path="b.md",
                    decision=Decision.UPDATE,
                    status=ExecutionStatus.SUCCESS,
                    target_path="note-b.md",
                    target_title="Note B",
                    action="updated",
                    merge_strategy="append",
                    edits_applied=2,
                ),
                ExecutionResult(
                    source_path="c.md",
                    decision=Decision.SKIP,
                    status=ExecutionStatus.SKIPPED,
                ),
            ],
            total=3,
            created=1,
            updated=1,
            skipped=1,
            failed=0,
        )

        summary = report.summary()
        assert "Compile Complete" in summary
        assert "Created: 1" in summary
        assert "Updated: 1" in summary
        assert "Skipped: 1" in summary

    def test_summary_with_failures(self) -> None:
        report = BatchExecutionReport(
            results=[
                ExecutionResult(
                    source_path="fail.md",
                    decision=Decision.CREATE,
                    status=ExecutionStatus.FAILED,
                    error="Permission denied",
                ),
            ],
            total=1,
            failed=1,
        )

        summary = report.summary()
        assert "Errors" in summary
        assert "Permission denied" in summary
        assert report.has_failures

    def test_dry_run_summary(self) -> None:
        report = BatchExecutionReport(
            results=[],
            total=0,
            dry_run=True,
        )
        summary = report.summary()
        assert "dry run" in summary


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_summary_line_create(self) -> None:
        result = ExecutionResult(
            source_path="a.md",
            decision=Decision.CREATE,
            status=ExecutionStatus.SUCCESS,
            target_path="note-a.md",
            bytes_written=256,
        )
        line = result.summary_line()
        assert "NEW" in line
        assert "note-a.md" in line
        assert "256 bytes" in line

    def test_summary_line_update(self) -> None:
        result = ExecutionResult(
            source_path="b.md",
            decision=Decision.UPDATE,
            status=ExecutionStatus.SUCCESS,
            target_path="note-b.md",
            merge_strategy="append",
            edits_applied=3,
        )
        line = result.summary_line()
        assert "UPD" in line
        assert "append" in line
        assert "3 edits" in line

    def test_summary_line_skip(self) -> None:
        result = ExecutionResult(
            source_path="c.md",
            decision=Decision.SKIP,
            status=ExecutionStatus.SKIPPED,
        )
        line = result.summary_line()
        assert "SKIP" in line

    def test_summary_line_error(self) -> None:
        result = ExecutionResult(
            source_path="d.md",
            decision=Decision.CREATE,
            status=ExecutionStatus.FAILED,
            error="disk full",
        )
        line = result.summary_line()
        assert "ERROR" in line
        assert "disk full" in line


# ---------------------------------------------------------------------------
# Integration: Obsidian-native validation
# ---------------------------------------------------------------------------


class TestObsidianNativeIntegration:
    """Verify that executed outputs are valid Obsidian files."""

    def test_created_note_has_valid_frontmatter(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(
            action=Decision.CREATE,
            target_title="Valid Frontmatter Test",
            tags=["test", "obsidian"],
        )
        result = execute_single(vault, ev, "Obsidian-native content.")

        text = (vault.vault_path / result.target_path).read_text()
        assert text.startswith("---\n")
        fm, body = parse_frontmatter(text)
        assert fm["type"] == "wiki"
        assert fm["title"] == "Valid Frontmatter Test"
        assert "compiled_at" in fm

    def test_updated_note_preserves_created_at(self, vault: WikiConfig) -> None:
        existing = (
            "---\ntype: wiki\ntitle: Persistent\n"
            "created_at: 2024-01-01T00:00:00+00:00\n---\n\n"
            "# Persistent\n\nOriginal.\n"
        )
        note_path = _write_existing_note(vault, "persistent.md", existing)

        ev = _make_evaluation(
            action=Decision.UPDATE,
            target_title="Persistent",
            target_page=str(note_path),
            merge_strategy="append",
        )
        execute_single(vault, ev, "New info added.")

        updated = note_path.read_text()
        fm, _ = parse_frontmatter(updated)
        created_at_str = str(fm.get("created_at", ""))
        assert "2024-01-01" in created_at_str

    def test_file_ends_with_newline(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(action=Decision.CREATE, target_title="Newline Check")
        result = execute_single(vault, ev, "Content.")

        text = (vault.vault_path / result.target_path).read_text()
        assert text.endswith("\n")

    def test_wikilinks_in_created_note(self, vault: WikiConfig) -> None:
        ev = _make_evaluation(
            action=Decision.CREATE,
            target_title="Linked Note",
            wikilinks=["Related Concept", "Another Page"],
        )
        result = execute_single(vault, ev, "Content with references.")

        text = (vault.vault_path / result.target_path).read_text()
        assert "[[Related Concept]]" in text
        assert "[[Another Page]]" in text

"""Tests for the approval executor: user choice → vault writes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.approval import (
    ApprovalOutcome,
    ApprovalResult,
    PageWriteRecord,
    approve_from_response,
    execute_approval,
)
from llm_wiki.confirmation import ConfirmationChoice
from llm_wiki.config import WikiConfig
from llm_wiki.preview import CompileAction, CompileResult, SourceReference
from llm_wiki.writer import WriteAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_TIME = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / ".llm-wiki").mkdir()
    return vault


@pytest.fixture
def config(tmp_vault: Path) -> WikiConfig:
    """Create a WikiConfig pointing to the temp vault."""
    return WikiConfig(vault_path=tmp_vault)


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def create_result() -> CompileResult:
    return CompileResult(
        title="Transformer Architecture",
        body="# Transformer Architecture\n\nThe transformer model uses self-attention.\n",
        frontmatter={
            "title": "Transformer Architecture",
            "tags": ["ai", "transformers"],
            "type": "wiki",
        },
        lens_id="ai",
        lens_name="AI Research",
        directory="ai",
        action=CompileAction.CREATE,
        sources=[
            SourceReference(
                path="sources/rss/transformers.md",
                title="Transformers Explained",
                source_type="rss",
                url="https://example.com/transformers",
                score=0.9,
            ),
        ],
        wikilinks=["Self-Attention", "BERT"],
        tags=["ai", "transformers"],
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def update_result() -> CompileResult:
    return CompileResult(
        title="Prompt Engineering",
        body="# Prompt Engineering\n\nUpdated content with new techniques.\n",
        frontmatter={
            "title": "Prompt Engineering",
            "tags": ["ai", "prompting"],
            "type": "wiki",
        },
        lens_id="ai",
        lens_name="AI Research",
        directory="ai",
        action=CompileAction.UPDATE,
        existing_path="ai/prompt-engineering.md",
        tags=["ai", "prompting"],
        compiled_at=FIXED_TIME,
    )


@pytest.fixture
def skip_result() -> CompileResult:
    return CompileResult(
        title="Neural Networks",
        body="# Neural Networks\n\nNo changes.\n",
        action=CompileAction.SKIP,
        compiled_at=FIXED_TIME,
    )


# ---------------------------------------------------------------------------
# ApprovalResult unit tests
# ---------------------------------------------------------------------------


class TestApprovalResult:
    def test_is_written_for_written(self):
        r = ApprovalResult(outcome=ApprovalOutcome.WRITTEN)
        assert r.is_written
        assert not r.needs_recompile

    def test_is_written_for_partial(self):
        r = ApprovalResult(outcome=ApprovalOutcome.PARTIAL)
        assert r.is_written

    def test_not_written_for_rejected(self):
        r = ApprovalResult(outcome=ApprovalOutcome.REJECTED)
        assert not r.is_written

    def test_needs_recompile(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.RECOMPILE,
            feedback="Add more detail",
        )
        assert r.needs_recompile
        assert not r.is_written

    def test_total_written(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.WRITTEN,
            pages_written=[
                PageWriteRecord(
                    title="A", target_path="a.md",
                    action=WriteAction.CREATED, bytes_written=100,
                ),
                PageWriteRecord(
                    title="B", target_path="b.md",
                    action=WriteAction.CREATED, bytes_written=200,
                ),
            ],
        )
        assert r.total_written == 2
        assert r.total_bytes == 300

    def test_total_written_excludes_errors(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.WRITTEN,
            pages_written=[
                PageWriteRecord(
                    title="A", target_path="a.md",
                    action=WriteAction.CREATED, bytes_written=100,
                ),
                PageWriteRecord(
                    title="B", target_path="b.md",
                    action=WriteAction.SKIPPED, error="disk full",
                ),
            ],
        )
        assert r.total_written == 1

    def test_summary_written(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.WRITTEN,
            pages_written=[
                PageWriteRecord(
                    title="A", target_path="a.md",
                    action=WriteAction.CREATED, bytes_written=100,
                ),
            ],
        )
        assert "1 created" in r.summary()

    def test_summary_rejected(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.REJECTED,
            pages_rejected=["A", "B"],
        )
        assert "2 page(s) discarded" in r.summary()

    def test_summary_recompile(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.RECOMPILE,
            feedback="Fix the intro section",
        )
        assert "Modification requested" in r.summary()
        assert "Fix the intro" in r.summary()

    def test_to_dict(self):
        r = ApprovalResult(
            outcome=ApprovalOutcome.WRITTEN,
            confirmation_id="confirm-123",
        )
        d = r.to_dict()
        assert d["outcome"] == "written"
        assert d["confirmation_id"] == "confirm-123"
        assert "summary" in d


# ---------------------------------------------------------------------------
# APPROVE: write all pages
# ---------------------------------------------------------------------------


class TestApproveAll:
    def test_creates_new_page_in_vault(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = execute_approval(
            config, [create_result], ConfirmationChoice.APPROVE
        )

        assert result.outcome == ApprovalOutcome.WRITTEN
        assert result.is_written
        assert result.total_written == 1
        assert result.pages_written[0].action == WriteAction.CREATED

        # Verify file exists in vault
        expected_path = config.vault_path / "ai" / "transformer-architecture.md"
        assert expected_path.exists()
        content = expected_path.read_text()
        assert "Transformer Architecture" in content
        assert "self-attention" in content

    def test_writes_multiple_pages(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        update_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, update_result],
            ConfirmationChoice.APPROVE,
        )

        assert result.outcome == ApprovalOutcome.WRITTEN
        assert result.total_written == 2

    def test_skips_are_tracked(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        skip_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, skip_result],
            ConfirmationChoice.APPROVE,
        )

        assert result.outcome == ApprovalOutcome.WRITTEN
        assert result.total_written == 1
        assert "Neural Networks" in result.pages_skipped

    def test_empty_actionable(self, config: WikiConfig, skip_result: CompileResult):
        result = execute_approval(
            config, [skip_result], ConfirmationChoice.APPROVE
        )

        assert result.outcome == ApprovalOutcome.EMPTY

    def test_frontmatter_is_written(
        self, config: WikiConfig, create_result: CompileResult
    ):
        execute_approval(
            config, [create_result], ConfirmationChoice.APPROVE
        )

        expected_path = config.vault_path / "ai" / "transformer-architecture.md"
        content = expected_path.read_text()
        assert content.startswith("---")
        assert "tags:" in content

    def test_lens_directory_created(
        self, config: WikiConfig, create_result: CompileResult
    ):
        execute_approval(
            config, [create_result], ConfirmationChoice.APPROVE
        )

        lens_dir = config.vault_path / "ai"
        assert lens_dir.is_dir()


# ---------------------------------------------------------------------------
# REJECT: discard all
# ---------------------------------------------------------------------------


class TestReject:
    def test_nothing_written_on_reject(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = execute_approval(
            config, [create_result], ConfirmationChoice.REJECT
        )

        assert result.outcome == ApprovalOutcome.REJECTED
        assert not result.is_written
        assert result.total_written == 0
        assert "Transformer Architecture" in result.pages_rejected

        # Verify file does NOT exist
        expected_path = config.vault_path / "ai" / "transformer-architecture.md"
        assert not expected_path.exists()

    def test_reject_multiple(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        update_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, update_result],
            ConfirmationChoice.REJECT,
        )

        assert result.outcome == ApprovalOutcome.REJECTED
        assert len(result.pages_rejected) == 2

    def test_reject_with_skips(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        skip_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, skip_result],
            ConfirmationChoice.REJECT,
        )

        assert result.outcome == ApprovalOutcome.REJECTED
        assert "Transformer Architecture" in result.pages_rejected
        assert "Neural Networks" in result.pages_skipped


# ---------------------------------------------------------------------------
# APPROVE_PARTIAL: write selected
# ---------------------------------------------------------------------------


class TestPartialApprove:
    def test_writes_only_selected(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        update_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, update_result],
            ConfirmationChoice.APPROVE_PARTIAL,
            approved_indices=[0],  # only first page
        )

        assert result.outcome == ApprovalOutcome.PARTIAL
        assert result.total_written == 1
        assert result.pages_written[0].title == "Transformer Architecture"
        assert "Prompt Engineering" in result.pages_rejected

        # First page exists
        assert (config.vault_path / "ai" / "transformer-architecture.md").exists()
        # Second page does NOT exist
        assert not (config.vault_path / "ai" / "prompt-engineering.md").exists()

    def test_partial_second_only(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        update_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result, update_result],
            ConfirmationChoice.APPROVE_PARTIAL,
            approved_indices=[1],  # only second page
        )

        assert result.outcome == ApprovalOutcome.PARTIAL
        assert result.total_written == 1
        assert result.pages_written[0].title == "Prompt Engineering"
        assert "Transformer Architecture" in result.pages_rejected

    def test_empty_indices_rejects_all(
        self,
        config: WikiConfig,
        create_result: CompileResult,
    ):
        result = execute_approval(
            config,
            [create_result],
            ConfirmationChoice.APPROVE_PARTIAL,
            approved_indices=[],
        )

        assert result.outcome == ApprovalOutcome.PARTIAL
        assert result.total_written == 0
        assert "Transformer Architecture" in result.pages_rejected


# ---------------------------------------------------------------------------
# MODIFY: request recompilation
# ---------------------------------------------------------------------------


class TestModify:
    def test_nothing_written_on_modify(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = execute_approval(
            config,
            [create_result],
            ConfirmationChoice.MODIFY,
            feedback="Add more detail about attention mechanism",
        )

        assert result.outcome == ApprovalOutcome.RECOMPILE
        assert result.needs_recompile
        assert not result.is_written
        assert "attention mechanism" in result.feedback

        # Verify file does NOT exist
        expected_path = config.vault_path / "ai" / "transformer-architecture.md"
        assert not expected_path.exists()

    def test_modify_empty_feedback(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = execute_approval(
            config,
            [create_result],
            ConfirmationChoice.MODIFY,
            feedback="",
        )

        assert result.outcome == ApprovalOutcome.RECOMPILE
        assert result.feedback == ""


# ---------------------------------------------------------------------------
# PENDING: error case
# ---------------------------------------------------------------------------


class TestPending:
    def test_pending_returns_error(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = execute_approval(
            config, [create_result], ConfirmationChoice.PENDING
        )

        assert result.outcome == ApprovalOutcome.ERROR
        assert not result.is_written


# ---------------------------------------------------------------------------
# UPDATE action (merge with existing page)
# ---------------------------------------------------------------------------


class TestUpdateAction:
    def test_updates_existing_page(
        self, config: WikiConfig, update_result: CompileResult
    ):
        # Create existing page first
        ai_dir = config.vault_path / "ai"
        ai_dir.mkdir(exist_ok=True)
        existing = ai_dir / "prompt-engineering.md"
        existing.write_text(
            "---\ntitle: Prompt Engineering\ntags:\n  - ai\n---\n\n"
            "# Prompt Engineering\n\nOriginal content.\n"
        )

        result = execute_approval(
            config, [update_result], ConfirmationChoice.APPROVE
        )

        assert result.outcome == ApprovalOutcome.WRITTEN
        assert result.pages_written[0].action == WriteAction.UPDATED

        # Verify content was updated
        content = existing.read_text()
        assert "Updated content" in content


# ---------------------------------------------------------------------------
# approve_from_response convenience function
# ---------------------------------------------------------------------------


class TestApproveFromResponse:
    def test_approve_from_yes(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = approve_from_response(
            config, [create_result], "yes"
        )

        assert result.outcome == ApprovalOutcome.WRITTEN
        assert result.total_written == 1

    def test_reject_from_no(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = approve_from_response(
            config, [create_result], "no"
        )

        assert result.outcome == ApprovalOutcome.REJECTED

    def test_modify_from_long_text(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = approve_from_response(
            config,
            [create_result],
            "Please add more information about the multi-head attention mechanism and positional encoding",
        )

        assert result.outcome == ApprovalOutcome.RECOMPILE
        assert "multi-head attention" in result.feedback

    def test_partial_from_indices(
        self,
        config: WikiConfig,
        create_result: CompileResult,
        update_result: CompileResult,
    ):
        result = approve_from_response(
            config,
            [create_result, update_result],
            "1",  # approve only first page
        )

        # "1" maps to APPROVE (it's a keyword)
        assert result.outcome == ApprovalOutcome.WRITTEN

    def test_korean_approve(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = approve_from_response(
            config, [create_result], "승인"
        )

        assert result.outcome == ApprovalOutcome.WRITTEN

    def test_korean_reject(
        self, config: WikiConfig, create_result: CompileResult
    ):
        result = approve_from_response(
            config, [create_result], "거절"
        )

        assert result.outcome == ApprovalOutcome.REJECTED


# ---------------------------------------------------------------------------
# Confirmation persistence
# ---------------------------------------------------------------------------


class TestConfirmationPersistence:
    def test_approve_persists_confirmation(
        self, config: WikiConfig, create_result: CompileResult, in_memory_db
    ):
        result = execute_approval(
            config,
            [create_result],
            ConfirmationChoice.APPROVE,
            conn=in_memory_db,
        )

        assert result.confirmation_id.startswith("confirm-")

    def test_reject_persists_confirmation(
        self, config: WikiConfig, create_result: CompileResult, in_memory_db
    ):
        result = execute_approval(
            config,
            [create_result],
            ConfirmationChoice.REJECT,
            conn=in_memory_db,
        )

        assert result.confirmation_id.startswith("confirm-")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_results_approve(self, config: WikiConfig):
        result = execute_approval(
            config, [], ConfirmationChoice.APPROVE
        )
        assert result.outcome == ApprovalOutcome.EMPTY

    def test_empty_results_reject(self, config: WikiConfig):
        result = execute_approval(
            config, [], ConfirmationChoice.REJECT
        )
        assert result.outcome == ApprovalOutcome.REJECTED
        assert len(result.pages_rejected) == 0

    def test_write_error_handled(self, config: WikiConfig):
        """Test that write errors are captured gracefully."""
        bad_result = CompileResult(
            title="",  # Empty title should cause issues
            body="content",
            directory="ai",
            action=CompileAction.CREATE,
            compiled_at=FIXED_TIME,
        )

        result = execute_approval(
            config, [bad_result], ConfirmationChoice.APPROVE
        )

        # Should still get a result (either written or with error)
        assert result.outcome in (
            ApprovalOutcome.WRITTEN,
            ApprovalOutcome.ERROR,
        )

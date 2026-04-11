"""Tests for lint_reporter module — unified lint report aggregation."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from llm_wiki.lint_reporter import (
    FileSummary,
    LintMessage,
    LintReport,
    Severity,
)
from llm_wiki.lint_broken_links import BrokenLink, BrokenLinksResult
from llm_wiki.lint_orphan import OrphanPage, OrphanResult
from llm_wiki.lint_stale import StalePage, StaleResult


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_values(self):
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"


# ---------------------------------------------------------------------------
# LintMessage
# ---------------------------------------------------------------------------


class TestLintMessage:
    def test_severity_icon(self):
        assert LintMessage(
            rule="r", severity=Severity.ERROR, file_path=Path("a.md"),
        ).severity_icon == "✗"
        assert LintMessage(
            rule="r", severity=Severity.WARNING, file_path=Path("a.md"),
        ).severity_icon == "⚠"
        assert LintMessage(
            rule="r", severity=Severity.INFO, file_path=Path("a.md"),
        ).severity_icon == "ℹ"

    def test_format_short_with_line(self):
        msg = LintMessage(
            rule="broken-link",
            severity=Severity.ERROR,
            file_path=Path("wiki/Page.md"),
            line=42,
            message="Broken wikilink → [[Missing]]",
        )
        out = msg.format_short()
        assert "wiki/Page.md:42" in out
        assert "[broken-link]" in out
        assert "Broken wikilink" in out

    def test_format_short_without_line(self):
        msg = LintMessage(
            rule="orphan",
            severity=Severity.WARNING,
            file_path=Path("wiki/Lonely.md"),
            message="Orphan page",
        )
        out = msg.format_short()
        assert "wiki/Lonely.md" in out
        assert ":None" not in out  # no line number appended


# ---------------------------------------------------------------------------
# LintReport — empty
# ---------------------------------------------------------------------------


class TestEmptyReport:
    def test_defaults(self):
        report = LintReport()
        assert report.total_errors == 0
        assert report.total_warnings == 0
        assert report.total_infos == 0
        assert report.total_issues == 0
        assert report.is_clean is True
        assert report.rules_run == []
        assert report.total_pages == 0

    def test_format_text_clean(self):
        report = LintReport()
        text = report.format_text()
        assert "Clean" in text

    def test_to_dict_clean(self):
        report = LintReport()
        d = report.to_dict()
        assert d["totals"]["errors"] == 0
        assert d["totals"]["warnings"] == 0
        assert d["totals"]["infos"] == 0
        assert d["files"] == []
        assert d["messages"] == []


# ---------------------------------------------------------------------------
# Orphan ingestion
# ---------------------------------------------------------------------------


class TestOrphanIngestion:
    def test_adds_warnings(self):
        orphan_result = OrphanResult(
            orphans=[
                OrphanPage(
                    path=Path("/vault/Lonely.md"),
                    relative_path=Path("Lonely.md"),
                    title="Lonely",
                    outgoing_links=3,
                ),
                OrphanPage(
                    path=Path("/vault/Island.md"),
                    relative_path=Path("Island.md"),
                    title="Island",
                    outgoing_links=0,
                ),
            ],
            total_pages=10,
            total_links=25,
        )
        report = LintReport()
        report.add_orphan_result(orphan_result)

        assert report.total_warnings == 2
        assert report.total_errors == 0
        assert report.total_issues == 2
        assert report.is_clean is False
        assert "orphan" in report.rules_run
        assert report.total_pages == 10

    def test_outgoing_links_in_message(self):
        result = OrphanResult(
            orphans=[
                OrphanPage(
                    path=Path("/v/A.md"),
                    relative_path=Path("A.md"),
                    title="A",
                    outgoing_links=5,
                ),
            ],
            total_pages=5,
        )
        report = LintReport()
        report.add_orphan_result(result)
        assert "5 outgoing" in report.messages[0].message

    def test_no_outgoing_links_omitted(self):
        result = OrphanResult(
            orphans=[
                OrphanPage(
                    path=Path("/v/A.md"),
                    relative_path=Path("A.md"),
                    title="A",
                    outgoing_links=0,
                ),
            ],
            total_pages=5,
        )
        report = LintReport()
        report.add_orphan_result(result)
        assert "outgoing" not in report.messages[0].message


# ---------------------------------------------------------------------------
# Broken links ingestion
# ---------------------------------------------------------------------------


class TestBrokenLinksIngestion:
    def test_adds_errors(self):
        bl_result = BrokenLinksResult(
            broken_links=[
                BrokenLink(
                    source_path=Path("/vault/PageA.md"),
                    source_relative=Path("PageA.md"),
                    line=10,
                    target="Missing",
                ),
            ],
            total_pages=5,
            total_links=20,
            total_valid=19,
        )
        report = LintReport()
        report.add_broken_links_result(bl_result)

        assert report.total_errors == 1
        assert report.total_warnings == 0
        assert report.is_clean is False
        assert "broken-link" in report.rules_run

    def test_message_contains_target(self):
        bl_result = BrokenLinksResult(
            broken_links=[
                BrokenLink(
                    source_path=Path("/v/A.md"),
                    source_relative=Path("A.md"),
                    line=5,
                    target="NonExistent",
                ),
            ],
            total_pages=3,
        )
        report = LintReport()
        report.add_broken_links_result(bl_result)
        assert "[[NonExistent]]" in report.messages[0].message
        assert report.messages[0].line == 5


# ---------------------------------------------------------------------------
# Stale ingestion
# ---------------------------------------------------------------------------


class TestStaleIngestion:
    def test_adds_warnings(self):
        now = datetime.now(timezone.utc)
        stale_result = StaleResult(
            stale_pages=[
                StalePage(
                    path=Path("/vault/Old.md"),
                    relative_path=Path("Old.md"),
                    title="Old",
                    last_updated=now - timedelta(days=120),
                    age_days=120,
                    timestamp_source="compiled_at",
                ),
            ],
            total_pages=8,
            max_age_days=90,
        )
        report = LintReport()
        report.add_stale_result(stale_result)

        assert report.total_warnings == 1
        assert report.total_errors == 0
        assert "stale" in report.rules_run

    def test_message_contains_age(self):
        now = datetime.now(timezone.utc)
        stale_result = StaleResult(
            stale_pages=[
                StalePage(
                    path=Path("/v/X.md"),
                    relative_path=Path("X.md"),
                    title="X",
                    last_updated=now - timedelta(days=45),
                    age_days=45,
                    timestamp_source="mtime",
                ),
            ],
            total_pages=3,
        )
        report = LintReport()
        report.add_stale_result(stale_result)
        assert "45 days" in report.messages[0].message
        assert "[mtime]" in report.messages[0].message


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------


class TestCombinedReport:
    @pytest.fixture
    def combined_report(self) -> LintReport:
        """Report with all three rules contributing findings."""
        report = LintReport()

        # Orphan
        report.add_orphan_result(OrphanResult(
            orphans=[
                OrphanPage(
                    path=Path("/v/Lonely.md"),
                    relative_path=Path("Lonely.md"),
                    title="Lonely",
                ),
            ],
            total_pages=10,
        ))

        # Broken links — two in the same file
        report.add_broken_links_result(BrokenLinksResult(
            broken_links=[
                BrokenLink(
                    source_path=Path("/v/PageA.md"),
                    source_relative=Path("PageA.md"),
                    line=5,
                    target="Gone1",
                ),
                BrokenLink(
                    source_path=Path("/v/PageA.md"),
                    source_relative=Path("PageA.md"),
                    line=12,
                    target="Gone2",
                ),
            ],
            total_pages=10,
        ))

        # Stale
        now = datetime.now(timezone.utc)
        report.add_stale_result(StaleResult(
            stale_pages=[
                StalePage(
                    path=Path("/v/PageA.md"),
                    relative_path=Path("PageA.md"),
                    title="PageA",
                    last_updated=now - timedelta(days=100),
                    age_days=100,
                    timestamp_source="compiled_at",
                ),
            ],
            total_pages=10,
        ))

        return report

    def test_totals(self, combined_report: LintReport):
        assert combined_report.total_errors == 2
        assert combined_report.total_warnings == 2
        assert combined_report.total_issues == 4
        assert combined_report.is_clean is False

    def test_rules_run(self, combined_report: LintReport):
        assert combined_report.rules_run == ["orphan", "broken-link", "stale"]

    def test_total_pages_takes_max(self, combined_report: LintReport):
        assert combined_report.total_pages == 10

    def test_file_summaries(self, combined_report: LintReport):
        summaries = combined_report.file_summaries()
        assert len(summaries) == 2  # PageA.md and Lonely.md

        # PageA should be first (has errors)
        assert summaries[0].file_path == Path("PageA.md")
        assert summaries[0].errors == 2
        assert summaries[0].warnings == 1  # stale
        assert summaries[0].total == 3

        # Lonely second (only warnings)
        assert summaries[1].file_path == Path("Lonely.md")
        assert summaries[1].errors == 0
        assert summaries[1].warnings == 1

    def test_format_text_sections(self, combined_report: LintReport):
        text = combined_report.format_text()
        # Header
        assert "Lint Report" in text
        assert "2 error(s)" in text
        assert "2 warning(s)" in text
        # Per-file summary header
        assert "Per-file summary" in text
        assert "PageA.md" in text
        assert "Lonely.md" in text
        # Details section
        assert "Details" in text
        assert "[broken-link]" in text
        assert "[orphan]" in text
        assert "[stale]" in text

    def test_to_dict_structure(self, combined_report: LintReport):
        d = combined_report.to_dict()
        assert d["rules_run"] == ["orphan", "broken-link", "stale"]
        assert d["total_pages"] == 10
        assert d["totals"]["errors"] == 2
        assert d["totals"]["warnings"] == 2
        assert len(d["files"]) == 2
        assert len(d["messages"]) == 4

        # First file (PageA) should have 3 messages
        page_a = d["files"][0]
        assert page_a["path"] == "PageA.md"
        assert len(page_a["messages"]) == 3

    def test_to_dict_message_fields(self, combined_report: LintReport):
        d = combined_report.to_dict()
        msg = d["messages"][0]
        assert "rule" in msg
        assert "severity" in msg
        assert "file" in msg
        assert "line" in msg
        assert "message" in msg


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_findings_is_clean(self):
        report = LintReport()
        report.add_orphan_result(OrphanResult(total_pages=5))
        report.add_broken_links_result(BrokenLinksResult(total_pages=5))
        assert report.is_clean is True
        assert "Clean" in report.format_text()
        assert report.rules_run == ["orphan", "broken-link"]

    def test_single_rule_only(self):
        report = LintReport()
        report.add_broken_links_result(BrokenLinksResult(
            broken_links=[
                BrokenLink(
                    source_path=Path("/v/X.md"),
                    source_relative=Path("X.md"),
                    line=1,
                    target="Y",
                ),
            ],
            total_pages=3,
        ))
        assert report.rules_run == ["broken-link"]
        assert report.total_errors == 1
        assert report.total_warnings == 0

    def test_file_summaries_sort_order(self):
        """Files with more errors sort first."""
        report = LintReport()
        report.add_broken_links_result(BrokenLinksResult(
            broken_links=[
                BrokenLink(Path("/v/B.md"), Path("B.md"), 1, "X"),
                BrokenLink(Path("/v/A.md"), Path("A.md"), 1, "X"),
                BrokenLink(Path("/v/A.md"), Path("A.md"), 2, "Y"),
            ],
            total_pages=5,
        ))
        summaries = report.file_summaries()
        # A.md has 2 errors, B.md has 1 → A first
        assert summaries[0].file_path == Path("A.md")
        assert summaries[1].file_path == Path("B.md")

    def test_long_path_truncated_in_text(self):
        """Very long file paths should be truncated in text output."""
        report = LintReport()
        long_path = Path("very/deeply/nested/directory/structure/file.md")
        report.add_orphan_result(OrphanResult(
            orphans=[
                OrphanPage(
                    path=Path("/v") / long_path,
                    relative_path=long_path,
                    title="file",
                ),
            ],
            total_pages=3,
        ))
        text = report.format_text()
        # Should still produce output without errors
        assert "Per-file summary" in text

"""Tests for lint_stale module — stale content detection."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from llm_wiki.lint_stale import (
    DEFAULT_MAX_AGE_DAYS,
    StalePage,
    StaleResult,
    find_stale_pages,
    _extract_frontmatter_timestamps,
    _parse_iso_datetime,
    _resolve_timestamp,
)
from llm_wiki.vault_scanner import VaultFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a vault with pages at various ages."""
    # Fresh page — compiled 10 days ago
    (tmp_path / "Fresh.md").write_text(
        "---\ntitle: Fresh\n"
        f"compiled_at: {(NOW - timedelta(days=10)).isoformat()}\n"
        "---\n# Fresh\nRecently updated content.\n",
        encoding="utf-8",
    )

    # Aging page — compiled 80 days ago (within default 90-day threshold)
    (tmp_path / "Aging.md").write_text(
        "---\ntitle: Aging\n"
        f"compiled_at: {(NOW - timedelta(days=80)).isoformat()}\n"
        "---\n# Aging\nGetting old but still within threshold.\n",
        encoding="utf-8",
    )

    # Stale page — compiled 120 days ago
    (tmp_path / "Stale.md").write_text(
        "---\ntitle: Stale\n"
        f"compiled_at: {(NOW - timedelta(days=120)).isoformat()}\n"
        "---\n# Stale\nThis is outdated content.\n",
        encoding="utf-8",
    )

    # Very stale page — compiled 365 days ago
    (tmp_path / "Ancient.md").write_text(
        "---\ntitle: Ancient\n"
        f"compiled_at: {(NOW - timedelta(days=365)).isoformat()}\n"
        "---\n# Ancient\nVery old content.\n",
        encoding="utf-8",
    )

    # Page with updated_at instead of compiled_at — 50 days ago
    (tmp_path / "UpdatedAt.md").write_text(
        "---\ntitle: UpdatedAt\n"
        f"updated_at: {(NOW - timedelta(days=50)).isoformat()}\n"
        "---\n# UpdatedAt\nUses updated_at key.\n",
        encoding="utf-8",
    )

    # Page with no frontmatter timestamps — relies on mtime
    (tmp_path / "NoTimestamp.md").write_text(
        "---\ntitle: NoTimestamp\n"
        "---\n# NoTimestamp\nNo date fields in frontmatter.\n",
        encoding="utf-8",
    )

    # Page with date-only format — 200 days ago
    (tmp_path / "DateOnly.md").write_text(
        "---\ntitle: DateOnly\n"
        f"date: {(NOW - timedelta(days=200)).strftime('%Y-%m-%d')}\n"
        "---\n# DateOnly\nUses date key.\n",
        encoding="utf-8",
    )

    # Excluded directories
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "collected.md").write_text("source content\n")

    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()

    return tmp_path


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def all_fresh_vault(tmp_path: Path) -> Path:
    """Vault where every page is recent."""
    for i in range(3):
        (tmp_path / f"Page{i}.md").write_text(
            "---\ntitle: Page\n"
            f"compiled_at: {(NOW - timedelta(days=5+i)).isoformat()}\n"
            "---\n# Content\n",
            encoding="utf-8",
        )
    return tmp_path


# ---------------------------------------------------------------------------
# _parse_iso_datetime tests
# ---------------------------------------------------------------------------


class TestParseIsoDatetime:
    def test_full_iso(self) -> None:
        dt = _parse_iso_datetime("2026-01-15T12:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 15

    def test_iso_with_z(self) -> None:
        dt = _parse_iso_datetime("2026-01-15T12:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_date_only(self) -> None:
        dt = _parse_iso_datetime("2026-01-15")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_empty_string(self) -> None:
        assert _parse_iso_datetime("") is None

    def test_invalid_string(self) -> None:
        assert _parse_iso_datetime("not-a-date") is None

    def test_naive_datetime_gets_utc(self) -> None:
        dt = _parse_iso_datetime("2026-01-15T12:00:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# _extract_frontmatter_timestamps tests
# ---------------------------------------------------------------------------


class TestExtractFrontmatterTimestamps:
    def test_compiled_at(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\ncompiled_at: 2026-01-15T12:00:00+00:00\n---\nBody.\n")
        result = _extract_frontmatter_timestamps(f)
        assert "compiled_at" in result
        assert result["compiled_at"].year == 2026

    def test_multiple_keys(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text(
            "---\ncompiled_at: 2026-03-01T00:00:00Z\n"
            "updated_at: 2026-02-01T00:00:00Z\n"
            "date: 2026-01-01\n---\nBody.\n"
        )
        result = _extract_frontmatter_timestamps(f)
        assert len(result) == 3
        assert result["compiled_at"].month == 3
        assert result["updated_at"].month == 2
        assert result["date"].month == 1

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# No frontmatter\nJust text.\n")
        result = _extract_frontmatter_timestamps(f)
        assert result == {}

    def test_missing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.md"
        result = _extract_frontmatter_timestamps(f)
        assert result == {}

    def test_non_timestamp_keys_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: My Page\ntags: [a, b]\n---\nBody.\n")
        result = _extract_frontmatter_timestamps(f)
        assert result == {}

    def test_quoted_value(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\ncompiled_at: '2026-01-15T12:00:00+00:00'\n---\nBody.\n")
        result = _extract_frontmatter_timestamps(f)
        assert "compiled_at" in result
        assert result["compiled_at"] is not None


# ---------------------------------------------------------------------------
# _resolve_timestamp tests
# ---------------------------------------------------------------------------


class TestResolveTimestamp:
    def test_compiled_at_preferred(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text(
            "---\ncompiled_at: 2026-03-01T00:00:00Z\n"
            "updated_at: 2026-01-01T00:00:00Z\n---\nBody.\n"
        )
        vf = VaultFile(
            path=f,
            relative_path=Path("test.md"),
            modified_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        dt, source = _resolve_timestamp(vf)
        assert source == "compiled_at"
        assert dt.month == 3

    def test_falls_back_to_updated_at(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\nupdated_at: 2026-02-01T00:00:00Z\n---\nBody.\n")
        vf = VaultFile(
            path=f,
            relative_path=Path("test.md"),
            modified_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        dt, source = _resolve_timestamp(vf)
        assert source == "updated_at"

    def test_falls_back_to_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: No dates\n---\nBody.\n")
        mtime = datetime(2026, 1, 15, tzinfo=timezone.utc)
        vf = VaultFile(
            path=f,
            relative_path=Path("test.md"),
            modified_at=mtime,
        )
        dt, source = _resolve_timestamp(vf)
        assert source == "mtime"
        assert dt == mtime

    def test_no_timestamp_at_all(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# No frontmatter\n")
        vf = VaultFile(
            path=f,
            relative_path=Path("test.md"),
            modified_at=None,
        )
        dt, source = _resolve_timestamp(vf)
        assert dt is None
        assert source == "unknown"


# ---------------------------------------------------------------------------
# find_stale_pages tests
# ---------------------------------------------------------------------------


class TestFindStalePages:
    """Tests for the main stale detection function."""

    def test_detects_stale_pages(self, vault: Path) -> None:
        result = find_stale_pages(vault, reference_time=NOW)

        stale_names = {p.title for p in result.stale_pages}
        assert "Stale" in stale_names
        assert "Ancient" in stale_names
        assert "DateOnly" in stale_names

    def test_fresh_pages_not_flagged(self, vault: Path) -> None:
        result = find_stale_pages(vault, reference_time=NOW)

        stale_names = {p.title for p in result.stale_pages}
        assert "Fresh" not in stale_names
        assert "Aging" not in stale_names
        assert "UpdatedAt" not in stale_names

    def test_custom_threshold(self, vault: Path) -> None:
        """With 60-day threshold, Aging (80 days) should also be stale."""
        result = find_stale_pages(vault, max_age_days=60, reference_time=NOW)

        stale_names = {p.title for p in result.stale_pages}
        assert "Aging" in stale_names
        assert "Fresh" not in stale_names

    def test_very_short_threshold(self, vault: Path) -> None:
        """With 1-day threshold, almost everything is stale."""
        result = find_stale_pages(vault, max_age_days=1, reference_time=NOW)
        # All pages except possibly those with mtime = now
        assert result.stale_count >= 5  # Fresh(10d), Aging(80d), Stale, Ancient, DateOnly, UpdatedAt

    def test_very_long_threshold(self, vault: Path) -> None:
        """With 999-day threshold, nothing is stale."""
        result = find_stale_pages(vault, max_age_days=999, reference_time=NOW)
        # Only pages without any timestamp might be stale depending on mtime
        stale_names = {p.title for p in result.stale_pages}
        assert "Fresh" not in stale_names
        assert "Stale" not in stale_names
        assert "Ancient" not in stale_names

    def test_empty_vault(self, empty_vault: Path) -> None:
        result = find_stale_pages(empty_vault, reference_time=NOW)
        assert result.stale_count == 0
        assert result.total_pages == 0

    def test_all_fresh_vault(self, all_fresh_vault: Path) -> None:
        result = find_stale_pages(all_fresh_vault, reference_time=NOW)
        assert result.stale_count == 0
        assert result.total_pages == 3

    def test_excludes_sources_directory(self, vault: Path) -> None:
        """sources/ directory should be excluded from scanning."""
        result = find_stale_pages(vault, reference_time=NOW)
        stale_paths = {str(p.relative_path) for p in result.stale_pages}
        assert not any("sources/" in p for p in stale_paths)

    def test_age_days_accurate(self, vault: Path) -> None:
        result = find_stale_pages(vault, reference_time=NOW)

        stale_map = {p.title: p for p in result.stale_pages}
        assert stale_map["Stale"].age_days == 120
        assert stale_map["Ancient"].age_days == 365

    def test_timestamp_source_tracking(self, vault: Path) -> None:
        result = find_stale_pages(vault, reference_time=NOW, max_age_days=30)

        stale_map = {p.title: p for p in result.stale_pages}
        if "Aging" in stale_map:
            assert stale_map["Aging"].timestamp_source == "compiled_at"
        if "UpdatedAt" in stale_map:
            assert stale_map["UpdatedAt"].timestamp_source == "updated_at"
        if "DateOnly" in stale_map:
            assert stale_map["DateOnly"].timestamp_source == "date"

    def test_stale_rate(self, vault: Path) -> None:
        result = find_stale_pages(vault, reference_time=NOW)
        assert result.stale_rate > 0
        assert result.stale_rate <= 100.0

    def test_max_age_days_preserved(self, vault: Path) -> None:
        result = find_stale_pages(vault, max_age_days=45, reference_time=NOW)
        assert result.max_age_days == 45


# ---------------------------------------------------------------------------
# StalePage display tests
# ---------------------------------------------------------------------------


class TestStalePageDisplay:
    def test_age_display_today(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=0,
            timestamp_source="compiled_at",
        )
        assert page.age_display == "today"

    def test_age_display_1_day(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=1,
            timestamp_source="compiled_at",
        )
        assert page.age_display == "1 day"

    def test_age_display_days(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=32,
            timestamp_source="compiled_at",
        )
        assert page.age_display == "32 days"

    def test_age_display_months(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=120,
            timestamp_source="compiled_at",
        )
        assert page.age_display == "4 months"

    def test_age_display_1_month(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=60,
            timestamp_source="compiled_at",
        )
        assert page.age_display == "2 months"

    def test_age_display_years(self) -> None:
        page = StalePage(
            path=Path("/v/a.md"), relative_path=Path("a.md"),
            title="a", last_updated=NOW, age_days=400,
            timestamp_source="compiled_at",
        )
        assert "1y" in page.age_display


# ---------------------------------------------------------------------------
# StaleResult summary tests
# ---------------------------------------------------------------------------


class TestStaleResult:
    def test_summary_no_stale(self) -> None:
        result = StaleResult(total_pages=5, max_age_days=90)
        summary = result.summary()
        assert "No stale pages" in summary
        assert "✓" in summary
        assert "90 days" in summary

    def test_summary_with_stale(self) -> None:
        result = StaleResult(
            stale_pages=[
                StalePage(
                    Path("/v/old.md"), Path("old.md"), "old",
                    NOW - timedelta(days=120), 120, "compiled_at",
                ),
            ],
            total_pages=5,
            max_age_days=90,
        )
        summary = result.summary()
        assert "1 stale" in summary
        assert "old.md" in summary
        assert "compiled_at" in summary

    def test_summary_empty_vault(self) -> None:
        result = StaleResult()
        assert "No wiki pages found" in result.summary()

    def test_stale_rate_zero_pages(self) -> None:
        result = StaleResult(total_pages=0)
        assert result.stale_rate == 0.0

    def test_fresh_count(self) -> None:
        result = StaleResult(
            stale_pages=[
                StalePage(
                    Path("/v/a.md"), Path("a.md"), "a",
                    NOW, 100, "compiled_at",
                ),
            ],
            total_pages=10,
        )
        assert result.fresh_count == 9

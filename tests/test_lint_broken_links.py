"""Tests for lint_broken_links module — broken wikilink detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.lint_broken_links import (
    BrokenLink,
    BrokenLinksResult,
    find_broken_links,
    _find_broken_in_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a vault with a mix of valid and broken wikilinks."""
    # Page A links to B (valid) and NonExistent (broken)
    (tmp_path / "PageA.md").write_text(
        "---\ntitle: Page A\n---\n"
        "# Page A\nSee [[PageB]] and [[NonExistent]].\n",
        encoding="utf-8",
    )
    # Page B links to A (valid) and Ghost (broken)
    (tmp_path / "PageB.md").write_text(
        "---\ntitle: Page B\n---\n"
        "# Page B\nRelated to [[PageA]] and [[Ghost]].\n",
        encoding="utf-8",
    )
    # Page C — all links valid
    (tmp_path / "PageC.md").write_text(
        "---\ntitle: Page C\n---\n"
        "# Page C\nSee [[PageA]] and [[PageB]].\n",
        encoding="utf-8",
    )
    # Page D — no links at all
    (tmp_path / "PageD.md").write_text(
        "---\ntitle: Page D\n---\n"
        "# Page D\nNo links here.\n",
        encoding="utf-8",
    )

    # Create excluded directories
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "collected.md").write_text("source with [[Broken]] link\n")

    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()

    return tmp_path


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """An empty vault with no markdown files."""
    return tmp_path


@pytest.fixture
def all_valid_vault(tmp_path: Path) -> Path:
    """A vault where every wikilink points to an existing page."""
    (tmp_path / "Alpha.md").write_text(
        "# Alpha\nSee [[Beta]].\n", encoding="utf-8",
    )
    (tmp_path / "Beta.md").write_text(
        "# Beta\nSee [[Alpha]].\n", encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# find_broken_links tests
# ---------------------------------------------------------------------------


class TestFindBrokenLinks:
    """Tests for the main broken link detection function."""

    def test_detects_broken_links(self, vault: Path) -> None:
        result = find_broken_links(vault)

        broken_targets = {bl.target for bl in result.broken_links}
        assert "NonExistent" in broken_targets
        assert "Ghost" in broken_targets
        assert result.broken_count == 2

    def test_valid_links_not_flagged(self, vault: Path) -> None:
        result = find_broken_links(vault)

        broken_targets = {bl.target for bl in result.broken_links}
        assert "PageA" not in broken_targets
        assert "PageB" not in broken_targets

    def test_total_pages(self, vault: Path) -> None:
        result = find_broken_links(vault)
        # 4 wiki pages (A-D), sources/collected.md excluded
        assert result.total_pages == 4

    def test_total_links(self, vault: Path) -> None:
        result = find_broken_links(vault)
        # A: PageB + NonExistent = 2
        # B: PageA + Ghost = 2
        # C: PageA + PageB = 2
        # D: 0
        # Total = 6
        assert result.total_links == 6

    def test_total_valid(self, vault: Path) -> None:
        result = find_broken_links(vault)
        # Valid: PageB(A), PageA(B), PageA(C), PageB(C) = 4
        assert result.total_valid == 4

    def test_broken_link_sources(self, vault: Path) -> None:
        result = find_broken_links(vault)

        sources = {bl.source_relative.name for bl in result.broken_links}
        assert "PageA.md" in sources
        assert "PageB.md" in sources
        assert "PageC.md" not in sources

    def test_empty_vault(self, empty_vault: Path) -> None:
        result = find_broken_links(empty_vault)
        assert result.broken_count == 0
        assert result.total_pages == 0

    def test_all_valid_vault(self, all_valid_vault: Path) -> None:
        result = find_broken_links(all_valid_vault)
        assert result.broken_count == 0
        assert result.total_pages == 2
        assert result.total_links == 2

    def test_case_insensitive_matching(self, tmp_path: Path) -> None:
        """[[pagea]] should resolve to PageA.md (not broken)."""
        (tmp_path / "PageA.md").write_text("# PageA\nContent.\n")
        (tmp_path / "PageB.md").write_text("# PageB\nSee [[pagea]].\n")

        result = find_broken_links(tmp_path)
        assert result.broken_count == 0

    def test_self_link_not_broken(self, tmp_path: Path) -> None:
        """A page linking to itself should not be flagged as broken."""
        (tmp_path / "Self.md").write_text("# Self\nI link to [[Self]].\n")

        result = find_broken_links(tmp_path)
        assert result.broken_count == 0

    def test_alias_links(self, tmp_path: Path) -> None:
        """[[Page|alias]] should check Page, not the alias text."""
        (tmp_path / "PageA.md").write_text("# PageA\nContent.\n")
        (tmp_path / "PageB.md").write_text(
            "# PageB\nSee [[PageA|click here]] and [[Missing|text]].\n"
        )

        result = find_broken_links(tmp_path)
        broken_targets = {bl.target for bl in result.broken_links}
        assert "Missing" in broken_targets
        assert "PageA" not in broken_targets

    def test_heading_links(self, tmp_path: Path) -> None:
        """[[Page#heading]] should check Page, not the heading."""
        (tmp_path / "PageA.md").write_text("# PageA\nContent.\n")
        (tmp_path / "PageB.md").write_text(
            "# PageB\nSee [[PageA#section]] and [[NoPage#header]].\n"
        )

        result = find_broken_links(tmp_path)
        broken_targets = {bl.target for bl in result.broken_links}
        assert "NoPage" in broken_targets
        assert "PageA" not in broken_targets

    def test_path_style_links(self, tmp_path: Path) -> None:
        """[[folder/Page]] should resolve to Page stem."""
        (tmp_path / "SubPage.md").write_text("# SubPage\nContent.\n")
        (tmp_path / "Main.md").write_text(
            "# Main\nSee [[folder/SubPage]] and [[folder/Missing]].\n"
        )

        result = find_broken_links(tmp_path)
        broken_targets = {bl.target for bl in result.broken_links}
        assert "Missing" in broken_targets
        assert "SubPage" not in broken_targets

    def test_frontmatter_links_ignored(self, tmp_path: Path) -> None:
        """Wikilinks in frontmatter should not be checked."""
        (tmp_path / "Page.md").write_text(
            '---\nrelated: "[[FrontmatterGhost]]"\n---\n'
            "Body with [[BodyGhost]].\n"
        )

        result = find_broken_links(tmp_path)
        broken_targets = {bl.target for bl in result.broken_links}
        assert "BodyGhost" in broken_targets
        assert "FrontmatterGhost" not in broken_targets

    def test_multiple_broken_on_same_line(self, tmp_path: Path) -> None:
        """Multiple broken links on one line should all be detected."""
        (tmp_path / "Page.md").write_text(
            "Compare [[Ghost1]], [[Ghost2]], and [[Ghost3]].\n"
        )

        result = find_broken_links(tmp_path)
        broken_targets = {bl.target for bl in result.broken_links}
        assert broken_targets == {"Ghost1", "Ghost2", "Ghost3"}
        assert result.broken_count == 3


# ---------------------------------------------------------------------------
# Line number accuracy tests
# ---------------------------------------------------------------------------


class TestLineNumbers:
    """Tests for accurate line number reporting."""

    def test_line_number_without_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "Page.md").write_text(
            "Line 1\n"
            "Line 2 with [[Ghost]]\n"
            "Line 3\n"
        )

        result = find_broken_links(tmp_path)
        assert result.broken_count >= 1
        ghost = next(bl for bl in result.broken_links if bl.target == "Ghost")
        assert ghost.line == 2

    def test_line_number_with_frontmatter(self, tmp_path: Path) -> None:
        content = (
            "---\n"           # line 1
            "title: Test\n"   # line 2
            "tags: [a]\n"     # line 3
            "---\n"           # line 4
            "Body line 1\n"   # line 5
            "Body [[Ghost]]\n"  # line 6
        )
        (tmp_path / "Page.md").write_text(content)

        result = find_broken_links(tmp_path)
        assert result.broken_count >= 1
        ghost = next(bl for bl in result.broken_links if bl.target == "Ghost")
        assert ghost.line == 6

    def test_line_number_first_body_line(self, tmp_path: Path) -> None:
        content = "---\ntitle: X\n---\n[[Ghost]] on first body line\n"
        (tmp_path / "Page.md").write_text(content)

        result = find_broken_links(tmp_path)
        ghost = next(bl for bl in result.broken_links if bl.target == "Ghost")
        assert ghost.line == 4


# ---------------------------------------------------------------------------
# _find_broken_in_file tests
# ---------------------------------------------------------------------------


class TestFindBrokenInFile:
    def test_returns_empty_for_valid_links(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[known]].\n")
        broken = _find_broken_in_file(f, Path("test.md"), {"known"})
        assert broken == []

    def test_returns_broken_for_unknown_targets(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[unknown]].\n")
        broken = _find_broken_in_file(f, Path("test.md"), {"other"})
        assert len(broken) == 1
        assert broken[0].target == "unknown"

    def test_unreadable_file(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.md"
        broken = _find_broken_in_file(f, Path("missing.md"), set())
        assert broken == []


# ---------------------------------------------------------------------------
# BrokenLinksResult tests
# ---------------------------------------------------------------------------


class TestBrokenLinksResult:
    def test_broken_rate(self) -> None:
        result = BrokenLinksResult(
            broken_links=[
                BrokenLink(Path("a.md"), Path("a.md"), 1, "ghost"),
            ],
            total_links=10,
        )
        assert result.broken_rate == 10.0

    def test_broken_rate_zero_links(self) -> None:
        result = BrokenLinksResult(total_links=0)
        assert result.broken_rate == 0.0

    def test_affected_files(self) -> None:
        result = BrokenLinksResult(
            broken_links=[
                BrokenLink(Path("a.md"), Path("a.md"), 1, "g1"),
                BrokenLink(Path("a.md"), Path("a.md"), 5, "g2"),
                BrokenLink(Path("b.md"), Path("b.md"), 3, "g3"),
            ],
        )
        assert result.affected_files == 2

    def test_summary_no_broken(self) -> None:
        result = BrokenLinksResult(total_pages=5, total_links=10)
        summary = result.summary()
        assert "No broken links" in summary
        assert "\u2713" in summary

    def test_summary_with_broken(self) -> None:
        result = BrokenLinksResult(
            broken_links=[
                BrokenLink(
                    Path("/v/a.md"), Path("a.md"), 10, "Ghost"
                ),
            ],
            total_pages=5,
            total_links=8,
        )
        summary = result.summary()
        assert "1 broken" in summary
        assert "a.md:10" in summary
        assert "[[Ghost]]" in summary

    def test_summary_empty_vault(self) -> None:
        result = BrokenLinksResult()
        assert "No wiki pages found" in result.summary()

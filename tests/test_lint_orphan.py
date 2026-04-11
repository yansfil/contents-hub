"""Tests for lint_orphan module — orphan page detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.lint_orphan import (
    DEFAULT_EXCLUDE_NAMES,
    OrphanPage,
    OrphanResult,
    extract_wikilinks,
    find_orphan_pages,
    _strip_frontmatter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a vault with a mix of linked and orphan pages."""
    # Page A links to B and C
    (tmp_path / "PageA.md").write_text(
        "---\ntitle: Page A\n---\n"
        "# Page A\nSee [[PageB]] and [[PageC]].\n",
        encoding="utf-8",
    )
    # Page B links to A
    (tmp_path / "PageB.md").write_text(
        "---\ntitle: Page B\n---\n"
        "# Page B\nRelated to [[PageA]].\n",
        encoding="utf-8",
    )
    # Page C links to B
    (tmp_path / "PageC.md").write_text(
        "---\ntitle: Page C\n---\n"
        "# Page C\nAlso see [[PageB]].\n",
        encoding="utf-8",
    )
    # Page D — orphan (no one links to it)
    (tmp_path / "PageD.md").write_text(
        "---\ntitle: Page D\n---\n"
        "# Page D\nThis is an orphan page.\n",
        encoding="utf-8",
    )
    # Page E — orphan but links outward
    (tmp_path / "PageE.md").write_text(
        "# Page E\nLinks to [[PageA]] but nobody links here.\n",
        encoding="utf-8",
    )

    # Create excluded directories
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "collected.md").write_text("source content\n")

    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()

    return tmp_path


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """An empty vault with no markdown files."""
    return tmp_path


@pytest.fixture
def fully_linked_vault(tmp_path: Path) -> Path:
    """A vault where every page is linked by at least one other."""
    (tmp_path / "Alpha.md").write_text(
        "# Alpha\nSee [[Beta]].\n", encoding="utf-8",
    )
    (tmp_path / "Beta.md").write_text(
        "# Beta\nSee [[Alpha]].\n", encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# extract_wikilinks tests
# ---------------------------------------------------------------------------


class TestExtractWikilinks:
    """Tests for wikilink extraction from markdown files."""

    def test_basic_wikilinks(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[PageA]] and [[PageB]].\n")
        assert extract_wikilinks(f) == ["PageA", "PageB"]

    def test_wikilink_with_alias(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[PageA|click here]] for details.\n")
        assert extract_wikilinks(f) == ["PageA"]

    def test_wikilink_with_heading(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[PageA#section]].\n")
        assert extract_wikilinks(f) == ["PageA"]

    def test_wikilink_with_heading_and_alias(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("See [[PageA#section|click]].\n")
        assert extract_wikilinks(f) == ["PageA"]

    def test_wikilink_with_path(self, tmp_path: Path) -> None:
        """Path-style links should resolve to the page name only."""
        f = tmp_path / "test.md"
        f.write_text("See [[folder/SubPage]].\n")
        assert extract_wikilinks(f) == ["SubPage"]

    def test_deduplication(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("[[PageA]] and again [[PageA]].\n")
        assert extract_wikilinks(f) == ["PageA"]

    def test_no_wikilinks(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("No links here, just plain text.\n")
        assert extract_wikilinks(f) == []

    def test_frontmatter_links_ignored(self, tmp_path: Path) -> None:
        """Wikilinks inside frontmatter should not be extracted."""
        f = tmp_path / "test.md"
        f.write_text(
            "---\ntitle: \"[[NotALink]]\"\n---\n"
            "Body with [[RealLink]].\n"
        )
        assert extract_wikilinks(f) == ["RealLink"]

    def test_unreadable_file(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.md"
        assert extract_wikilinks(f) == []

    def test_multiple_on_same_line(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("Compare [[A]], [[B]], and [[C]].\n")
        assert extract_wikilinks(f) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# _strip_frontmatter tests
# ---------------------------------------------------------------------------


class TestStripFrontmatter:
    def test_with_frontmatter(self) -> None:
        content = "---\ntitle: Test\ntags: [a]\n---\nBody text.\n"
        assert _strip_frontmatter(content) == "Body text.\n"

    def test_without_frontmatter(self) -> None:
        content = "# Just a heading\nBody.\n"
        assert _strip_frontmatter(content) == content

    def test_unclosed_frontmatter(self) -> None:
        content = "---\ntitle: No close\n"
        assert _strip_frontmatter(content) == content

    def test_empty_string(self) -> None:
        assert _strip_frontmatter("") == ""


# ---------------------------------------------------------------------------
# find_orphan_pages tests
# ---------------------------------------------------------------------------


class TestFindOrphanPages:
    """Tests for the main orphan detection function."""

    def test_detects_orphans(self, vault: Path) -> None:
        result = find_orphan_pages(vault)

        orphan_names = {o.title for o in result.orphans}
        assert "PageD" in orphan_names
        assert "PageE" in orphan_names
        assert result.orphan_count == 2

    def test_connected_pages_not_orphaned(self, vault: Path) -> None:
        result = find_orphan_pages(vault)

        orphan_names = {o.title for o in result.orphans}
        assert "PageA" not in orphan_names
        assert "PageB" not in orphan_names
        assert "PageC" not in orphan_names

    def test_total_pages_excludes_sources(self, vault: Path) -> None:
        result = find_orphan_pages(vault)
        # 5 wiki pages (A-E), sources/collected.md excluded
        assert result.total_pages == 5

    def test_total_links(self, vault: Path) -> None:
        result = find_orphan_pages(vault)
        # A→B,C (2), B→A (1), C→B (1), D→none (0), E→A (1) = 5
        assert result.total_links == 5

    def test_orphan_outgoing_links(self, vault: Path) -> None:
        result = find_orphan_pages(vault)
        orphan_map = {o.title: o for o in result.orphans}

        assert orphan_map["PageD"].outgoing_links == 0
        assert orphan_map["PageE"].outgoing_links == 1

    def test_empty_vault(self, empty_vault: Path) -> None:
        result = find_orphan_pages(empty_vault)
        assert result.orphan_count == 0
        assert result.total_pages == 0

    def test_fully_linked_vault(self, fully_linked_vault: Path) -> None:
        result = find_orphan_pages(fully_linked_vault)
        assert result.orphan_count == 0
        assert result.total_pages == 2

    def test_exclude_names(self, tmp_path: Path) -> None:
        """Index page should not be flagged as orphan."""
        (tmp_path / "index.md").write_text("# Index\nWelcome.\n")
        (tmp_path / "Page.md").write_text("# Page\nContent.\n")

        result = find_orphan_pages(tmp_path)
        orphan_names = {o.title for o in result.orphans}

        # index excluded by default, Page is orphan
        assert "index" not in orphan_names
        assert "Page" in orphan_names

    def test_custom_exclude_names(self, tmp_path: Path) -> None:
        (tmp_path / "start.md").write_text("# Start\n")
        (tmp_path / "Orphan.md").write_text("# Orphan\n")

        result = find_orphan_pages(
            tmp_path,
            exclude_names=frozenset({"start"}),
        )
        orphan_names = {o.title for o in result.orphans}
        assert "start" not in orphan_names
        assert "Orphan" in orphan_names

    def test_self_link_not_counted(self, tmp_path: Path) -> None:
        """A page linking to itself should still be an orphan."""
        (tmp_path / "Self.md").write_text("# Self\nI link to [[Self]].\n")

        result = find_orphan_pages(tmp_path)
        assert result.orphan_count == 1
        assert result.orphans[0].title == "Self"

    def test_case_insensitive_matching(self, tmp_path: Path) -> None:
        """[[pagea]] should match PageA.md."""
        (tmp_path / "PageA.md").write_text("# PageA\nContent.\n")
        (tmp_path / "PageB.md").write_text("# PageB\nSee [[pagea]].\n")

        result = find_orphan_pages(tmp_path)
        orphan_names = {o.title for o in result.orphans}

        assert "PageA" not in orphan_names  # linked by PageB
        assert "PageB" in orphan_names      # no one links to PageB


# ---------------------------------------------------------------------------
# OrphanResult tests
# ---------------------------------------------------------------------------


class TestOrphanResult:
    def test_orphan_rate(self) -> None:
        result = OrphanResult(
            orphans=[
                OrphanPage(Path("a.md"), Path("a.md"), "a"),
            ],
            total_pages=10,
        )
        assert result.orphan_rate == 10.0

    def test_orphan_rate_zero_pages(self) -> None:
        result = OrphanResult(total_pages=0)
        assert result.orphan_rate == 0.0

    def test_connected_count(self) -> None:
        result = OrphanResult(
            orphans=[
                OrphanPage(Path("a.md"), Path("a.md"), "a"),
            ],
            total_pages=5,
        )
        assert result.connected_count == 4

    def test_summary_no_orphans(self) -> None:
        result = OrphanResult(total_pages=5, total_links=10)
        summary = result.summary()
        assert "No orphan pages" in summary
        assert "✓" in summary

    def test_summary_with_orphans(self) -> None:
        result = OrphanResult(
            orphans=[
                OrphanPage(Path("/v/a.md"), Path("a.md"), "a", outgoing_links=2),
            ],
            total_pages=5,
            total_links=8,
        )
        summary = result.summary()
        assert "1 orphan" in summary
        assert "a.md" in summary
        assert "2 outgoing" in summary

    def test_summary_empty_vault(self) -> None:
        result = OrphanResult()
        assert "No wiki pages found" in result.summary()

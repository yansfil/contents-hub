"""Tests for vault_scanner module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.vault_scanner import (
    VaultFile,
    VaultSummary,
    scan_vault_files,
    vault_summary,
    _is_excluded,
    DEFAULT_EXCLUDE_PREFIXES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal Obsidian vault with various markdown files."""
    # Root-level wiki pages
    (tmp_path / "transformers.md").write_text(
        "---\ntitle: Transformers\n---\n# Transformers\n",
        encoding="utf-8",
    )
    (tmp_path / "prompt-engineering.md").write_text(
        "---\ntitle: Prompt Engineering\n---\n# Prompt Engineering\n",
        encoding="utf-8",
    )
    (tmp_path / "quick-note.md").write_text(
        "# Quick Note\nNo frontmatter here.\n",
        encoding="utf-8",
    )

    # Subdirectory with wiki pages
    ai_dir = tmp_path / "ai-research"
    ai_dir.mkdir()
    (ai_dir / "rag.md").write_text(
        "---\ntitle: RAG\n---\n# RAG\n",
        encoding="utf-8",
    )
    (ai_dir / "attention.md").write_text(
        "---\ntitle: Attention Mechanisms\n---\n# Attention\n",
        encoding="utf-8",
    )

    # Nested subdirectory
    deep_dir = tmp_path / "cs" / "algorithms"
    deep_dir.mkdir(parents=True)
    (deep_dir / "sorting.md").write_text("# Sorting Algorithms\n", encoding="utf-8")

    # --- Excluded directories (should NOT appear in results) ---

    # sources/ (immutable collected content)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    (sources_dir / "article.md").write_text("source content", encoding="utf-8")

    # .obsidian/ (Obsidian settings)
    obsidian_dir = tmp_path / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "workspace.md").write_text("workspace config", encoding="utf-8")

    # .llm-wiki/ (metadata)
    meta_dir = tmp_path / ".llm-wiki"
    meta_dir.mkdir()
    (meta_dir / "state.md").write_text("state", encoding="utf-8")

    # .trash/ (Obsidian trash)
    trash_dir = tmp_path / ".trash"
    trash_dir.mkdir()
    (trash_dir / "deleted.md").write_text("deleted", encoding="utf-8")

    # lenses/ (lens config)
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "config.md").write_text("lens config", encoding="utf-8")

    # Non-markdown files (should be ignored by default pattern)
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("text file", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# scan_vault_files: basic scanning
# ---------------------------------------------------------------------------


class TestScanVaultFiles:
    def test_finds_all_wiki_pages(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "transformers.md" in names
        assert "prompt-engineering.md" in names
        assert "quick-note.md" in names
        assert "rag.md" in names
        assert "attention.md" in names
        assert "sorting.md" in names

    def test_correct_count(self, vault: Path):
        files = scan_vault_files(vault)
        assert len(files) == 6

    def test_excludes_sources(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "article.md" not in names

    def test_excludes_obsidian(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "workspace.md" not in names

    def test_excludes_llm_wiki_meta(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "state.md" not in names

    def test_excludes_trash(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "deleted.md" not in names

    def test_excludes_lenses(self, vault: Path):
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "config.md" not in names

    def test_excludes_non_markdown(self, vault: Path):
        files = scan_vault_files(vault)
        extensions = {f.path.suffix for f in files}
        assert extensions == {".md"}

    def test_sorted_by_relative_path(self, vault: Path):
        files = scan_vault_files(vault)
        paths = [str(f.relative_path) for f in files]
        assert paths == sorted(paths)

    def test_relative_paths_are_correct(self, vault: Path):
        files = scan_vault_files(vault)
        rag = next(f for f in files if f.stem == "rag")
        assert rag.relative_path == Path("ai-research/rag.md")

    def test_absolute_paths_exist(self, vault: Path):
        files = scan_vault_files(vault)
        for f in files:
            assert f.path.is_absolute()
            assert f.path.exists()


# ---------------------------------------------------------------------------
# scan_vault_files: file metadata
# ---------------------------------------------------------------------------


class TestFileMetadata:
    def test_size_bytes_positive(self, vault: Path):
        files = scan_vault_files(vault)
        for f in files:
            assert f.size_bytes > 0

    def test_modified_at_is_utc(self, vault: Path):
        files = scan_vault_files(vault)
        for f in files:
            assert f.modified_at is not None
            assert f.modified_at.tzinfo == timezone.utc

    def test_modified_at_reasonable(self, vault: Path):
        files = scan_vault_files(vault)
        now = datetime.now(tz=timezone.utc)
        for f in files:
            # File was just created, should be within the last minute
            assert (now - f.modified_at).total_seconds() < 60


# ---------------------------------------------------------------------------
# scan_vault_files: VaultFile properties
# ---------------------------------------------------------------------------


class TestVaultFileProperties:
    def test_stem(self, vault: Path):
        files = scan_vault_files(vault)
        rag = next(f for f in files if f.name == "rag.md")
        assert rag.stem == "rag"

    def test_directory_root(self, vault: Path):
        files = scan_vault_files(vault)
        root_file = next(f for f in files if f.stem == "transformers")
        assert root_file.directory == ""

    def test_directory_subdir(self, vault: Path):
        files = scan_vault_files(vault)
        rag = next(f for f in files if f.stem == "rag")
        assert rag.directory == "ai-research"

    def test_directory_nested(self, vault: Path):
        files = scan_vault_files(vault)
        sorting = next(f for f in files if f.stem == "sorting")
        assert sorting.directory == "cs/algorithms"

    def test_depth_root(self, vault: Path):
        files = scan_vault_files(vault)
        root_file = next(f for f in files if f.stem == "transformers")
        assert root_file.depth == 0

    def test_depth_subdir(self, vault: Path):
        files = scan_vault_files(vault)
        rag = next(f for f in files if f.stem == "rag")
        assert rag.depth == 1

    def test_depth_nested(self, vault: Path):
        files = scan_vault_files(vault)
        sorting = next(f for f in files if f.stem == "sorting")
        assert sorting.depth == 2


# ---------------------------------------------------------------------------
# scan_vault_files: subdirectory filter
# ---------------------------------------------------------------------------


class TestSubdirectoryFilter:
    def test_subdirectory_filter(self, vault: Path):
        files = scan_vault_files(vault, subdirectory="ai-research")
        assert len(files) == 2
        names = {f.stem for f in files}
        assert names == {"rag", "attention"}

    def test_subdirectory_relative_to_vault(self, vault: Path):
        files = scan_vault_files(vault, subdirectory="ai-research")
        for f in files:
            assert str(f.relative_path).startswith("ai-research/")

    def test_nonexistent_subdirectory(self, vault: Path):
        files = scan_vault_files(vault, subdirectory="nonexistent")
        assert files == []

    def test_nested_subdirectory(self, vault: Path):
        files = scan_vault_files(vault, subdirectory="cs/algorithms")
        assert len(files) == 1
        assert files[0].stem == "sorting"


# ---------------------------------------------------------------------------
# scan_vault_files: custom exclusions
# ---------------------------------------------------------------------------


class TestCustomExclusions:
    def test_no_exclusions(self, vault: Path):
        files = scan_vault_files(vault, exclude_prefixes=())
        names = {f.name for f in files}
        # Should include everything — sources, .obsidian, etc.
        assert "article.md" in names
        assert "workspace.md" in names
        assert "state.md" in names

    def test_custom_exclusion(self, vault: Path):
        # Only exclude ai-research/
        files = scan_vault_files(vault, exclude_prefixes=("ai-research/",))
        names = {f.stem for f in files}
        assert "rag" not in names
        assert "attention" not in names
        # But sources/ etc. are included since we overrode defaults
        assert "article" in names

    def test_multiple_custom_exclusions(self, vault: Path):
        files = scan_vault_files(
            vault,
            exclude_prefixes=("ai-research/", "cs/"),
        )
        names = {f.stem for f in files}
        assert "rag" not in names
        assert "sorting" not in names


# ---------------------------------------------------------------------------
# scan_vault_files: error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_nonexistent_vault_raises(self, tmp_path: Path):
        fake_path = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            scan_vault_files(fake_path)

    def test_file_as_vault_raises(self, tmp_path: Path):
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("hello")
        with pytest.raises(NotADirectoryError):
            scan_vault_files(file_path)

    def test_empty_vault(self, tmp_path: Path):
        files = scan_vault_files(tmp_path)
        assert files == []

    def test_vault_with_only_excluded_dirs(self, tmp_path: Path):
        sources = tmp_path / "sources"
        sources.mkdir()
        (sources / "test.md").write_text("test")
        files = scan_vault_files(tmp_path)
        assert files == []

    def test_string_path_accepted(self, vault: Path):
        files = scan_vault_files(str(vault))
        assert len(files) == 6

    def test_symlink_file(self, vault: Path):
        """Symlinked markdown files should be included."""
        target = vault / "transformers.md"
        link = vault / "transformers-link.md"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")
        files = scan_vault_files(vault)
        names = {f.name for f in files}
        assert "transformers-link.md" in names


# ---------------------------------------------------------------------------
# _is_excluded helper
# ---------------------------------------------------------------------------


class TestIsExcluded:
    def test_excluded_prefix(self):
        assert _is_excluded("sources/rss/article.md", DEFAULT_EXCLUDE_PREFIXES)

    def test_not_excluded(self):
        assert not _is_excluded("ai-research/rag.md", DEFAULT_EXCLUDE_PREFIXES)

    def test_obsidian_excluded(self):
        assert _is_excluded(".obsidian/workspace.md", DEFAULT_EXCLUDE_PREFIXES)

    def test_root_file_not_excluded(self):
        assert not _is_excluded("notes.md", DEFAULT_EXCLUDE_PREFIXES)

    def test_empty_prefixes(self):
        assert not _is_excluded("sources/test.md", ())


# ---------------------------------------------------------------------------
# vault_summary
# ---------------------------------------------------------------------------


class TestVaultSummary:
    def test_total_files(self, vault: Path):
        summary = vault_summary(vault)
        assert summary.total_files == 6

    def test_total_bytes_positive(self, vault: Path):
        summary = vault_summary(vault)
        assert summary.total_bytes > 0

    def test_total_directories(self, vault: Path):
        summary = vault_summary(vault)
        # (root), ai-research, cs/algorithms
        assert summary.total_directories == 3

    def test_directory_counts(self, vault: Path):
        summary = vault_summary(vault)
        assert summary.directories.get("(root)") == 3
        assert summary.directories.get("ai-research") == 2
        assert summary.directories.get("cs/algorithms") == 1

    def test_deepest_depth(self, vault: Path):
        summary = vault_summary(vault)
        assert summary.deepest_depth == 2  # cs/algorithms/sorting.md

    def test_files_included(self, vault: Path):
        summary = vault_summary(vault)
        assert len(summary.files) == 6

    def test_vault_path(self, vault: Path):
        summary = vault_summary(vault)
        assert summary.vault_path == vault.resolve()

    def test_empty_vault(self, tmp_path: Path):
        summary = vault_summary(tmp_path)
        assert summary.total_files == 0
        assert summary.total_bytes == 0
        assert summary.total_directories == 0
        assert summary.deepest_depth == 0

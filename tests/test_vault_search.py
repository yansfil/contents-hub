"""Tests for vault_search module."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.vault_search import (
    VaultSearch,
    WikiPage,
    SearchResult,
    _normalize_tag,
    _filename_to_title,
    _scan_vault,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal Obsidian vault with test wiki pages."""
    # Wiki pages at root
    _write_page(tmp_path / "transformers.md", {
        "title": "Transformers",
        "aliases": ["Attention Is All You Need", "AIAYN"],
        "tags": ["ai", "deep-learning", "nlp"],
    }, "# Transformers\n\nThe transformer architecture...")

    _write_page(tmp_path / "prompt-engineering.md", {
        "title": "Prompt Engineering",
        "aliases": ["Prompting"],
        "tags": ["ai", "llm", "techniques"],
    }, "# Prompt Engineering\n\nTechniques for crafting prompts...")

    _write_page(tmp_path / "rust-language.md", {
        "title": "Rust",
        "aliases": ["Rust Programming Language", "rustlang"],
        "tags": ["programming", "systems"],
    }, "# Rust\n\nA systems programming language...")

    # Wiki page in a lens directory
    ai_dir = tmp_path / "ai-research"
    ai_dir.mkdir()
    _write_page(ai_dir / "rag.md", {
        "title": "Retrieval-Augmented Generation",
        "aliases": ["RAG"],
        "tags": ["ai", "llm", "rag"],
    }, "# RAG\n\nCombining retrieval with generation...")

    # Page with no frontmatter
    (tmp_path / "quick-note.md").write_text("# Quick Note\n\nJust a quick note.\n")

    # Page with nested tags
    _write_page(tmp_path / "gpt-4.md", {
        "title": "GPT-4",
        "aliases": ["GPT4"],
        "tags": ["ai/llm", "ai/openai", "model"],
    }, "# GPT-4\n\nOpenAI's multimodal model...")

    # --- Excluded directories (should NOT appear in results) ---

    # Source files
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(sources_dir / "2024-01-15-some-article.md", {
        "title": "Some Article",
        "tags": ["ai"],
        "source_type": "rss",
    }, "# Some Article\n\nContent from RSS...")

    # Obsidian metadata
    obsidian_dir = tmp_path / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "workspace.md").write_text("workspace config")

    # llm-wiki metadata
    meta_dir = tmp_path / ".llm-wiki"
    meta_dir.mkdir()
    (meta_dir / "state.md").write_text("state")

    return tmp_path


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def search(config: WikiConfig) -> VaultSearch:
    return VaultSearch(config)


def _write_page(path: Path, fm: dict, body: str) -> None:
    """Helper to write a markdown page with YAML frontmatter."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{key}: |")
            for line in value.split("\n"):
                lines.append(f"  {line}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# _normalize_tag
# ---------------------------------------------------------------------------


class TestNormalizeTag:
    def test_strips_hash(self):
        assert _normalize_tag("#ai") == "ai"

    def test_lowercase(self):
        assert _normalize_tag("AI") == "ai"

    def test_preserves_nested(self):
        assert _normalize_tag("#ai/llm") == "ai/llm"

    def test_strips_whitespace(self):
        assert _normalize_tag("  #research  ") == "research"

    def test_plain_tag(self):
        assert _normalize_tag("programming") == "programming"


# ---------------------------------------------------------------------------
# _filename_to_title
# ---------------------------------------------------------------------------


class TestFilenameToTitle:
    def test_hyphen_to_space(self):
        assert _filename_to_title("prompt-engineering") == "prompt engineering"

    def test_strips_date_prefix(self):
        assert _filename_to_title("2024-01-15-my-article") == "my article"

    def test_underscore_to_space(self):
        assert _filename_to_title("rust_language") == "rust language"

    def test_no_date_prefix(self):
        assert _filename_to_title("transformers") == "transformers"


# ---------------------------------------------------------------------------
# VaultSearch.by_title
# ---------------------------------------------------------------------------


class TestByTitle:
    def test_exact_match(self, search: VaultSearch):
        results = search.by_title("Transformers")
        assert len(results) >= 1
        assert results[0].page.title == "Transformers"
        assert results[0].match_type == "title"

    def test_case_insensitive(self, search: VaultSearch):
        results = search.by_title("transformers")
        assert len(results) >= 1
        assert results[0].page.title == "Transformers"

    def test_substring_match(self, search: VaultSearch):
        results = search.by_title("prompt")
        assert len(results) >= 1
        titles = [r.page.title for r in results]
        assert "Prompt Engineering" in titles

    def test_no_match(self, search: VaultSearch):
        results = search.by_title("quantum-computing")
        assert len(results) == 0

    def test_excludes_sources(self, search: VaultSearch):
        results = search.by_title("Some Article")
        assert len(results) == 0

    def test_matches_filename(self, search: VaultSearch):
        # "quick-note" has no frontmatter title, matches by filename
        results = search.by_title("quick note")
        assert len(results) >= 1
        assert results[0].page.name == "quick-note"

    def test_finds_in_subdirectory(self, search: VaultSearch):
        results = search.by_title("Retrieval-Augmented")
        assert len(results) >= 1
        assert results[0].page.title == "Retrieval-Augmented Generation"


# ---------------------------------------------------------------------------
# VaultSearch.by_alias
# ---------------------------------------------------------------------------


class TestByAlias:
    def test_exact_alias(self, search: VaultSearch):
        results = search.by_alias("RAG")
        assert len(results) >= 1
        assert results[0].page.title == "Retrieval-Augmented Generation"
        assert results[0].match_type == "alias"

    def test_case_insensitive_alias(self, search: VaultSearch):
        results = search.by_alias("aiayn")
        assert len(results) >= 1
        assert results[0].page.title == "Transformers"

    def test_substring_alias(self, search: VaultSearch):
        results = search.by_alias("Attention")
        assert len(results) >= 1
        assert results[0].page.title == "Transformers"

    def test_no_match(self, search: VaultSearch):
        results = search.by_alias("nonexistent-alias")
        assert len(results) == 0

    def test_multiple_aliases(self, search: VaultSearch):
        # "Rust Programming Language" and "rustlang" are both aliases
        r1 = search.by_alias("rustlang")
        r2 = search.by_alias("Rust Programming")
        assert len(r1) >= 1
        assert len(r2) >= 1
        assert r1[0].page.path == r2[0].page.path


# ---------------------------------------------------------------------------
# VaultSearch.by_tag
# ---------------------------------------------------------------------------


class TestByTag:
    def test_exact_tag(self, search: VaultSearch):
        results = search.by_tag("ai")
        assert len(results) >= 3  # transformers, prompt-eng, rag, gpt-4
        assert results[0].match_type == "tag"

    def test_tag_with_hash(self, search: VaultSearch):
        results = search.by_tag("#programming")
        assert len(results) >= 1
        assert results[0].page.title == "Rust"

    def test_nested_tag_parent(self, search: VaultSearch):
        # Searching "ai" should match "ai/llm" and "ai/openai"
        results = search.by_tag("ai")
        pages_with_nested = [r for r in results if r.page.title == "GPT-4"]
        assert len(pages_with_nested) >= 1

    def test_nested_tag_exact(self, search: VaultSearch):
        results = search.by_tag("ai/llm")
        assert len(results) >= 1
        titles = [r.page.title for r in results]
        assert "GPT-4" in titles

    def test_no_match(self, search: VaultSearch):
        results = search.by_tag("nonexistent")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# VaultSearch.find (combined)
# ---------------------------------------------------------------------------


class TestFind:
    def test_combined_deduplicates(self, search: VaultSearch):
        # "RAG" matches both alias and tag — should appear once
        results = search.find("RAG")
        paths = [r.page.path for r in results]
        assert len(paths) == len(set(paths)), "Results contain duplicates"

    def test_title_before_alias(self, search: VaultSearch):
        # "Rust" matches title "Rust" and alias "Rust Programming Language"
        results = search.find("Rust")
        assert len(results) >= 1
        assert results[0].match_type == "title"

    def test_finds_across_all_types(self, search: VaultSearch):
        # "ai" matches tags on multiple pages
        results = search.find("ai")
        assert len(results) >= 3


# ---------------------------------------------------------------------------
# VaultSearch.find_exact
# ---------------------------------------------------------------------------


class TestFindExact:
    def test_exact_by_filename(self, search: VaultSearch):
        page = search.find_exact("transformers")
        assert page is not None
        assert page.title == "Transformers"

    def test_exact_by_title(self, search: VaultSearch):
        page = search.find_exact("Prompt Engineering")
        assert page is not None
        assert page.name == "prompt-engineering"

    def test_exact_by_alias(self, search: VaultSearch):
        page = search.find_exact("RAG")
        assert page is not None
        assert page.title == "Retrieval-Augmented Generation"

    def test_exact_not_found(self, search: VaultSearch):
        page = search.find_exact("nonexistent page")
        assert page is None

    def test_case_insensitive(self, search: VaultSearch):
        page = search.find_exact("TRANSFORMERS")
        assert page is not None


# ---------------------------------------------------------------------------
# VaultSearch.all_tags
# ---------------------------------------------------------------------------


class TestAllTags:
    def test_returns_all_tags(self, search: VaultSearch):
        tags = search.all_tags()
        assert "ai" in tags
        assert "programming" in tags

    def test_counts_are_correct(self, search: VaultSearch):
        tags = search.all_tags()
        # "ai" appears in: transformers, prompt-eng, rag, gpt-4 (as ai/llm, ai/openai)
        assert tags["ai"] >= 3

    def test_sorted_by_count(self, search: VaultSearch):
        tags = search.all_tags()
        counts = list(tags.values())
        assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# VaultSearch.pages_in_directory
# ---------------------------------------------------------------------------


class TestPagesInDirectory:
    def test_finds_pages_in_dir(self, search: VaultSearch):
        pages = search.pages_in_directory("ai-research")
        assert len(pages) == 1
        assert pages[0].title == "Retrieval-Augmented Generation"

    def test_empty_directory(self, search: VaultSearch):
        pages = search.pages_in_directory("nonexistent")
        assert len(pages) == 0


# ---------------------------------------------------------------------------
# WikiPage methods
# ---------------------------------------------------------------------------


class TestWikiPage:
    def test_wikilink(self):
        page = WikiPage(
            path=Path("/vault/test.md"),
            relative_path=Path("test.md"),
            title="Test Page",
        )
        assert page.wikilink == "[[test]]"

    def test_matches_title(self):
        page = WikiPage(
            path=Path("/vault/test.md"),
            relative_path=Path("test.md"),
            title="Machine Learning",
        )
        assert page.matches_title("machine")
        assert page.matches_title("LEARNING")
        assert not page.matches_title("quantum")

    def test_matches_alias(self):
        page = WikiPage(
            path=Path("/vault/test.md"),
            relative_path=Path("test.md"),
            title="Test",
            aliases=["ML", "Statistical Learning"],
        )
        assert page.matches_alias("ML")
        assert page.matches_alias("statistical")
        assert not page.matches_alias("quantum")

    def test_matches_tag(self):
        page = WikiPage(
            path=Path("/vault/test.md"),
            relative_path=Path("test.md"),
            title="Test",
            tags=["ai/llm", "research"],
        )
        assert page.matches_tag("ai")
        assert page.matches_tag("ai/llm")
        assert page.matches_tag("#research")
        assert not page.matches_tag("programming")


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    def test_cache_invalidation(self, config: WikiConfig, search: VaultSearch):
        # First scan
        results1 = search.by_title("Transformers")
        assert len(results1) >= 1

        # Add a new page
        _write_page(config.vault_path / "new-page.md", {
            "title": "New Page",
            "tags": ["test"],
        }, "# New Page")

        # Without invalidation, cache still has old data
        results2 = search.by_title("New Page")
        assert len(results2) == 0  # cached, doesn't see new file

        # After invalidation, picks up new file
        search.invalidate_cache()
        results3 = search.by_title("New Page")
        assert len(results3) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_vault(self, tmp_path: Path):
        config = WikiConfig(vault_path=tmp_path)
        search = VaultSearch(config)
        assert search.by_title("anything") == []
        assert search.all_tags() == {}

    def test_page_with_string_aliases(self, tmp_path: Path):
        """aliases: can be a single string instead of list."""
        _write_page(tmp_path / "test.md", {
            "title": "Test",
            "aliases": "Single Alias",  # string, not list
        }, "# Test")
        config = WikiConfig(vault_path=tmp_path)
        search = VaultSearch(config)
        results = search.by_alias("Single Alias")
        assert len(results) == 1

    def test_page_with_string_tags(self, tmp_path: Path):
        """tags: can be a single string instead of list."""
        _write_page(tmp_path / "test.md", {
            "title": "Test",
            "tags": "solo-tag",
        }, "# Test")
        config = WikiConfig(vault_path=tmp_path)
        search = VaultSearch(config)
        results = search.by_tag("solo-tag")
        assert len(results) == 1

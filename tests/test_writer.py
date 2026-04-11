"""Tests for Obsidian vault filesystem writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.writer import (
    WriteAction,
    WriteResult,
    write_source,
    write_wiki_page,
    update_wiki_page,
    write_sources_batch,
    ensure_vault_structure,
    ensure_lens_directory,
    list_source_files,
    list_wiki_pages,
    parse_frontmatter,
    wiki_filename,
    resolve_wikilink,
    _parse_simple_value,
    MAX_SLUG_LENGTH,
)

FIXED_TIME = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """Create a WikiConfig pointing to a temporary vault directory."""
    return WikiConfig(vault_path=tmp_path)


@pytest.fixture
def vault_with_structure(vault: WikiConfig) -> WikiConfig:
    """Vault with standard directory structure created."""
    ensure_vault_structure(vault)
    return vault


@pytest.fixture
def sample_source_content() -> str:
    return (
        "---\n"
        "source_type: rss\n"
        "url: https://example.com/post\n"
        "title: Sample Post\n"
        "collected: 2024-01-15T10:30:00+00:00\n"
        "---\n"
        "\n"
        "# Sample Post\n"
        "\n"
        "This is the content.\n"
        "\n"
        "---\n"
        "Source: https://example.com/post\n"
    )


@pytest.fixture
def sample_wiki_content() -> str:
    return (
        "---\n"
        "title: Transformers\n"
        "type: wiki\n"
        "lenses:\n"
        "  - ai\n"
        "  - nlp\n"
        "tags:\n"
        "  - deep-learning\n"
        "  - attention\n"
        "---\n"
        "\n"
        "# Transformers\n"
        "\n"
        "Transformers are a neural network architecture.\n"
        "\n"
        "## Key Concepts\n"
        "\n"
        "- [[Self-Attention]]\n"
        "- [[Multi-Head Attention]]\n"
    )


# ---------------------------------------------------------------------------
# write_source
# ---------------------------------------------------------------------------


class TestWriteSource:
    def test_creates_file(self, vault: WikiConfig, sample_source_content: str) -> None:
        result = write_source(
            vault, "rss", "Sample Post", sample_source_content,
            published_at=FIXED_TIME,
        )

        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert result.path.read_text(encoding="utf-8") == sample_source_content
        assert result.bytes_written > 0

    def test_creates_parent_directories(self, vault: WikiConfig) -> None:
        result = write_source(
            vault, "rss", "Test", "# Test\n",
            published_at=FIXED_TIME,
        )

        assert result.path.parent.exists()
        assert result.path.parent.name == "rss"
        assert result.path.parent.parent.name == "sources"

    def test_skips_existing_file(self, vault: WikiConfig) -> None:
        content = "# Original\n"
        result1 = write_source(
            vault, "rss", "Test", content, published_at=FIXED_TIME,
        )
        result2 = write_source(
            vault, "rss", "Test", "# Modified\n", published_at=FIXED_TIME,
        )

        assert result1.action == WriteAction.CREATED
        assert result2.action == WriteAction.SKIPPED
        # Content should remain original
        assert result1.path.read_text(encoding="utf-8") == content

    def test_overwrite_flag(self, vault: WikiConfig) -> None:
        write_source(vault, "rss", "Test", "# Original\n", published_at=FIXED_TIME)
        result = write_source(
            vault, "rss", "Test", "# Modified\n",
            published_at=FIXED_TIME, overwrite=True,
        )

        assert result.action == WriteAction.UPDATED  # file existed, so it's an update
        assert result.path.read_text(encoding="utf-8") == "# Modified\n"

    def test_relative_path(self, vault: WikiConfig) -> None:
        result = write_source(
            vault, "youtube", "My Video", "# My Video\n",
            published_at=FIXED_TIME,
        )

        assert result.relative_path == Path("sources/youtube/2024-01-15-my-video.md")

    def test_different_source_types(self, vault: WikiConfig) -> None:
        for stype in ("rss", "youtube", "browser", "twitter"):
            result = write_source(
                vault, stype, f"Test {stype}", f"# Test {stype}\n",
                published_at=FIXED_TIME,
            )
            assert stype in str(result.relative_path)


# ---------------------------------------------------------------------------
# write_wiki_page
# ---------------------------------------------------------------------------


class TestWriteWikiPage:
    def test_creates_page(self, vault: WikiConfig) -> None:
        result = write_wiki_page(
            vault, "Transformers", "# Transformers\n\nA neural network architecture.\n"
        )

        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert "Transformers" in result.path.read_text(encoding="utf-8")

    def test_filename_from_title(self, vault: WikiConfig) -> None:
        result = write_wiki_page(vault, "Prompt Engineering", "# Prompt Engineering\n")

        assert result.relative_path == Path("prompt-engineering.md")

    def test_with_directory(self, vault: WikiConfig) -> None:
        result = write_wiki_page(
            vault, "Transformers", "# Transformers\n", directory="ai"
        )

        assert result.relative_path == Path("ai/transformers.md")
        assert result.path.parent.name == "ai"

    def test_with_frontmatter_dict(self, vault: WikiConfig) -> None:
        result = write_wiki_page(
            vault,
            "Test Page",
            "# Test Page\n\nContent here.\n",
            frontmatter={"title": "Test Page", "type": "wiki", "tags": ["test"]},
        )

        content = result.path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "title: Test Page" in content
        assert "type: wiki" in content
        assert "  - test" in content
        assert "# Test Page" in content

    def test_frontmatter_not_duplicated(self, vault: WikiConfig) -> None:
        """If content already has frontmatter, don't add another."""
        content_with_fm = "---\ntitle: Existing\n---\n\n# Existing\n"
        result = write_wiki_page(
            vault, "Existing", content_with_fm,
            frontmatter={"title": "Override"},
        )

        content = result.path.read_text(encoding="utf-8")
        # Should only have one frontmatter block
        assert content.count("---") == 2  # opening + closing

    def test_overwrites_existing(self, vault: WikiConfig) -> None:
        write_wiki_page(vault, "Page", "# Original\n")
        result = write_wiki_page(vault, "Page", "# Updated\n")

        assert result.action == WriteAction.UPDATED
        assert "Updated" in result.path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# update_wiki_page
# ---------------------------------------------------------------------------


class TestUpdateWikiPage:
    def test_creates_if_not_exists(self, vault: WikiConfig) -> None:
        result = update_wiki_page(
            vault, "New Page", "# New Page\n",
            merge_frontmatter={"type": "wiki"},
        )

        assert result.action == WriteAction.CREATED
        content = result.path.read_text(encoding="utf-8")
        assert "type: wiki" in content

    def test_merges_frontmatter(self, vault: WikiConfig) -> None:
        # Create initial page
        initial = "---\ntitle: Test\ntags:\n  - old\n---\n\n# Test\n\nOld body.\n"
        write_wiki_page(vault, "Test", initial)

        # Update with merged frontmatter
        result = update_wiki_page(
            vault, "Test", "# Test\n\nNew body.\n",
            merge_frontmatter={"updated": "2024-01-15", "type": "wiki"},
        )

        assert result.action == WriteAction.UPDATED
        content = result.path.read_text(encoding="utf-8")
        # Original frontmatter preserved
        assert "title: Test" in content
        # New frontmatter added
        assert "type: wiki" in content
        # Body replaced
        assert "New body" in content
        assert "Old body" not in content

    def test_update_preserves_existing_keys(self, vault: WikiConfig) -> None:
        initial = "---\ntitle: Page\nauthor: Alice\n---\n\n# Page\n"
        write_wiki_page(vault, "Page", initial)

        result = update_wiki_page(
            vault, "Page", "# Page\n\nUpdated content.\n",
            merge_frontmatter={"status": "draft"},
        )

        content = result.path.read_text(encoding="utf-8")
        assert "author: Alice" in content
        assert "status: draft" in content


# ---------------------------------------------------------------------------
# write_sources_batch
# ---------------------------------------------------------------------------


class TestWriteSourcesBatch:
    def test_batch_write(self, vault: WikiConfig) -> None:
        items = [
            ("rss", "Post 1", "# Post 1\n", FIXED_TIME, None),
            ("rss", "Post 2", "# Post 2\n", FIXED_TIME, None),
            ("youtube", "Video 1", "# Video 1\n", FIXED_TIME, None),
        ]

        results = write_sources_batch(vault, items)

        assert len(results) == 3
        assert all(r.action == WriteAction.CREATED for r in results)
        assert all(r.path.exists() for r in results)

    def test_batch_skip_existing(self, vault: WikiConfig) -> None:
        write_source(vault, "rss", "Existing", "# Existing\n", published_at=FIXED_TIME)

        items = [
            ("rss", "Existing", "# Existing\n", FIXED_TIME, None),
            ("rss", "New", "# New\n", FIXED_TIME, None),
        ]

        results = write_sources_batch(vault, items)

        assert results[0].action == WriteAction.SKIPPED
        assert results[1].action == WriteAction.CREATED


# ---------------------------------------------------------------------------
# ensure_vault_structure
# ---------------------------------------------------------------------------


class TestEnsureVaultStructure:
    def test_creates_directories(self, vault: WikiConfig) -> None:
        created = ensure_vault_structure(vault)

        assert len(created) >= 5
        assert (vault.vault_path / "sources" / "rss").is_dir()
        assert (vault.vault_path / "sources" / "youtube").is_dir()
        assert (vault.vault_path / "sources" / "browser").is_dir()
        assert (vault.vault_path / "sources" / "twitter").is_dir()
        assert vault.meta_path.is_dir()

    def test_idempotent(self, vault: WikiConfig) -> None:
        ensure_vault_structure(vault)
        created = ensure_vault_structure(vault)

        # Second call should create nothing
        assert len(created) == 0


# ---------------------------------------------------------------------------
# ensure_lens_directory
# ---------------------------------------------------------------------------


class TestEnsureLensDirectory:
    def test_creates_lens_dir(self, vault: WikiConfig) -> None:
        path = ensure_lens_directory(vault, "AI & Machine Learning")

        assert path.is_dir()
        assert path.name == "ai-machine-learning"

    def test_idempotent(self, vault: WikiConfig) -> None:
        path1 = ensure_lens_directory(vault, "DevOps")
        path2 = ensure_lens_directory(vault, "DevOps")

        assert path1 == path2

    def test_empty_lens_fallback(self, vault: WikiConfig) -> None:
        path = ensure_lens_directory(vault, "")

        assert path.name == "uncategorized"


# ---------------------------------------------------------------------------
# list_source_files
# ---------------------------------------------------------------------------


class TestListSourceFiles:
    def test_list_all(self, vault_with_structure: WikiConfig) -> None:
        vault = vault_with_structure
        # Create some source files
        write_source(vault, "rss", "Post A", "# A\n", published_at=FIXED_TIME)
        write_source(vault, "youtube", "Video B", "# B\n", published_at=FIXED_TIME)

        files = list_source_files(vault)
        assert len(files) == 2

    def test_filter_by_type(self, vault_with_structure: WikiConfig) -> None:
        vault = vault_with_structure
        write_source(vault, "rss", "Post", "# Post\n", published_at=FIXED_TIME)
        write_source(vault, "youtube", "Video", "# Video\n", published_at=FIXED_TIME)

        rss_files = list_source_files(vault, source_type="rss")
        assert len(rss_files) == 1
        assert "rss" in str(rss_files[0])

    def test_empty_vault(self, vault: WikiConfig) -> None:
        files = list_source_files(vault)
        assert files == []


# ---------------------------------------------------------------------------
# list_wiki_pages
# ---------------------------------------------------------------------------


class TestListWikiPages:
    def test_lists_root_pages(self, vault_with_structure: WikiConfig) -> None:
        vault = vault_with_structure
        write_wiki_page(vault, "Page A", "# A\n")
        write_wiki_page(vault, "Page B", "# B\n")

        pages = list_wiki_pages(vault)
        assert len(pages) == 2

    def test_excludes_sources(self, vault_with_structure: WikiConfig) -> None:
        vault = vault_with_structure
        write_source(vault, "rss", "Source", "# Source\n", published_at=FIXED_TIME)
        write_wiki_page(vault, "Wiki Page", "# Wiki\n")

        pages = list_wiki_pages(vault)
        assert len(pages) == 1
        assert "wiki-page" in pages[0].name

    def test_lists_from_subdirectory(self, vault_with_structure: WikiConfig) -> None:
        vault = vault_with_structure
        write_wiki_page(vault, "Transformers", "# Transformers\n", directory="ai")
        write_wiki_page(vault, "GPT", "# GPT\n", directory="ai")

        pages = list_wiki_pages(vault, directory="ai")
        assert len(pages) == 2


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_basic_frontmatter(self) -> None:
        content = "---\ntitle: Hello\ntype: wiki\n---\n\n# Hello\n"
        fm, body = parse_frontmatter(content)

        assert fm["title"] == "Hello"
        assert fm["type"] == "wiki"
        assert "# Hello" in body

    def test_no_frontmatter(self) -> None:
        content = "# Just a heading\n\nSome content.\n"
        fm, body = parse_frontmatter(content)

        assert fm == {}
        assert body == content

    def test_list_in_frontmatter(self) -> None:
        content = "---\ntags:\n  - ai\n  - nlp\n---\n\nBody\n"
        fm, body = parse_frontmatter(content)

        assert fm["tags"] == ["ai", "nlp"]

    def test_unclosed_frontmatter(self) -> None:
        content = "---\ntitle: Hello\nNo closing fence"
        fm, body = parse_frontmatter(content)

        # Should return empty frontmatter since it's malformed
        assert fm == {}

    def test_empty_frontmatter(self) -> None:
        content = "---\n---\n\nBody here\n"
        fm, body = parse_frontmatter(content)

        assert fm == {} or fm is None or not fm
        assert "Body here" in body


# ---------------------------------------------------------------------------
# wiki_filename
# ---------------------------------------------------------------------------


class TestWikiFilename:
    def test_basic(self) -> None:
        assert wiki_filename("Transformers") == "transformers.md"

    def test_multi_word(self) -> None:
        assert wiki_filename("Prompt Engineering") == "prompt-engineering.md"

    def test_special_chars(self) -> None:
        result = wiki_filename("AI: The Future (2024)")
        assert result == "ai-the-future-2024.md"

    def test_empty_fallback(self) -> None:
        assert wiki_filename("") == "untitled.md"

    def test_long_title_truncated(self) -> None:
        long = "a" * 200
        result = wiki_filename(long)
        assert len(result) <= MAX_SLUG_LENGTH + 3 + 5  # generous bound


# ---------------------------------------------------------------------------
# resolve_wikilink
# ---------------------------------------------------------------------------


class TestResolveWikilink:
    def test_finds_in_root(self, vault: WikiConfig) -> None:
        write_wiki_page(vault, "Transformers", "# Transformers\n")

        result = resolve_wikilink(vault, "Transformers")
        assert result is not None
        assert result.name == "transformers.md"

    def test_finds_in_directory(self, vault: WikiConfig) -> None:
        write_wiki_page(vault, "GPT", "# GPT\n", directory="ai")

        result = resolve_wikilink(vault, "GPT", directory="ai")
        assert result is not None
        assert result.name == "gpt.md"

    def test_preferred_directory_first(self, vault: WikiConfig) -> None:
        write_wiki_page(vault, "Test", "# Root\n")
        write_wiki_page(vault, "Test", "# AI\n", directory="ai")

        # Should find the one in the preferred directory
        result = resolve_wikilink(vault, "Test", directory="ai")
        assert result is not None
        assert "ai" in str(result)

    def test_not_found(self, vault: WikiConfig) -> None:
        result = resolve_wikilink(vault, "Nonexistent")
        assert result is None

    def test_ignores_source_files(self, vault: WikiConfig) -> None:
        write_source(vault, "rss", "Test", "# Test\n", published_at=FIXED_TIME)

        result = resolve_wikilink(vault, "Test")
        assert result is None  # sources should not be resolved as wiki pages


# ---------------------------------------------------------------------------
# _parse_simple_value
# ---------------------------------------------------------------------------


class TestParseSimpleValue:
    def test_null(self) -> None:
        assert _parse_simple_value("null") is None
        assert _parse_simple_value("~") is None

    def test_bool(self) -> None:
        assert _parse_simple_value("true") is True
        assert _parse_simple_value("false") is False

    def test_int(self) -> None:
        assert _parse_simple_value("42") == 42

    def test_float(self) -> None:
        assert _parse_simple_value("3.14") == 3.14

    def test_quoted_string(self) -> None:
        assert _parse_simple_value('"hello"') == "hello"
        assert _parse_simple_value("'hello'") == "hello"

    def test_plain_string(self) -> None:
        assert _parse_simple_value("hello world") == "hello world"

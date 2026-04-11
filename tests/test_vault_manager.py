"""Tests for the VaultManager unified facade."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.lens import Lens
from llm_wiki.vault_manager import (
    VaultManager,
    WriteAction,
    _source_dedup_key,
    _wiki_dedup_key,
)
from llm_wiki.writer import ensure_vault_structure

FIXED_TIME = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """A temporary directory acting as an Obsidian vault."""
    return tmp_path


@pytest.fixture
def vm(vault_dir: Path) -> VaultManager:
    """VaultManager with a temp vault."""
    return VaultManager(vault_dir)


@pytest.fixture
def vm_with_structure(vm: VaultManager) -> VaultManager:
    """VaultManager with standard directory structure created."""
    vm.ensure_structure()
    return vm


@pytest.fixture
def sample_source_content() -> str:
    return (
        "---\n"
        "source_type: rss\n"
        "url: https://example.com/post\n"
        "title: Sample Post\n"
        "---\n\n"
        "# Sample Post\n\n"
        "Content here.\n"
    )


@pytest.fixture
def lens_ai(vm_with_structure: VaultManager) -> Lens:
    """Create and save an AI research lens."""
    lens = Lens(
        id="ai-research",
        name="AI Research",
        description="Artificial intelligence papers and articles",
        keywords=["machine learning", "deep learning", "LLM"],
        default_tags=["ai", "research"],
        wiki_directory="topics/ai-research",
    )
    vm_with_structure.lens_store.save(lens)
    return lens


# ---------------------------------------------------------------------------
# Initialization and Properties
# ---------------------------------------------------------------------------


class TestInit:
    def test_from_path(self, vault_dir: Path) -> None:
        vm = VaultManager(vault_dir)
        assert vm.vault_path == vault_dir

    def test_from_config(self, vault_dir: Path) -> None:
        config = WikiConfig(vault_path=vault_dir)
        vm = VaultManager(config=config)
        assert vm.vault_path == vault_dir

    def test_vault_name(self, vault_dir: Path) -> None:
        vm = VaultManager(vault_dir)
        assert vm.vault_name == vault_dir.name

    def test_sources_path(self, vm: VaultManager) -> None:
        assert vm.sources_path == vm.vault_path / "sources"

    def test_meta_path(self, vm: VaultManager) -> None:
        assert vm.meta_path == vm.vault_path / ".llm-wiki"


# ---------------------------------------------------------------------------
# Directory Structure
# ---------------------------------------------------------------------------


class TestEnsureStructure:
    def test_creates_standard_dirs(self, vm: VaultManager) -> None:
        created = vm.ensure_structure()

        assert (vm.vault_path / "sources" / "rss").is_dir()
        assert (vm.vault_path / "sources" / "youtube").is_dir()
        assert (vm.vault_path / "sources" / "browser").is_dir()
        assert (vm.vault_path / "sources" / "twitter").is_dir()
        assert (vm.vault_path / ".llm-wiki").is_dir()
        assert (vm.vault_path / "lenses").is_dir()
        assert len(created) >= 6

    def test_idempotent(self, vm: VaultManager) -> None:
        vm.ensure_structure()
        created = vm.ensure_structure()
        assert len(created) == 0

    def test_ensure_lens_dir_from_string(self, vm_with_structure: VaultManager) -> None:
        path = vm_with_structure.ensure_lens_dir("AI & ML")
        assert path.is_dir()
        assert "ai-ml" in path.name.lower()

    def test_ensure_lens_dir_from_lens(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        path = vm_with_structure.ensure_lens_dir(lens_ai)
        assert path.is_dir()
        assert path == vm_with_structure.vault_path / "topics" / "ai-research"

    def test_ensure_lens_dir_from_lens_id(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        path = vm_with_structure.ensure_lens_dir("ai-research")
        assert path.is_dir()
        assert path == vm_with_structure.vault_path / "topics" / "ai-research"


# ---------------------------------------------------------------------------
# Source File Writing
# ---------------------------------------------------------------------------


class TestWriteSource:
    def test_creates_source_file(
        self, vm_with_structure: VaultManager, sample_source_content: str
    ) -> None:
        result = vm_with_structure.write_source(
            "rss",
            "Sample Post",
            sample_source_content,
            url="https://example.com/post",
            published_at=FIXED_TIME,
        )

        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert "sources/rss" in str(result.relative_path)

    def test_skips_duplicate_by_path(
        self, vm_with_structure: VaultManager
    ) -> None:
        content = "# Test\n"
        r1 = vm_with_structure.write_source(
            "rss", "Test", content, published_at=FIXED_TIME
        )
        r2 = vm_with_structure.write_source(
            "rss", "Test", content, published_at=FIXED_TIME
        )

        assert r1.action == WriteAction.CREATED
        assert r2.action == WriteAction.SKIPPED

    def test_session_dedup_by_url(
        self, vm_with_structure: VaultManager
    ) -> None:
        url = "https://example.com/unique-article"
        r1 = vm_with_structure.write_source(
            "rss", "First Title", "# Content\n",
            url=url, published_at=FIXED_TIME,
        )
        # Different title but same URL → session dedup catches it
        r2 = vm_with_structure.write_source(
            "rss", "Different Title", "# Different\n",
            url=url, published_at=FIXED_TIME,
        )

        assert r1.action == WriteAction.CREATED
        assert r2.action == WriteAction.SKIPPED

    def test_overwrite_bypasses_dedup(
        self, vm_with_structure: VaultManager
    ) -> None:
        url = "https://example.com/article"
        vm_with_structure.write_source(
            "rss", "Test", "# Original\n",
            url=url, published_at=FIXED_TIME,
        )
        result = vm_with_structure.write_source(
            "rss", "Test", "# Updated\n",
            url=url, published_at=FIXED_TIME, overwrite=True,
        )

        assert result.action == WriteAction.UPDATED

    def test_batch_write(self, vm_with_structure: VaultManager) -> None:
        items = [
            {
                "source_type": "rss",
                "title": "Post A",
                "content": "# A\n",
                "url": "https://a.example.com",
                "published_at": FIXED_TIME,
            },
            {
                "source_type": "youtube",
                "title": "Video B",
                "content": "# B\n",
                "url": "https://b.example.com",
                "published_at": FIXED_TIME,
            },
        ]

        results = vm_with_structure.write_sources_batch(items)
        assert len(results) == 2
        assert all(r.action == WriteAction.CREATED for r in results)


# ---------------------------------------------------------------------------
# Wiki Page Writing
# ---------------------------------------------------------------------------


class TestWriteWiki:
    def test_creates_wiki_page(self, vm_with_structure: VaultManager) -> None:
        result = vm_with_structure.write_wiki(
            "Transformers",
            "Transformers use self-attention to process sequences.",
            tags=["ai", "deep-learning"],
        )

        assert result.action == WriteAction.CREATED
        assert result.path.exists()
        assert "transformers" in result.path.name

    def test_with_lens_object(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        result = vm_with_structure.write_wiki(
            "Attention Mechanism",
            "Self-attention is a key component of Transformers.",
            lens=lens_ai,
        )

        assert result.action == WriteAction.CREATED
        assert "topics/ai-research" in str(result.relative_path)
        # Should auto-apply lens tags
        content = result.path.read_text(encoding="utf-8")
        assert "ai" in content
        assert "research" in content

    def test_with_lens_id(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        result = vm_with_structure.write_wiki(
            "BERT",
            "BERT is a bidirectional encoder.",
            lens="ai-research",
        )

        assert result.action == WriteAction.CREATED
        assert "topics/ai-research" in str(result.relative_path)

    def test_with_unknown_lens_string(
        self, vm_with_structure: VaultManager
    ) -> None:
        result = vm_with_structure.write_wiki(
            "React Hooks",
            "React hooks let you use state in functional components.",
            lens="frontend",
        )

        assert result.action == WriteAction.CREATED
        assert "frontend" in str(result.relative_path)
        assert (vm_with_structure.vault_path / "frontend").is_dir()

    def test_with_wikilinks(self, vm_with_structure: VaultManager) -> None:
        result = vm_with_structure.write_wiki(
            "GPT",
            "GPT is a generative pre-trained transformer.",
            wikilinks=["Transformers", "BERT", "Self-Attention"],
        )

        content = result.path.read_text(encoding="utf-8")
        assert "[[Transformers]]" in content
        assert "[[BERT]]" in content

    def test_update_existing(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page", "# Original content")
        result = vm_with_structure.write_wiki("Page", "# Updated content")

        assert result.action == WriteAction.UPDATED
        content = result.path.read_text(encoding="utf-8")
        assert "Updated content" in content

    def test_skip_existing(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page", "# Original")
        result = vm_with_structure.write_wiki(
            "Page", "# New", update_existing=False
        )

        assert result.action == WriteAction.SKIPPED

    def test_batch_write(self, vm_with_structure: VaultManager) -> None:
        notes = [
            {"title": "Page A", "content": "Content A"},
            {"title": "Page B", "content": "Content B", "tags": ["test"]},
        ]

        results = vm_with_structure.write_wiki_batch(notes)
        assert len(results) == 2
        assert all(r.action == WriteAction.CREATED for r in results)


# ---------------------------------------------------------------------------
# File Queries
# ---------------------------------------------------------------------------


class TestFileQueries:
    def test_list_sources(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_source(
            "rss", "Post", "# Post\n", published_at=FIXED_TIME
        )
        vm_with_structure.write_source(
            "youtube", "Video", "# Video\n", published_at=FIXED_TIME
        )

        all_sources = vm_with_structure.list_sources()
        assert len(all_sources) == 2

        rss_only = vm_with_structure.list_sources(source_type="rss")
        assert len(rss_only) == 1

    def test_list_wiki_pages(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page A", "Content A")
        vm_with_structure.write_wiki("Page B", "Content B")

        pages = vm_with_structure.list_wiki_pages()
        assert len(pages) == 2

    def test_list_wiki_pages_by_lens(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        vm_with_structure.write_wiki(
            "Transformers", "Content", lens=lens_ai
        )
        vm_with_structure.write_wiki("React", "Content")  # root level

        lens_pages = vm_with_structure.list_wiki_pages(lens=lens_ai)
        assert len(lens_pages) == 1

    def test_resolve_wikilink(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Transformers", "Content")

        resolved = vm_with_structure.resolve_wikilink("Transformers")
        assert resolved is not None
        assert resolved.name == "transformers.md"

    def test_resolve_wikilink_not_found(
        self, vm_with_structure: VaultManager
    ) -> None:
        resolved = vm_with_structure.resolve_wikilink("Nonexistent")
        assert resolved is None

    def test_scan_vault(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page", "Content")

        files = vm_with_structure.scan_vault()
        assert len(files) >= 1

    def test_vault_summary(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page", "Content")

        summary = vm_with_structure.vault_summary()
        assert summary.total_files >= 1


# ---------------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_resolve_source_path(self, vm: VaultManager) -> None:
        abs_path, rel_path = vm.resolve_source_path(
            "rss", "Test Post", published_at=FIXED_TIME
        )

        assert abs_path.is_absolute()
        assert str(rel_path).startswith("sources/rss/")
        assert "test-post" in str(rel_path)
        assert str(rel_path).endswith(".md")

    def test_resolve_wiki_path(self, vm: VaultManager) -> None:
        abs_path, rel_path = vm.resolve_wiki_path("Transformers")

        assert abs_path.is_absolute()
        assert rel_path == Path("transformers.md")

    def test_resolve_wiki_path_with_lens(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        abs_path, rel_path = vm_with_structure.resolve_wiki_path(
            "Transformers", lens=lens_ai
        )

        assert str(rel_path) == "topics/ai-research/transformers.md"

    def test_generate_source_filename(self, vm: VaultManager) -> None:
        filename = vm.generate_source_filename(
            "rss", "My Article", FIXED_TIME
        )
        assert filename == "2024-01-15-my-article.md"

    def test_generate_wiki_filename(self, vm: VaultManager) -> None:
        assert vm.generate_wiki_filename("Transformers") == "transformers.md"
        assert vm.generate_wiki_filename("Prompt Engineering") == "prompt-engineering.md"
        assert vm.generate_wiki_filename("") == "untitled.md"


# ---------------------------------------------------------------------------
# Duplicate Detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_source_exists_by_path(
        self, vm_with_structure: VaultManager
    ) -> None:
        vm_with_structure.write_source(
            "rss", "Test", "# Test\n", published_at=FIXED_TIME
        )

        assert vm_with_structure.source_exists(
            "rss", "Test", published_at=FIXED_TIME
        )

    def test_source_exists_by_url(
        self, vm_with_structure: VaultManager
    ) -> None:
        url = "https://example.com/article"
        vm_with_structure.write_source(
            "rss", "Test", "# Test\n", url=url, published_at=FIXED_TIME
        )

        # Same URL but different title → still detected
        assert vm_with_structure.source_exists(
            "rss", "Different Title", url=url
        )

    def test_source_not_exists(self, vm_with_structure: VaultManager) -> None:
        assert not vm_with_structure.source_exists("rss", "Nonexistent")

    def test_wiki_exists(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Transformers", "Content")
        assert vm_with_structure.wiki_exists("Transformers")

    def test_wiki_not_exists(self, vm_with_structure: VaultManager) -> None:
        assert not vm_with_structure.wiki_exists("Nonexistent")

    def test_wiki_exists_in_lens(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        vm_with_structure.write_wiki("Transformers", "Content", lens=lens_ai)

        assert vm_with_structure.wiki_exists("Transformers", lens=lens_ai)
        assert not vm_with_structure.wiki_exists("Transformers")  # not at root


# ---------------------------------------------------------------------------
# Dedup Cache
# ---------------------------------------------------------------------------


class TestDedupCache:
    def test_cache_size(self, vm_with_structure: VaultManager) -> None:
        assert vm_with_structure.dedup_cache_size == 0

        vm_with_structure.write_source(
            "rss", "Test", "# Test\n",
            url="https://example.com/1", published_at=FIXED_TIME,
        )

        assert vm_with_structure.dedup_cache_size == 1

    def test_clear_cache(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_source(
            "rss", "Test", "# Test\n",
            url="https://example.com/1", published_at=FIXED_TIME,
        )

        cleared = vm_with_structure.clear_dedup_cache()
        assert cleared == 1
        assert vm_with_structure.dedup_cache_size == 0

    def test_clear_allows_rewrite(
        self, vm_with_structure: VaultManager
    ) -> None:
        url = "https://example.com/article"
        vm_with_structure.write_source(
            "rss", "Test", "# Test\n", url=url, published_at=FIXED_TIME,
        )
        # Second write skipped by session dedup
        r = vm_with_structure.write_source(
            "rss", "Test V2", "# V2\n", url=url, published_at=FIXED_TIME,
        )
        assert r.action == WriteAction.SKIPPED

        # After clearing, still skipped by file-exists check
        vm_with_structure.clear_dedup_cache()
        # But with overwrite=True, should succeed
        r = vm_with_structure.write_source(
            "rss", "Test", "# V2\n", url=url,
            published_at=FIXED_TIME, overwrite=True,
        )
        assert r.action == WriteAction.UPDATED


# ---------------------------------------------------------------------------
# Dedup Key Helpers
# ---------------------------------------------------------------------------


class TestDedupKeys:
    def test_source_dedup_key_deterministic(self) -> None:
        url = "https://example.com/article"
        k1 = _source_dedup_key(url)
        k2 = _source_dedup_key(url)
        assert k1 == k2
        assert k1.startswith("src:")

    def test_source_dedup_key_different_urls(self) -> None:
        k1 = _source_dedup_key("https://a.com")
        k2 = _source_dedup_key("https://b.com")
        assert k1 != k2

    def test_wiki_dedup_key(self) -> None:
        k1 = _wiki_dedup_key("Transformers", "ai-research")
        k2 = _wiki_dedup_key("Transformers", "ai-research")
        assert k1 == k2
        assert k1.startswith("wiki:")

    def test_wiki_dedup_key_different_lens(self) -> None:
        k1 = _wiki_dedup_key("Test", "ai")
        k2 = _wiki_dedup_key("Test", "frontend")
        assert k1 != k2

    def test_wiki_dedup_key_root(self) -> None:
        k = _wiki_dedup_key("Test", "")
        assert "_root" in k


# ---------------------------------------------------------------------------
# Obsidian Integration
# ---------------------------------------------------------------------------


class TestObsidianIntegration:
    def test_build_obsidian_uri(self, vm: VaultManager) -> None:
        uri = vm.build_obsidian_uri("ai-research/transformers")
        assert uri.startswith("obsidian://open?vault=")
        assert "ai-research" in uri

    def test_build_obsidian_uri_root(self, vm: VaultManager) -> None:
        uri = vm.build_obsidian_uri()
        assert uri.startswith("obsidian://open?vault=")

    def test_build_search_uri(self, vm: VaultManager) -> None:
        uri = vm.build_search_uri("transformer architecture")
        assert "obsidian://search?" in uri
        assert "transformer" in uri


# ---------------------------------------------------------------------------
# Init and Status
# ---------------------------------------------------------------------------


class TestInitAndStatus:
    def test_init_vault(self, vm: VaultManager) -> None:
        info = vm.init_vault()

        assert info["vault_path"] == str(vm.vault_path)
        assert info["vault_name"] == vm.vault_name
        assert len(info["created_dirs"]) >= 6

    def test_status(self, vm_with_structure: VaultManager) -> None:
        vm_with_structure.write_wiki("Page", "Content")
        vm_with_structure.write_source(
            "rss", "Post", "# Post\n", published_at=FIXED_TIME
        )

        status = vm_with_structure.status()

        assert status["vault_path"] == str(vm_with_structure.vault_path)
        assert status["total_sources"] == 1
        assert status["sources_by_type"]["rss"] == 1
        assert isinstance(status["lenses"], list)

    def test_list_lens_dirs(
        self, vm_with_structure: VaultManager, lens_ai: Lens
    ) -> None:
        # Create the directory
        vm_with_structure.ensure_lens_dir(lens_ai)

        dirs = vm_with_structure.list_lens_dirs()
        assert len(dirs) == 1
        assert dirs[0][0] == "ai-research"

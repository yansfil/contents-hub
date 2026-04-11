"""Tests for backlink_resolver module: bidirectional link resolution.

Tests cover:
  - Protected zone detection (frontmatter, code, headings, URLs)
  - Single page scanning for unlinked mentions
  - Already-linked page detection
  - Alias matching (case-insensitive)
  - Batch backlink resolution
  - Patch application (dry_run and real)
  - Self-exclusion (target page not patched)
  - Edge cases (empty vault, no matches, max_patches limit)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from llm_wiki.backlink_resolver import (
    BacklinkPatch,
    BacklinkResult,
    BatchBacklinkResult,
    apply_backlinks,
    resolve_backlinks,
    resolve_backlinks_batch,
    _find_first_unlinked_mention,
    _find_protected_zones,
    _has_wikilink_to,
)
from llm_wiki.config import WikiConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Create a minimal vault directory structure."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def config(vault_dir: Path) -> WikiConfig:
    """Create a WikiConfig pointing to the temp vault."""
    return WikiConfig(vault_path=vault_dir)


def _write_page(vault: Path, relative_path: str, content: str) -> Path:
    """Write a markdown page to the vault."""
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _has_wikilink_to()
# ---------------------------------------------------------------------------


class TestHasWikilinkTo:
    def test_basic_wikilink(self):
        assert _has_wikilink_to("See [[PyTorch]] for details.", "PyTorch")

    def test_wikilink_with_alias(self):
        assert _has_wikilink_to("See [[PyTorch|PT]] for details.", "PyTorch")

    def test_wikilink_with_heading(self):
        assert _has_wikilink_to("See [[PyTorch#Installation]].", "PyTorch")

    def test_case_insensitive(self):
        assert _has_wikilink_to("See [[pytorch]] for details.", "PyTorch")
        assert _has_wikilink_to("See [[PYTORCH]] for details.", "PyTorch")

    def test_no_wikilink(self):
        assert not _has_wikilink_to("PyTorch is great.", "PyTorch")

    def test_different_target(self):
        assert not _has_wikilink_to("See [[TensorFlow]].", "PyTorch")


# ---------------------------------------------------------------------------
# _find_protected_zones()
# ---------------------------------------------------------------------------


class TestProtectedZones:
    def test_frontmatter_protected(self):
        text = "---\ntitle: Test\ntags: [ai]\n---\n\nBody text."
        zones = _find_protected_zones(text)
        # Frontmatter zone should cover the --- block
        assert any(s == 0 for s, _ in zones)

    def test_code_block_protected(self):
        text = "Some text.\n```python\nimport torch\n```\nMore text."
        zones = _find_protected_zones(text)
        assert len(zones) > 0

    def test_heading_protected(self):
        text = "# My Heading\n\nBody text."
        zones = _find_protected_zones(text)
        assert len(zones) > 0

    def test_existing_wikilink_protected(self):
        text = "See [[PyTorch]] for more."
        zones = _find_protected_zones(text)
        assert len(zones) > 0


# ---------------------------------------------------------------------------
# _find_first_unlinked_mention()
# ---------------------------------------------------------------------------


class TestFindFirstUnlinkedMention:
    def test_basic_match(self):
        content = "---\ntitle: AI\n---\n\nPyTorch is a great framework."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        assert patch is not None
        assert patch.wikilink == "[[PyTorch]]"
        assert patch.matched_text == "PyTorch"

    def test_case_variant_uses_display_name(self):
        content = "I use pytorch every day."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        assert patch is not None
        assert patch.wikilink == "[[PyTorch|pytorch]]"

    def test_alias_match(self):
        content = "The Transformer Architecture is revolutionary."
        patch = _find_first_unlinked_mention(
            content,
            ["Transformer Architecture", "Transformer"],
            "Transformer",
        )
        assert patch is not None
        # Should match the longer alias first
        assert "Transformer Architecture" in patch.matched_text

    def test_no_match_in_frontmatter(self):
        content = "---\ntitle: PyTorch Guide\n---\n\nNo mention here."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        # "PyTorch" is in frontmatter (protected), not in body
        assert patch is None

    def test_no_match_in_code_block(self):
        content = "Text before.\n```\nPyTorch code\n```\nText after."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        assert patch is None

    def test_no_match_in_heading(self):
        content = "# PyTorch Tutorial\n\nSome other content."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        assert patch is None

    def test_skip_already_linked(self):
        content = "See [[PyTorch]] for more. Also PyTorch rocks."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        # The first "PyTorch" is in a wikilink (protected).
        # The second "PyTorch" should be found.
        # Actually, "PyTorch rocks" is NOT in a protected zone
        assert patch is not None

    def test_no_match_returns_none(self):
        content = "Nothing relevant here at all."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        assert patch is None

    def test_word_boundary_respected(self):
        content = "PyTorchLightning is a wrapper."
        patch = _find_first_unlinked_mention(
            content, ["PyTorch"], "PyTorch"
        )
        # "PyTorch" inside "PyTorchLightning" should NOT match
        assert patch is None

    def test_longest_match_first(self):
        content = "Machine Learning Engineering is a skill."
        patch = _find_first_unlinked_mention(
            content,
            ["Machine Learning Engineering", "Machine Learning", "Machine"],
            "Machine Learning",
        )
        assert patch is not None
        assert "Machine Learning Engineering" in patch.matched_text


# ---------------------------------------------------------------------------
# resolve_backlinks() — integration with vault scanning
# ---------------------------------------------------------------------------


class TestResolveBacklinks:
    def test_finds_unlinked_mentions(self, config: WikiConfig, vault_dir: Path):
        """Pages mentioning 'PyTorch' without linking get patches."""
        _write_page(vault_dir, "ai-overview.md", textwrap.dedent("""\
            ---
            title: AI Overview
            ---

            PyTorch and TensorFlow are popular frameworks.
        """))
        _write_page(vault_dir, "ml-tools.md", textwrap.dedent("""\
            ---
            title: ML Tools
            ---

            Use PyTorch for research and TensorFlow for production.
        """))

        result = resolve_backlinks(config, "PyTorch")
        assert result.total_mentions == 2
        assert result.pages_affected == 2

    def test_skips_already_linked(self, config: WikiConfig, vault_dir: Path):
        """Pages with [[PyTorch]] already present are not patched."""
        _write_page(vault_dir, "linked.md", textwrap.dedent("""\
            ---
            title: Linked
            ---

            We use [[PyTorch]] extensively.
        """))
        _write_page(vault_dir, "unlinked.md", textwrap.dedent("""\
            ---
            title: Unlinked
            ---

            PyTorch is the best framework.
        """))

        result = resolve_backlinks(config, "PyTorch")
        assert result.total_mentions == 1
        assert result.pages_already_linked == 1
        assert result.patches[0].relative_path == "unlinked.md"

    def test_skips_self(self, config: WikiConfig, vault_dir: Path):
        """The target page itself is not patched."""
        _write_page(vault_dir, "pytorch.md", textwrap.dedent("""\
            ---
            title: PyTorch
            ---

            PyTorch is a deep learning framework by Meta.
        """))
        _write_page(vault_dir, "other.md", textwrap.dedent("""\
            ---
            title: Other
            ---

            PyTorch is great.
        """))

        result = resolve_backlinks(config, "PyTorch")
        assert result.skipped_self
        assert result.total_mentions == 1
        assert result.patches[0].relative_path == "other.md"

    def test_alias_matching(self, config: WikiConfig, vault_dir: Path):
        """Aliases are found and linked with display-name syntax."""
        _write_page(vault_dir, "page.md", textwrap.dedent("""\
            ---
            title: Research
            ---

            The Transformer Architecture changed everything.
        """))

        result = resolve_backlinks(
            config,
            "Transformer",
            target_aliases=["Transformer Architecture"],
        )
        assert result.total_mentions == 1
        # Should use [[Transformer|Transformer Architecture]] since display differs
        patch = result.patches[0]
        assert "Transformer" in patch.wikilink

    def test_excludes_sources_directory(self, config: WikiConfig, vault_dir: Path):
        """Files in sources/ are not scanned."""
        _write_page(vault_dir, "sources/rss/2024-01-01-article.md", textwrap.dedent("""\
            ---
            title: Article
            ---

            PyTorch is mentioned here but sources/ is excluded.
        """))
        _write_page(vault_dir, "wiki-page.md", textwrap.dedent("""\
            ---
            title: Wiki Page
            ---

            PyTorch is mentioned here too.
        """))

        result = resolve_backlinks(config, "PyTorch")
        assert result.total_mentions == 1
        assert result.patches[0].relative_path == "wiki-page.md"

    def test_max_patches_limit(self, config: WikiConfig, vault_dir: Path):
        """Respects max_patches limit."""
        for i in range(10):
            _write_page(vault_dir, f"page-{i}.md", f"PyTorch mentioned on page {i}.")

        result = resolve_backlinks(config, "PyTorch", max_patches=3)
        assert result.total_mentions == 3

    def test_empty_vault(self, config: WikiConfig, vault_dir: Path):
        """Empty vault returns empty result."""
        result = resolve_backlinks(config, "PyTorch")
        assert result.total_mentions == 0
        assert result.pages_affected == 0

    def test_no_matches(self, config: WikiConfig, vault_dir: Path):
        """Pages without the search term return empty result."""
        _write_page(vault_dir, "unrelated.md", "This page talks about cooking.")

        result = resolve_backlinks(config, "PyTorch")
        assert result.total_mentions == 0

    def test_summary(self, config: WikiConfig, vault_dir: Path):
        """Summary string is readable."""
        _write_page(vault_dir, "page.md", "PyTorch is mentioned here.")

        result = resolve_backlinks(config, "PyTorch")
        summary = result.summary()
        assert "PyTorch" in summary
        assert "1" in summary


# ---------------------------------------------------------------------------
# resolve_backlinks_batch()
# ---------------------------------------------------------------------------


class TestResolveBacklinksBatch:
    def test_multiple_targets(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "ai.md", textwrap.dedent("""\
            ---
            title: AI Research
            ---

            PyTorch and TensorFlow are both used.
        """))

        targets = [
            {"title": "PyTorch", "aliases": []},
            {"title": "TensorFlow", "aliases": []},
        ]
        batch = resolve_backlinks_batch(config, targets)
        assert len(batch.results) == 2
        assert batch.total_patches == 2

    def test_empty_targets(self, config: WikiConfig, vault_dir: Path):
        batch = resolve_backlinks_batch(config, [])
        assert batch.total_patches == 0

    def test_summary(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "page.md", "PyTorch is great.")
        targets = [{"title": "PyTorch"}]
        batch = resolve_backlinks_batch(config, targets)
        summary = batch.summary()
        assert "Backlink Resolution" in summary


# ---------------------------------------------------------------------------
# apply_backlinks()
# ---------------------------------------------------------------------------


class TestApplyBacklinks:
    def test_dry_run(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "page.md", "PyTorch is great.")

        result = resolve_backlinks(config, "PyTorch")
        count = apply_backlinks(result, dry_run=True)
        assert count == 1

        # File should be unchanged
        content = (vault_dir / "page.md").read_text()
        assert "[[PyTorch]]" not in content

    def test_apply_single(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "page.md", "PyTorch is great.")

        result = resolve_backlinks(config, "PyTorch")
        count = apply_backlinks(result)
        assert count == 1

        # File should now contain the wikilink
        content = (vault_dir / "page.md").read_text()
        assert "[[PyTorch]]" in content

    def test_apply_preserves_rest(self, config: WikiConfig, vault_dir: Path):
        original = textwrap.dedent("""\
            ---
            title: ML Overview
            tags: [ai]
            ---

            PyTorch is a deep learning framework.
            TensorFlow is another option.
            Both are widely used.
        """)
        _write_page(vault_dir, "overview.md", original)

        result = resolve_backlinks(config, "PyTorch")
        apply_backlinks(result)

        content = (vault_dir / "overview.md").read_text()
        # Wikilink injected
        assert "[[PyTorch]]" in content
        # Rest preserved
        assert "TensorFlow is another option." in content
        assert "Both are widely used." in content
        assert "title: ML Overview" in content
        assert "tags: [ai]" in content

    def test_apply_batch(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "ai.md", textwrap.dedent("""\
            ---
            title: AI
            ---

            PyTorch and TensorFlow are popular.
        """))

        targets = [
            {"title": "PyTorch", "aliases": []},
            {"title": "TensorFlow", "aliases": []},
        ]
        batch = resolve_backlinks_batch(config, targets)
        count = apply_backlinks(batch)
        assert count == 2

        content = (vault_dir / "ai.md").read_text()
        assert "[[PyTorch]]" in content
        assert "[[TensorFlow]]" in content

    def test_apply_case_variant(self, config: WikiConfig, vault_dir: Path):
        _write_page(vault_dir, "page.md", "I love pytorch for research.")

        result = resolve_backlinks(config, "PyTorch")
        apply_backlinks(result)

        content = (vault_dir / "page.md").read_text()
        assert "[[PyTorch|pytorch]]" in content

    def test_idempotent(self, config: WikiConfig, vault_dir: Path):
        """Applying backlinks twice should not double-link."""
        _write_page(vault_dir, "page.md", "PyTorch is great.")

        result1 = resolve_backlinks(config, "PyTorch")
        apply_backlinks(result1)

        # Second pass: should find no new patches
        result2 = resolve_backlinks(config, "PyTorch")
        assert result2.total_mentions == 0
        assert result2.pages_already_linked == 1

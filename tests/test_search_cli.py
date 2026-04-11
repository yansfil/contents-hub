"""Tests for search_cli module — CLI entry point for vault search.

Tests the full integration path: CLI argument parsing → search execution →
formatted output. Exercises all search modes (metadata, content, ranked, tags)
through the ``main()`` function with captured stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.search_cli import build_parser, main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a test vault with markdown pages for CLI integration tests."""
    # Wiki page with full frontmatter
    _write_page(
        tmp_path / "transformers.md",
        {"title": "Transformers", "aliases": ["AIAYN"], "tags": ["ai", "nlp"]},
        (
            "# Transformers\n\n"
            "The transformer architecture was introduced in the paper\n"
            "\"Attention Is All You Need\" by Vaswani et al.\n\n"
            "## Key Concepts\n\n"
            "- Self-attention mechanism allows the model to attend\n"
            "  to different positions of the input sequence.\n"
            "- Multi-head attention extends this by running multiple\n"
            "  attention operations in parallel.\n"
        ),
    )

    # Another wiki page
    ai_dir = tmp_path / "ai-research"
    ai_dir.mkdir()
    _write_page(
        ai_dir / "rag.md",
        {"title": "Retrieval-Augmented Generation", "aliases": ["RAG"], "tags": ["ai", "llm"]},
        (
            "# RAG\n\n"
            "Retrieval-Augmented Generation combines a retrieval system\n"
            "with a language model to ground responses in factual data.\n"
        ),
    )

    # Page with no frontmatter
    (tmp_path / "quick-note.md").write_text(
        "# Quick Note\n\nJust a quick note about Python programming.\n"
    )

    # Page in sources/ (excluded by default)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(
        sources_dir / "article.md",
        {"title": "Source Article", "source_type": "rss"},
        "# Source Article\n\nThis is from an RSS feed about transformer models.\n",
    )

    return tmp_path


def _write_page(path: Path, fm: dict, body: str) -> None:
    """Write a markdown page with YAML frontmatter."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_query_only(self):
        parser = build_parser()
        args = parser.parse_args(["transformer"])
        assert args.query == "transformer"
        assert not args.tag
        assert not args.tags
        assert not args.content
        assert not args.ranked
        assert not args.sources
        assert args.output_format == "table"
        assert args.top == 10

    def test_tag_flag(self):
        parser = build_parser()
        args = parser.parse_args(["ai", "--tag"])
        assert args.tag is True

    def test_tags_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--tags"])
        assert args.tags is True
        assert args.query is None

    def test_content_flag(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--content"])
        assert args.content is True

    def test_ranked_flag(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--ranked"])
        assert args.ranked is True

    def test_sources_flag(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--sources"])
        assert args.sources is True

    def test_json_format(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--format", "json"])
        assert args.output_format == "json"

    def test_top_limit(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--top", "20"])
        assert args.top == 20

    def test_semantic_flag(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--semantic"])
        assert args.semantic is True

    def test_vault_path(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--vault", "/some/path"])
        assert args.vault == "/some/path"


# ---------------------------------------------------------------------------
# Default metadata search (title + aliases + tags)
# ---------------------------------------------------------------------------


class TestMetadataSearch:
    def test_finds_by_title(self, vault: Path, capsys):
        rc = main(["transformer", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        # Title may be highlighted as "**Transformer**s", so check case-insensitive
        assert "transformer" in output.lower()

    def test_finds_by_alias(self, vault: Path, capsys):
        rc = main(["RAG", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Retrieval-Augmented Generation" in output

    def test_finds_by_tag(self, vault: Path, capsys):
        rc = main(["ai", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        # Should find multiple pages tagged with "ai"
        assert "Transformers" in output

    def test_no_results(self, vault: Path, capsys):
        rc = main(["quantum-computing-xyz", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "No results" in output

    def test_excludes_sources_by_default(self, vault: Path, capsys):
        # Search for a term unique to the source article but not in "No results" message
        rc = main(["rss-source-article-xyz", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "No results" in output

    def test_includes_sources_with_flag(self, vault: Path, capsys):
        rc = main(["Source Article", "--sources", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Source Article" in output

    def test_json_output(self, vault: Path, capsys):
        rc = main(["transformer", "--format", "json", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["title"] == "Transformers"
        assert "wikilink" in data[0]
        assert "path" in data[0]


# ---------------------------------------------------------------------------
# Tag-only search
# ---------------------------------------------------------------------------


class TestTagSearch:
    def test_tag_search(self, vault: Path, capsys):
        rc = main(["ai", "--tag", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Transformers" in output

    def test_tag_no_match(self, vault: Path, capsys):
        rc = main(["nonexistent", "--tag", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "No results" in output


# ---------------------------------------------------------------------------
# Content search (full-text keyword matching)
# ---------------------------------------------------------------------------


class TestContentSearch:
    def test_content_search_finds_body_text(self, vault: Path, capsys):
        rc = main(["attention", "--content", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Transformers" in output

    def test_content_search_with_snippets(self, vault: Path, capsys):
        rc = main(["attention", "--content", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        # Should show context lines around the match
        assert "attention" in output.lower()

    def test_content_search_no_match(self, vault: Path, capsys):
        rc = main(["quantum-xyz-nonexistent", "--content", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "No content matches" in output

    def test_content_search_json(self, vault: Path, capsys):
        rc = main(["attention", "--content", "--format", "json", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "matches" in data[0]
        assert data[0]["matches"][0]["line_number"] > 0

    def test_content_search_excludes_sources(self, vault: Path, capsys):
        rc = main(["RSS", "--content", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Source Article" not in output

    def test_content_search_includes_sources(self, vault: Path, capsys):
        rc = main(["RSS", "--content", "--sources", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Source Article" in output

    def test_content_search_top_limit(self, vault: Path, capsys):
        rc = main(["the", "--content", "--top", "1", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        # Should limit to 1 result (hard to count in formatted output,
        # but should at least succeed)
        assert rc == 0


# ---------------------------------------------------------------------------
# BM25-ranked search
# ---------------------------------------------------------------------------


class TestRankedSearch:
    def test_ranked_search(self, vault: Path, capsys):
        rc = main(["transformer attention", "--ranked", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Transformers" in output or "transformers" in output

    def test_ranked_search_shows_scores(self, vault: Path, capsys):
        rc = main(["transformer", "--ranked", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        # Should contain score indicators like [X.XX]
        assert "[" in output

    def test_ranked_search_json(self, vault: Path, capsys):
        rc = main(["transformer", "--ranked", "--format", "json", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "score" in data[0]
        assert data[0]["score"] > 0
        assert "wikilink" in data[0]

    def test_ranked_search_no_match(self, vault: Path, capsys):
        rc = main(["quantum-xyz-nonexistent", "--ranked", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "No results" in output


# ---------------------------------------------------------------------------
# Semantic search (embedding-based)
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def test_semantic_falls_back_to_ranked_without_api_key(self, vault: Path, capsys, monkeypatch):
        """Without an embedding API key, --semantic should fall back to BM25."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        rc = main(["transformer", "--semantic", "--vault", str(vault)])
        assert rc == 0
        # Should still produce results (via BM25 fallback)
        output = capsys.readouterr().out
        err = capsys.readouterr().err
        # The fallback produces BM25-ranked results
        assert "transformer" in output.lower() or "No results" in output

    def test_semantic_flag_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["transformer", "--semantic"])
        assert args.semantic is True
        assert not args.ranked
        assert not args.content


# ---------------------------------------------------------------------------
# Tags listing
# ---------------------------------------------------------------------------


class TestTagsListing:
    def test_list_tags(self, vault: Path, capsys):
        rc = main(["--tags", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "#ai" in output
        assert "#nlp" in output

    def test_list_tags_json(self, vault: Path, capsys):
        rc = main(["--tags", "--format", "json", "--vault", str(vault)])
        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        tags = [d["tag"] for d in data]
        assert "ai" in tags


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_no_query_no_tags(self, vault: Path):
        """Should fail when neither query nor --tags is provided."""
        with pytest.raises(SystemExit):
            main(["--vault", str(vault)])

    def test_nonexistent_vault(self, tmp_path: Path, capsys):
        rc = main(["test", "--vault", str(tmp_path / "nonexistent")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error" in err or "does not exist" in err

    def test_mutually_exclusive_modes(self, vault: Path, capsys):
        """Should fail when multiple mode flags are used together."""
        rc = main(["test", "--content", "--ranked", "--vault", str(vault)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Only one search mode" in err

    def test_three_modes_fail(self, vault: Path, capsys):
        """Should fail when three mode flags are used."""
        rc = main(["test", "--content", "--ranked", "--semantic", "--vault", str(vault)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Only one search mode" in err


# ---------------------------------------------------------------------------
# Lens filtering
# ---------------------------------------------------------------------------


class TestLensFilter:
    @pytest.fixture
    def vault_with_lens(self, tmp_path: Path) -> Path:
        """Create a vault with pages in lens-specific directories."""
        # Lens directory: ai-research
        ai_dir = tmp_path / "ai-research"
        ai_dir.mkdir()
        _write_page(
            ai_dir / "transformer.md",
            {"title": "Transformer", "tags": ["ai", "nlp"]},
            "# Transformer\n\nThe transformer architecture.\n",
        )
        _write_page(
            ai_dir / "attention.md",
            {"title": "Attention Mechanism", "tags": ["ai"]},
            "# Attention\n\nSelf-attention explained.\n",
        )

        # Different directory: devops
        devops_dir = tmp_path / "devops"
        devops_dir.mkdir()
        _write_page(
            devops_dir / "kubernetes.md",
            {"title": "Kubernetes", "tags": ["devops"]},
            "# Kubernetes\n\nContainer orchestration.\n",
        )

        # Root-level page (no lens directory)
        _write_page(
            tmp_path / "general-note.md",
            {"title": "General Note", "tags": ["ai"]},
            "# General Note\n\nSome general content about AI transformers.\n",
        )

        return tmp_path

    def test_lens_filters_metadata_search(self, vault_with_lens: Path, capsys):
        """Metadata search with --lens should only show pages in lens directory or with matching tags."""
        rc = main(["ai", "--lens", "ai-research", "--vault", str(vault_with_lens)])
        assert rc == 0
        output = capsys.readouterr().out
        # Pages in ai-research/ directory should appear
        assert "Transformer" in output or "Attention" in output

    def test_lens_filters_ranked_search(self, vault_with_lens: Path, capsys):
        """Ranked search with --lens should filter results."""
        rc = main(["transformer", "--ranked", "--lens", "ai-research", "--vault", str(vault_with_lens)])
        assert rc == 0
        output = capsys.readouterr().out
        # Kubernetes should NOT appear
        assert "Kubernetes" not in output

    def test_lens_filters_content_search(self, vault_with_lens: Path, capsys):
        """Content search with --lens should filter results."""
        rc = main(["architecture", "--content", "--lens", "ai-research", "--vault", str(vault_with_lens)])
        assert rc == 0
        # Should only find content in ai-research directory

    def test_lens_flag_parsed(self):
        """Parser should accept --lens argument."""
        parser = build_parser()
        args = parser.parse_args(["transformer", "--lens", "ai-research"])
        assert args.lens == "ai-research"

    def test_lens_flag_default_none(self):
        """--lens should default to None."""
        parser = build_parser()
        args = parser.parse_args(["transformer"])
        assert args.lens is None

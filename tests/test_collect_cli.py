"""Tests for the collect CLI entry point — argument parsing and file creation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.collect_cli import (
    build_collect_parser,
    collect_file,
    collect_memo,
    collect_text,
    collect_url,
    detect_source_type,
    handle_collect,
    main,
    slugify,
)
from llm_wiki.config import WikiConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path: Path) -> WikiConfig:
    """Return a WikiConfig pointing at a temp vault directory."""
    return WikiConfig(vault_path=tmp_path)


# ---------------------------------------------------------------------------
# Unit: slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("Café & Résumé!") == "cafe-resume"

    def test_truncation(self):
        result = slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty(self):
        assert slugify("") == "untitled"

    def test_unicode_cjk_removed(self):
        # CJK chars are stripped by NFKD + ascii encode
        assert slugify("테스트") == "untitled"


# ---------------------------------------------------------------------------
# Unit: detect_source_type
# ---------------------------------------------------------------------------

class TestDetectSourceType:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://twitter.com/user/status/123", "twitter"),
        ("https://x.com/user/status/123", "twitter"),
        ("https://reddit.com/r/python", "reddit"),
        ("https://github.com/user/repo", "github"),
        ("https://arxiv.org/abs/2301.00001", "arxiv"),
        ("https://blog.substack.com/p/post", "substack"),
        ("https://example.com/article", "webpage"),
    ])
    def test_detection(self, url: str, expected: str):
        assert detect_source_type(url) == expected


# ---------------------------------------------------------------------------
# Unit: collect_* functions
# ---------------------------------------------------------------------------

class TestCollectUrl:
    def test_creates_file(self, vault: WikiConfig):
        result = collect_url(vault, "https://example.com/article", title="Test Article")
        assert result.exists()
        assert result.parent == vault.sources_path
        content = result.read_text()
        assert "type: webpage" in content
        assert 'url: "https://example.com/article"' in content
        assert "status: pending" in content
        assert "# Test Article" in content

    def test_auto_type_youtube(self, vault: WikiConfig):
        result = collect_url(vault, "https://www.youtube.com/watch?v=abc123")
        content = result.read_text()
        assert "type: youtube" in content

    def test_memo_annotation(self, vault: WikiConfig):
        result = collect_url(
            vault, "https://example.com",
            memo="This is important",
        )
        content = result.read_text()
        assert "> This is important" in content

    def test_tags(self, vault: WikiConfig):
        result = collect_url(
            vault, "https://example.com",
            tags=["ai", "research"],
        )
        content = result.read_text()
        assert "  - ai" in content
        assert "  - research" in content


class TestCollectText:
    def test_creates_file(self, vault: WikiConfig):
        result = collect_text(vault, "Some important insight about LLMs")
        assert result.exists()
        content = result.read_text()
        assert "type: text" in content
        assert "Some important insight about LLMs" in content


class TestCollectMemo:
    def test_creates_file(self, vault: WikiConfig):
        result = collect_memo(vault, "Idea: combine RAG with wiki")
        assert result.exists()
        content = result.read_text()
        assert "type: memo" in content
        assert "-memo-" in result.name


class TestCollectFile:
    def test_creates_file(self, vault: WikiConfig, tmp_path: Path):
        # Create a source file to import
        src = tmp_path / "notes.txt"
        src.write_text("My research notes here")

        result = collect_file(vault, str(src))
        assert result.exists()
        content = result.read_text()
        assert "type: file" in content
        assert "My research notes here" in content

    def test_missing_file(self, vault: WikiConfig):
        with pytest.raises(FileNotFoundError):
            collect_file(vault, "/nonexistent/file.txt")


class TestImmutability:
    def test_no_overwrite(self, vault: WikiConfig):
        """Collecting the same URL twice creates two distinct files."""
        r1 = collect_url(vault, "https://example.com/same")
        r2 = collect_url(vault, "https://example.com/same")
        assert r1 != r2
        assert r1.exists()
        assert r2.exists()


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestCollectParser:
    def test_standalone_parser(self):
        parser = build_collect_parser()
        args = parser.parse_args(["url", "https://example.com", "--title", "Test"])
        assert args.collect_subcommand == "url"
        assert args.url == "https://example.com"
        assert args.title == "Test"

    def test_url_with_memo(self):
        parser = build_collect_parser()
        args = parser.parse_args([
            "url", "https://example.com",
            "--memo", "Important article",
            "--tags", "ai,ml",
        ])
        assert args.memo == "Important article"
        assert args.tags == "ai,ml"

    def test_text_subcommand(self):
        parser = build_collect_parser()
        args = parser.parse_args(["text", "Some snippet", "-t", "My Snippet"])
        assert args.collect_subcommand == "text"
        assert args.content == "Some snippet"
        assert args.title == "My Snippet"

    def test_memo_subcommand(self):
        parser = build_collect_parser()
        args = parser.parse_args(["memo", "A thought"])
        assert args.collect_subcommand == "memo"
        assert args.content == "A thought"

    def test_file_subcommand(self):
        parser = build_collect_parser()
        args = parser.parse_args(["file", "/tmp/notes.txt", "--tags", "ref"])
        assert args.collect_subcommand == "file"
        assert args.path == "/tmp/notes.txt"
        assert args.tags == "ref"

    def test_missing_subcommand_exits(self):
        parser = build_collect_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_standalone_has_vault_flag(self):
        parser = build_collect_parser()
        args = parser.parse_args(["--vault", "/my/vault", "memo", "test"])
        assert args.vault == "/my/vault"


# ---------------------------------------------------------------------------
# Integration: handle_collect via main()
# ---------------------------------------------------------------------------

class TestMainEntrypoint:
    def test_collect_url_via_main(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LLM_WIKI_VAULT", str(tmp_path))
        exit_code = main(["url", "https://example.com", "--title", "Test"])
        assert exit_code == 0
        sources = list((tmp_path / "sources").glob("*.md"))
        assert len(sources) == 1

    def test_collect_memo_via_main(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LLM_WIKI_VAULT", str(tmp_path))
        exit_code = main(["memo", "My idea"])
        assert exit_code == 0

    def test_collect_text_via_main(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LLM_WIKI_VAULT", str(tmp_path))
        exit_code = main(["text", "Some important text", "--tags", "ai,ml"])
        assert exit_code == 0
        sources = list((tmp_path / "sources").glob("*.md"))
        assert len(sources) == 1
        content = sources[0].read_text()
        assert "  - ai" in content
        assert "  - ml" in content

    def test_vault_flag(self, tmp_path: Path):
        exit_code = main(["--vault", str(tmp_path), "memo", "test memo"])
        assert exit_code == 0

    def test_invalid_vault(self):
        exit_code = main(["--vault", "/nonexistent/path", "memo", "test"])
        assert exit_code == 1

    def test_json_output(self, tmp_path: Path, capsys):
        exit_code = main(["--vault", str(tmp_path), "url", "https://example.com"])
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "collected"
        assert data["filename"].endswith(".md")
        assert "relative" in data

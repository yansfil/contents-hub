"""Tests for the CLI entry point (add, remove, list subcommands)."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from llm_wiki.cli import build_parser, main
from llm_wiki.config import WikiConfig
from llm_wiki.subscriptions import SubscriptionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    return SubscriptionStore(config)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_add_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["add", "https://example.com/feed.xml"])
        assert args.command == "add"
        assert args.url == "https://example.com/feed.xml"
        assert args.title == ""
        assert args.source_type == ""
        assert args.lenses is None

    def test_add_with_all_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "add", "https://example.com/feed.xml",
            "--title", "My Blog",
            "--type", "rss",
            "--lens", "tech",
            "--lens", "ai",
            "--tag", "favorite",
        ])
        assert args.title == "My Blog"
        assert args.source_type == "rss"
        assert args.lenses == ["tech", "ai"]
        assert args.tags == ["favorite"]

    def test_remove_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["remove", "https://example.com/feed.xml"])
        assert args.command == "remove"
        assert args.url == "https://example.com/feed.xml"

    def test_rm_alias(self):
        parser = build_parser()
        args = parser.parse_args(["rm", "https://example.com/feed.xml"])
        assert args.command == "rm"

    def test_list_command_parses(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.source_type is None
        assert args.status is None
        assert args.lens is None
        assert args.output_format == "table"

    def test_ls_alias(self):
        parser = build_parser()
        args = parser.parse_args(["ls"])
        assert args.command == "ls"

    def test_list_with_filters(self):
        parser = build_parser()
        args = parser.parse_args([
            "list",
            "--type", "youtube",
            "--status", "active",
            "--lens", "tech",
            "--format", "json",
        ])
        assert args.source_type == "youtube"
        assert args.status == "active"
        assert args.lens == "tech"
        assert args.output_format == "json"

    def test_vault_option(self):
        parser = build_parser()
        args = parser.parse_args(["--vault", "/tmp/my-vault", "list"])
        assert args.vault == "/tmp/my-vault"

    def test_missing_command_raises(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_invalid_type_raises(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["add", "https://example.com", "--type", "email"])


# ---------------------------------------------------------------------------
# Integration tests (main function)
# ---------------------------------------------------------------------------


class TestMainAdd:
    def test_add_success(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--title", "Test Blog"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Added: https://example.com/feed.xml" in captured.out
        assert "Test Blog" in captured.out

    def test_add_with_lenses(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--lens", "ai", "--lens", "tech"])
        assert code == 0
        captured = capsys.readouterr()
        assert "ai, tech" in captured.out

    def test_add_auto_detects_youtube(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "add", "https://youtube.com/@channel"])
        assert code == 0
        captured = capsys.readouterr()
        assert "youtube" in captured.out

    def test_add_auto_detects_twitter(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "add", "https://x.com/user"])
        assert code == 0
        captured = capsys.readouterr()
        assert "twitter" in captured.out

    def test_add_duplicate_fails(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml"])
        code = main(["--vault", str(vault), "add", "https://example.com/feed.xml"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Already subscribed" in captured.err

    def test_add_invalid_url_fails(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "add", "not-a-url"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Invalid feed URL" in captured.err


class TestMainRemove:
    def test_remove_success(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--title", "Blog"])
        code = main(["--vault", str(vault), "remove", "https://example.com/feed.xml"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Removed:" in captured.out

    def test_remove_not_found(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "remove", "https://example.com/nope"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Not subscribed" in captured.err

    def test_rm_alias_works(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml"])
        code = main(["--vault", str(vault), "rm", "https://example.com/feed.xml"])
        assert code == 0


class TestMainList:
    def test_list_empty(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "list"])
        assert code == 0
        captured = capsys.readouterr()
        assert "No subscriptions found" in captured.out

    def test_list_with_subscriptions(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--title", "Blog"])
        main(["--vault", str(vault), "add", "https://youtube.com/@channel", "--title", "YT Channel"])
        code = main(["--vault", str(vault), "list"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Blog" in captured.out
        assert "YT Channel" in captured.out
        assert "Total: 2" in captured.out

    def test_list_filter_by_type(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--title", "RSS Feed"])
        main(["--vault", str(vault), "add", "https://youtube.com/@ch", "--title", "YT"])
        capsys.readouterr()  # clear captured output from add commands
        code = main(["--vault", str(vault), "list", "--type", "youtube"])
        assert code == 0
        captured = capsys.readouterr()
        assert "YT" in captured.out
        assert "RSS Feed" not in captured.out

    def test_list_filter_by_lens(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://a.com/feed", "--lens", "ai"])
        main(["--vault", str(vault), "add", "https://b.com/feed", "--lens", "cooking"])
        capsys.readouterr()  # clear captured output from add commands
        code = main(["--vault", str(vault), "list", "--lens", "ai"])
        assert code == 0
        captured = capsys.readouterr()
        assert "a.com" in captured.out
        assert "b.com" not in captured.out

    def test_list_json_format(self, vault: Path, capsys):
        main(["--vault", str(vault), "add", "https://example.com/feed.xml", "--title", "Blog"])
        capsys.readouterr()  # clear captured output from add command
        code = main(["--vault", str(vault), "list", "--format", "json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "Blog"
        assert data[0]["url"] == "https://example.com/feed.xml"

    def test_ls_alias_works(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "ls"])
        assert code == 0

    def test_list_json_empty(self, vault: Path, capsys):
        code = main(["--vault", str(vault), "list", "--format", "json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == []


class TestMainVaultResolution:
    def test_invalid_vault_path(self, capsys):
        code = main(["--vault", "/nonexistent/vault/path", "list"])
        assert code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

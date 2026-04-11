"""Tests for obsidian_opener module — URI building, path resolution, open commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.obsidian_opener import (
    build_open_uri,
    build_open_uri_absolute,
    build_search_uri,
    build_new_uri,
    vault_name_from_config,
    vault_name_from_path,
    resolve_relative_path,
    open_in_obsidian,
    open_vault,
    search_in_obsidian,
    cli_open,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    vault = tmp_path / "my-wiki"
    vault.mkdir()
    return vault


@pytest.fixture
def config(vault_path: Path) -> WikiConfig:
    """Create a WikiConfig pointing to the temp vault."""
    return WikiConfig(vault_path=vault_path)


# ---------------------------------------------------------------------------
# URI builders
# ---------------------------------------------------------------------------


class TestBuildOpenUri:
    def test_vault_only(self):
        uri = build_open_uri("my-wiki")
        assert uri == "obsidian://open?vault=my-wiki"

    def test_vault_and_file(self):
        uri = build_open_uri("my-wiki", "topics/transformers")
        assert uri == "obsidian://open?vault=my-wiki&file=topics%2Ftransformers"

    def test_strips_md_extension(self):
        uri = build_open_uri("my-wiki", "note.md")
        assert uri == "obsidian://open?vault=my-wiki&file=note"

    def test_encodes_spaces(self):
        uri = build_open_uri("My Vault", "my note")
        assert "vault=My%20Vault" in uri
        assert "file=my%20note" in uri

    def test_encodes_special_chars(self):
        uri = build_open_uri("vault", "path/to/note (draft)")
        assert "file=path%2Fto%2Fnote%20%28draft%29" in uri

    def test_empty_file_path(self):
        uri = build_open_uri("vault", "")
        assert uri == "obsidian://open?vault=vault"


class TestBuildOpenUriAbsolute:
    def test_absolute_path(self):
        uri = build_open_uri_absolute("/Users/me/vault/note.md")
        assert uri == "obsidian://open?path=%2FUsers%2Fme%2Fvault%2Fnote.md"

    def test_encodes_spaces(self):
        uri = build_open_uri_absolute("/Users/me/my vault/my note.md")
        assert "path=" in uri
        assert "%20" in uri


class TestBuildSearchUri:
    def test_basic_search(self):
        uri = build_search_uri("my-wiki", "attention mechanism")
        assert uri == "obsidian://search?vault=my-wiki&query=attention%20mechanism"


class TestBuildNewUri:
    def test_basic_new(self):
        uri = build_new_uri("my-wiki", name="New Note")
        assert "obsidian://new?vault=my-wiki" in uri
        assert "name=New%20Note" in uri

    def test_with_content(self):
        uri = build_new_uri("my-wiki", name="Test", content="Hello world")
        assert "content=Hello%20world" in uri

    def test_name_only(self):
        uri = build_new_uri("my-wiki", name="Draft")
        assert "name=Draft" in uri


# ---------------------------------------------------------------------------
# Vault name resolution
# ---------------------------------------------------------------------------


class TestVaultNameResolution:
    def test_from_config(self, config: WikiConfig):
        name = vault_name_from_config(config)
        assert name == "my-wiki"

    def test_from_path_string(self, vault_path: Path):
        name = vault_name_from_path(str(vault_path))
        assert name == "my-wiki"

    def test_from_path_object(self, vault_path: Path):
        name = vault_name_from_path(vault_path)
        assert name == "my-wiki"


# ---------------------------------------------------------------------------
# Relative path resolution
# ---------------------------------------------------------------------------


class TestResolveRelativePath:
    def test_absolute_path_inside_vault(self, config: WikiConfig, vault_path: Path):
        abs_path = vault_path / "topics" / "note.md"
        result = resolve_relative_path(config, str(abs_path))
        assert result == "topics/note"

    def test_relative_path(self, config: WikiConfig):
        result = resolve_relative_path(config, "topics/note.md")
        assert result == "topics/note"

    def test_strips_md_extension(self, config: WikiConfig):
        result = resolve_relative_path(config, "note.md")
        assert result == "note"

    def test_no_md_extension(self, config: WikiConfig):
        result = resolve_relative_path(config, "topics/note")
        assert result == "topics/note"

    def test_path_object(self, config: WikiConfig):
        result = resolve_relative_path(config, Path("topics/note.md"))
        assert result == "topics/note"


# ---------------------------------------------------------------------------
# Open in Obsidian
# ---------------------------------------------------------------------------


class TestOpenInObsidian:
    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_open_file(self, mock_launch, config: WikiConfig):
        result = open_in_obsidian(config, "topics/note.md")
        assert result["success"] is True
        assert result["vault_name"] == "my-wiki"
        assert "file_path" in result
        assert "obsidian://open" in result["uri"]
        mock_launch.assert_called_once()

    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_open_vault_root(self, mock_launch, config: WikiConfig):
        result = open_in_obsidian(config)
        assert result["success"] is True
        assert result["uri"] == "obsidian://open?vault=my-wiki"
        mock_launch.assert_called_once()

    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_open_with_absolute_uri(self, mock_launch, config: WikiConfig, vault_path: Path):
        result = open_in_obsidian(config, "topics/note.md", use_absolute=True)
        assert result["success"] is True
        assert "path=" in result["uri"]
        mock_launch.assert_called_once()

    @patch("llm_wiki.obsidian_opener._launch_uri", side_effect=FileNotFoundError("open not found"))
    def test_open_failure(self, mock_launch, config: WikiConfig):
        result = open_in_obsidian(config, "note.md")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestOpenVault:
    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_open_vault(self, mock_launch, config: WikiConfig):
        result = open_vault(config)
        assert result["success"] is True
        assert "vault=my-wiki" in result["uri"]


class TestSearchInObsidian:
    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_search(self, mock_launch, config: WikiConfig):
        result = search_in_obsidian(config, "attention")
        assert result["success"] is True
        assert "search" in result["uri"]
        assert "attention" in result["query"]

    @patch("llm_wiki.obsidian_opener._launch_uri", side_effect=Exception("fail"))
    def test_search_failure(self, mock_launch, config: WikiConfig):
        result = search_in_obsidian(config, "test")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliOpen:
    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_uri_only_file(self, mock_launch, vault_path: Path):
        exit_code = cli_open(["--vault", str(vault_path), "--uri-only", "note.md"])
        assert exit_code == 0
        mock_launch.assert_not_called()

    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_uri_only_vault(self, mock_launch, vault_path: Path):
        exit_code = cli_open(["--vault", str(vault_path), "--uri-only"])
        assert exit_code == 0

    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_uri_only_search(self, mock_launch, vault_path: Path):
        exit_code = cli_open(["--vault", str(vault_path), "--uri-only", "--search", "test"])
        assert exit_code == 0

    @patch("llm_wiki.obsidian_opener._launch_uri")
    def test_open_file(self, mock_launch, vault_path: Path):
        exit_code = cli_open(["--vault", str(vault_path), "note.md"])
        assert exit_code == 0
        mock_launch.assert_called_once()

    def test_invalid_vault(self):
        exit_code = cli_open(["--vault", "/nonexistent/path"])
        assert exit_code == 1

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


def test_contents_hub_is_canonical_import_surface():
    package = importlib.import_module("contents_hub")
    cli = importlib.import_module("contents_hub.cli")
    naming = importlib.import_module("contents_hub.naming")

    assert package.__version__ == "0.2.0"
    assert cli.build_parser().prog == "contents-hub"
    assert naming.PYTHON_PACKAGE.canonical == "contents_hub"


def test_llm_wiki_import_surface_remains_compatible():
    legacy = importlib.import_module("llm_wiki")
    legacy_cli = importlib.import_module("llm_wiki.cli")

    assert legacy.__version__ == "0.2.0"
    assert legacy_cli.build_parser().prog == "contents-hub"


def test_pyproject_distribution_and_scripts_are_renamed():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    assert data["project"]["name"] == "contents-hub"
    assert data["project"]["scripts"]["contents-hub"] == "contents_hub.cli:main"
    assert data["project"]["scripts"]["llm-wiki"] == "contents_hub.cli:main"
    assert "src/contents_hub" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/llm_wiki" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]


def test_claude_sdk_mcp_namespace_is_canonical():
    runner_mod = importlib.import_module("contents_hub.runners.claude_sdk")

    assert runner_mod._MCP_SERVER_NAME == "contents_hub"

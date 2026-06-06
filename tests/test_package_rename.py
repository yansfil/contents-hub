from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest


def _removed_package_name() -> str:
    return "llm" + "_" + "wiki"


def _removed_script_name() -> str:
    return "llm" + "-" + "wiki"


def test_contents_hub_is_only_public_import_surface():
    package = importlib.import_module("contents_hub")
    cli = importlib.import_module("contents_hub.cli")
    naming = importlib.import_module("contents_hub.naming")

    assert package.__version__ == "0.2.0"
    assert cli.build_parser().prog == "contents-hub"
    assert naming.PYTHON_PACKAGE.canonical == "contents_hub"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(_removed_package_name())


def test_pyproject_distribution_and_scripts_are_contents_hub_only():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    scripts = data["project"]["scripts"]
    packages = data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert data["project"]["name"] == "contents-hub"
    assert data["project"]["version"] == importlib.import_module("contents_hub").__version__
    assert scripts == {"contents-hub": "contents_hub.cli:main"}
    assert packages == ["src/contents_hub"]
    assert _removed_script_name() not in scripts


def test_lockfile_version_matches_package_metadata():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    lock = tomllib.loads(Path("uv.lock").read_text())
    project_version = data["project"]["version"]
    lock_package = next(
        package
        for package in lock["package"]
        if package["name"] == data["project"]["name"]
    )

    assert lock_package["version"] == project_version
    assert project_version == importlib.import_module("contents_hub").__version__


def test_claude_sdk_mcp_namespace_is_canonical():
    runner_mod = importlib.import_module("contents_hub.runners.claude_sdk")

    assert runner_mod._MCP_SERVER_NAME == "contents_hub"

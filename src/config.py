"""LLM Wiki configuration — vault path and directory conventions."""

import json
import os
from pathlib import Path

CONFIG_FILENAME = ".llm-wiki.json"
DEFAULT_SOURCES_DIR = "sources"


def find_config() -> Path | None:
    """Search for .llm-wiki.json in CWD and parent directories."""
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


def load_config() -> dict:
    """Load config from .llm-wiki.json or return defaults."""
    config_path = find_config()
    if config_path:
        with open(config_path) as f:
            return json.load(f)
    return {}


def get_vault_path() -> Path:
    """Get the Obsidian vault path from config or env."""
    config = load_config()

    # Priority: config file > env var > CWD
    vault = (
        config.get("vault_path")
        or os.environ.get("LLM_WIKI_VAULT")
        or str(Path.cwd())
    )
    return Path(vault).expanduser().resolve()


def get_sources_dir() -> Path:
    """Get the sources/ directory path inside the vault."""
    config = load_config()
    sources_name = config.get("sources_dir", DEFAULT_SOURCES_DIR)
    return get_vault_path() / sources_name

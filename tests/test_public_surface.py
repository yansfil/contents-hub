from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "install.md",
    ".env.example",
    "docs",
    "skills",
    "src",
    "tests",
    "pyproject.toml",
]
IGNORED_DIRS = {
    ".git",
    ".venv",
    ".contents-hub",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}


def _public_files() -> list[Path]:
    paths: list[Path] = []
    for public_root in PUBLIC_ROOTS:
        path = ROOT / public_root
        if path.is_file():
            paths.append(path)
            continue
        for candidate in path.rglob("*"):
            if candidate.is_dir():
                continue
            if any(part in IGNORED_DIRS for part in candidate.relative_to(ROOT).parts):
                continue
            if candidate.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif"}:
                continue
            paths.append(candidate)
    return paths


def test_public_surface_uses_canonical_contents_hub_names_only():
    forbidden = [
        "llm" + "_wiki",
        "llm" + "-wiki",
        "LLM" + "_WIKI",
        "." + "llm" + "-wiki",
    ]
    offenders: list[str] = []
    for path in _public_files():
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []


def test_public_surface_does_not_contain_private_paths_or_secret_literals():
    forbidden = [
        "/" + "Users/",
        "ANTHROPIC" + "_API_KEY",
        "TELE" + "GRAM",
        "BOT" + "_TOKEN",
        "i" + "Cloud",
        "." + "hoy" + "eon",
    ]
    offenders: list[str] = []
    for path in _public_files():
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []

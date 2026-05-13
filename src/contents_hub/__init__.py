"""contents-hub canonical Python package."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.2.0"

# During the compatibility window the implementation files still live in the
# legacy package directory. Expose that directory as the canonical package path
# so new imports use ``contents_hub.*`` while old ``llm_wiki.*`` imports keep
# resolving.
_LEGACY_PACKAGE_DIR = Path(__file__).resolve().parent.parent / "llm_wiki"
__path__ = [str(_LEGACY_PACKAGE_DIR)]

from contents_hub.frontmatter import (  # noqa: E402
    Frontmatter,
    assemble_markdown,
    extract_raw_frontmatter,
    parse_frontmatter,
    parse_to_frontmatter,
    serialize_frontmatter,
    update_file_frontmatter,
)

__all__ = [
    "Frontmatter",
    "assemble_markdown",
    "extract_raw_frontmatter",
    "parse_frontmatter",
    "parse_to_frontmatter",
    "serialize_frontmatter",
    "update_file_frontmatter",
]

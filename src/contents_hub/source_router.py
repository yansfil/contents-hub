"""URL → canonical source_type classifier.

This module is the compatibility facade for older callers. The source of
truth lives in :mod:`contents_hub.source_types`, where each source type also
declares its default versioned recipe.
"""

from __future__ import annotations

from contents_hub.source_types import (
    SOURCE_TYPES as SOURCE_TYPE_SPECS,
    auth_signin_homepages,
    classify_url,
    detect_source_type as _detect_source_type,
)


SOURCE_TYPES = tuple(spec.id for spec in SOURCE_TYPE_SPECS)

# Source types whose sign-in homepage should be opened via chromux. Includes
# legacy aliases for existing rows.
AUTH_SIGNIN_HOMEPAGES: dict[str, str] = auth_signin_homepages()


def detect_source_type(url: str) -> str:
    """Return the canonical `source_type` for a URL."""
    return _detect_source_type(url)


def detect_content_type(url: str) -> str:
    """Alias used by collect_cli. Kept thin for back-compat."""
    return detect_source_type(url)


def classify(url: str) -> dict:
    """Return the classification payload used by the web UI add-form.

    Keys consumed by templates/JS:
        source_type, recipe_base, suggested_title
    """
    return classify_url(url)

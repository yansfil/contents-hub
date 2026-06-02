from __future__ import annotations

from contents_hub import api


def test_post_fetch_lens_timeout_allows_two_llm_backed_lenses():
    """Regression: 30s cancelled normal two-Lens routing before inserts.

    The Lens classifier is LLM-backed and the live deployment routes through two
    enabled automatic Lenses. One classification call commonly takes >20s, so a
    30s envelope can cancel the second Lens and lose all matches because
    persistence happens after classification returns.
    """

    assert api.DEFAULT_POST_FETCH_LENS_TIMEOUT_SECONDS >= 120.0

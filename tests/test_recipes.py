"""Tests for llm_wiki.recipes.RecipeRegistry."""
from __future__ import annotations

import pytest

from llm_wiki.recipes import RecipeRegistry
from llm_wiki.subscriptions import Subscription


FEATURED_BASES = [
    "youtube", "twitter", "linkedin",
    "substack", "medium", "reddit", "rss",
]


@pytest.mark.parametrize("base", FEATURED_BASES)
def test_seed_load_featured(base: str) -> None:
    text = RecipeRegistry.get_seed(base)
    assert text is not None, f"missing seed: {base}"
    assert "## LIST_STRATEGY" in text
    assert "## CONTENT_STRATEGY" in text
    assert "## METADATA" in text


def test_seed_unknown_returns_none() -> None:
    assert RecipeRegistry.get_seed("not-a-real-source") is None


def test_seed_empty_base_returns_none() -> None:
    assert RecipeRegistry.get_seed("") is None


def test_get_recipe_override_wins() -> None:
    sub = Subscription(
        url="https://example.com",
        source_type="webpage",
        config={"recipe": "OVERRIDE RECIPE"},
    )
    assert RecipeRegistry.get_recipe(sub) == "OVERRIDE RECIPE"


def test_get_recipe_seed_fallback() -> None:
    sub = Subscription(
        url="https://x.com/foo",
        source_type="twitter",
        config={"recipe_base": "twitter"},
    )
    result = RecipeRegistry.get_recipe(sub)
    assert result is not None
    assert "## LIST_STRATEGY" in result


def test_get_recipe_no_base_no_override_returns_none() -> None:
    # Use a SimpleNamespace to bypass Subscription.__post_init__ defaulting
    # an empty source_type to 'rss'.
    from types import SimpleNamespace

    sub = SimpleNamespace(url="https://example.com", source_type="", config={})
    assert RecipeRegistry.get_recipe(sub) is None


def test_set_override_then_clear() -> None:
    sub = Subscription(
        url="https://example.com",
        source_type="youtube",
        config={"recipe_base": "youtube"},
    )
    # seed is used initially
    initial = RecipeRegistry.get_recipe(sub)
    assert initial and "## LIST_STRATEGY" in initial

    RecipeRegistry.set_override(sub, "CUSTOM RECIPE BODY")
    assert sub.config.get("recipe") == "CUSTOM RECIPE BODY"
    assert RecipeRegistry.get_recipe(sub) == "CUSTOM RECIPE BODY"

    RecipeRegistry.clear_override(sub)
    assert "recipe" not in sub.config
    after_clear = RecipeRegistry.get_recipe(sub)
    assert after_clear == initial


def test_templates_exist() -> None:
    for name in ("explore_prompt", "execute_prompt", "relearn_prompt"):
        text = RecipeRegistry.get_template(name)
        assert text
        assert len(text) > 20


def test_template_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        RecipeRegistry.get_template("no-such-template")

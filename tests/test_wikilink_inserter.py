"""Tests for wikilink_inserter module: [[wikilink]] insertion engine."""

from __future__ import annotations

import pytest

from llm_wiki.entity_extractor import Entity, EntityType, ExtractionResult
from llm_wiki.wikilink_inserter import (
    InsertionResult,
    WikilinkInsertion,
    insert_wikilinks,
    insert_wikilinks_from_extraction,
    _find_protected_zones,
    _find_existing_wikilinks,
    _format_wikilink,
    _merge_zones,
    _build_candidates,
    _match_entities_to_vault,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _entity(name: str, etype: EntityType = EntityType.CONCEPT, confidence: float = 0.8, aliases: list[str] | None = None) -> Entity:
    """Helper to create an Entity."""
    return Entity(name=name, entity_type=etype, confidence=confidence, aliases=aliases or [])


SAMPLE_ENTITIES = [
    _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
    _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
    _entity("Geoffrey Hinton", EntityType.PERSON, 0.95),
    _entity("Transformer", EntityType.CONCEPT, 0.95),
    _entity("NLP", EntityType.ACRONYM, 0.8, aliases=["Natural Language Processing"]),
    _entity("OpenAI", EntityType.ORGANIZATION, 0.9),
]

SAMPLE_TEXT = """\
# Transformers and NLP

The Transformer architecture revolutionized NLP. Geoffrey Hinton contributed
to deep learning foundations. PyTorch and TensorFlow are popular frameworks.

OpenAI has built large language models using Transformer-based architectures.
NLP tasks like translation and summarization benefit greatly.
"""


# ---------------------------------------------------------------------------
# Protected zones
# ---------------------------------------------------------------------------


class TestProtectedZones:
    def test_frontmatter_protected(self):
        text = "---\ntitle: Test\ntags: [ai]\n---\nBody text here."
        zones = _find_protected_zones(text)
        # Frontmatter zone should cover --- to ---
        assert any(s == 0 for s, e in zones)

    def test_code_block_protected(self):
        text = "Before\n```python\ncode here\n```\nAfter"
        zones = _find_protected_zones(text)
        assert len(zones) >= 1
        # The code block range should be protected
        code_start = text.index("```python")
        assert any(s <= code_start < e for s, e in zones)

    def test_inline_code_protected(self):
        text = "Use `PyTorch` for deep learning."
        zones = _find_protected_zones(text)
        assert len(zones) >= 1
        code_start = text.index("`PyTorch`")
        assert any(s <= code_start < e for s, e in zones)

    def test_existing_wikilink_protected(self):
        text = "See [[Transformer]] for details."
        zones = _find_protected_zones(text)
        link_start = text.index("[[Transformer]]")
        assert any(s <= link_start < e for s, e in zones)

    def test_heading_protected(self):
        text = "# Transformer Architecture\n\nBody text."
        zones = _find_protected_zones(text)
        assert any(s == 0 for s, e in zones)

    def test_url_protected(self):
        text = "Visit https://pytorch.org for docs."
        zones = _find_protected_zones(text)
        url_start = text.index("https://")
        assert any(s <= url_start < e for s, e in zones)

    def test_no_protected_zones_in_plain_text(self):
        text = "Just a simple paragraph about AI."
        zones = _find_protected_zones(text)
        assert len(zones) == 0


class TestMergeZones:
    def test_no_overlap(self):
        assert _merge_zones([(0, 5), (10, 15)]) == [(0, 5), (10, 15)]

    def test_overlapping(self):
        assert _merge_zones([(0, 10), (5, 15)]) == [(0, 15)]

    def test_adjacent(self):
        assert _merge_zones([(0, 5), (5, 10)]) == [(0, 10)]

    def test_contained(self):
        assert _merge_zones([(0, 20), (5, 10)]) == [(0, 20)]

    def test_empty(self):
        assert _merge_zones([]) == []

    def test_unsorted_input(self):
        assert _merge_zones([(10, 15), (0, 5)]) == [(0, 5), (10, 15)]


# ---------------------------------------------------------------------------
# Existing wikilink detection
# ---------------------------------------------------------------------------


class TestFindExistingWikilinks:
    def test_basic(self):
        text = "See [[Transformer]] and [[BERT]] here."
        existing = _find_existing_wikilinks(text)
        assert "transformer" in existing
        assert "bert" in existing

    def test_with_display_text(self):
        text = "See [[Transformer|transformer architecture]] here."
        existing = _find_existing_wikilinks(text)
        assert "transformer" in existing

    def test_no_links(self):
        text = "No links here."
        existing = _find_existing_wikilinks(text)
        assert len(existing) == 0


# ---------------------------------------------------------------------------
# Wikilink formatting
# ---------------------------------------------------------------------------


class TestFormatWikilink:
    def test_exact_match(self):
        assert _format_wikilink("PyTorch", "PyTorch") == "[[PyTorch]]"

    def test_case_differs(self):
        assert _format_wikilink("PyTorch", "pytorch") == "[[PyTorch|pytorch]]"

    def test_full_case_differs(self):
        assert _format_wikilink("Machine Learning", "machine learning") == "[[Machine Learning|machine learning]]"


# ---------------------------------------------------------------------------
# Core insertion: insert_wikilinks
# ---------------------------------------------------------------------------


class TestInsertWikilinks:
    def test_basic_insertion(self):
        text = "PyTorch is a great framework."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert "[[PyTorch]]" in result.text
        assert result.count == 1

    def test_first_occurrence_only(self):
        text = "PyTorch is great. I love PyTorch. PyTorch forever."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert result.text.count("[[PyTorch]]") == 1
        # The remaining occurrences should be plain text
        assert result.text.count("PyTorch") == 3  # 1 linked + 2 plain

    def test_case_insensitive_match(self):
        text = "I use pytorch for training."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert "[[PyTorch|pytorch]]" in result.text
        assert result.count == 1

    def test_skip_existing_wikilinks(self):
        text = "See [[PyTorch]] for details. PyTorch is fast."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        # Should not add another link
        assert result.text.count("[[PyTorch]]") == 1
        assert "PyTorch" in result.skipped_existing

    def test_skip_in_heading(self):
        text = "# PyTorch Guide\n\nPyTorch is a framework."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        # Should link in body, not heading
        assert "# PyTorch Guide" in result.text  # heading unchanged
        assert result.count == 1

    def test_skip_in_code_block(self):
        text = "```python\nimport pytorch\n```\nPyTorch is fast."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert result.count == 1
        # Code block should be unchanged
        assert "import pytorch" in result.text

    def test_skip_in_inline_code(self):
        text = "Run `PyTorch` command. PyTorch is great."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert result.count == 1
        assert "`PyTorch`" in result.text  # inline code unchanged

    def test_skip_in_frontmatter(self):
        text = "---\ntitle: PyTorch Guide\n---\nPyTorch is great."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert "title: PyTorch Guide" in result.text  # frontmatter unchanged
        assert result.count == 1

    def test_word_boundary(self):
        text = "LLMOps is about managing LLM deployments."
        entities = [_entity("LLM", EntityType.ACRONYM)]
        result = insert_wikilinks(text, entities)
        # Should NOT match inside "LLMOps", but should match standalone "LLM"
        assert "LLMOps" in result.text  # not broken
        assert "[[LLM]]" in result.text
        assert result.count == 1

    def test_longer_match_first(self):
        text = "Machine Learning and Machine are different."
        entities = [
            _entity("Machine", EntityType.CONCEPT),
            _entity("Machine Learning", EntityType.CONCEPT),
        ]
        result = insert_wikilinks(text, entities)
        assert "[[Machine Learning]]" in result.text
        assert "[[Machine]]" in result.text
        assert result.count == 2

    def test_multiple_entities(self):
        result = insert_wikilinks(SAMPLE_TEXT, SAMPLE_ENTITIES)
        assert result.count > 0
        assert "[[PyTorch]]" in result.text
        assert "[[TensorFlow]]" in result.text
        assert "[[Geoffrey Hinton]]" in result.text

    def test_alias_matching(self):
        text = "Natural Language Processing has evolved."
        entities = [_entity("NLP", EntityType.ACRONYM, aliases=["Natural Language Processing"])]
        result = insert_wikilinks(text, entities)
        assert "[[NLP|Natural Language Processing]]" in result.text
        assert result.count == 1

    def test_min_confidence_filter(self):
        text = "PyTorch and some weak entity."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK, confidence=0.9),
            _entity("weak entity", EntityType.CONCEPT, confidence=0.2),
        ]
        result = insert_wikilinks(text, entities, min_confidence=0.5)
        assert "[[PyTorch]]" in result.text
        # "weak entity" should not be linked due to low confidence
        assert "[[weak entity]]" not in result.text

    def test_max_links(self):
        text = "PyTorch and TensorFlow and OpenAI are here."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK),
            _entity("TensorFlow", EntityType.FRAMEWORK),
            _entity("OpenAI", EntityType.ORGANIZATION),
        ]
        result = insert_wikilinks(text, entities, max_links=2)
        assert result.count == 2

    def test_empty_text(self):
        result = insert_wikilinks("", SAMPLE_ENTITIES)
        assert result.text == ""
        assert result.count == 0

    def test_empty_entities(self):
        result = insert_wikilinks("Some text", [])
        assert result.text == "Some text"
        assert result.count == 0

    def test_no_match_reported(self):
        text = "Nothing relevant here."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert result.count == 0
        assert "PyTorch" in result.skipped_no_match

    def test_vault_pages_used_for_link_target(self):
        text = "I use pytorch daily."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        # Vault has a page named "pytorch-framework"
        vault_pages = {"pytorch": "pytorch-framework"}
        result = insert_wikilinks(text, entities, vault_pages=vault_pages)
        assert "[[pytorch-framework|pytorch]]" in result.text

    def test_url_not_linked(self):
        text = "Visit https://pytorch.org for PyTorch docs."
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        result = insert_wikilinks(text, entities)
        assert "https://pytorch.org" in result.text  # URL unchanged
        assert result.count == 1  # only the standalone mention

    def test_preserves_original_position_report(self):
        text = "PyTorch is used with TensorFlow."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK),
            _entity("TensorFlow", EntityType.FRAMEWORK),
        ]
        result = insert_wikilinks(text, entities)
        assert result.count == 2
        # Insertions should be sorted by position
        assert result.insertions[0].position < result.insertions[1].position

    def test_multi_word_entity_in_sentence(self):
        text = "Geoffrey Hinton won the Nobel Prize."
        entities = [_entity("Geoffrey Hinton", EntityType.PERSON)]
        result = insert_wikilinks(text, entities)
        assert "[[Geoffrey Hinton]]" in result.text
        assert "won the Nobel Prize" in result.text

    def test_heading_not_linked_but_body_is(self):
        text = "# Geoffrey Hinton\n\nGeoffrey Hinton is a computer scientist."
        entities = [_entity("Geoffrey Hinton", EntityType.PERSON)]
        result = insert_wikilinks(text, entities)
        assert result.text.startswith("# Geoffrey Hinton\n")  # heading intact
        assert "[[Geoffrey Hinton]]" in result.text
        assert result.count == 1


# ---------------------------------------------------------------------------
# Build candidates
# ---------------------------------------------------------------------------


class TestBuildCandidates:
    def test_sorted_by_length_descending(self):
        entities = [
            _entity("AI", EntityType.ACRONYM),
            _entity("Machine Learning", EntityType.CONCEPT),
            _entity("Deep Learning", EntityType.CONCEPT),
        ]
        candidates = _build_candidates(entities)
        lengths = [len(c.match_text) for c in candidates]
        assert lengths == sorted(lengths, reverse=True)

    def test_includes_aliases(self):
        entities = [_entity("NLP", EntityType.ACRONYM, aliases=["Natural Language Processing"])]
        candidates = _build_candidates(entities)
        match_texts = {c.match_text for c in candidates}
        assert "NLP" in match_texts
        assert "Natural Language Processing" in match_texts

    def test_deduplicates_across_entities(self):
        entities = [
            _entity("GPT", EntityType.ACRONYM, aliases=["GPT"]),
        ]
        candidates = _build_candidates(entities)
        match_texts = [c.match_text for c in candidates]
        assert match_texts.count("GPT") == 1

    def test_vault_pages_flag(self):
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        vault_pages = {"pytorch": "PyTorch"}
        candidates = _build_candidates(entities, vault_pages)
        assert candidates[0].is_vault_page is True

    def test_no_vault_pages(self):
        entities = [_entity("PyTorch", EntityType.FRAMEWORK)]
        candidates = _build_candidates(entities)
        assert candidates[0].is_vault_page is False


# ---------------------------------------------------------------------------
# High-level API: insert_wikilinks_from_extraction
# ---------------------------------------------------------------------------


class TestInsertWikilinksFromExtraction:
    def test_basic(self):
        extraction = ExtractionResult(
            entities=[
                _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
            ],
            source_text_length=100,
            method="llm",
        )
        text = "PyTorch and TensorFlow are popular."
        result = insert_wikilinks_from_extraction(text, extraction)
        assert "[[PyTorch]]" in result.text
        assert "[[TensorFlow]]" in result.text
        assert result.count == 2

    def test_with_min_confidence(self):
        extraction = ExtractionResult(
            entities=[
                _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                _entity("SomeWeakEntity", EntityType.CONCEPT, 0.2),
            ],
        )
        text = "PyTorch and SomeWeakEntity are here."
        result = insert_wikilinks_from_extraction(text, extraction, min_confidence=0.5)
        assert "[[PyTorch]]" in result.text
        assert "[[SomeWeakEntity]]" not in result.text


# ---------------------------------------------------------------------------
# Integration: complex real-world scenarios
# ---------------------------------------------------------------------------


class TestIntegrationScenarios:
    def test_full_wiki_page(self):
        """Test with a realistic wiki page."""
        text = """\
---
title: Deep Learning Overview
tags: [ai, deep-learning]
---

# Deep Learning Overview

Deep learning is a subset of machine learning that uses neural networks
with multiple layers. Geoffrey Hinton, often called the "godfather of
deep learning", made foundational contributions.

## Frameworks

The two most popular deep learning frameworks are PyTorch and TensorFlow.
PyTorch is favored in research, while TensorFlow is popular in production.

## Key Concepts

- **Backpropagation** — the algorithm used to train neural networks
- **Transformer** — architecture behind modern LLM systems
- NLP tasks benefit from transfer learning

## Code Example

```python
import torch
model = torch.nn.Linear(10, 5)
```
"""
        entities = [
            _entity("Geoffrey Hinton", EntityType.PERSON, 0.95),
            _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
            _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
            _entity("Backpropagation", EntityType.CONCEPT, 0.85),
            _entity("Transformer", EntityType.CONCEPT, 0.9),
            _entity("LLM", EntityType.ACRONYM, 0.8),
            _entity("NLP", EntityType.ACRONYM, 0.8),
            _entity("Deep Learning", EntityType.CONCEPT, 0.7),
        ]

        result = insert_wikilinks(text, entities)

        # Should link in body, not frontmatter or headings
        assert "title: Deep Learning Overview" in result.text  # frontmatter intact
        assert "# Deep Learning Overview" in result.text  # heading intact

        # Should link entities in body paragraphs
        assert "[[Geoffrey Hinton]]" in result.text
        assert "[[PyTorch]]" in result.text
        assert "[[TensorFlow]]" in result.text

        # PyTorch should be linked only once (first occurrence in body)
        assert result.text.count("[[PyTorch]]") == 1

        # Code block should be unchanged
        assert "import torch" in result.text
        assert "[[torch]]" not in result.text

        # Check insertion count is reasonable
        assert result.count >= 4

    def test_already_heavily_linked(self):
        """Text that already has many wikilinks."""
        text = "[[PyTorch]] is great. [[TensorFlow]] too. Both use [[CUDA]]."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK),
            _entity("TensorFlow", EntityType.FRAMEWORK),
            _entity("CUDA", EntityType.TECHNOLOGY),
        ]
        result = insert_wikilinks(text, entities)
        # All entities already linked — no new insertions
        assert result.count == 0
        assert len(result.skipped_existing) == 3

    def test_mixed_existing_and_new(self):
        """Some entities already linked, some new."""
        text = "[[PyTorch]] is great. TensorFlow and OpenAI are also notable."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK),
            _entity("TensorFlow", EntityType.FRAMEWORK),
            _entity("OpenAI", EntityType.ORGANIZATION),
        ]
        result = insert_wikilinks(text, entities)
        assert "[[PyTorch]]" in result.text  # existing, kept
        assert "[[TensorFlow]]" in result.text  # new
        assert "[[OpenAI]]" in result.text  # new
        assert result.count == 2
        assert "PyTorch" in result.skipped_existing

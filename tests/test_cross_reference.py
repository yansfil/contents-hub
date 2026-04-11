"""Tests for cross_reference module: integration of entity extraction + wikilink insertion.

Tests cover:
  - Unit tests for cross_reference() with various input modes
  - Unit tests for cross_reference_with_llm() with mock LLM
  - Batch cross-referencing
  - Vault-aware entity classification
  - Integration tests simulating full pipeline with realistic data
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.entity_extractor import (
    Entity,
    EntityType,
    ExtractionResult,
)
from llm_wiki.cross_reference import (
    BatchCrossReferenceItem,
    CrossReferenceResult,
    cross_reference,
    cross_reference_batch,
    cross_reference_with_llm,
    _classify_entities,
)
from llm_wiki.vault_search import VaultSearch, WikiPage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _entity(
    name: str,
    etype: EntityType = EntityType.CONCEPT,
    confidence: float = 0.8,
    aliases: list[str] | None = None,
) -> Entity:
    """Helper to create an Entity."""
    return Entity(
        name=name,
        entity_type=etype,
        confidence=confidence,
        aliases=aliases or [],
    )


SAMPLE_TEXT = """\
# Transformers and NLP

The Transformer architecture revolutionized NLP. Geoffrey Hinton contributed
to deep learning foundations. PyTorch and TensorFlow are popular frameworks.

OpenAI has built large language models using Transformer-based architectures.
"""

SAMPLE_ENTITIES = [
    _entity("Transformer", EntityType.CONCEPT, 0.95),
    _entity("NLP", EntityType.ACRONYM, 0.8, aliases=["Natural Language Processing"]),
    _entity("Geoffrey Hinton", EntityType.PERSON, 0.95),
    _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
    _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
    _entity("OpenAI", EntityType.ORGANIZATION, 0.9),
]

SAMPLE_LLM_RESPONSE = json.dumps({
    "entities": [
        {
            "name": "Transformer",
            "type": "concept",
            "context": "The Transformer architecture",
            "confidence": 0.95,
            "aliases": [],
        },
        {
            "name": "NLP",
            "type": "acronym",
            "context": "revolutionized NLP",
            "confidence": 0.8,
            "aliases": ["Natural Language Processing"],
        },
        {
            "name": "Geoffrey Hinton",
            "type": "person",
            "context": "Geoffrey Hinton contributed",
            "confidence": 0.95,
            "aliases": [],
        },
        {
            "name": "PyTorch",
            "type": "framework",
            "context": "PyTorch and TensorFlow",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "TensorFlow",
            "type": "framework",
            "context": "PyTorch and TensorFlow",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "OpenAI",
            "type": "organization",
            "context": "OpenAI has built",
            "confidence": 0.9,
            "aliases": [],
        },
    ]
})


class FakeVaultSearch:
    """Fake VaultSearch that returns pre-configured pages."""

    def __init__(self, pages: dict[str, WikiPage] | None = None):
        self._pages = pages or {}

    def find_exact(self, title: str) -> WikiPage | None:
        return self._pages.get(title.lower())

    def by_title(self, query: str):
        return []

    def by_alias(self, query: str):
        return []

    def by_tag(self, tag: str):
        return []


def _make_wiki_page(name: str, path: str = "") -> WikiPage:
    """Create a fake WikiPage.

    Note: WikiPage.name returns the filename stem (Path.stem),
    which is used as the wikilink target. We use the canonical name
    directly to avoid case-mismatch in display aliases.
    """
    if not path:
        # Use canonical name as filename to keep WikiPage.name == name
        path = f"{name}.md"
    return WikiPage(
        path=Path(f"/vault/{path}"),
        relative_path=Path(path),
        title=name,
    )


def _make_mock_llm_client(response_text: str) -> MagicMock:
    """Create a mock Anthropic client."""
    mock_block = MagicMock()
    mock_block.text = response_text
    mock_message = MagicMock()
    mock_message.content = [mock_block]
    client = MagicMock()
    client.messages.create.return_value = mock_message
    return client


# ---------------------------------------------------------------------------
# cross_reference() — with pre-extracted entities
# ---------------------------------------------------------------------------


class TestCrossReferenceWithEntities:
    """Test cross_reference() when entities are provided directly."""

    def test_basic_insertion(self):
        result = cross_reference(
            SAMPLE_TEXT,
            entities=SAMPLE_ENTITIES,
        )
        assert result.entities_found == 6
        assert result.links_inserted > 0
        assert result.extraction_method == "provided"
        assert "[[PyTorch]]" in result.text
        assert "[[TensorFlow]]" in result.text
        assert "[[Geoffrey Hinton]]" in result.text

    def test_min_confidence_filter(self):
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
            _entity("weak", EntityType.CONCEPT, 0.1),
        ]
        text = "PyTorch and weak concepts."
        result = cross_reference(text, entities=entities, min_confidence=0.5)
        assert "[[PyTorch]]" in result.text
        assert "[[weak]]" not in result.text

    def test_max_links(self):
        result = cross_reference(
            SAMPLE_TEXT,
            entities=SAMPLE_ENTITIES,
            max_links=2,
        )
        assert result.links_inserted == 2

    def test_empty_text(self):
        result = cross_reference("", entities=SAMPLE_ENTITIES)
        assert result.text == ""
        assert result.links_inserted == 0

    def test_empty_entities(self):
        result = cross_reference(SAMPLE_TEXT, entities=[])
        assert result.text == SAMPLE_TEXT
        assert result.entities_found == 0
        assert result.links_inserted == 0

    def test_result_properties(self):
        result = cross_reference(SAMPLE_TEXT, entities=SAMPLE_ENTITIES)
        assert isinstance(result.entity_names, list)
        assert "PyTorch" in result.entity_names
        assert isinstance(result.inserted_wikilinks, list)
        assert isinstance(result.summary(), str)
        assert "Entities:" in result.summary()

    def test_to_dict(self):
        result = cross_reference(SAMPLE_TEXT, entities=SAMPLE_ENTITIES)
        d = result.to_dict()
        assert "entities_found" in d
        assert "links_inserted" in d
        assert "extraction_method" in d
        assert "entity_names" in d
        assert d["extraction_method"] == "provided"


# ---------------------------------------------------------------------------
# cross_reference() — with LLM response
# ---------------------------------------------------------------------------


class TestCrossReferenceWithLlmResponse:
    """Test cross_reference() when llm_response is provided."""

    def test_basic(self):
        result = cross_reference(
            SAMPLE_TEXT,
            llm_response=SAMPLE_LLM_RESPONSE,
        )
        assert result.extraction_method == "llm"
        assert result.entities_found == 6
        assert result.links_inserted > 0
        assert "[[PyTorch]]" in result.text

    def test_invalid_llm_response_falls_back_to_regex(self):
        result = cross_reference(
            SAMPLE_TEXT,
            llm_response="not valid json at all",
        )
        assert result.extraction_method == "regex"
        assert result.entities_found > 0  # regex finds something

    def test_llm_response_with_code_fences(self):
        fenced = f"```json\n{SAMPLE_LLM_RESPONSE}\n```"
        result = cross_reference(SAMPLE_TEXT, llm_response=fenced)
        assert result.extraction_method == "llm"
        assert result.entities_found == 6


# ---------------------------------------------------------------------------
# cross_reference() — regex fallback
# ---------------------------------------------------------------------------


class TestCrossReferenceRegexFallback:
    """Test cross_reference() without LLM response (regex fallback)."""

    def test_regex_fallback(self):
        result = cross_reference(SAMPLE_TEXT)
        assert result.extraction_method == "regex"
        assert result.entities_found > 0
        # Regex should find capitalized phrases, acronyms, etc.
        names = result.entity_names
        assert any("Hinton" in n for n in names)

    def test_regex_finds_existing_wikilinks(self):
        text = "See [[Transformer]] and [[BERT]] for more info."
        result = cross_reference(text)
        assert result.extraction_method == "regex"
        assert "Transformer" in result.entity_names
        assert "BERT" in result.entity_names


# ---------------------------------------------------------------------------
# cross_reference() — vault-aware
# ---------------------------------------------------------------------------


class TestCrossReferenceVaultAware:
    """Test cross_reference() with vault search integration."""

    def test_vault_matches_tracked(self):
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
            "openai": _make_wiki_page("OpenAI"),
        })
        result = cross_reference(
            SAMPLE_TEXT,
            entities=SAMPLE_ENTITIES,
            vault_search=vault,
        )
        assert "PyTorch" in result.vault_matches
        assert "OpenAI" in result.vault_matches
        assert result.vault_matches["PyTorch"] == "PyTorch"

    def test_new_entities_tracked(self):
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
        })
        result = cross_reference(
            SAMPLE_TEXT,
            entities=SAMPLE_ENTITIES,
            vault_search=vault,
        )
        new_names = result.new_page_candidates
        # Entities not in vault should appear as new
        assert "Geoffrey Hinton" in new_names
        assert "TensorFlow" in new_names
        assert "PyTorch" not in new_names  # in vault

    def test_no_vault_all_new(self):
        result = cross_reference(
            SAMPLE_TEXT,
            entities=SAMPLE_ENTITIES,
        )
        # Without vault search, all entities are new
        assert len(result.new_entities) == 6
        assert len(result.vault_matches) == 0

    def test_vault_alias_matching(self):
        entities = [
            _entity("NLP", EntityType.ACRONYM, aliases=["Natural Language Processing"]),
        ]
        vault = FakeVaultSearch({
            "natural language processing": _make_wiki_page("Natural Language Processing"),
        })
        result = cross_reference(
            "NLP is important.",
            entities=entities,
            vault_search=vault,
        )
        # Should match via alias
        assert "NLP" in result.vault_matches


# ---------------------------------------------------------------------------
# cross_reference_with_llm() — mock LLM
# ---------------------------------------------------------------------------


class TestCrossReferenceWithLlm:
    """Test cross_reference_with_llm() with mock LLM client."""

    def test_successful_extraction(self):
        client = _make_mock_llm_client(SAMPLE_LLM_RESPONSE)
        result = cross_reference_with_llm(
            SAMPLE_TEXT,
            client=client,
        )
        assert result.extraction_method == "llm"
        assert result.entities_found == 6
        assert result.links_inserted > 0
        assert "[[PyTorch]]" in result.text

    def test_llm_failure_falls_back(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        result = cross_reference_with_llm(
            SAMPLE_TEXT,
            client=client,
            fallback_on_error=True,
        )
        assert result.extraction_method == "regex"
        assert result.entities_found > 0

    def test_with_vault_search(self):
        client = _make_mock_llm_client(SAMPLE_LLM_RESPONSE)
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
        })
        result = cross_reference_with_llm(
            SAMPLE_TEXT,
            client=client,
            vault_search=vault,
        )
        assert "PyTorch" in result.vault_matches

    def test_prompt_sent_to_llm(self):
        client = _make_mock_llm_client(SAMPLE_LLM_RESPONSE)
        cross_reference_with_llm(SAMPLE_TEXT, client=client)
        call_kwargs = client.messages.create.call_args[1]
        assert "messages" in call_kwargs
        # Prompt should contain the source text
        assert "Transformer" in call_kwargs["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Batch cross-referencing
# ---------------------------------------------------------------------------


class TestCrossReferenceBatch:
    """Test cross_reference_batch()."""

    def test_basic_batch(self):
        items = [
            BatchCrossReferenceItem(
                key="page1",
                text="PyTorch is a deep learning framework.",
                entities=[_entity("PyTorch", EntityType.FRAMEWORK)],
            ),
            BatchCrossReferenceItem(
                key="page2",
                text="TensorFlow is popular for production.",
                entities=[_entity("TensorFlow", EntityType.FRAMEWORK)],
            ),
        ]
        results = cross_reference_batch(items)
        assert len(results) == 2
        assert "page1" in results
        assert "page2" in results
        assert "[[PyTorch]]" in results["page1"].text
        assert "[[TensorFlow]]" in results["page2"].text

    def test_batch_with_llm_responses(self):
        items = [
            BatchCrossReferenceItem(
                key="page1",
                text=SAMPLE_TEXT,
                llm_response=SAMPLE_LLM_RESPONSE,
            ),
        ]
        results = cross_reference_batch(items)
        assert results["page1"].extraction_method == "llm"
        assert results["page1"].entities_found == 6

    def test_batch_empty(self):
        results = cross_reference_batch([])
        assert results == {}

    def test_batch_shared_vault(self):
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
        })
        items = [
            BatchCrossReferenceItem(
                key="p1",
                text="PyTorch is great.",
                entities=[_entity("PyTorch", EntityType.FRAMEWORK)],
            ),
            BatchCrossReferenceItem(
                key="p2",
                text="I love PyTorch too.",
                entities=[_entity("PyTorch", EntityType.FRAMEWORK)],
            ),
        ]
        results = cross_reference_batch(items, vault_search=vault)
        # Both items should recognize PyTorch as a vault page
        assert "PyTorch" in results["p1"].vault_matches
        assert "PyTorch" in results["p2"].vault_matches


# ---------------------------------------------------------------------------
# _classify_entities helper
# ---------------------------------------------------------------------------


class TestClassifyEntities:
    def test_without_vault(self):
        matches, new = _classify_entities(SAMPLE_ENTITIES, None)
        assert matches == {}
        assert len(new) == 6

    def test_with_vault_partial_match(self):
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
            "openai": _make_wiki_page("OpenAI"),
        })
        matches, new = _classify_entities(SAMPLE_ENTITIES, vault)
        assert "PyTorch" in matches
        assert "OpenAI" in matches
        assert len(matches) == 2
        new_names = [e.name for e in new]
        assert "PyTorch" not in new_names
        assert "Geoffrey Hinton" in new_names

    def test_with_vault_all_match(self):
        pages = {}
        for e in SAMPLE_ENTITIES:
            pages[e.name.lower()] = _make_wiki_page(e.name)
        vault = FakeVaultSearch(pages)
        matches, new = _classify_entities(SAMPLE_ENTITIES, vault)
        assert len(matches) == 6
        assert len(new) == 0


# ---------------------------------------------------------------------------
# Integration tests — realistic pipeline scenarios
# ---------------------------------------------------------------------------


class TestIntegrationScenarios:
    """End-to-end integration tests simulating realistic wiki compile flows."""

    def test_full_pipeline_with_llm_response(self):
        """Simulate: source → extract (LLM) → insert links → result."""
        wiki_text = textwrap.dedent("""\
            ---
            title: Introduction to Transformers
            tags: [ai, deep-learning, nlp]
            ---

            # Introduction to Transformers

            The Transformer architecture was introduced by Google Brain in 2017.
            It uses attention mechanisms instead of recurrence, enabling parallel
            processing of sequences.

            ## Key Concepts

            Self-attention allows the model to weigh different parts of the input.
            Multi-head attention extends this by running multiple attention operations
            in parallel.

            ## Impact

            Transformers are the foundation of BERT, GPT, and other large language
            models. PyTorch and TensorFlow both provide robust Transformer
            implementations. OpenAI and Google DeepMind have pushed the boundaries
            of what these models can achieve.
        """)

        llm_response = json.dumps({
            "entities": [
                {"name": "Transformer", "type": "concept", "confidence": 0.95,
                 "context": "Transformer architecture", "aliases": ["Transformer Architecture"]},
                {"name": "Google Brain", "type": "organization", "confidence": 0.9,
                 "context": "introduced by Google Brain", "aliases": []},
                {"name": "attention mechanism", "type": "concept", "confidence": 0.9,
                 "context": "uses attention mechanisms", "aliases": ["self-attention", "multi-head attention"]},
                {"name": "BERT", "type": "acronym", "confidence": 0.85,
                 "context": "foundation of BERT", "aliases": []},
                {"name": "GPT", "type": "acronym", "confidence": 0.85,
                 "context": "foundation of GPT", "aliases": []},
                {"name": "PyTorch", "type": "framework", "confidence": 0.9,
                 "context": "PyTorch and TensorFlow", "aliases": []},
                {"name": "TensorFlow", "type": "framework", "confidence": 0.9,
                 "context": "PyTorch and TensorFlow", "aliases": []},
                {"name": "OpenAI", "type": "organization", "confidence": 0.9,
                 "context": "OpenAI and Google DeepMind", "aliases": []},
                {"name": "Google DeepMind", "type": "organization", "confidence": 0.9,
                 "context": "OpenAI and Google DeepMind", "aliases": ["DeepMind"]},
            ]
        })

        # Set up vault with some existing pages
        vault = FakeVaultSearch({
            "transformer": _make_wiki_page("Transformer"),
            "pytorch": _make_wiki_page("PyTorch"),
            "bert": _make_wiki_page("BERT"),
        })

        result = cross_reference(
            wiki_text,
            llm_response=llm_response,
            vault_search=vault,
        )

        # Verify extraction
        assert result.extraction_method == "llm"
        assert result.entities_found == 9

        # Verify vault classification
        assert "Transformer" in result.vault_matches
        assert "PyTorch" in result.vault_matches
        assert "BERT" in result.vault_matches
        assert len(result.vault_matches) == 3

        # Verify new page candidates
        new_names = result.new_page_candidates
        assert "Google Brain" in new_names
        assert "OpenAI" in new_names
        assert "Google DeepMind" in new_names

        # Verify wikilinks inserted
        assert result.links_inserted > 0
        assert "[[PyTorch]]" in result.text
        assert "[[TensorFlow]]" in result.text

        # Frontmatter should be untouched
        assert "title: Introduction to Transformers" in result.text
        assert "tags: [ai, deep-learning, nlp]" in result.text

        # Headings should be untouched
        assert "# Introduction to Transformers" in result.text

        # Summary should be readable
        summary = result.summary()
        assert "Entities:" in summary
        assert "Links:" in summary

    def test_pipeline_with_existing_wikilinks(self):
        """Source already has some wikilinks — should not duplicate."""
        text = textwrap.dedent("""\
            [[PyTorch]] is the most popular framework. TensorFlow is also used.
            Geoffrey Hinton contributed to the field of deep learning.
            OpenAI builds large language models.
        """)

        result = cross_reference(
            text,
            entities=[
                _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
                _entity("Geoffrey Hinton", EntityType.PERSON, 0.95),
                _entity("OpenAI", EntityType.ORGANIZATION, 0.9),
            ],
        )

        # PyTorch was already linked
        assert "PyTorch" in result.insertion.skipped_existing
        # Others should be newly linked
        assert "[[TensorFlow]]" in result.text
        assert "[[Geoffrey Hinton]]" in result.text
        assert "[[OpenAI]]" in result.text
        # Original link preserved
        assert "[[PyTorch]]" in result.text

    def test_pipeline_preserves_code_blocks(self):
        """Code blocks should not get wikilinks."""
        text = textwrap.dedent("""\
            # PyTorch Tutorial

            PyTorch is a deep learning framework.

            ```python
            import torch
            model = torch.nn.Transformer()
            ```

            Use PyTorch for training models.
        """)

        result = cross_reference(
            text,
            entities=[
                _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                _entity("Transformer", EntityType.CONCEPT, 0.85),
            ],
        )

        # Code block should be unchanged
        assert "import torch" in result.text
        assert "torch.nn.Transformer()" in result.text
        # PyTorch should be linked once in body (not heading or code)
        assert "[[PyTorch]]" in result.text

    def test_pipeline_with_frontmatter(self):
        """Frontmatter should not be modified."""
        text = textwrap.dedent("""\
            ---
            title: PyTorch Guide
            tags: [pytorch, deep-learning]
            aliases: [PyTorch Tutorial]
            ---

            PyTorch is a popular framework for deep learning.
            TensorFlow is an alternative.
        """)

        result = cross_reference(
            text,
            entities=[
                _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
            ],
        )

        # Frontmatter untouched
        assert "title: PyTorch Guide" in result.text
        assert "aliases: [PyTorch Tutorial]" in result.text
        # Body gets links
        assert "[[PyTorch]]" in result.text or "[[TensorFlow]]" in result.text

    def test_pipeline_round_trip_idempotent(self):
        """Running cross-reference on already-linked text should not add more links."""
        text = "PyTorch and TensorFlow are frameworks."
        entities = [
            _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
            _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
        ]

        # First pass
        result1 = cross_reference(text, entities=entities)
        assert result1.links_inserted == 2

        # Second pass on the output
        result2 = cross_reference(result1.text, entities=entities)
        # Should not insert any new links — they're already there
        assert result2.links_inserted == 0
        assert result2.text == result1.text

    def test_batch_pipeline_multiple_pages(self):
        """Simulate batch processing of multiple wiki pages."""
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
            "transformer": _make_wiki_page("Transformer"),
        })

        items = [
            BatchCrossReferenceItem(
                key="ai-overview.md",
                text="PyTorch and TensorFlow power modern AI. Transformers changed NLP.",
                entities=[
                    _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                    _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
                    _entity("Transformer", EntityType.CONCEPT, 0.95),
                    _entity("NLP", EntityType.ACRONYM, 0.8),
                ],
            ),
            BatchCrossReferenceItem(
                key="ml-tools.md",
                text="Use PyTorch or JAX for research. TensorFlow for production.",
                entities=[
                    _entity("PyTorch", EntityType.FRAMEWORK, 0.9),
                    _entity("JAX", EntityType.FRAMEWORK, 0.85),
                    _entity("TensorFlow", EntityType.FRAMEWORK, 0.9),
                ],
            ),
        ]

        results = cross_reference_batch(items, vault_search=vault)

        # Page 1: all entities linked
        r1 = results["ai-overview.md"]
        assert r1.links_inserted >= 3
        assert "PyTorch" in r1.vault_matches
        assert "Transformer" in r1.vault_matches
        assert "NLP" in r1.new_page_candidates

        # Page 2: entities linked
        r2 = results["ml-tools.md"]
        assert r2.links_inserted >= 2
        assert "PyTorch" in r2.vault_matches
        assert "JAX" in r2.new_page_candidates

    def test_end_to_end_mock_llm(self):
        """Full end-to-end test with mock LLM client."""
        client = _make_mock_llm_client(SAMPLE_LLM_RESPONSE)
        vault = FakeVaultSearch({
            "pytorch": _make_wiki_page("PyTorch"),
            "nlp": _make_wiki_page("NLP"),
        })

        result = cross_reference_with_llm(
            SAMPLE_TEXT,
            client=client,
            vault_search=vault,
            min_confidence=0.5,
        )

        # Extraction via LLM
        assert result.extraction_method == "llm"
        assert result.entities_found == 6

        # Vault matches
        assert "PyTorch" in result.vault_matches
        assert "NLP" in result.vault_matches

        # Links inserted
        assert result.links_inserted > 0
        assert "[[PyTorch]]" in result.text

        # New entity candidates
        new_names = result.new_page_candidates
        assert "Geoffrey Hinton" in new_names
        assert "OpenAI" in new_names

        # Serialization
        d = result.to_dict()
        assert d["extraction_method"] == "llm"
        assert d["entities_found"] == 6

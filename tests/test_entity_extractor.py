"""Tests for entity_extractor module: LLM-based concept/entity extraction."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.entity_extractor import (
    Entity,
    EntityType,
    ExtractionError,
    ExtractionParseError,
    ExtractionResult,
    MAX_INPUT_CHARS,
    build_extraction_prompt,
    extract_entities,
    extract_entities_regex,
    extract_entities_with_llm,
    parse_extraction_response,
    _deduplicate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SAMPLE_MARKDOWN = """\
# Transformers and Attention Mechanisms

The **Transformer** architecture, introduced by Vaswani et al. in the paper
"Attention Is All You Need", revolutionized NLP. Unlike previous RNN-based
models, Transformers use [[self-attention]] to process sequences in parallel.

## Key People

- **Geoffrey Hinton** — pioneer of deep learning
- **Ilya Sutskever** — co-founder of OpenAI

## Tools & Frameworks

The most popular implementations include `PyTorch` and `TensorFlow`.
Google's [[BERT]] and OpenAI's GPT series are built on this architecture.
Meta AI released LLaMA as an open-source alternative.

#ai #deep-learning #nlp/transformers
"""


SAMPLE_LLM_RESPONSE = json.dumps({
    "entities": [
        {
            "name": "Transformer",
            "type": "concept",
            "context": "The Transformer architecture",
            "confidence": 0.95,
            "aliases": ["Transformer Architecture"],
        },
        {
            "name": "Attention Mechanism",
            "type": "concept",
            "context": "use self-attention to process sequences",
            "confidence": 0.9,
            "aliases": ["self-attention"],
        },
        {
            "name": "Geoffrey Hinton",
            "type": "person",
            "context": "pioneer of deep learning",
            "confidence": 0.95,
            "aliases": [],
        },
        {
            "name": "Ilya Sutskever",
            "type": "person",
            "context": "co-founder of OpenAI",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "OpenAI",
            "type": "organization",
            "context": "co-founder of OpenAI",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "PyTorch",
            "type": "framework",
            "context": "most popular implementations include PyTorch",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "TensorFlow",
            "type": "framework",
            "context": "most popular implementations include TensorFlow",
            "confidence": 0.9,
            "aliases": [],
        },
        {
            "name": "BERT",
            "type": "acronym",
            "context": "Google's BERT",
            "confidence": 0.85,
            "aliases": ["Bidirectional Encoder Representations from Transformers"],
        },
        {
            "name": "GPT",
            "type": "acronym",
            "context": "OpenAI's GPT series",
            "confidence": 0.85,
            "aliases": ["Generative Pre-trained Transformer"],
        },
        {
            "name": "NLP",
            "type": "acronym",
            "context": "revolutionized NLP",
            "confidence": 0.8,
            "aliases": ["Natural Language Processing"],
        },
        {
            "name": "Meta AI",
            "type": "organization",
            "context": "Meta AI released LLaMA",
            "confidence": 0.85,
            "aliases": [],
        },
        {
            "name": "LLaMA",
            "type": "tool",
            "context": "Meta AI released LLaMA",
            "confidence": 0.8,
            "aliases": [],
        },
    ]
})


# ---------------------------------------------------------------------------
# Entity basics
# ---------------------------------------------------------------------------


class TestEntity:
    def test_wikilink(self):
        e = Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK)
        assert e.wikilink == "[[PyTorch]]"

    def test_normalized_name(self):
        e = Entity(name="  Geoffrey Hinton  ", entity_type=EntityType.PERSON)
        assert e.normalized_name == "geoffrey hinton"

    def test_frozen(self):
        e = Entity(name="Test", entity_type=EntityType.CONCEPT)
        with pytest.raises(AttributeError):
            e.name = "Other"  # type: ignore


class TestExtractionResult:
    def test_entity_names(self):
        entities = [
            Entity(name="A", entity_type=EntityType.CONCEPT),
            Entity(name="B", entity_type=EntityType.TOOL),
        ]
        result = ExtractionResult(entities=entities)
        assert result.entity_names == ["A", "B"]

    def test_wikilinks(self):
        entities = [
            Entity(name="A", entity_type=EntityType.CONCEPT),
            Entity(name="B", entity_type=EntityType.TOOL),
        ]
        result = ExtractionResult(entities=entities)
        assert result.wikilinks == ["[[A]]", "[[B]]"]

    def test_by_type(self):
        entities = [
            Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK),
            Entity(name="Hinton", entity_type=EntityType.PERSON),
            Entity(name="React", entity_type=EntityType.FRAMEWORK),
        ]
        result = ExtractionResult(entities=entities)
        frameworks = result.by_type(EntityType.FRAMEWORK)
        assert len(frameworks) == 2
        assert frameworks[0].name == "PyTorch"
        assert frameworks[1].name == "React"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildExtractionPrompt:
    def test_short_text_not_truncated(self):
        prompt, truncated = build_extraction_prompt("Hello world")
        assert not truncated
        assert "Hello world" in prompt
        assert "[...text truncated...]" not in prompt

    def test_long_text_truncated(self):
        long_text = "x" * (MAX_INPUT_CHARS + 100)
        prompt, truncated = build_extraction_prompt(long_text)
        assert truncated
        assert "[...text truncated...]" in prompt
        # Original text should be cut
        assert len(prompt) < len(long_text) + 1000

    def test_prompt_contains_format_instructions(self):
        prompt, _ = build_extraction_prompt("Some text about AI")
        assert "entities" in prompt
        assert "JSON" in prompt
        assert "concept" in prompt
        assert "person" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseExtractionResponse:
    def test_valid_json(self):
        entities = parse_extraction_response(SAMPLE_LLM_RESPONSE)
        assert len(entities) > 0
        names = [e.name for e in entities]
        assert "Transformer" in names
        assert "Geoffrey Hinton" in names
        assert "PyTorch" in names

    def test_json_with_code_fences(self):
        fenced = f"```json\n{SAMPLE_LLM_RESPONSE}\n```"
        entities = parse_extraction_response(fenced)
        assert len(entities) > 0

    def test_entity_types_parsed(self):
        entities = parse_extraction_response(SAMPLE_LLM_RESPONSE)
        type_map = {e.name: e.entity_type for e in entities}
        assert type_map["Transformer"] == EntityType.CONCEPT
        assert type_map["Geoffrey Hinton"] == EntityType.PERSON
        assert type_map["PyTorch"] == EntityType.FRAMEWORK
        assert type_map["OpenAI"] == EntityType.ORGANIZATION
        assert type_map["NLP"] == EntityType.ACRONYM

    def test_aliases_parsed(self):
        entities = parse_extraction_response(SAMPLE_LLM_RESPONSE)
        bert = next(e for e in entities if e.name == "BERT")
        assert "Bidirectional Encoder Representations from Transformers" in bert.aliases

    def test_confidence_parsed(self):
        entities = parse_extraction_response(SAMPLE_LLM_RESPONSE)
        transformer = next(e for e in entities if e.name == "Transformer")
        assert transformer.confidence == 0.95

    def test_empty_entities_list(self):
        response = json.dumps({"entities": []})
        entities = parse_extraction_response(response)
        assert entities == []

    def test_invalid_json_raises(self):
        with pytest.raises(ExtractionParseError):
            parse_extraction_response("not json at all")

    def test_missing_entities_key_returns_empty(self):
        response = json.dumps({"something_else": "value"})
        entities = parse_extraction_response(response)
        assert entities == []

    def test_unknown_type_defaults_to_concept(self):
        response = json.dumps({
            "entities": [{"name": "Foo", "type": "unknown_type"}]
        })
        entities = parse_extraction_response(response)
        assert len(entities) == 1
        assert entities[0].entity_type == EntityType.CONCEPT

    def test_skip_entity_with_empty_name(self):
        response = json.dumps({
            "entities": [
                {"name": "", "type": "concept"},
                {"name": "Valid", "type": "concept"},
            ]
        })
        entities = parse_extraction_response(response)
        assert len(entities) == 1
        assert entities[0].name == "Valid"

    def test_confidence_clamped(self):
        response = json.dumps({
            "entities": [{"name": "A", "type": "concept", "confidence": 5.0}]
        })
        entities = parse_extraction_response(response)
        assert entities[0].confidence == 1.0

    def test_messy_json_extracted(self):
        """LLM sometimes includes extra text around JSON."""
        messy = 'Here are the entities:\n{"entities": [{"name": "Test", "type": "concept"}]}\nDone!'
        entities = parse_extraction_response(messy)
        assert len(entities) == 1
        assert entities[0].name == "Test"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_removes_exact_duplicates(self):
        entities = [
            Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK, confidence=0.9),
            Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK, confidence=0.8),
        ]
        deduped = _deduplicate(entities)
        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9  # keeps higher confidence

    def test_case_insensitive_dedup(self):
        entities = [
            Entity(name="pytorch", entity_type=EntityType.FRAMEWORK, confidence=0.7),
            Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK, confidence=0.9),
        ]
        deduped = _deduplicate(entities)
        assert len(deduped) == 1
        assert deduped[0].name == "PyTorch"  # keeps name from higher confidence

    def test_merges_aliases(self):
        entities = [
            Entity(name="GPT", entity_type=EntityType.ACRONYM, aliases=["Generative Pre-trained Transformer"]),
            Entity(name="GPT", entity_type=EntityType.ACRONYM, aliases=["OpenAI GPT"]),
        ]
        deduped = _deduplicate(entities)
        assert len(deduped) == 1
        assert "Generative Pre-trained Transformer" in deduped[0].aliases
        assert "OpenAI GPT" in deduped[0].aliases

    def test_distinct_entities_preserved(self):
        entities = [
            Entity(name="PyTorch", entity_type=EntityType.FRAMEWORK),
            Entity(name="TensorFlow", entity_type=EntityType.FRAMEWORK),
        ]
        deduped = _deduplicate(entities)
        assert len(deduped) == 2


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------


class TestExtractEntitiesRegex:
    def test_extracts_wikilinks(self):
        text = "See [[Transformers]] and [[BERT]] for details."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "Transformers" in names
        assert "BERT" in names

    def test_extracts_wikilinks_with_alias(self):
        text = "See [[Transformer|transformer architecture]] for details."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "Transformer" in names

    def test_extracts_hashtags(self):
        text = "Topics: #ai #deep-learning #nlp/transformers"
        entities = extract_entities_regex(text)
        names_lower = [e.normalized_name for e in entities]
        assert "ai" in names_lower
        assert "deep learning" in names_lower

    def test_extracts_backtick_terms(self):
        text = "Install `Docker` and run `kubectl apply`."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "Docker" in names
        assert "kubectl apply" in names

    def test_skips_url_like_backtick(self):
        text = "Run `https://example.com` or `$HOME/bin`."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert not any("https" in n for n in names)
        assert not any("$HOME" in n for n in names)

    def test_extracts_capitalized_phrases(self):
        text = "Geoffrey Hinton and Andrew Ng are pioneers."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "Geoffrey Hinton" in names
        assert "Andrew Ng" in names

    def test_extracts_acronyms(self):
        text = "LLM and RAG are important techniques in NLP."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "LLM" in names
        assert "RAG" in names
        assert "NLP" in names

    def test_filters_acronym_stopwords(self):
        text = "THE AND FOR are not entities."
        entities = extract_entities_regex(text)
        names = [e.name for e in entities]
        assert "THE" not in names
        assert "AND" not in names
        assert "FOR" not in names

    def test_deduplicates(self):
        text = "[[PyTorch]] is great. Use `PyTorch` for deep learning."
        entities = extract_entities_regex(text)
        pytorch_entities = [e for e in entities if e.normalized_name == "pytorch"]
        assert len(pytorch_entities) == 1

    def test_empty_text(self):
        entities = extract_entities_regex("")
        assert entities == []

    def test_full_sample(self):
        entities = extract_entities_regex(SAMPLE_MARKDOWN)
        names = [e.name for e in entities]
        # Should find wikilinks
        assert "self-attention" in names
        assert "BERT" in names
        # Should find backtick terms
        assert "PyTorch" in names
        assert "TensorFlow" in names
        # Should find acronyms
        assert "NLP" in names or "RNN" in names
        # Should find people
        assert "Geoffrey Hinton" in names


# ---------------------------------------------------------------------------
# High-level API: extract_entities
# ---------------------------------------------------------------------------


class TestExtractEntities:
    def test_with_llm_response(self):
        result = extract_entities(SAMPLE_MARKDOWN, llm_response=SAMPLE_LLM_RESPONSE)
        assert result.method == "llm"
        assert len(result.entities) > 0
        assert "Transformer" in result.entity_names

    def test_without_llm_response_falls_back_to_regex(self):
        result = extract_entities(SAMPLE_MARKDOWN)
        assert result.method == "regex"
        assert len(result.entities) > 0

    def test_invalid_llm_response_falls_back_to_regex(self):
        result = extract_entities(SAMPLE_MARKDOWN, llm_response="not json")
        assert result.method == "regex"
        assert len(result.entities) > 0

    def test_source_text_length_recorded(self):
        result = extract_entities("short text")
        assert result.source_text_length == 10

    def test_truncation_flag(self):
        long_text = "x" * (MAX_INPUT_CHARS + 100)
        result = extract_entities(long_text)
        assert result.was_truncated is True

        short_text = "hello"
        result = extract_entities(short_text)
        assert result.was_truncated is False


# ---------------------------------------------------------------------------
# End-to-end with mock LLM
# ---------------------------------------------------------------------------


class TestExtractEntitiesWithLlm:
    def _make_mock_client(self, response_text: str) -> MagicMock:
        """Create a mock Anthropic client that returns given text."""
        mock_block = MagicMock()
        mock_block.text = response_text
        mock_message = MagicMock()
        mock_message.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_successful_extraction(self):
        client = self._make_mock_client(SAMPLE_LLM_RESPONSE)
        result = extract_entities_with_llm(
            SAMPLE_MARKDOWN, client=client
        )
        assert result.method == "llm"
        assert len(result.entities) > 0
        assert "Transformer" in result.entity_names

    def test_llm_failure_falls_back_to_regex(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        result = extract_entities_with_llm(
            SAMPLE_MARKDOWN, client=client, fallback_on_error=True
        )
        assert result.method == "regex"
        assert len(result.entities) > 0

    def test_llm_failure_raises_when_no_fallback(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        with pytest.raises(ExtractionError):
            extract_entities_with_llm(
                SAMPLE_MARKDOWN, client=client, fallback_on_error=False
            )

    def test_empty_response_falls_back(self):
        mock_message = MagicMock()
        mock_message.content = []
        client = MagicMock()
        client.messages.create.return_value = mock_message
        result = extract_entities_with_llm(
            SAMPLE_MARKDOWN, client=client, fallback_on_error=True
        )
        assert result.method == "regex"

    def test_prompt_sent_to_llm(self):
        client = self._make_mock_client(SAMPLE_LLM_RESPONSE)
        extract_entities_with_llm(SAMPLE_MARKDOWN, client=client)

        call_kwargs = client.messages.create.call_args[1]
        assert "system" in call_kwargs
        assert "messages" in call_kwargs
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"
        # Prompt should contain the markdown text
        assert "Transformer" in call_kwargs["messages"][0]["content"]

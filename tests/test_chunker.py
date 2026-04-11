"""Tests for llm_wiki.chunker — markdown-aware document chunking."""

from __future__ import annotations

import pytest

from llm_wiki.chunker import (
    ChunkingConfig,
    DocumentChunk,
    chunk_document,
    chunk_vault_page,
    count_tokens,
    _content_hash,
    _split_by_headings,
    _split_paragraphs,
    _split_sentences,
    _chunk_text_block,
    _apply_overlap,
)


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


class TestCountTokens:
    def test_empty(self):
        assert count_tokens("") == 0
        assert count_tokens("   ") == 0

    def test_simple(self):
        assert count_tokens("hello world") == 2
        assert count_tokens("one two three four") == 4

    def test_multiline(self):
        assert count_tokens("hello\nworld\nfoo") == 3


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_deterministic(self):
        h1 = _content_hash("hello")
        h2 = _content_hash("hello")
        assert h1 == h2

    def test_different_content(self):
        h1 = _content_hash("hello")
        h2 = _content_hash("world")
        assert h1 != h2

    def test_returns_hex_string(self):
        h = _content_hash("test")
        assert len(h) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# _split_by_headings
# ---------------------------------------------------------------------------


class TestSplitByHeadings:
    def test_no_headings(self):
        sections = _split_by_headings("Just some text.\nMore text.")
        assert len(sections) == 1
        assert sections[0].heading == ""
        assert sections[0].level == 0

    def test_single_heading(self):
        text = "# Title\n\nBody text here."
        sections = _split_by_headings(text)
        assert len(sections) == 1
        assert sections[0].heading == "Title"
        assert sections[0].level == 1
        assert "Body text here" in sections[0].body

    def test_multiple_headings(self):
        text = "Preamble.\n\n## Section A\n\nText A.\n\n## Section B\n\nText B."
        sections = _split_by_headings(text)
        assert len(sections) == 3  # preamble + 2 sections
        assert sections[0].heading == ""  # preamble
        assert sections[1].heading == "Section A"
        assert sections[2].heading == "Section B"

    def test_nested_headings(self):
        text = "# Main\n\n## Sub\n\nText.\n\n### SubSub\n\nDeep."
        sections = _split_by_headings(text)
        assert len(sections) == 3
        assert sections[0].level == 1
        assert sections[1].level == 2
        assert sections[2].level == 3

    def test_empty_text(self):
        assert _split_by_headings("") == []
        assert _split_by_headings("   ") == []

    def test_heading_only(self):
        sections = _split_by_headings("## Heading Only")
        assert len(sections) == 1
        assert sections[0].heading == "Heading Only"
        assert sections[0].body == ""


# ---------------------------------------------------------------------------
# _split_paragraphs
# ---------------------------------------------------------------------------


class TestSplitParagraphs:
    def test_single_paragraph(self):
        result = _split_paragraphs("Hello world.")
        assert result == ["Hello world."]

    def test_multiple_paragraphs(self):
        result = _split_paragraphs("Para 1.\n\nPara 2.\n\nPara 3.")
        assert len(result) == 3

    def test_empty(self):
        assert _split_paragraphs("") == []
        assert _split_paragraphs("   ") == []


# ---------------------------------------------------------------------------
# _split_sentences
# ---------------------------------------------------------------------------


class TestSplitSentences:
    def test_simple(self):
        result = _split_sentences("First sentence. Second sentence.")
        assert len(result) == 2

    def test_question_and_exclamation(self):
        result = _split_sentences("What? Yes! Done.")
        assert len(result) == 3

    def test_single_sentence(self):
        result = _split_sentences("Just one.")
        assert result == ["Just one."]


# ---------------------------------------------------------------------------
# ChunkingConfig validation
# ---------------------------------------------------------------------------


class TestChunkingConfig:
    def test_defaults(self):
        config = ChunkingConfig()
        assert config.max_tokens == 512
        assert config.overlap_tokens == 50
        assert config.min_tokens == 30

    def test_custom(self):
        config = ChunkingConfig(max_tokens=256, overlap_tokens=20, min_tokens=10)
        assert config.max_tokens == 256

    def test_too_small_max(self):
        with pytest.raises(ValueError, match="max_tokens"):
            ChunkingConfig(max_tokens=10)

    def test_overlap_exceeds_max(self):
        with pytest.raises(ValueError, match="overlap_tokens"):
            ChunkingConfig(max_tokens=100, overlap_tokens=200)

    def test_negative_min(self):
        with pytest.raises(ValueError, match="min_tokens"):
            ChunkingConfig(min_tokens=-1)


# ---------------------------------------------------------------------------
# _chunk_text_block
# ---------------------------------------------------------------------------


class TestChunkTextBlock:
    def test_short_text_single_chunk(self):
        result = _chunk_text_block("Short text here.", max_tokens=100)
        assert len(result) == 1
        assert result[0] == "Short text here."

    def test_long_text_splits(self):
        # Create text with 200 words (single paragraph, no sentence boundaries)
        words = ["word"] * 200
        text = " ".join(words)
        result = _chunk_text_block(text, max_tokens=50)
        assert len(result) == 4  # 200 / 50 = 4 chunks
        for chunk in result:
            assert count_tokens(chunk) <= 50

    def test_paragraph_split(self):
        para1 = " ".join(["alpha"] * 30)
        para2 = " ".join(["beta"] * 30)
        text = f"{para1}\n\n{para2}"
        result = _chunk_text_block(text, max_tokens=35)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _apply_overlap
# ---------------------------------------------------------------------------


class TestApplyOverlap:
    def test_no_overlap(self):
        chunks = ["chunk one", "chunk two"]
        result = _apply_overlap(chunks, overlap_tokens=0)
        assert result == chunks

    def test_single_chunk(self):
        result = _apply_overlap(["only one"], overlap_tokens=5)
        assert result == ["only one"]

    def test_overlap_applied(self):
        chunks = ["first chunk of text here", "second chunk starts"]
        result = _apply_overlap(chunks, overlap_tokens=2)
        assert len(result) == 2
        assert result[0] == "first chunk of text here"
        # Second chunk should start with overlap from first
        assert "text here" in result[1]
        assert "second chunk starts" in result[1]


# ---------------------------------------------------------------------------
# chunk_document (integration)
# ---------------------------------------------------------------------------


class TestChunkDocument:
    def test_empty_text(self):
        assert chunk_document("", "doc1") == []
        assert chunk_document("   ", "doc1") == []

    def test_short_document(self):
        # Must exceed min_tokens (default 30)
        text = "# My Page\n\n" + " ".join(["knowledge"] * 40)
        chunks = chunk_document(text, "my-page")
        assert len(chunks) >= 1
        assert chunks[0].doc_id == "my-page"
        assert chunks[0].index == 0
        assert chunks[0].content_hash  # non-empty

    def test_chunk_id_format(self):
        text = "# Title\n\n" + " ".join(["content"] * 40)
        chunks = chunk_document(text, "test-doc")
        assert chunks[0].chunk_id == "test-doc::0"

    def test_multi_section_document(self):
        sections = []
        for i in range(5):
            body = " ".join([f"word{i}"] * 100)
            sections.append(f"## Section {i}\n\n{body}")
        text = "\n\n".join(sections)

        config = ChunkingConfig(max_tokens=120, overlap_tokens=10, min_tokens=5)
        chunks = chunk_document(text, "multi", config)
        assert len(chunks) >= 5  # at least one chunk per section

        # All chunks should have the correct doc_id
        for chunk in chunks:
            assert chunk.doc_id == "multi"

        # Indices should be sequential
        indices = [c.index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_heading_preserved(self):
        text = "## Introduction\n\n" + " ".join(["topic"] * 40)
        chunks = chunk_document(text, "doc1")
        assert any(c.heading == "Introduction" for c in chunks)

    def test_min_tokens_filter(self):
        text = "## A\n\nTiny.\n\n## B\n\n" + " ".join(["substantial"] * 50)
        config = ChunkingConfig(max_tokens=512, overlap_tokens=10, min_tokens=30)
        chunks = chunk_document(text, "doc1", config)
        for chunk in chunks:
            assert chunk.token_count >= 30

    def test_content_hash_deterministic(self):
        text = "## Test\n\n" + " ".join(["same"] * 40)
        c1 = chunk_document(text, "doc1")
        c2 = chunk_document(text, "doc1")
        assert c1[0].content_hash == c2[0].content_hash

    def test_content_hash_changes_with_content(self):
        c1 = chunk_document("## A\n\n" + " ".join(["alpha"] * 40), "doc1")
        c2 = chunk_document("## A\n\n" + " ".join(["beta"] * 40), "doc1")
        assert c1[0].content_hash != c2[0].content_hash


# ---------------------------------------------------------------------------
# chunk_vault_page
# ---------------------------------------------------------------------------


class TestChunkVaultPage:
    def test_derives_doc_id_from_path(self):
        chunks = chunk_vault_page(
            "/vault/my-wiki-page.md",
            "Some content that is long enough to be chunked.",
        )
        assert all(c.doc_id == "my-wiki-page" for c in chunks)

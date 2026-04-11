"""Tests for tokenizer module — Korean/English bilingual tokenization."""

from __future__ import annotations

import pytest

from llm_wiki.tokenizer import (
    ALL_STOPWORDS,
    ENGLISH_STOPWORDS,
    KOREAN_STOPWORDS,
    normalize_text,
    remove_stopwords,
    split_tokens,
    tokenize,
    tokenize_document,
    tokenize_document_unique,
    tokenize_query,
    tokenize_query_unique,
    tokenize_unique,
    _is_korean,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Hello WORLD") == "hello world"

    def test_strips_markdown(self):
        result = normalize_text("**bold** and [[wikilink]] and `code`")
        assert "**" not in result
        assert "[[" not in result
        assert "`" not in result

    def test_strips_urls(self):
        result = normalize_text("Visit https://example.com for more")
        assert "https" not in result
        assert "example" not in result
        assert "visit" in result

    def test_strips_punctuation(self):
        result = normalize_text("hello, world! how's it?")
        # Punctuation replaced with spaces
        assert "," not in result
        assert "!" not in result

    def test_collapses_whitespace(self):
        result = normalize_text("hello   world  \n  foo")
        assert "  " not in result

    def test_nfkc_normalization(self):
        # Full-width characters → normal
        result = normalize_text("Ｔｅｓｔ")
        assert result == "test"

    def test_korean_preserved(self):
        result = normalize_text("한글 테스트입니다")
        assert "한글" in result
        assert "테스트입니다" in result

    def test_empty_input(self):
        assert normalize_text("") == ""

    def test_mixed_korean_english(self):
        result = normalize_text("Transformer 모델의 구조")
        assert "transformer" in result
        assert "모델의" in result


# ---------------------------------------------------------------------------
# split_tokens
# ---------------------------------------------------------------------------


class TestSplitTokens:
    def test_english_only(self):
        tokens = split_tokens("the transformer architecture")
        assert tokens == ["the", "transformer", "architecture"]

    def test_korean_only(self):
        tokens = split_tokens("한글 토큰 분리")
        assert tokens == ["한글", "토큰", "분리"]

    def test_mixed_language(self):
        """Korean and English should be split into separate tokens."""
        tokens = split_tokens("transformer모델")
        assert "transformer" in tokens
        assert "모델" in tokens

    def test_mixed_sentence(self):
        tokens = split_tokens("gpt 모델은 대규모 언어모델입니다")
        assert "gpt" in tokens
        assert "모델은" in tokens
        assert "대규모" in tokens
        assert "언어모델입니다" in tokens

    def test_discards_punctuation(self):
        tokens = split_tokens("hello, world!")
        assert tokens == ["hello", "world"]

    def test_discards_jamo(self):
        """Individual jamo consonants/vowels should be discarded."""
        tokens = split_tokens("ㄱ ㄴ ㄷ test")
        assert "test" in tokens
        assert "ㄱ" not in tokens

    def test_preserves_numbers(self):
        tokens = split_tokens("gpt4 model v2")
        assert "gpt4" in tokens
        assert "model" in tokens
        assert "v2" in tokens

    def test_empty(self):
        assert split_tokens("") == []
        assert split_tokens("   ") == []

    def test_only_punctuation(self):
        assert split_tokens("...!!!???") == []


# ---------------------------------------------------------------------------
# _is_korean
# ---------------------------------------------------------------------------


class TestIsKorean:
    def test_hangul_syllables(self):
        assert _is_korean("한글") is True
        assert _is_korean("모델") is True
        assert _is_korean("가") is True

    def test_english(self):
        assert _is_korean("hello") is False
        assert _is_korean("test123") is False

    def test_mixed(self):
        # Full match required — mixed is not purely Korean
        assert _is_korean("한글abc") is False

    def test_empty(self):
        assert _is_korean("") is False


# ---------------------------------------------------------------------------
# remove_stopwords
# ---------------------------------------------------------------------------


class TestRemoveStopwords:
    def test_removes_english_stopwords(self):
        tokens = ["the", "transformer", "is", "a", "model"]
        result = remove_stopwords(tokens)
        assert "transformer" in result
        assert "model" in result
        assert "the" not in result
        assert "is" not in result
        assert "a" not in result

    def test_removes_korean_stopwords(self):
        tokens = ["모델", "은", "크다", "의"]
        result = remove_stopwords(tokens)
        assert "모델" in result
        assert "크다" in result
        assert "은" not in result
        assert "의" not in result

    def test_mixed_language_stopwords(self):
        tokens = ["the", "모델", "은", "transformer", "에서"]
        result = remove_stopwords(tokens)
        assert "모델" in result
        assert "transformer" in result
        assert "the" not in result
        assert "은" not in result
        assert "에서" not in result

    def test_english_min_length(self):
        """English tokens shorter than 2 chars are filtered."""
        tokens = ["a", "i", "go", "run"]
        result = remove_stopwords(tokens)
        assert "go" in result
        assert "run" in result

    def test_korean_single_syllable_preserved(self):
        """Korean single syllables (non-stopword) should be kept."""
        tokens = ["집", "길", "책"]
        result = remove_stopwords(tokens)
        assert "집" in result
        assert "길" in result
        assert "책" in result

    def test_custom_stopwords(self):
        tokens = ["hello", "world", "foo"]
        result = remove_stopwords(tokens, stopwords=frozenset({"hello"}))
        assert "world" in result
        assert "foo" in result
        assert "hello" not in result

    def test_empty_input(self):
        assert remove_stopwords([]) == []


# ---------------------------------------------------------------------------
# tokenize_query
# ---------------------------------------------------------------------------


class TestTokenizeQuery:
    def test_english_query(self):
        tokens = tokenize_query("The Transformer architecture")
        assert "transformer" in tokens
        assert "architecture" in tokens
        assert "the" not in tokens

    def test_korean_query(self):
        tokens = tokenize_query("트랜스포머 모델의 구조에 대해서")
        assert "트랜스포머" in tokens
        # Without morphological analysis, particles stay attached to words
        # (e.g., "모델의" is one token). Only standalone particles/fillers
        # are removed as stopwords.
        assert "의" not in tokens  # standalone particle removed
        assert "대해서" not in tokens  # standalone filler removed
        # "모델의" is NOT split — particle attached to noun stays
        assert "모델의" in tokens

    def test_mixed_query(self):
        tokens = tokenize_query("Transformer 모델의 attention mechanism에 대해서")
        assert "transformer" in tokens
        assert "모델" in tokens or "모델의" in tokens
        assert "attention" in tokens
        assert "mechanism" in tokens

    def test_empty_query(self):
        assert tokenize_query("") == []
        assert tokenize_query("   ") == []
        assert tokenize_query(None) == []  # type: ignore

    def test_stopwords_only(self):
        tokens = tokenize_query("the a an is are")
        assert tokens == []

    def test_url_stripped(self):
        tokens = tokenize_query("check https://example.com details")
        assert "example" not in tokens
        assert "check" in tokens
        assert "details" in tokens

    def test_markdown_stripped(self):
        tokens = tokenize_query("**bold** [[link]] `code`")
        assert "bold" in tokens
        assert "link" in tokens
        assert "code" in tokens

    def test_preserves_technical_terms(self):
        tokens = tokenize_query("BM25 scoring algorithm")
        assert "bm25" in tokens
        assert "scoring" in tokens
        assert "algorithm" in tokens

    def test_korean_particles_removed(self):
        tokens = tokenize_query("모델은 학습을 통해")
        assert "은" not in tokens
        assert "을" not in tokens
        assert "통해" not in tokens


class TestTokenizeQueryUnique:
    def test_deduplicates(self):
        result = tokenize_query_unique("model model model test")
        assert result == {"model", "test"}


# ---------------------------------------------------------------------------
# tokenize_document
# ---------------------------------------------------------------------------


class TestTokenizeDocument:
    def test_basic_document(self):
        tokens = tokenize_document("The transformer architecture uses self-attention.")
        assert "transformer" in tokens
        assert "architecture" in tokens
        assert "self" in tokens
        assert "attention" in tokens

    def test_korean_document(self):
        tokens = tokenize_document("트랜스포머는 자연어 처리에서 사용되는 모델입니다")
        assert "트랜스포머는" in tokens or "트랜스포머" in tokens
        assert "자연어" in tokens
        assert "모델입니다" in tokens

    def test_stopword_removal(self):
        tokens = tokenize_document("The model is very powerful")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "model" in tokens
        assert "powerful" in tokens

    def test_no_stopword_removal(self):
        tokens = tokenize_document("The model is powerful", remove_stops=False)
        assert "the" in tokens
        assert "is" in tokens

    def test_empty(self):
        assert tokenize_document("") == []
        assert tokenize_document("   ") == []

    def test_preserves_duplicates_for_tf(self):
        """Document tokenizer keeps duplicates for term frequency counting."""
        tokens = tokenize_document("attention attention attention model")
        assert tokens.count("attention") == 3


class TestTokenizeDocumentUnique:
    def test_deduplicates(self):
        result = tokenize_document_unique("attention attention model")
        assert "attention" in result
        assert "model" in result
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Backward-compatible tokenize / tokenize_unique
# ---------------------------------------------------------------------------


class TestBackwardCompatibleTokenize:
    def test_basic(self):
        tokens = tokenize("The Transformer architecture")
        assert "the" in tokens
        assert "transformer" in tokens
        assert "architecture" in tokens

    def test_strips_markdown(self):
        tokens = tokenize("**bold** and [[wikilink]] and `code`")
        assert "bold" in tokens
        assert "wikilink" in tokens
        assert "code" in tokens

    def test_strips_urls(self):
        tokens = tokenize("Visit https://example.com for more info")
        assert "visit" in tokens
        assert "more" in tokens
        assert "info" in tokens

    def test_filters_short_english(self):
        """English tokens shorter than 2 are filtered."""
        tokens = tokenize("I am a big cat")
        assert "am" in tokens
        assert "big" in tokens
        assert "cat" in tokens

    def test_keeps_korean_syllables(self):
        """Korean single syllables should be preserved."""
        tokens = tokenize("집에 가다")
        assert "집" in tokens or "집에" in tokens

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_unicode_korean(self):
        tokens = tokenize("Transformer 모델은 자연어 처리에 사용됩니다")
        assert "transformer" in tokens
        # Korean tokens are present
        korean_tokens = [t for t in tokens if _is_korean(t)]
        assert len(korean_tokens) > 0

    def test_no_stopword_removal(self):
        """Backward-compatible tokenize does NOT remove stopwords."""
        tokens = tokenize("the model is powerful")
        assert "the" in tokens
        assert "is" in tokens


class TestBackwardCompatibleTokenizeUnique:
    def test_deduplicates(self):
        unique = tokenize_unique("the cat sat on the cat")
        assert "cat" in unique
        assert "the" in unique
        assert isinstance(unique, set)


# ---------------------------------------------------------------------------
# Stopword lists coverage
# ---------------------------------------------------------------------------


class TestStopwordLists:
    def test_english_stopwords_not_empty(self):
        assert len(ENGLISH_STOPWORDS) > 50

    def test_korean_stopwords_not_empty(self):
        assert len(KOREAN_STOPWORDS) > 20

    def test_all_stopwords_is_union(self):
        assert ALL_STOPWORDS == ENGLISH_STOPWORDS | KOREAN_STOPWORDS

    def test_common_english_particles(self):
        for word in ["the", "a", "an", "is", "are", "in", "on", "at"]:
            assert word in ENGLISH_STOPWORDS, f"'{word}' should be a stopword"

    def test_common_korean_particles(self):
        for word in ["은", "는", "이", "가", "을", "를", "의", "에", "에서"]:
            assert word in KOREAN_STOPWORDS, f"'{word}' should be a stopword"

    def test_no_meaningful_korean_words(self):
        """Important Korean nouns should NOT be in stopwords."""
        for word in ["모델", "학습", "데이터", "분석", "기술"]:
            assert word not in KOREAN_STOPWORDS, f"'{word}' should NOT be a stopword"


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_only_punctuation(self):
        assert tokenize_query("...!!!???") == []
        assert tokenize_document("---") == []

    def test_only_numbers(self):
        tokens = tokenize_query("123 456")
        assert "123" in tokens
        assert "456" in tokens

    def test_mixed_with_numbers(self):
        tokens = tokenize_query("GPT4 모델 v2")
        assert "gpt4" in tokens
        assert "모델" in tokens
        assert "v2" in tokens

    def test_full_width_characters(self):
        """Full-width Latin should be normalized to ASCII."""
        tokens = tokenize_query("Ｔｒａｎｓｆｏｒｍｅｒ")
        assert "transformer" in tokens

    def test_repeated_whitespace(self):
        tokens = tokenize_query("hello     world")
        assert tokens == ["hello", "world"]

    def test_newlines_and_tabs(self):
        tokens = tokenize_query("hello\nworld\ttab")
        assert "hello" in tokens
        assert "world" in tokens
        assert "tab" in tokens

    def test_long_korean_sentence(self):
        text = "대규모 언어 모델은 자연어 처리 분야에서 혁신적인 성과를 보여주고 있습니다"
        tokens = tokenize_query(text)
        assert "대규모" in tokens
        assert "언어" in tokens
        # Stopwords removed
        assert "은" not in tokens
        assert "에서" not in tokens

    def test_hyphenated_english(self):
        """Hyphens are treated as punctuation → splits the word."""
        tokens = tokenize_query("self-attention mechanism")
        assert "self" in tokens
        assert "attention" in tokens
        assert "mechanism" in tokens

    def test_apostrophe_handling(self):
        tokens = tokenize_query("it's a model's output")
        # Apostrophe stripped, "s" may appear or be filtered
        assert "model" in tokens or "models" in tokens
        assert "output" in tokens

    def test_camelcase_not_split(self):
        """CamelCase is not split — treated as one token."""
        tokens = tokenize_query("TensorFlow PyTorch")
        assert "tensorflow" in tokens
        assert "pytorch" in tokens

    def test_query_preserves_order(self):
        tokens = tokenize_query("alpha beta gamma")
        assert tokens == ["alpha", "beta", "gamma"]

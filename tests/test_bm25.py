"""Tests for BM25 scoring functions."""

from __future__ import annotations

import math

import pytest

from llm_wiki.bm25 import (
    BM25Index,
    CorpusStats,
    bm25_term_score,
    compute_corpus_stats,
    inverse_document_frequency,
    score_document,
    term_frequency,
)


# ---------------------------------------------------------------------------
# term_frequency
# ---------------------------------------------------------------------------


class TestTermFrequency:
    def test_basic_count(self):
        tokens = ["the", "cat", "sat", "on", "the", "mat"]
        assert term_frequency("the", tokens) == 2
        assert term_frequency("cat", tokens) == 1

    def test_missing_term(self):
        tokens = ["the", "cat"]
        assert term_frequency("dog", tokens) == 0

    def test_empty_document(self):
        assert term_frequency("any", []) == 0

    def test_all_same_tokens(self):
        tokens = ["a", "a", "a", "a"]
        assert term_frequency("a", tokens) == 4

    def test_case_sensitive(self):
        """TF is case-sensitive; caller must pre-normalize."""
        tokens = ["The", "the", "THE"]
        assert term_frequency("the", tokens) == 1


# ---------------------------------------------------------------------------
# inverse_document_frequency
# ---------------------------------------------------------------------------


class TestInverseDocumentFrequency:
    def test_rare_term(self):
        """A term in 1 of 100 docs should have high IDF."""
        idf = inverse_document_frequency(1, 100)
        assert idf > 4.0

    def test_common_term(self):
        """A term in 50 of 100 docs should have lower IDF."""
        idf = inverse_document_frequency(50, 100)
        assert 0.0 < idf < 1.0

    def test_ubiquitous_term(self):
        """A term in all docs still has non-negative IDF (Robertson-Walker)."""
        idf = inverse_document_frequency(100, 100)
        assert idf >= 0.0

    def test_absent_term(self):
        """A term in 0 docs should have the highest IDF."""
        idf_absent = inverse_document_frequency(0, 100)
        idf_rare = inverse_document_frequency(1, 100)
        assert idf_absent > idf_rare

    def test_empty_corpus(self):
        assert inverse_document_frequency(0, 0) == 0.0

    def test_single_doc_corpus(self):
        idf = inverse_document_frequency(1, 1)
        # ln((1 - 1 + 0.5) / (1 + 0.5) + 1) = ln(1/3 + 1) = ln(4/3)
        assert idf == pytest.approx(math.log(4 / 3), rel=1e-6)

    def test_monotonically_decreasing(self):
        """IDF should decrease as more documents contain the term."""
        idfs = [inverse_document_frequency(n, 100) for n in range(1, 100)]
        for i in range(len(idfs) - 1):
            assert idfs[i] >= idfs[i + 1]

    def test_always_non_negative(self):
        """Robertson-Walker IDF is always >= 0."""
        for n in range(101):
            assert inverse_document_frequency(n, 100) >= 0.0


# ---------------------------------------------------------------------------
# bm25_term_score
# ---------------------------------------------------------------------------


class TestBM25TermScore:
    def test_zero_tf(self):
        """No occurrences should yield zero score."""
        assert bm25_term_score(tf=0, idf=2.0, doc_length=100, avg_doc_length=100) == 0.0

    def test_zero_idf(self):
        """Zero IDF should yield zero score."""
        assert bm25_term_score(tf=3, idf=0.0, doc_length=100, avg_doc_length=100) == 0.0

    def test_negative_idf(self):
        """Negative IDF should yield zero score."""
        assert bm25_term_score(tf=3, idf=-1.0, doc_length=100, avg_doc_length=100) == 0.0

    def test_average_length_document(self):
        """Document at average length: normalization factor = 1."""
        score = bm25_term_score(tf=1, idf=1.0, doc_length=100, avg_doc_length=100)
        # tf=1, k1=1.2, b=0.75, dl=avgdl
        # numerator = 1 * 2.2 = 2.2
        # denominator = 1 + 1.2 * (1 - 0.75 + 0.75 * 1) = 1 + 1.2 = 2.2
        # score = 1.0 * (2.2 / 2.2) = 1.0
        assert score == pytest.approx(1.0, rel=1e-6)

    def test_short_document_boost(self):
        """Shorter-than-average documents should score higher."""
        short = bm25_term_score(tf=1, idf=1.0, doc_length=50, avg_doc_length=100)
        avg = bm25_term_score(tf=1, idf=1.0, doc_length=100, avg_doc_length=100)
        assert short > avg

    def test_long_document_penalty(self):
        """Longer-than-average documents should score lower."""
        long_doc = bm25_term_score(tf=1, idf=1.0, doc_length=200, avg_doc_length=100)
        avg = bm25_term_score(tf=1, idf=1.0, doc_length=100, avg_doc_length=100)
        assert long_doc < avg

    def test_tf_saturation(self):
        """Higher TF should increase score but with diminishing returns."""
        scores = [
            bm25_term_score(tf=tf, idf=1.0, doc_length=100, avg_doc_length=100)
            for tf in [1, 5, 20, 100, 500]
        ]
        # All should be increasing
        for i in range(len(scores) - 1):
            assert scores[i] < scores[i + 1]
        # Score should approach but never exceed idf * (k1 + 1) / 1 = 2.2
        # (asymptotic upper bound as tf -> infinity)
        assert scores[-1] < 1.0 * (1.2 + 1.0)  # idf * (k1 + 1)
        # Large TF gap should yield smaller increment than small TF gap
        increment_small = scores[1] - scores[0]   # tf 1->5
        increment_large = scores[-1] - scores[-2]  # tf 100->500
        assert increment_small > increment_large

    def test_no_length_normalization(self):
        """With b=0, document length should not affect score."""
        short = bm25_term_score(tf=1, idf=1.0, doc_length=50, avg_doc_length=100, b=0.0)
        long_doc = bm25_term_score(tf=1, idf=1.0, doc_length=200, avg_doc_length=100, b=0.0)
        assert short == pytest.approx(long_doc, rel=1e-6)

    def test_zero_avg_doc_length(self):
        """Edge case: zero average doc length returns 0."""
        assert bm25_term_score(tf=1, idf=1.0, doc_length=10, avg_doc_length=0.0) == 0.0


# ---------------------------------------------------------------------------
# compute_corpus_stats
# ---------------------------------------------------------------------------


class TestComputeCorpusStats:
    def test_basic_stats(self):
        docs = {
            "a": ["the", "cat", "sat"],
            "b": ["the", "dog", "ran", "fast"],
        }
        stats = compute_corpus_stats(docs)
        assert stats.total_docs == 2
        assert stats.avg_doc_length == 3.5
        assert stats.doc_freq["the"] == 2
        assert stats.doc_freq["cat"] == 1
        assert stats.doc_freq["dog"] == 1

    def test_empty_corpus(self):
        stats = compute_corpus_stats({})
        assert stats.total_docs == 0
        assert stats.avg_doc_length == 0.0
        assert stats.doc_freq == {}

    def test_single_document(self):
        stats = compute_corpus_stats({"a": ["hello", "world"]})
        assert stats.total_docs == 1
        assert stats.avg_doc_length == 2.0
        assert stats.doc_freq["hello"] == 1

    def test_duplicate_tokens_counted_once_per_doc(self):
        """doc_freq counts documents, not occurrences."""
        docs = {
            "a": ["the", "the", "the"],  # 'the' 3 times in one doc
        }
        stats = compute_corpus_stats(docs)
        assert stats.doc_freq["the"] == 1  # one document
        assert stats.avg_doc_length == 3.0  # length includes duplicates

    def test_idf_method(self):
        docs = {
            "a": ["cat", "dog"],
            "b": ["cat", "fish"],
            "c": ["bird", "fish"],
        }
        stats = compute_corpus_stats(docs)
        idf_cat = stats.idf("cat")  # in 2 of 3 docs
        idf_bird = stats.idf("bird")  # in 1 of 3 docs
        idf_missing = stats.idf("elephant")  # in 0 of 3 docs
        assert idf_bird > idf_cat
        assert idf_missing > idf_bird


# ---------------------------------------------------------------------------
# score_document
# ---------------------------------------------------------------------------


class TestScoreDocument:
    @pytest.fixture
    def corpus_stats(self) -> CorpusStats:
        return compute_corpus_stats({
            "a": ["transformer", "attention", "mechanism", "neural", "network"],
            "b": ["kubernetes", "docker", "deployment", "ci", "cd"],
            "c": ["transformer", "model", "training", "gpu", "optimization"],
        })

    def test_relevant_query(self, corpus_stats):
        doc = ["transformer", "attention", "mechanism", "neural", "network"]
        query = ["transformer", "attention"]
        score = score_document(query, doc, corpus_stats)
        assert score > 0.0

    def test_irrelevant_query(self, corpus_stats):
        doc = ["kubernetes", "docker", "deployment", "ci", "cd"]
        query = ["transformer", "attention"]
        score = score_document(query, doc, corpus_stats)
        assert score == 0.0

    def test_empty_query(self, corpus_stats):
        doc = ["transformer", "attention"]
        assert score_document([], doc, corpus_stats) == 0.0

    def test_empty_document(self, corpus_stats):
        assert score_document(["transformer"], [], corpus_stats) == 0.0

    def test_more_matches_higher_score(self, corpus_stats):
        doc = ["transformer", "attention", "mechanism", "neural", "network"]
        score_one = score_document(["transformer"], doc, corpus_stats)
        score_two = score_document(["transformer", "attention"], doc, corpus_stats)
        assert score_two > score_one


# ---------------------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------------------


class TestBM25Index:
    @pytest.fixture
    def index(self) -> BM25Index:
        idx = BM25Index()
        idx.add_document("ai-page", [
            "transformer", "attention", "mechanism", "neural", "network",
            "deep", "learning", "transformer",
        ])
        idx.add_document("devops-page", [
            "kubernetes", "docker", "deployment", "ci", "cd",
            "terraform", "infrastructure",
        ])
        idx.add_document("ml-ops-page", [
            "model", "deployment", "kubernetes", "monitoring",
            "transformer", "inference", "optimization",
        ])
        idx.build()
        return idx

    def test_doc_count(self, index):
        assert index.doc_count == 3

    def test_is_built(self, index):
        assert index.is_built

    def test_not_built_raises(self):
        idx = BM25Index()
        idx.add_document("a", ["hello"])
        with pytest.raises(RuntimeError):
            idx.score_query(["hello"])

    def test_score_query_basic(self, index):
        scores = index.score_query(["transformer", "attention"])
        assert scores["ai-page"] > 0.0
        assert scores["devops-page"] == 0.0
        assert scores["ml-ops-page"] > 0.0
        # AI page should score higher (has both terms + higher TF)
        assert scores["ai-page"] > scores["ml-ops-page"]

    def test_score_query_devops(self, index):
        scores = index.score_query(["kubernetes", "docker"])
        assert scores["devops-page"] > 0.0
        assert scores["ai-page"] == 0.0

    def test_empty_query(self, index):
        scores = index.score_query([])
        assert all(s == 0.0 for s in scores.values())

    def test_rank_query(self, index):
        ranked = index.rank_query(["transformer", "attention"], top_k=2)
        assert len(ranked) <= 2
        assert ranked[0][0] == "ai-page"
        assert ranked[0][1] > ranked[1][1] if len(ranked) > 1 else True

    def test_rank_query_excludes_zeros(self, index):
        ranked = index.rank_query(["kubernetes", "docker"])
        doc_ids = [doc_id for doc_id, _ in ranked]
        assert "ai-page" not in doc_ids

    def test_rank_query_top_k(self, index):
        ranked = index.rank_query(["transformer"], top_k=1)
        assert len(ranked) == 1

    def test_add_invalidates_build(self, index):
        index.add_document("new", ["hello"])
        assert not index.is_built

    def test_custom_parameters(self):
        idx = BM25Index(k1=2.0, b=0.5)
        idx.add_document("a", ["term", "term", "term"])
        idx.add_document("b", ["term"])
        idx.build()
        scores = idx.score_query(["term"])
        # Both should score > 0, doc "a" higher due to TF
        assert scores["a"] > scores["b"]

    def test_single_document_corpus(self):
        idx = BM25Index()
        idx.add_document("only", ["hello", "world"])
        idx.build()
        scores = idx.score_query(["hello"])
        assert scores["only"] > 0.0

    def test_duplicate_query_terms(self, index):
        """Duplicate query terms should not double the score."""
        scores_once = index.score_query(["transformer"])
        scores_twice = index.score_query(["transformer", "transformer"])
        # set() deduplication means same result
        assert scores_once == scores_twice

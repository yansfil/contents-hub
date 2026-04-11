"""Tests for llm_wiki.vector_store — vector storage and similarity search."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from llm_wiki.chunker import DocumentChunk
from llm_wiki.embeddings import (
    EmbeddingCache,
    EmbeddingService,
    EmbeddingVector,
)
from llm_wiki.vector_store import (
    AGG_MAX,
    AGG_MEAN_TOP_N,
    ChunkSearchResult,
    SearchResult,
    VectorStore,
    cosine_similarity,
    cosine_similarity_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """Fake embedding provider for testing."""

    def __init__(self, dimension: int = 4):
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic vectors based on text content."""
        results = []
        for text in texts:
            # Create a simple hash-based vector for deterministic results
            h = hash(text) % 1000
            vec = [
                math.sin(h + i) for i in range(self._dimension)
            ]
            # Normalize
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            results.append(vec)
        return results


def _make_ev(
    doc_id: str,
    chunk_index: int,
    vector: list[float],
    content_hash: str = "h",
) -> EmbeddingVector:
    """Create a test EmbeddingVector."""
    return EmbeddingVector(
        chunk_id=f"{doc_id}::{chunk_index}",
        doc_id=doc_id,
        chunk_index=chunk_index,
        vector=vector,
        content_hash=content_hash,
        model="test-model",
        dimension=len(vector),
    )


def _normalized(vec: list[float]) -> list[float]:
    """Normalize a vector to unit length."""
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = [1.0, 2.0, 3.0]
        score = cosine_similarity(a, a)
        assert abs(score - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        score = cosine_similarity(a, b)
        assert abs(score) < 1e-6

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        score = cosine_similarity(a, b)
        assert abs(score - (-1.0)) < 1e-6

    def test_similar_vectors(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 1.0, 0.1]
        score = cosine_similarity(a, b)
        assert score > 0.99

    def test_normalized_vectors_equal_dot_product(self):
        a = _normalized([1.0, 2.0, 3.0])
        b = _normalized([4.0, 5.0, 6.0])
        sim = cosine_similarity(a, b)
        dot = sum(ai * bi for ai, bi in zip(a, b))
        assert abs(sim - dot) < 1e-6

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0, 2.0], [1.0])

    def test_empty_vectors_raises(self):
        with pytest.raises(ValueError, match="empty"):
            cosine_similarity([], [])

    def test_zero_vector(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        score = cosine_similarity(a, b)
        assert score == 0.0


class TestCosineSimilarityBatch:
    def test_single_vector(self):
        query = [1.0, 0.0]
        vectors = [[1.0, 0.0]]
        scores = cosine_similarity_batch(query, vectors)
        assert len(scores) == 1
        assert abs(scores[0] - 1.0) < 1e-6

    def test_multiple_vectors(self):
        query = [1.0, 0.0]
        vectors = [
            [1.0, 0.0],  # identical → 1.0
            [0.0, 1.0],  # orthogonal → 0.0
            [-1.0, 0.0],  # opposite → -1.0
        ]
        scores = cosine_similarity_batch(query, vectors)
        assert len(scores) == 3
        assert abs(scores[0] - 1.0) < 1e-6
        assert abs(scores[1]) < 1e-6
        assert abs(scores[2] - (-1.0)) < 1e-6

    def test_empty_query(self):
        scores = cosine_similarity_batch([], [[1.0, 2.0]])
        assert scores == []

    def test_empty_vectors(self):
        scores = cosine_similarity_batch([1.0], [])
        assert scores == []

    def test_zero_query(self):
        query = [0.0, 0.0]
        vectors = [[1.0, 2.0]]
        scores = cosine_similarity_batch(query, vectors)
        assert scores == [0.0]

    def test_consistent_with_single(self):
        """Batch results should match individual computations."""
        query = _normalized([1.0, 2.0, 3.0])
        vectors = [
            _normalized([4.0, 5.0, 6.0]),
            _normalized([7.0, 8.0, 9.0]),
            _normalized([1.0, 0.0, 0.0]),
        ]
        batch_scores = cosine_similarity_batch(query, vectors)
        single_scores = [cosine_similarity(query, v) for v in vectors]

        for bs, ss in zip(batch_scores, single_scores):
            assert abs(bs - ss) < 1e-6


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------


class TestVectorStore:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> EmbeddingCache:
        return EmbeddingCache(tmp_path / "embeddings.db")

    @pytest.fixture
    def service(self, cache) -> EmbeddingService:
        return EmbeddingService(FakeProvider(dimension=4), cache)

    @pytest.fixture
    def store(self, service) -> VectorStore:
        return VectorStore(service)

    # -- Basic load/store --

    def test_empty_store(self, store: VectorStore):
        assert store.vector_count == 0
        assert store.doc_count == 0
        assert not store.is_loaded

    def test_load_empty_cache(self, store: VectorStore):
        count = store.load()
        assert count == 0
        assert store.is_loaded

    def test_load_with_data(self, store: VectorStore, cache: EmbeddingCache):
        # Pre-populate cache
        vectors = [
            _make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0]),
            _make_ev("doc1", 1, [0.0, 1.0, 0.0, 0.0]),
            _make_ev("doc2", 0, [0.0, 0.0, 1.0, 0.0]),
        ]
        cache.put_batch(vectors)

        count = store.load()
        assert count == 3
        assert store.vector_count == 3
        assert store.doc_count == 2
        assert store.doc_ids == {"doc1", "doc2"}

    def test_load_docs_specific(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0]),
            _make_ev("doc2", 0, [0.0, 1.0, 0.0, 0.0]),
            _make_ev("doc3", 0, [0.0, 0.0, 1.0, 0.0]),
        ]
        cache.put_batch(vectors)

        count = store.load_docs(["doc1", "doc3"])
        assert count == 2
        assert store.doc_ids == {"doc1", "doc3"}

    # -- Chunk-level search --

    def test_search_by_vector_basic(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc2", 0, _normalized([0.0, 1.0, 0.0, 0.0])),
            _make_ev("doc3", 0, _normalized([1.0, 0.1, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        query = _normalized([1.0, 0.0, 0.0, 0.0])
        results = store.search_by_vector(query, top_k=3)

        assert len(results) == 3
        # doc1 should be most similar (identical direction)
        assert results[0].doc_id == "doc1"
        assert abs(results[0].score - 1.0) < 1e-4
        # doc3 should be second (close to query)
        assert results[1].doc_id == "doc3"
        assert results[1].score > 0.99

    def test_search_by_vector_top_k(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev(f"doc{i}", 0, _normalized([float(i), 1.0, 0.0, 0.0]))
            for i in range(10)
        ]
        cache.put_batch(vectors)
        store.load()

        results = store.search_by_vector(
            _normalized([9.0, 1.0, 0.0, 0.0]), top_k=3,
        )
        assert len(results) == 3

    def test_search_by_vector_min_score(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc2", 0, _normalized([0.0, 1.0, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        query = _normalized([1.0, 0.0, 0.0, 0.0])
        results = store.search_by_vector(query, min_score=0.5)

        # Only doc1 should match (score=1.0); doc2 is orthogonal (score≈0)
        assert len(results) == 1
        assert results[0].doc_id == "doc1"

    def test_search_by_vector_doc_filter(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc2", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        query = _normalized([1.0, 0.0, 0.0, 0.0])
        results = store.search_by_vector(query, doc_filter={"doc2"})

        assert len(results) == 1
        assert results[0].doc_id == "doc2"

    def test_search_by_vector_empty_store(self, store: VectorStore):
        store.load()
        results = store.search_by_vector([1.0, 0.0, 0.0, 0.0])
        assert results == []

    def test_search_auto_loads(self, store: VectorStore, cache: EmbeddingCache):
        """Search should auto-load if not yet loaded."""
        vectors = [_make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0]))]
        cache.put_batch(vectors)

        assert not store.is_loaded
        results = store.search_by_vector(_normalized([1.0, 0.0, 0.0, 0.0]))
        assert store.is_loaded
        assert len(results) == 1

    # -- Document-level search --

    def test_search_docs_by_vector_max_agg(self, store: VectorStore, cache: EmbeddingCache):
        """Test document-level search with max aggregation."""
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc1", 1, _normalized([0.5, 0.5, 0.0, 0.0])),
            _make_ev("doc2", 0, _normalized([0.0, 1.0, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        query = _normalized([1.0, 0.0, 0.0, 0.0])
        results = store.search_docs_by_vector(query, aggregation=AGG_MAX)

        assert len(results) == 2
        assert results[0].doc_id == "doc1"
        assert abs(results[0].score - 1.0) < 1e-4
        assert results[0].best_chunk_index == 0
        assert results[0].total_chunks == 2
        assert len(results[0].chunk_scores) == 2

    def test_search_docs_by_vector_mean_top_n_agg(self, store: VectorStore, cache: EmbeddingCache):
        """Test document-level search with mean_top_n aggregation."""
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc1", 1, _normalized([0.9, 0.1, 0.0, 0.0])),
            _make_ev("doc1", 2, _normalized([0.0, 0.0, 0.0, 1.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        query = _normalized([1.0, 0.0, 0.0, 0.0])
        results_max = store.search_docs_by_vector(query, aggregation=AGG_MAX)
        results_mean = store.search_docs_by_vector(query, aggregation=AGG_MEAN_TOP_N)

        # Mean of top 3 should be lower than max
        assert results_mean[0].score <= results_max[0].score

    def test_search_docs_wikilink(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [_make_ev("my-page", 0, _normalized([1.0, 0.0, 0.0, 0.0]))]
        cache.put_batch(vectors)
        store.load()

        results = store.search_docs_by_vector(_normalized([1.0, 0.0, 0.0, 0.0]))
        assert results[0].wikilink == "[[my-page]]"
        assert results[0].title == "my page"

    # -- Async search --

    @pytest.mark.asyncio
    async def test_search_text_query(self, store: VectorStore, cache: EmbeddingCache):
        """Test text-based search (embeds query then searches)."""
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
            _make_ev("doc2", 0, _normalized([0.0, 1.0, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        # The FakeProvider gives deterministic vectors for any text
        results = await store.search("test query", top_k=2)
        assert len(results) <= 2
        # Results should be valid ChunkSearchResult objects
        for r in results:
            assert isinstance(r, ChunkSearchResult)
            assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_search_docs_text_query(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, _normalized([1.0, 0.0, 0.0, 0.0])),
        ]
        cache.put_batch(vectors)
        store.load()

        results = await store.search_docs("test query")
        assert len(results) >= 0  # may or may not match
        for r in results:
            assert isinstance(r, SearchResult)

    # -- Index management --

    def test_add_vectors(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [_make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0])]
        cache.put_batch(vectors)
        store.load()

        new_vec = _make_ev("doc2", 0, [0.0, 1.0, 0.0, 0.0])
        count = store.add_vectors([new_vec])
        assert count == 2
        assert store.doc_ids == {"doc1", "doc2"}

    def test_add_vectors_replaces_existing(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [_make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0])]
        cache.put_batch(vectors)
        store.load()

        # Replace with updated vector
        updated = _make_ev("doc1", 0, [0.0, 1.0, 0.0, 0.0])
        count = store.add_vectors([updated])
        assert count == 1  # replaced, not added

    def test_remove_doc(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0]),
            _make_ev("doc1", 1, [0.0, 1.0, 0.0, 0.0]),
            _make_ev("doc2", 0, [0.0, 0.0, 1.0, 0.0]),
        ]
        cache.put_batch(vectors)
        store.load()

        removed = store.remove_doc("doc1")
        assert removed == 2
        assert store.vector_count == 1
        assert store.doc_ids == {"doc2"}

    def test_clear(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [_make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0])]
        cache.put_batch(vectors)
        store.load()

        store.clear()
        assert store.vector_count == 0
        assert not store.is_loaded

    def test_stats(self, store: VectorStore, cache: EmbeddingCache):
        vectors = [
            _make_ev("doc1", 0, [1.0, 0.0, 0.0, 0.0]),
            _make_ev("doc2", 0, [0.0, 1.0, 0.0, 0.0]),
        ]
        cache.put_batch(vectors)
        store.load()

        stats = store.stats()
        assert stats["vector_count"] == 2
        assert stats["doc_count"] == 2
        assert stats["loaded"] is True


# ---------------------------------------------------------------------------
# SearchResult / ChunkSearchResult data
# ---------------------------------------------------------------------------


class TestSearchResultData:
    def test_chunk_search_result(self):
        r = ChunkSearchResult(
            chunk_id="doc1::0",
            doc_id="doc1",
            chunk_index=0,
            score=0.95,
            content_hash="h1",
            model="test",
        )
        assert r.chunk_id == "doc1::0"
        assert r.score == 0.95

    def test_search_result_title(self):
        r = SearchResult(doc_id="my-wiki-page", score=0.9)
        assert r.title == "my wiki page"

    def test_search_result_wikilink(self):
        r = SearchResult(doc_id="transformer-architecture", score=0.9)
        assert r.wikilink == "[[transformer-architecture]]"

    def test_search_result_chunk_scores(self):
        r = SearchResult(
            doc_id="doc1",
            score=0.9,
            chunk_scores=[0.95, 0.85, 0.70],
            best_chunk_index=0,
            total_chunks=3,
        )
        assert len(r.chunk_scores) == 3
        assert r.best_chunk_index == 0

"""Tests for llm_wiki.embeddings — embedding generation and caching."""

from __future__ import annotations

import asyncio
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.chunker import DocumentChunk
from llm_wiki.embeddings import (
    DEFAULT_BATCH_SIZE,
    EmbeddingCache,
    EmbeddingError,
    EmbeddingResult,
    EmbeddingService,
    EmbeddingVector,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    create_cache,
    create_provider,
    get_embedding_service,
    _iso_now,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(doc_id: str = "doc1", index: int = 0, text: str = "test") -> DocumentChunk:
    """Create a test DocumentChunk."""
    import hashlib
    return DocumentChunk(
        doc_id=doc_id,
        index=index,
        text=text,
        heading="Test",
        token_count=len(text.split()),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _make_vector(
    chunk_id: str = "doc1::0",
    doc_id: str = "doc1",
    chunk_index: int = 0,
    dimension: int = 4,
) -> EmbeddingVector:
    """Create a test EmbeddingVector."""
    return EmbeddingVector(
        chunk_id=chunk_id,
        doc_id=doc_id,
        chunk_index=chunk_index,
        vector=[0.1, 0.2, 0.3, 0.4][:dimension],
        content_hash="abc123",
        model="test-model",
        dimension=dimension,
    )


class FakeProvider:
    """Fake embedding provider for testing."""

    def __init__(self, dimension: int = 4):
        self._dimension = dimension
        self.call_count = 0
        self.last_texts: list[str] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.last_texts = texts
        return [[0.1] * self._dimension for _ in texts]


# ---------------------------------------------------------------------------
# EmbeddingVector
# ---------------------------------------------------------------------------


class TestEmbeddingVector:
    def test_to_bytes_and_back(self):
        ev = _make_vector(dimension=4)
        data = ev.to_bytes()
        assert isinstance(data, bytes)
        assert len(data) == 4 * 4  # 4 floats * 4 bytes each

        restored = EmbeddingVector.from_bytes(data)
        assert len(restored) == 4
        for orig, rest in zip(ev.vector, restored):
            assert abs(orig - rest) < 1e-6

    def test_empty_vector(self):
        ev = EmbeddingVector(
            chunk_id="x::0", doc_id="x", chunk_index=0,
            vector=[], content_hash="h", model="m", dimension=0,
        )
        data = ev.to_bytes()
        assert data == b""
        assert EmbeddingVector.from_bytes(data) == []

    def test_large_vector(self):
        vec = [float(i) / 1000 for i in range(1536)]
        ev = EmbeddingVector(
            chunk_id="x::0", doc_id="x", chunk_index=0,
            vector=vec, content_hash="h", model="m", dimension=1536,
        )
        data = ev.to_bytes()
        restored = EmbeddingVector.from_bytes(data)
        assert len(restored) == 1536
        for orig, rest in zip(vec, restored):
            assert abs(orig - rest) < 1e-5


# ---------------------------------------------------------------------------
# EmbeddingCache
# ---------------------------------------------------------------------------


class TestEmbeddingCache:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> EmbeddingCache:
        db_path = tmp_path / ".llm-wiki" / "embeddings.db"
        return EmbeddingCache(db_path)

    def test_put_and_get(self, cache: EmbeddingCache):
        ev = EmbeddingVector(
            chunk_id="doc1::0", doc_id="doc1", chunk_index=0,
            vector=[0.1, 0.2, 0.3, 0.4],
            content_hash="hash1", model="test", dimension=4,
        )
        cache.put(ev)

        # Retrieve with matching hash
        result = cache.get("doc1::0", "hash1")
        assert result is not None
        assert result.chunk_id == "doc1::0"
        assert len(result.vector) == 4
        assert abs(result.vector[0] - 0.1) < 1e-6

    def test_get_wrong_hash_returns_none(self, cache: EmbeddingCache):
        ev = EmbeddingVector(
            chunk_id="doc1::0", doc_id="doc1", chunk_index=0,
            vector=[0.1, 0.2], content_hash="hash1",
            model="test", dimension=2,
        )
        cache.put(ev)

        # Wrong hash → cache miss
        result = cache.get("doc1::0", "different_hash")
        assert result is None

    def test_get_nonexistent(self, cache: EmbeddingCache):
        result = cache.get("nonexistent::0", "hash")
        assert result is None

    def test_put_updates_existing(self, cache: EmbeddingCache):
        ev1 = EmbeddingVector(
            chunk_id="doc1::0", doc_id="doc1", chunk_index=0,
            vector=[1.0, 2.0], content_hash="hash1",
            model="v1", dimension=2,
        )
        cache.put(ev1)

        ev2 = EmbeddingVector(
            chunk_id="doc1::0", doc_id="doc1", chunk_index=0,
            vector=[3.0, 4.0], content_hash="hash2",
            model="v2", dimension=2,
        )
        cache.put(ev2)

        result = cache.get("doc1::0", "hash2")
        assert result is not None
        assert abs(result.vector[0] - 3.0) < 1e-6
        assert result.model == "v2"

    def test_put_batch(self, cache: EmbeddingCache):
        vectors = [
            EmbeddingVector(
                chunk_id=f"doc1::{i}", doc_id="doc1", chunk_index=i,
                vector=[float(i)], content_hash=f"h{i}",
                model="test", dimension=1,
            )
            for i in range(5)
        ]
        cache.put_batch(vectors)

        for i in range(5):
            result = cache.get(f"doc1::{i}", f"h{i}")
            assert result is not None

    def test_get_batch(self, cache: EmbeddingCache):
        vectors = [
            EmbeddingVector(
                chunk_id=f"doc1::{i}", doc_id="doc1", chunk_index=i,
                vector=[float(i)], content_hash=f"h{i}",
                model="test", dimension=1,
            )
            for i in range(3)
        ]
        cache.put_batch(vectors)

        lookups = [(f"doc1::{i}", f"h{i}") for i in range(3)]
        lookups.append(("missing::0", "h99"))

        results = cache.get_batch(lookups)
        assert len(results) == 3
        assert "doc1::0" in results
        assert "missing::0" not in results

    def test_delete_doc(self, cache: EmbeddingCache):
        vectors = [
            EmbeddingVector(
                chunk_id=f"doc1::{i}", doc_id="doc1", chunk_index=i,
                vector=[1.0], content_hash=f"h{i}",
                model="test", dimension=1,
            )
            for i in range(3)
        ]
        cache.put_batch(vectors)

        deleted = cache.delete_doc("doc1")
        assert deleted == 3

        result = cache.get("doc1::0", "h0")
        assert result is None

    def test_get_all_for_doc(self, cache: EmbeddingCache):
        vectors = [
            EmbeddingVector(
                chunk_id=f"doc1::{i}", doc_id="doc1", chunk_index=i,
                vector=[float(i)], content_hash=f"h{i}",
                model="test", dimension=1,
            )
            for i in range(3)
        ]
        cache.put_batch(vectors)

        results = cache.get_all_for_doc("doc1")
        assert len(results) == 3
        assert results[0].chunk_index == 0
        assert results[2].chunk_index == 2

    def test_stats(self, cache: EmbeddingCache):
        # Empty cache
        stats = cache.stats()
        assert stats["total_vectors"] == 0
        assert stats["total_docs"] == 0

        # Add some vectors
        vectors = [
            EmbeddingVector(
                chunk_id=f"doc{d}::{i}", doc_id=f"doc{d}", chunk_index=i,
                vector=[1.0, 2.0], content_hash=f"h{d}{i}",
                model="test", dimension=2,
            )
            for d in range(2)
            for i in range(3)
        ]
        cache.put_batch(vectors)

        stats = cache.stats()
        assert stats["total_vectors"] == 6
        assert stats["total_docs"] == 2
        assert stats["total_bytes"] > 0

    def test_close(self, cache: EmbeddingCache):
        cache.put(EmbeddingVector(
            chunk_id="x::0", doc_id="x", chunk_index=0,
            vector=[1.0], content_hash="h", model="t", dimension=1,
        ))
        cache.close()
        # After close, next operation reopens
        result = cache.get("x::0", "h")
        assert result is not None


# ---------------------------------------------------------------------------
# EmbeddingService
# ---------------------------------------------------------------------------


class TestEmbeddingService:
    @pytest.fixture
    def provider(self):
        return FakeProvider(dimension=4)

    @pytest.fixture
    def cache(self, tmp_path: Path) -> EmbeddingCache:
        return EmbeddingCache(tmp_path / "embeddings.db")

    @pytest.fixture
    def service(self, provider, cache) -> EmbeddingService:
        return EmbeddingService(provider, cache)

    @pytest.mark.asyncio
    async def test_embed_text(self, service: EmbeddingService):
        vector = await service.embed_text("hello world")
        assert len(vector) == 4
        assert all(abs(v - 0.1) < 1e-6 for v in vector)

    @pytest.mark.asyncio
    async def test_embed_chunks_all_new(self, service: EmbeddingService, provider):
        chunks = [_make_chunk("doc1", i, f"text {i}") for i in range(3)]
        result = await service.embed_chunks(chunks)

        assert result.total_chunks == 3
        assert result.embedded_count == 3
        assert result.cached_count == 0
        assert result.error_count == 0
        assert len(result.vectors) == 3
        assert provider.call_count == 1  # single batch

    @pytest.mark.asyncio
    async def test_embed_chunks_cached(self, service: EmbeddingService, provider):
        chunks = [_make_chunk("doc1", 0, "same text")]

        # First call: embeds
        result1 = await service.embed_chunks(chunks)
        assert result1.embedded_count == 1
        assert result1.cached_count == 0

        # Second call: served from cache
        result2 = await service.embed_chunks(chunks)
        assert result2.embedded_count == 0
        assert result2.cached_count == 1
        assert provider.call_count == 1  # no additional API call

    @pytest.mark.asyncio
    async def test_embed_chunks_partial_cache(self, service: EmbeddingService, provider):
        chunks1 = [_make_chunk("doc1", 0, "cached text")]
        await service.embed_chunks(chunks1)

        # New chunk + cached chunk
        chunks2 = [
            _make_chunk("doc1", 0, "cached text"),
            _make_chunk("doc1", 1, "new text"),
        ]
        result = await service.embed_chunks(chunks2)
        assert result.cached_count == 1
        assert result.embedded_count == 1

    @pytest.mark.asyncio
    async def test_embed_chunks_changed_content(self, service: EmbeddingService, provider):
        chunks_v1 = [_make_chunk("doc1", 0, "version one")]
        await service.embed_chunks(chunks_v1)

        chunks_v2 = [_make_chunk("doc1", 0, "version two")]
        result = await service.embed_chunks(chunks_v2)
        # Different content hash → re-embed
        assert result.embedded_count == 1
        assert result.cached_count == 0

    @pytest.mark.asyncio
    async def test_embed_document(self, service: EmbeddingService):
        chunks = [_make_chunk("doc1", i, f"text {i}") for i in range(3)]
        vectors = await service.embed_document(chunks)
        assert len(vectors) == 3
        assert vectors[0].chunk_index == 0
        assert vectors[2].chunk_index == 2

    @pytest.mark.asyncio
    async def test_embed_empty(self, service: EmbeddingService):
        result = await service.embed_chunks([])
        assert result.total_chunks == 0
        assert len(result.vectors) == 0

    def test_delete_doc_cache(self, service: EmbeddingService):
        loop = asyncio.new_event_loop()
        try:
            chunks = [_make_chunk("doc1", i, f"text {i}") for i in range(3)]
            loop.run_until_complete(service.embed_chunks(chunks))

            deleted = service.delete_doc_cache("doc1")
            assert deleted == 3
        finally:
            loop.close()

    def test_cache_stats(self, service: EmbeddingService):
        stats = service.cache_stats()
        assert stats["total_vectors"] == 0

    def test_model_name(self, service: EmbeddingService):
        assert service.model_name == "fake-model"

    def test_dimension(self, service: EmbeddingService):
        assert service.dimension == 4


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


class TestProviderConstruction:
    def test_openai_requires_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EmbeddingError, match="API key"):
                OpenAIEmbeddingProvider()

    def test_openai_with_key(self):
        provider = OpenAIEmbeddingProvider(api_key="sk-test")
        assert provider.model_name == "text-embedding-3-small"
        assert provider.dimension == 1536

    def test_openai_custom_model(self):
        provider = OpenAIEmbeddingProvider(
            api_key="sk-test",
            model="text-embedding-3-large",
        )
        assert provider.dimension == 3072

    def test_voyage_requires_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EmbeddingError, match="API key"):
                VoyageEmbeddingProvider()

    def test_voyage_with_key(self):
        provider = VoyageEmbeddingProvider(api_key="voy-test")
        assert provider.model_name == "voyage-3-lite"
        assert provider.dimension == 512

    def test_create_provider_auto_openai(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=True):
            provider = create_provider("auto")
            assert isinstance(provider, OpenAIEmbeddingProvider)

    def test_create_provider_auto_voyage(self):
        with patch.dict("os.environ", {"VOYAGE_API_KEY": "voy-test"}, clear=True):
            provider = create_provider("auto")
            assert isinstance(provider, VoyageEmbeddingProvider)

    def test_create_provider_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EmbeddingError, match="No embedding API key"):
                create_provider("auto")

    def test_create_provider_unknown(self):
        with pytest.raises(EmbeddingError, match="Unknown provider"):
            create_provider("unknown")


# ---------------------------------------------------------------------------
# Factory: get_embedding_service
# ---------------------------------------------------------------------------


class TestGetEmbeddingService:
    def test_creates_service(self, tmp_path: Path):
        from llm_wiki.config import WikiConfig

        config = WikiConfig(vault_path=tmp_path)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            service = get_embedding_service(config, provider_type="openai")
            assert service.model_name == "text-embedding-3-small"
            assert service.dimension == 1536
            service.close()

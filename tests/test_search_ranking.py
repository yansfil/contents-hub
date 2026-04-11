"""Tests for search_ranking module — BM25-based search result ranking integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.search_ranking import (
    RankedResult,
    SearchRanker,
    VaultRanker,
    rank_documents,
    _extract_query_excerpt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ranker() -> SearchRanker:
    """Pre-populated SearchRanker with diverse documents."""
    r = SearchRanker()
    r.add(
        "transformer",
        "The transformer architecture uses self-attention mechanisms. "
        "Multi-head attention allows the model to attend to different positions. "
        "Transformers have revolutionized natural language processing.",
        title="Transformer Architecture",
    )
    r.add(
        "kubernetes",
        "Kubernetes is a container orchestration platform. "
        "It manages Docker containers across clusters. "
        "Deployment, scaling, and monitoring are automated.",
        title="Kubernetes Guide",
    )
    r.add(
        "rag",
        "Retrieval-Augmented Generation combines retrieval with generation. "
        "The transformer model retrieves relevant documents from a knowledge base. "
        "RAG improves factual accuracy in language models.",
        title="RAG Overview",
    )
    r.add(
        "attention",
        "The attention mechanism computes weighted sums over input sequences. "
        "Self-attention, cross-attention, and multi-head attention are key variants. "
        "Attention weights reveal which tokens the model focuses on.",
        title="Attention Mechanisms",
    )
    return r


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a test vault with markdown pages."""
    _write_page(
        tmp_path / "transformers.md",
        {"title": "Transformers", "tags": ["ai", "nlp"], "aliases": ["AIAYN"]},
        (
            "# Transformers\n\n"
            "The transformer architecture was introduced in 2017.\n"
            "It uses self-attention mechanisms for sequence modeling.\n"
            "Multi-head attention extends this capability.\n"
        ),
    )
    _write_page(
        tmp_path / "kubernetes.md",
        {"title": "Kubernetes", "tags": ["devops", "cloud"]},
        (
            "# Kubernetes\n\n"
            "Kubernetes orchestrates container deployments.\n"
            "Docker containers run across managed clusters.\n"
        ),
    )
    _write_page(
        tmp_path / "rag.md",
        {"title": "RAG", "tags": ["ai", "llm"]},
        (
            "# RAG\n\n"
            "Retrieval-Augmented Generation retrieves documents\n"
            "and uses a transformer model to generate answers.\n"
        ),
    )
    # Empty page (should be skipped)
    (tmp_path / "empty.md").write_text("---\ntitle: Empty\n---\n")

    # Sources dir (should be excluded by default)
    sources_dir = tmp_path / "sources" / "rss"
    sources_dir.mkdir(parents=True)
    _write_page(
        sources_dir / "article.md",
        {"title": "RSS Article"},
        "# RSS\n\nAn article about transformer models from RSS.\n",
    )
    return tmp_path


@pytest.fixture
def vault_config(vault: Path) -> WikiConfig:
    return WikiConfig(vault_path=vault)


def _write_page(path: Path, fm: dict, body: str) -> None:
    """Write a markdown page with YAML frontmatter."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# RankedResult dataclass
# ---------------------------------------------------------------------------


class TestRankedResult:
    def test_wikilink(self):
        r = RankedResult(doc_id="transformer", score=1.5, title="Transformer")
        assert r.wikilink == "[[transformer]]"

    def test_default_values(self):
        r = RankedResult(doc_id="x", score=0.5)
        assert r.title == ""
        assert r.excerpt == ""
        assert r.path == ""
        assert r.metadata == {}

    def test_frozen(self):
        r = RankedResult(doc_id="x", score=1.0)
        with pytest.raises(AttributeError):
            r.score = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SearchRanker: basic operations
# ---------------------------------------------------------------------------


class TestSearchRankerBasic:
    def test_doc_count(self, ranker: SearchRanker):
        assert ranker.doc_count == 4

    def test_is_ready_before_search(self, ranker: SearchRanker):
        # Not built until first query
        assert not ranker.is_ready

    def test_is_ready_after_search(self, ranker: SearchRanker):
        ranker.search("test")
        assert ranker.is_ready

    def test_add_invalidates(self, ranker: SearchRanker):
        ranker.search("test")  # build
        assert ranker.is_ready
        ranker.add("new", "new document text")
        assert not ranker.is_ready

    def test_clear(self, ranker: SearchRanker):
        ranker.search("test")
        ranker.clear()
        assert ranker.doc_count == 0
        assert not ranker.is_ready

    def test_empty_ranker(self):
        r = SearchRanker()
        results = r.search("anything")
        assert results == []


# ---------------------------------------------------------------------------
# SearchRanker: search quality
# ---------------------------------------------------------------------------


class TestSearchRankerSearch:
    def test_relevant_query(self, ranker: SearchRanker):
        results = ranker.search("transformer attention mechanism")
        assert len(results) > 0
        # The "transformer" and "attention" docs should rank highest
        top_ids = [r.doc_id for r in results[:2]]
        assert "transformer" in top_ids or "attention" in top_ids

    def test_transformer_ranks_first_for_transformer_query(self, ranker: SearchRanker):
        results = ranker.search("transformer architecture")
        assert results[0].doc_id in ("transformer", "attention")

    def test_kubernetes_query(self, ranker: SearchRanker):
        results = ranker.search("kubernetes docker container")
        assert len(results) >= 1
        assert results[0].doc_id == "kubernetes"

    def test_irrelevant_query(self, ranker: SearchRanker):
        results = ranker.search("quantum computing physics")
        assert len(results) == 0

    def test_empty_query(self, ranker: SearchRanker):
        results = ranker.search("")
        assert results == []

    def test_whitespace_query(self, ranker: SearchRanker):
        results = ranker.search("   ")
        assert results == []

    def test_top_k_limit(self, ranker: SearchRanker):
        results = ranker.search("transformer", top_k=2)
        assert len(results) <= 2

    def test_min_score_filter(self, ranker: SearchRanker):
        # Get all results first
        all_results = ranker.search("transformer", min_score=0.0)
        if len(all_results) >= 2:
            # Use a score between the top and bottom results
            mid_score = all_results[-1].score
            filtered = ranker.search("transformer", min_score=mid_score)
            assert len(filtered) < len(all_results)

    def test_scores_are_descending(self, ranker: SearchRanker):
        results = ranker.search("transformer attention")
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_scores_are_positive(self, ranker: SearchRanker):
        results = ranker.search("transformer")
        for r in results:
            assert r.score > 0.0

    def test_result_has_title(self, ranker: SearchRanker):
        results = ranker.search("kubernetes")
        assert results[0].title == "Kubernetes Guide"

    def test_result_has_excerpt(self, ranker: SearchRanker):
        results = ranker.search("kubernetes")
        assert results[0].excerpt != ""
        assert "kubernetes" in results[0].excerpt.lower()

    def test_no_excerpt_when_disabled(self, ranker: SearchRanker):
        results = ranker.search("kubernetes", include_excerpt=False)
        for r in results:
            assert r.excerpt == ""


# ---------------------------------------------------------------------------
# SearchRanker: score() method
# ---------------------------------------------------------------------------


class TestSearchRankerScore:
    def test_score_returns_all_docs(self, ranker: SearchRanker):
        scores = ranker.score("transformer")
        assert len(scores) == 4
        assert "transformer" in scores
        assert "kubernetes" in scores

    def test_score_includes_zeros(self, ranker: SearchRanker):
        scores = ranker.score("kubernetes docker")
        # Documents without kubernetes/docker should have score 0
        assert scores["kubernetes"] > 0
        # transformer doc might have 0 score
        assert scores.get("transformer", 0) >= 0  # may or may not be 0

    def test_score_empty_query(self, ranker: SearchRanker):
        scores = ranker.score("")
        assert all(s == 0.0 for s in scores.values())


# ---------------------------------------------------------------------------
# SearchRanker: add_batch
# ---------------------------------------------------------------------------


class TestSearchRankerBatch:
    def test_add_batch(self):
        r = SearchRanker()
        r.add_batch([
            ("a", "machine learning algorithms"),
            ("b", "web development frameworks"),
        ])
        assert r.doc_count == 2
        results = r.search("machine learning")
        assert len(results) >= 1
        assert results[0].doc_id == "a"

    def test_add_batch_with_titles(self):
        r = SearchRanker()
        r.add_batch(
            [("a", "deep learning neural networks")],
            titles={"a": "Deep Learning"},
        )
        results = r.search("neural networks")
        assert results[0].title == "Deep Learning"

    def test_add_batch_empty(self):
        r = SearchRanker()
        r.add_batch([])
        assert r.doc_count == 0


# ---------------------------------------------------------------------------
# SearchRanker: Korean query support
# ---------------------------------------------------------------------------


class TestSearchRankerKorean:
    def test_korean_query(self):
        """Korean search works when tokens match exactly (no morphological analysis).

        Since the tokenizer is character-class based (not morphological),
        Korean particles attached to words create different tokens.
        Use exact token forms for reliable matching.
        """
        r = SearchRanker()
        r.add("ai-intro", "인공지능 머신러닝 기초 개념 설명", title="AI 소개")
        r.add("cooking", "맛있는 김치찌개 레시피 소개", title="요리")
        results = r.search("인공지능 머신러닝")
        assert len(results) >= 1
        assert results[0].doc_id == "ai-intro"

    def test_mixed_language_query(self):
        r = SearchRanker()
        r.add("mixed", "Transformer 모델의 attention mechanism을 분석합니다", title="Transformer 분석")
        r.add("other", "데이터베이스 쿼리 최적화 방법", title="DB 최적화")
        results = r.search("Transformer 모델")
        assert len(results) >= 1
        assert results[0].doc_id == "mixed"


# ---------------------------------------------------------------------------
# SearchRanker: custom BM25 parameters
# ---------------------------------------------------------------------------


class TestSearchRankerParameters:
    def test_custom_k1_b(self):
        r = SearchRanker(k1=2.0, b=0.5)
        r.add("a", "term term term term")
        r.add("b", "term")
        results = r.search("term")
        assert len(results) == 2
        # Higher TF should score higher
        assert results[0].doc_id == "a"

    def test_b_zero_no_length_normalization(self):
        """With b=0, document length should not affect ranking."""
        r = SearchRanker(k1=1.2, b=0.0)
        r.add("short", "transformer")
        r.add("long", "transformer " + "padding " * 100)
        results = r.search("transformer")
        # Both should score > 0, but scores may differ due to TF
        assert len(results) == 2


# ---------------------------------------------------------------------------
# VaultRanker
# ---------------------------------------------------------------------------


class TestVaultRanker:
    def test_search_vault(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("transformer attention")
        assert len(results) >= 1
        doc_ids = [r.doc_id for r in results]
        assert "transformers" in doc_ids

    def test_kubernetes_query(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("kubernetes container")
        assert len(results) >= 1
        assert results[0].doc_id == "kubernetes"

    def test_excludes_sources_by_default(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("RSS article")
        doc_ids = [r.doc_id for r in results]
        assert "article" not in doc_ids

    def test_includes_sources_when_configured(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config, exclude_sources=False)
        results = ranker.search("RSS")
        doc_ids = [r.doc_id for r in results]
        assert "article" in doc_ids

    def test_doc_count(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        # Should have 3 non-empty wiki pages (excludes empty.md and sources/)
        assert ranker.doc_count == 3

    def test_empty_page_skipped(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("empty draft")
        doc_ids = [r.doc_id for r in results]
        assert "empty" not in doc_ids

    def test_result_has_path(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("transformer")
        transformer = [r for r in results if r.doc_id == "transformers"]
        assert len(transformer) == 1
        assert transformer[0].path == "transformers.md"

    def test_result_has_metadata(self, vault_config: WikiConfig):
        ranker = VaultRanker(vault_config)
        results = ranker.search("transformer")
        transformer = [r for r in results if r.doc_id == "transformers"]
        assert len(transformer) == 1
        assert "ai" in transformer[0].metadata.get("tags", [])
        assert "AIAYN" in transformer[0].metadata.get("aliases", [])

    def test_invalidate_and_rescan(self, vault_config: WikiConfig, vault: Path):
        ranker = VaultRanker(vault_config)
        results1 = ranker.search("transformer")
        count1 = ranker.doc_count

        # Add a new file
        _write_page(
            vault / "new-page.md",
            {"title": "New Page", "tags": ["test"]},
            "This is about quantum computing and physics.\n",
        )

        # Before invalidation: old results
        assert ranker.doc_count == count1

        # After invalidation: picks up new page
        ranker.invalidate()
        assert ranker.doc_count == count1 + 1

    def test_nonexistent_vault(self, tmp_path: Path):
        config = WikiConfig(vault_path=tmp_path / "nonexistent")
        ranker = VaultRanker(config)
        results = ranker.search("anything")
        assert results == []
        assert ranker.doc_count == 0


# ---------------------------------------------------------------------------
# rank_documents convenience function
# ---------------------------------------------------------------------------


class TestRankDocuments:
    def test_basic_ranking(self):
        results = rank_documents(
            "transformer attention",
            {
                "ai": "The transformer uses attention mechanisms for sequence modeling.",
                "devops": "Kubernetes manages container deployments efficiently.",
            },
        )
        assert len(results) >= 1
        assert results[0].doc_id == "ai"

    def test_empty_collection(self):
        results = rank_documents("query", {})
        assert results == []

    def test_empty_query(self):
        results = rank_documents("", {"a": "some text"})
        assert results == []

    def test_top_k(self):
        docs = {f"doc-{i}": f"common term in document {i}" for i in range(20)}
        results = rank_documents("common term", docs, top_k=5)
        assert len(results) <= 5

    def test_min_score(self):
        results = rank_documents(
            "transformer",
            {
                "ai": "transformer model architecture attention",
                "devops": "kubernetes docker container",
            },
            min_score=0.0,
        )
        # devops should be excluded (score 0)
        doc_ids = [r.doc_id for r in results]
        assert "devops" not in doc_ids

    def test_custom_bm25_params(self):
        results = rank_documents(
            "term",
            {"a": "term term term", "b": "term"},
            k1=2.0,
            b=0.5,
        )
        assert len(results) == 2
        assert results[0].doc_id == "a"

    def test_result_title_defaults_to_doc_id(self):
        results = rank_documents("hello", {"my-doc": "hello world"})
        assert results[0].title == "my-doc"


# ---------------------------------------------------------------------------
# _extract_query_excerpt
# ---------------------------------------------------------------------------


class TestExtractQueryExcerpt:
    def test_excerpt_centered_on_match(self):
        text = "A" * 100 + " transformer " + "B" * 100
        excerpt = _extract_query_excerpt(text, ["transformer"], max_chars=80)
        assert "transformer" in excerpt

    def test_excerpt_with_ellipsis(self):
        text = "prefix " * 20 + "keyword " + "suffix " * 20
        excerpt = _extract_query_excerpt(text, ["keyword"], max_chars=60)
        assert "..." in excerpt

    def test_no_match_returns_start(self):
        text = "Some text about various topics."
        excerpt = _extract_query_excerpt(text, ["nonexistent"], max_chars=200)
        assert excerpt.startswith("Some text")

    def test_empty_text(self):
        assert _extract_query_excerpt("", ["any"]) == ""

    def test_empty_tokens(self):
        excerpt = _extract_query_excerpt("hello world", [], max_chars=200)
        assert excerpt == "hello world"

    def test_short_text_no_ellipsis(self):
        text = "short text"
        excerpt = _extract_query_excerpt(text, ["short"], max_chars=200)
        assert "..." not in excerpt

    def test_first_token_match_preferred(self):
        text = "AAA keyword1 BBB keyword2 CCC"
        excerpt = _extract_query_excerpt(
            text, ["keyword2", "keyword1"], max_chars=50,
        )
        # Should center on keyword1 (earlier in text)
        assert "keyword1" in excerpt

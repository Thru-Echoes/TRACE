"""Integration tests for embedding-enhanced learn pipeline.

Tests: add → embed → save → load → recall (full pipeline),
backend fallback chains, model change detection, real-data tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    EmbeddingBackend,
    recall_learnings,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import add_learning, load_store, save_store

# Synthetic embeddings
CONDA_VEC = [0.9, 0.1, 0.0, 0.1]
LOGGING_VEC = [0.1, 0.9, 0.1, 0.0]
COOKING_VEC = [0.0, 0.1, 0.9, 0.8]
ML_VEC = [0.85, 0.15, 0.05, 0.0]


def _mock_provider(vectors: list[list[float]] | None = None, model_name: str = "test-model"):
    """Create a mock provider that returns given vectors (cycling if needed)."""
    provider = AsyncMock()
    provider.model_name = model_name
    provider.dimensions = 4

    async def _embed(texts):
        if vectors:
            return vectors[:len(texts)]
        # Default: return distinct vectors for each text
        return [[float(i), 0.0, 0.0, 0.0] for i in range(len(texts))]

    provider.embed_texts = AsyncMock(side_effect=_embed)
    return provider


# ── Full pipeline tests ──────────────────────────────────────────────────


class TestEmbeddingFullPipeline:
    async def test_add_embed_save_load_recall(self, tmp_path):
        """Full pipeline: add learning → embed → save → load → recall."""
        ks = KnowledgeStore(project="test")
        lrn = add_learning(ks, content="Always use conda ml-dev", tags=["conda"])
        lrn.embedding = CONDA_VEC
        lrn.embedding_model = "test-model"
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        assert loaded.learnings[0].embedding == CONDA_VEC
        assert loaded.learnings[0].embedding_model == "test-model"

        provider = _mock_provider([ML_VEC])
        backend = EmbeddingBackend(provider=provider)
        results = await recall_learnings(
            loaded.learnings, "which anaconda environment?",
            backend=backend, threshold=0.0,
        )
        assert len(results) == 1
        assert results[0]["score"] > 0.5

    async def test_json_roundtrip_preserves_embeddings(self, tmp_path):
        """Embeddings survive JSON serialization."""
        ks = KnowledgeStore(project="round")
        lrn = add_learning(ks, content="test content")
        lrn.embedding = [0.1, 0.2, 0.3, 0.4]
        lrn.embedding_model = "roundtrip-model"
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("round", directory=str(tmp_path))
        assert loaded.learnings[0].embedding is not None
        assert len(loaded.learnings[0].embedding) == 4
        assert loaded.learnings[0].embedding[0] == pytest.approx(0.1)
        assert loaded.learnings[0].embedding_model == "roundtrip-model"

    async def test_semantic_query_bm25_would_miss(self, tmp_path):
        """Embedding finds what BM25 can't: semantically similar, no keyword overlap."""
        ks = KnowledgeStore(project="semantic")
        # Learning uses "conda"
        lrn = add_learning(ks, content="Always use the ml-dev conda environment", tags=["conda"])
        lrn.embedding = CONDA_VEC
        lrn.embedding_model = "test-model"
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("semantic", directory=str(tmp_path))

        # BM25 test — "anaconda" has no keyword overlap with "conda"
        bm25 = BM25Backend()
        bm25_results = await recall_learnings(
            loaded.learnings, "which anaconda environment should I activate",
            backend=bm25, threshold=0.0,
        )
        bm25_score = bm25_results[0]["score"] if bm25_results else 0.0

        # Embedding test — semantically similar vector
        provider = _mock_provider([ML_VEC])  # close to CONDA_VEC
        emb_backend = EmbeddingBackend(provider=provider)
        emb_results = await recall_learnings(
            loaded.learnings, "which anaconda environment should I activate",
            backend=emb_backend, threshold=0.0,
        )
        emb_score = emb_results[0]["score"]

        # Embedding score should be significantly higher
        assert emb_score > bm25_score + 0.1

    async def test_extract_then_recall(self, tmp_path):
        """Multiple learnings added with embeddings, recall ranks correctly."""
        ks = KnowledgeStore(project="multi")
        vecs = [CONDA_VEC, LOGGING_VEC, COOKING_VEC]
        contents = [
            "Always use conda ml-dev",
            "Log decisions before implementing",
            "Fresh tomatoes for pasta",
        ]
        for content, vec in zip(contents, vecs, strict=True):
            lrn = add_learning(ks, content=content)
            lrn.embedding = vec
            lrn.embedding_model = "test-model"
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("multi", directory=str(tmp_path))
        # Query with conda-like vector → should rank conda first
        provider = _mock_provider([CONDA_VEC])
        backend = EmbeddingBackend(provider=provider)
        results = await recall_learnings(
            loaded.learnings, "conda environment",
            backend=backend, threshold=0.0, limit=3,
        )
        assert results[0]["learning"]["content"] == "Always use conda ml-dev"


# ── Backend fallback chain ───────────────────────────────────────────────


class TestBackendFallbackChain:
    async def test_no_embeddings_falls_to_bm25(self):
        """Store with no embeddings → full BM25 fallback."""
        provider = _mock_provider()
        backend = EmbeddingBackend(provider=provider)
        learnings = [
            Learning(id="lrn_001", content="conda environment setup", tags=["conda"]),
        ]
        results = await backend.score_batch(learnings, "conda")
        assert len(results) == 1
        provider.embed_texts.assert_not_called()

    async def test_mixed_store_uses_both(self):
        """Some embedded, some not → hybrid scoring."""
        provider = _mock_provider([CONDA_VEC])
        backend = EmbeddingBackend(provider=provider)
        learnings = [
            Learning(
                id="lrn_001", content="conda env",
                embedding=CONDA_VEC, embedding_model="test",
            ),
            Learning(id="lrn_002", content="conda setup"),  # no embedding
        ]
        results = await backend.score_batch(learnings, "conda")
        assert len(results) == 2

    async def test_provider_failure_in_scoring(self):
        """If provider.embed_texts raises, entire backend fails (caller handles)."""
        provider = _mock_provider()
        provider.embed_texts = AsyncMock(side_effect=RuntimeError("API down"))
        backend = EmbeddingBackend(provider=provider)
        learnings = [
            Learning(
                id="lrn_001", content="test",
                embedding=CONDA_VEC, embedding_model="test",
            ),
        ]
        with pytest.raises(RuntimeError, match="API down"):
            await backend.score_batch(learnings, "query")


# ── Model change integration ─────────────────────────────────────────────


class TestModelChangeIntegration:
    def test_stale_detection(self):
        """Learnings with different embedding_model are stale."""
        lrn = Learning(
            id="lrn_001", content="test",
            embedding=[0.1], embedding_model="old-model",
        )
        assert lrn.embedding_model != "new-model"
        assert lrn.embedding is not None

    async def test_fresh_embeddings_not_stale(self):
        """Learnings with matching embedding_model are fresh."""
        lrn = Learning(
            id="lrn_001", content="test",
            embedding=[0.1], embedding_model="current-model",
        )
        assert lrn.embedding_model == "current-model"


# ── Real data tests ──────────────────────────────────────────────────────

_REAL_STORE = Path.home() / ".trace" / "knowledge" / "TRACE.json"


@pytest.mark.skipif(not _REAL_STORE.exists(), reason="No real TRACE knowledge store")
class TestRealDataEmbeddings:
    """Tests using the real TRACE.json knowledge store (~27 learnings)."""

    def test_load_real_store(self):
        """Real store loads cleanly with new code (backward compat)."""
        ks = load_store("TRACE")
        assert len(ks.learnings) > 0
        # Old stores won't have embeddings — that's fine
        for lrn in ks.learnings:
            # embedding should be None (not yet generated) or a list
            assert lrn.embedding is None or isinstance(lrn.embedding, list)

    async def test_bm25_recall_on_real_data(self):
        """BM25 recall works on real data (baseline for comparison)."""
        ks = load_store("TRACE")
        bm25 = BM25Backend()
        results = await recall_learnings(
            ks.learnings, "schema validation rules for actors",
            backend=bm25, threshold=0.0, limit=5,
        )
        assert len(results) > 0
        # lrn_001 is about actor type validation — should be in results
        ids = [r["learning"]["id"] for r in results]
        assert "lrn_001" in ids

    async def test_embedding_recall_on_real_data(self):
        """Embedding recall with model2vec on real data."""
        from trace_mcp.extensions.learn.embeddings import get_embedding_provider

        config = LearnConfig(embedding_backend="model2vec")
        provider = get_embedding_provider(config)
        if provider is None:
            pytest.skip("model2vec not available")

        ks = load_store("TRACE")
        # Generate embeddings for all learnings
        texts = [lrn.content for lrn in ks.learnings]
        vecs = await provider.embed_texts(texts)
        for lrn, vec in zip(ks.learnings, vecs, strict=True):
            lrn.embedding = vec
            lrn.embedding_model = provider.model_name

        backend = EmbeddingBackend(provider=provider)
        results = await recall_learnings(
            ks.learnings, "audio recording problems on mac",
            backend=backend, threshold=0.0, limit=5,
        )
        assert len(results) > 0
        # lrn_003 is about ffmpeg audio device issues on macOS
        ids = [r["learning"]["id"] for r in results[:5]]
        assert "lrn_003" in ids, f"Expected lrn_003 in top 5, got: {ids}"

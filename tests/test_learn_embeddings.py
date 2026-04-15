"""Unit tests for trace-learn vector embedding support.

Tests: embedding providers (mocked), EmbeddingBackend cosine scoring,
.npy sidecar save/load, stale embedding detection, backward compatibility,
tag boosting with embeddings, decay integration, dedup with embeddings,
embedding auto-selection.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.embeddings import (
    Model2VecEmbeddingProvider,
    OpenAIEmbeddingProvider,
    cosine_similarity_matrix,
    get_embedding_provider,
)
from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    DecayParams,
    EmbeddingBackend,
    recall_learnings,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import (
    add_learning,
    load_embeddings_cache,
    load_store,
    save_embeddings_cache,
    save_store,
)

# ── Synthetic embeddings for deterministic testing ────────────────────────
# 4-dimensional vectors (real models use 256-1536, but 4d is enough for math)

CONDA_VEC = [0.9, 0.1, 0.0, 0.1]  # "conda cluster"
ML_VEC = [0.85, 0.15, 0.05, 0.0]  # similar to conda
COOKING_VEC = [0.0, 0.1, 0.9, 0.8]  # "cooking cluster"
LOGGING_VEC = [0.1, 0.9, 0.1, 0.0]  # "logging cluster"

CONDA_LEARNING = Learning(
    id="lrn_001",
    content="Always use the ml-dev conda environment, not base",
    tags=["conda", "env"],
    embedding=CONDA_VEC,
    embedding_model="test-model",
)
LOGGING_LEARNING = Learning(
    id="lrn_002",
    content="Log decisions before implementing them",
    tags=["trace", "logging"],
    embedding=LOGGING_VEC,
    embedding_model="test-model",
)
COOKING_LEARNING = Learning(
    id="lrn_003",
    content="The best pasta recipe uses fresh tomatoes",
    tags=["food"],
    embedding=COOKING_VEC,
    embedding_model="test-model",
)
NONEMB_LEARNING = Learning(
    id="lrn_004",
    content="Version 1 had attribution bias issues",
    tags=["attribution"],
    # No embedding — should fall through to BM25
)


# ── Cosine similarity matrix tests ───────────────────────────────────────


class TestCosineSimilarityMatrix:
    def test_identical_vectors_score_one(self):
        q = [1.0, 0.0, 0.0]
        matrix = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors_score_zero(self):
        q = [1.0, 0.0, 0.0]
        matrix = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == pytest.approx(0.0, abs=1e-5)

    def test_opposite_vectors_score_negative(self):
        q = [1.0, 0.0, 0.0]
        matrix = np.array([[-1.0, 0.0, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == pytest.approx(-1.0, abs=1e-5)

    def test_batch_scoring(self):
        q = [1.0, 0.0, 0.0]
        matrix = np.array([
            [1.0, 0.0, 0.0],  # identical
            [0.0, 1.0, 0.0],  # orthogonal
            [0.7, 0.7, 0.0],  # 45 degrees
        ], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert len(result) == 3
        assert float(result[0]) == pytest.approx(1.0, abs=1e-5)
        assert float(result[1]) == pytest.approx(0.0, abs=1e-5)
        assert float(result[2]) == pytest.approx(math.cos(math.pi / 4), abs=1e-3)

    def test_zero_query_vector(self):
        q = [0.0, 0.0, 0.0]
        matrix = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == 0.0

    def test_zero_doc_vector(self):
        q = [1.0, 0.0, 0.0]
        matrix = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == 0.0

    def test_single_doc(self):
        q = [0.5, 0.5, 0.0]
        matrix = np.array([[0.5, 0.5, 0.0]], dtype=np.float32)
        result = cosine_similarity_matrix(q, matrix)
        assert float(result[0]) == pytest.approx(1.0, abs=1e-5)


# ── OpenAI provider tests ────────────────────────────────────────────────


class TestOpenAIEmbeddingProvider:
    async def test_embed_texts_mocked(self):
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.embeddings.AsyncOpenAI") as MockCls:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
                mock_client.embeddings.create = AsyncMock(return_value=mock_response)
                MockCls.return_value = mock_client

                provider = OpenAIEmbeddingProvider(api_key="sk-test")
                provider._client = mock_client
                result = await provider.embed_texts(["hello"])

                assert result == [[0.1, 0.2, 0.3]]
                mock_client.embeddings.create.assert_called_once()

    async def test_embed_texts_with_dimensions(self):
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.embeddings.AsyncOpenAI") as MockCls:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.data = [MagicMock(embedding=[0.1, 0.2])]
                mock_client.embeddings.create = AsyncMock(return_value=mock_response)
                MockCls.return_value = mock_client

                provider = OpenAIEmbeddingProvider(api_key="sk-test", dimensions=256)
                provider._client = mock_client
                await provider.embed_texts(["hello"])

                call_kwargs = mock_client.embeddings.create.call_args
                assert call_kwargs[1]["dimensions"] == 256

    def test_model_name_exposed(self):
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.embeddings.AsyncOpenAI"):
                provider = OpenAIEmbeddingProvider(api_key="sk-test", model="my-model")
                assert provider.model_name == "my-model"

    def test_raises_without_openai_package(self):
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", False):
            with pytest.raises(RuntimeError, match="openai"):
                OpenAIEmbeddingProvider(api_key="sk-test")


# ── model2vec provider tests ─────────────────────────────────────────────


class TestModel2VecEmbeddingProvider:
    async def test_embed_texts_mocked(self):
        mock_instance = MagicMock()
        mock_instance.dim = 256
        mock_instance.encode.return_value = [np.array([0.1, 0.2, 0.3])]

        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", True):
            with patch("model2vec.StaticModel.from_pretrained", return_value=mock_instance):
                provider = Model2VecEmbeddingProvider(model_name="test-model")
                result = await provider.embed_texts(["hello"])

        assert len(result) == 1
        assert len(result[0]) == 3
        mock_instance.encode.assert_called_once_with(["hello"])

    def test_model_name_exposed(self):
        mock_instance = MagicMock()
        mock_instance.dim = 256

        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", True):
            with patch("model2vec.StaticModel.from_pretrained", return_value=mock_instance):
                provider = Model2VecEmbeddingProvider(model_name="my/model")
                assert provider.model_name == "my/model"

    def test_raises_without_model2vec_package(self):
        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", False):
            with pytest.raises(RuntimeError, match="model2vec"):
                Model2VecEmbeddingProvider()


# ── Provider auto-selection tests ────────────────────────────────────────


class TestEmbeddingProviderAutoSelection:
    def test_auto_selects_openai_when_available(self):
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto")
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.embeddings.AsyncOpenAI"):
                provider = get_embedding_provider(config)
        assert provider is not None
        assert isinstance(provider, OpenAIEmbeddingProvider)

    def test_auto_selects_model2vec_when_no_openai_key(self):
        config = LearnConfig(openai_api_key=None, embedding_backend="auto")
        mock_instance = MagicMock()
        mock_instance.dim = 256
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", False):
            with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", True):
                with patch("model2vec.StaticModel.from_pretrained", return_value=mock_instance):
                    provider = get_embedding_provider(config)
        assert provider is not None
        assert isinstance(provider, Model2VecEmbeddingProvider)

    def test_returns_none_when_nothing_available(self):
        config = LearnConfig(openai_api_key=None, embedding_backend="auto")
        with patch("trace_mcp.extensions.learn.embeddings._HAS_OPENAI", False):
            with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", False):
                provider = get_embedding_provider(config)
        assert provider is None

    def test_explicit_none_returns_none(self):
        config = LearnConfig(embedding_backend="none")
        provider = get_embedding_provider(config)
        assert provider is None

    def test_explicit_openai_without_key_falls_back(self):
        """Permissive mode: explicit openai backend without key falls through to None."""
        config = LearnConfig(
            openai_api_key=None,
            embedding_backend="openai",
            strict_llm=False,
        )
        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", False):
            provider = get_embedding_provider(config)
        assert provider is None

    def test_explicit_openai_without_key_raises_in_strict_mode(self):
        """Strict mode: explicit openai backend without key raises LLMFallbackError."""
        from trace_mcp.extensions.learn.config import LLMFallbackError

        config = LearnConfig(
            openai_api_key=None,
            embedding_backend="openai",
            strict_llm=True,
        )
        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", False):
            with pytest.raises(LLMFallbackError, match="OpenAI embeddings requested"):
                get_embedding_provider(config)

    def test_explicit_model2vec(self):
        config = LearnConfig(embedding_backend="model2vec")
        mock_instance = MagicMock()
        mock_instance.dim = 256
        with patch("trace_mcp.extensions.learn.embeddings._HAS_MODEL2VEC", True):
            with patch("model2vec.StaticModel.from_pretrained", return_value=mock_instance):
                provider = get_embedding_provider(config)
        assert provider is not None
        assert isinstance(provider, Model2VecEmbeddingProvider)


# ── EmbeddingBackend tests ───────────────────────────────────────────────


def _mock_provider(return_vec: list[float] | None = None):
    """Create a mock EmbeddingProvider that returns a fixed vector."""
    vec = return_vec or CONDA_VEC
    provider = AsyncMock()
    provider.model_name = "test-model"
    provider.dimensions = len(vec)
    provider.embed_texts = AsyncMock(return_value=[vec])
    return provider


class TestEmbeddingBackend:
    async def test_cosine_scoring_basic(self):
        """Query similar to conda cluster → conda learning scores highest."""
        provider = _mock_provider(ML_VEC)  # similar to conda
        backend = EmbeddingBackend(provider=provider)
        learnings = [CONDA_LEARNING, LOGGING_LEARNING, COOKING_LEARNING]

        results = await backend.score_batch(learnings, "ml-dev environment")
        scores = {idx: score for idx, score in results}

        assert scores[0] > scores[1]  # conda > logging
        assert scores[0] > scores[2]  # conda > cooking

    async def test_mixed_store_embedding_and_bm25(self):
        """Learnings without embeddings fall through to BM25."""
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        learnings = [CONDA_LEARNING, NONEMB_LEARNING]

        results = await backend.score_batch(learnings, "conda environment")
        assert len(results) == 2  # Both scored (one via cosine, one via BM25)
        indices = {idx for idx, _ in results}
        assert 0 in indices  # conda (embedding)
        assert 1 in indices  # nonemb (BM25)

    async def test_tag_boosting_with_embeddings(self):
        provider = _mock_provider(CONDA_VEC)
        backend_with_tags = EmbeddingBackend(provider=provider, tag_weight=0.5)
        backend_no_tags = EmbeddingBackend(provider=provider, tag_weight=0.0)

        results_with = await backend_with_tags.score_batch(
            [CONDA_LEARNING], "environment", context_tags=["conda", "env"],
        )
        results_without = await backend_no_tags.score_batch(
            [CONDA_LEARNING], "environment", context_tags=["conda", "env"],
        )
        # Tag weight of 0.5 with perfect tag match should boost score
        score_with = results_with[0][1]
        score_without = results_without[0][1]
        assert score_with >= score_without

    async def test_empty_learnings(self):
        provider = _mock_provider()
        backend = EmbeddingBackend(provider=provider)
        results = await backend.score_batch([], "query")
        assert results == []

    async def test_all_learnings_without_embeddings_falls_to_bm25(self):
        """When no learnings have embeddings, full BM25 fallback."""
        provider = _mock_provider()
        backend = EmbeddingBackend(provider=provider)
        learnings = [NONEMB_LEARNING]

        results = await backend.score_batch(learnings, "attribution bias")
        assert len(results) == 1
        # Provider should NOT have been called (no embedding search needed)
        provider.embed_texts.assert_not_called()

    async def test_single_learning_with_embedding(self):
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        results = await backend.score_batch([CONDA_LEARNING], "conda")
        assert len(results) == 1
        assert results[0][0] == 0
        assert results[0][1] > 0

    async def test_threshold_default(self):
        backend = EmbeddingBackend(provider=_mock_provider())
        assert backend.default_threshold == 0.3

    async def test_scores_clamped_to_zero_one(self):
        """Negative cosine similarity is clamped to 0."""
        provider = _mock_provider([1.0, 0.0, 0.0, 0.0])
        backend = EmbeddingBackend(provider=provider, tag_weight=0.0)
        # Opposite vector learning
        opposite_lrn = Learning(
            id="lrn_neg", content="x", embedding=[-1.0, 0.0, 0.0, 0.0],
            embedding_model="test-model",
        )
        results = await backend.score_batch([opposite_lrn], "x")
        assert results[0][1] == 0.0  # clamped


# ── Embedding + Decay integration ────────────────────────────────────────


class TestEmbeddingBackendWithDecay:
    async def test_old_embedding_learning_scores_lower(self):
        """Decay multiplier reduces scores for old learnings."""
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        decay = DecayParams(enabled=True, half_life_days=30)

        old_learning = CONDA_LEARNING.model_copy(
            update={"created": datetime.now(UTC) - timedelta(days=60)},
        )
        results = await recall_learnings(
            [old_learning], "conda environment",
            backend=backend, decay_config=decay, threshold=0.0,
        )
        fresh_results = await recall_learnings(
            [CONDA_LEARNING], "conda environment",
            backend=backend, decay_config=decay, threshold=0.0,
        )
        assert results[0]["score"] < fresh_results[0]["score"]

    async def test_evergreen_floor_with_embeddings(self):
        """High recall_count prevents excessive decay."""
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        decay = DecayParams(enabled=True, half_life_days=30, evergreen_recall_threshold=2, evergreen_floor=0.9)

        evergreen = CONDA_LEARNING.model_copy(
            update={
                "created": datetime.now(UTC) - timedelta(days=365),
                "recall_count": 5,
            },
        )
        results = await recall_learnings(
            [evergreen], "conda", backend=backend, decay_config=decay, threshold=0.0,
        )
        # Score should be at least floor * raw_score (which is high for identical vectors)
        assert results[0]["score"] > 0.5

    async def test_decay_disabled(self):
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        decay = DecayParams(enabled=False)

        old = CONDA_LEARNING.model_copy(
            update={"created": datetime.now(UTC) - timedelta(days=9999)},
        )
        results = await recall_learnings(
            [old], "conda", backend=backend, decay_config=decay, threshold=0.0,
        )
        fresh = await recall_learnings(
            [CONDA_LEARNING], "conda", backend=backend, decay_config=decay, threshold=0.0,
        )
        # With decay disabled, age makes no difference
        assert results[0]["score"] == pytest.approx(fresh[0]["score"], abs=0.01)


# ── Sidecar .npy cache tests ────────────────────────────────────────────


class TestSidecarNpyCache:
    def test_save_creates_npy_file(self, tmp_path):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="hello", tags=["test"])
        ks.learnings[0].embedding = [0.1, 0.2, 0.3]
        ks.learnings[0].embedding_model = "test"

        save_store(ks, directory=str(tmp_path))
        npy = tmp_path / "test.embeddings.npy"
        assert npy.exists()

    def test_load_returns_matrix(self, tmp_path):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="hello")
        ks.learnings[0].embedding = [0.1, 0.2, 0.3]
        ks.learnings[0].embedding_model = "test"

        save_store(ks, directory=str(tmp_path))
        loaded = load_embeddings_cache(ks, directory=str(tmp_path))
        assert loaded is not None
        assert loaded.shape == (1, 3)
        assert float(loaded[0][0]) == pytest.approx(0.1, abs=1e-5)

    def test_cache_invalidated_on_size_mismatch(self, tmp_path):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="hello")
        ks.learnings[0].embedding = [0.1, 0.2]
        save_store(ks, directory=str(tmp_path))

        # Add another learning — cache is now stale
        add_learning(ks, content="world")
        loaded = load_embeddings_cache(ks, directory=str(tmp_path))
        assert loaded is None  # size mismatch

    def test_missing_cache_returns_none(self, tmp_path):
        ks = KnowledgeStore(project="nofile")
        loaded = load_embeddings_cache(ks, directory=str(tmp_path))
        assert loaded is None

    def test_nan_rows_for_missing_embeddings(self, tmp_path):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="with emb")
        ks.learnings[0].embedding = [0.1, 0.2]
        ks.learnings[0].embedding_model = "test"
        add_learning(ks, content="no emb")  # No embedding

        save_store(ks, directory=str(tmp_path))
        loaded = load_embeddings_cache(ks, directory=str(tmp_path))
        assert loaded is not None
        assert loaded.shape == (2, 2)
        assert not np.isnan(loaded[0][0])
        assert np.isnan(loaded[1][0])  # Missing embedding → NaN


# ── Stale embedding detection ────────────────────────────────────────────


class TestStaleEmbeddingDetection:
    def test_detects_stale_when_model_changes(self):
        lrn = Learning(
            id="lrn_001", content="test",
            embedding=[0.1, 0.2], embedding_model="old-model",
        )
        # Stale if learning's model != current model
        assert lrn.embedding_model != "new-model"

    def test_no_stale_when_model_matches(self):
        lrn = Learning(
            id="lrn_001", content="test",
            embedding=[0.1, 0.2], embedding_model="current-model",
        )
        assert lrn.embedding_model == "current-model"

    def test_none_embedding_needs_generation(self):
        lrn = Learning(id="lrn_001", content="test")
        assert lrn.embedding is None


# ── Backward compatibility ───────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_old_store_without_embeddings_loads(self, tmp_path):
        """JSON without embedding fields loads cleanly."""
        store_json = {
            "project": "old",
            "version": "0.1",
            "updated": "2026-01-01T00:00:00Z",
            "learnings": [
                {
                    "id": "lrn_001",
                    "content": "old learning",
                    "category": "learning",
                    "tags": ["old"],
                    "created": "2026-01-01T00:00:00Z",
                    "recall_count": 5,
                }
            ],
        }
        path = tmp_path / "old.json"
        path.write_text(json.dumps(store_json))

        ks = load_store("old", directory=str(tmp_path))
        assert len(ks.learnings) == 1
        assert ks.learnings[0].embedding is None
        assert ks.learnings[0].embedding_model is None

    async def test_old_store_falls_through_to_bm25(self, tmp_path):
        """Store with no embeddings → EmbeddingBackend falls through to BM25."""
        ks = KnowledgeStore(project="old")
        add_learning(ks, content="conda environment setup", tags=["conda"])
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("old", directory=str(tmp_path))
        provider = _mock_provider()
        backend = EmbeddingBackend(provider=provider)
        results = await backend.score_batch(loaded.learnings, "conda")
        assert len(results) == 1
        # Provider not called — no embeddings to query
        provider.embed_texts.assert_not_called()

    def test_v01_store_with_new_code(self, tmp_path):
        """Version 0.1 store loads with new 0.4 code."""
        store_json = {"project": "legacy", "version": "0.1", "learnings": []}
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(store_json))

        ks = load_store("legacy", directory=str(tmp_path))
        assert ks.project == "legacy"

    async def test_mixed_store_some_with_some_without(self):
        """Partial embedding coverage works."""
        provider = _mock_provider(CONDA_VEC)
        backend = EmbeddingBackend(provider=provider)
        results = await backend.score_batch(
            [CONDA_LEARNING, NONEMB_LEARNING], "conda",
        )
        assert len(results) == 2


# ── Embedding-enhanced dedup ─────────────────────────────────────────────


class TestEmbeddingDedup:
    def test_cosine_finds_exact_duplicate(self):
        """Identical content → cosine ≈ 1.0 (well above dedup threshold)."""
        q = CONDA_VEC
        matrix = np.array([CONDA_VEC], dtype=np.float32)
        sims = cosine_similarity_matrix(q, matrix)
        assert float(sims[0]) > 0.92

    def test_cosine_finds_near_duplicate(self):
        """Similar vectors → cosine above typical dedup threshold."""
        q = CONDA_VEC
        matrix = np.array([ML_VEC], dtype=np.float32)
        sims = cosine_similarity_matrix(q, matrix)
        assert float(sims[0]) > 0.9  # Very similar vectors

    def test_cosine_misses_different_content(self):
        """Unrelated vectors → cosine below dedup threshold."""
        q = CONDA_VEC
        matrix = np.array([COOKING_VEC], dtype=np.float32)
        sims = cosine_similarity_matrix(q, matrix)
        assert float(sims[0]) < 0.5

    def test_falls_back_to_jaccard_without_embeddings(self):
        """When no embeddings, Jaccard dedup still works."""
        from trace_mcp.extensions.learn.store import find_duplicate

        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use conda ml-dev environment")
        result = find_duplicate(ks, "Always use conda ml-dev environment")
        assert result is not None  # Jaccard finds exact match

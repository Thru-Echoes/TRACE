"""Unit tests for the local-strong embedding tier.

Covers the additive, no-OpenAI-required embedding options:
- ``FastEmbedEmbeddingProvider`` (ONNX via fastembed, no PyTorch) + curated model allowlist
- ``OpenAIEmbeddingProvider`` ``base_url`` passthrough (point at any OpenAI-compatible
  local server: Ollama / LM Studio / text-embeddings-inference / vLLM)
- local-first ``auto`` selection: a mere OpenAI key no longer routes embeddings to the cloud

fastembed is an optional dependency (not in the base/dev env), so these unit tests mock it
via the module-level ``_HAS_FASTEMBED`` / ``TextEmbedding`` patch targets — mirroring how the
existing suite mocks ``_HAS_OPENAI`` / ``AsyncOpenAI`` and ``_HAS_MODEL2VEC`` / ``StaticModel``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from trace_mcp.extensions.learn.config import LearnConfig, load_config
from trace_mcp.extensions.learn.embeddings import (
    DEFAULT_FASTEMBED_MODEL,
    FASTEMBED_ALLOWLIST,
    FastEmbedEmbeddingProvider,
    Model2VecEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning, load_embeddings_cache, save_store

_EMB = "trace_mcp.extensions.learn.embeddings"


def _mock_fastembed_model(vec: tuple[float, ...] = (0.1, 0.2, 0.3)) -> MagicMock:
    """A stand-in for ``fastembed.TextEmbedding`` whose ``.embed`` yields np arrays."""
    model = MagicMock()
    model.embed.return_value = iter([np.array(vec, dtype=np.float32)])
    return model


# ── FastEmbed provider ────────────────────────────────────────────────────


class TestFastEmbedProvider:
    async def test_embed_texts_mocked(self):
        model = _mock_fastembed_model((0.1, 0.2, 0.3))
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=model):
                provider = FastEmbedEmbeddingProvider(model_name="snowflake/snowflake-arctic-embed-s")
                result = await provider.embed_texts(["hello"])
        assert len(result) == 1
        assert result[0] == pytest.approx([0.1, 0.2, 0.3], abs=1e-5)

    def test_model_name_exposed(self):
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=MagicMock()):
                provider = FastEmbedEmbeddingProvider(model_name="BAAI/bge-small-en-v1.5")
                assert provider.model_name == "BAAI/bge-small-en-v1.5"

    def test_raises_without_fastembed_package(self):
        with patch(f"{_EMB}._HAS_FASTEMBED", False):
            with pytest.raises(RuntimeError, match="fastembed"):
                FastEmbedEmbeddingProvider()

    def test_default_model_is_permissive_and_allowlisted(self):
        assert DEFAULT_FASTEMBED_MODEL == "snowflake/snowflake-arctic-embed-s"
        assert DEFAULT_FASTEMBED_MODEL in FASTEMBED_ALLOWLIST
        # Every allowlisted model must carry a permissive (Apache-2.0/MIT) license marker.
        for meta in FASTEMBED_ALLOWLIST.values():
            assert meta["license"] in ("Apache-2.0", "MIT")


# ── Selection: fastembed + local-first auto ───────────────────────────────


class TestFastEmbedSelection:
    def test_explicit_fastembed_backend(self):
        config = LearnConfig(embedding_backend="fastembed")
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=MagicMock()):
                provider = get_embedding_provider(config)
        assert isinstance(provider, FastEmbedEmbeddingProvider)

    def test_auto_prefers_fastembed_over_openai_even_with_key(self):
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto")
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=MagicMock()):
                with patch(f"{_EMB}._HAS_OPENAI", True):
                    with patch(f"{_EMB}.AsyncOpenAI"):
                        provider = get_embedding_provider(config)
        assert isinstance(provider, FastEmbedEmbeddingProvider)

    def test_auto_does_not_select_openai_even_with_key(self):
        """Local-first: a mere key must NOT route embeddings to OpenAI when no local backend exists."""
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto", strict_llm=False)
        with patch(f"{_EMB}._HAS_FASTEMBED", False):
            with patch(f"{_EMB}._HAS_MODEL2VEC", False):
                with patch(f"{_EMB}._HAS_OPENAI", True):
                    with patch(f"{_EMB}.AsyncOpenAI"):
                        provider = get_embedding_provider(config)
        assert provider is None

    def test_auto_falls_to_model2vec_when_no_fastembed(self):
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto")
        mock_instance = MagicMock()
        mock_instance.dim = 256
        with patch(f"{_EMB}._HAS_FASTEMBED", False):
            with patch(f"{_EMB}._HAS_MODEL2VEC", True):
                with patch("model2vec.StaticModel.from_pretrained", return_value=mock_instance):
                    provider = get_embedding_provider(config)
        assert isinstance(provider, Model2VecEmbeddingProvider)

    def test_fastembed_ignores_openai_default_model(self):
        """When the OpenAI default model string is left in place, fastembed uses its own default."""
        config = LearnConfig(embedding_backend="fastembed", embedding_model="text-embedding-3-small")
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=MagicMock()):
                provider = get_embedding_provider(config)
        assert provider is not None
        assert provider.model_name == DEFAULT_FASTEMBED_MODEL

    def test_fastembed_respects_allowlisted_model(self):
        config = LearnConfig(embedding_backend="fastembed", embedding_model="BAAI/bge-small-en-v1.5")
        with patch(f"{_EMB}._HAS_FASTEMBED", True):
            with patch(f"{_EMB}.TextEmbedding", return_value=MagicMock()):
                provider = get_embedding_provider(config)
        assert provider is not None
        assert provider.model_name == "BAAI/bge-small-en-v1.5"

    def test_explicit_fastembed_without_package_raises_in_strict(self):
        from trace_mcp.extensions.learn.config import LLMFallbackError

        config = LearnConfig(embedding_backend="fastembed", strict_llm=True)
        with patch(f"{_EMB}._HAS_FASTEMBED", False):
            with pytest.raises(LLMFallbackError, match="fastembed"):
                get_embedding_provider(config)


# ── OpenAI base_url passthrough (bring-your-own OpenAI-compatible endpoint) ─


class TestOpenAIBaseUrlPassthrough:
    def test_base_url_passed_to_client(self):
        # __init__ re-imports AsyncOpenAI from the openai package, so patch there.
        with patch(f"{_EMB}._HAS_OPENAI", True):
            with patch("openai.AsyncOpenAI") as MockCls:
                provider = OpenAIEmbeddingProvider(api_key="sk-test", base_url="http://localhost:11434/v1")
        assert provider.base_url == "http://localhost:11434/v1"
        assert MockCls.call_args.kwargs.get("base_url") == "http://localhost:11434/v1"

    def test_no_base_url_omits_kwarg(self):
        with patch(f"{_EMB}._HAS_OPENAI", True):
            with patch("openai.AsyncOpenAI") as MockCls:
                OpenAIEmbeddingProvider(api_key="sk-test")
        assert "base_url" not in MockCls.call_args.kwargs

    def test_config_reads_openai_base_url_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
        cfg = load_config()
        assert cfg.embedding_base_url == "http://localhost:1234/v1"

    def test_get_provider_passes_base_url(self):
        config = LearnConfig(
            openai_api_key="sk-test",
            embedding_backend="openai",
            embedding_base_url="http://localhost:11434/v1",
        )
        with patch(f"{_EMB}._HAS_OPENAI", True):
            with patch(f"{_EMB}.AsyncOpenAI"):
                provider = get_embedding_provider(config)
        assert isinstance(provider, OpenAIEmbeddingProvider)
        assert provider.base_url == "http://localhost:11434/v1"


# ── Sidecar cache: mixed-dimension safety (backend migration) ──────────────


class TestSaveCacheDimensionGuard:
    def test_mixed_dimension_rows_do_not_crash(self, tmp_path):
        """Switching backends changes vector dimension; a partially-migrated store
        (rows of differing length) must not crash the .npy cache writer — the
        off-dimension rows are treated as missing (NaN), never broadcast-errored."""
        ks = KnowledgeStore(project="mix")
        add_learning(ks, content="four-dim")
        ks.learnings[0].embedding = [0.1, 0.2, 0.3, 0.4]
        ks.learnings[0].embedding_model = "model-4d"
        add_learning(ks, content="two-dim")
        ks.learnings[1].embedding = [0.9, 0.8]  # different dimension
        ks.learnings[1].embedding_model = "model-2d"

        save_store(ks, directory=str(tmp_path))  # must not raise
        loaded = load_embeddings_cache(ks, directory=str(tmp_path))
        assert loaded is not None
        assert loaded.shape == (2, 4)  # dim taken from the first embedded row
        assert not np.isnan(loaded[0][0])
        assert np.isnan(loaded[1][0])  # off-dimension row → treated as missing

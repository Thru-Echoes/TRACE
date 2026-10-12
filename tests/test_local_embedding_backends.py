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

from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_auto_without_fastembed_falls_to_bm25_even_when_model2vec_is_installed(self):
        """Static vectors ranked below keyword search on the benchmark store, so auto never picks them."""
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto")
        with patch(f"{_EMB}._HAS_FASTEMBED", False), patch(f"{_EMB}._HAS_MODEL2VEC", True):
            assert get_embedding_provider(config) is None

    def test_model2vec_is_available_on_explicit_request(self):
        config = LearnConfig(embedding_backend="model2vec")
        with patch(f"{_EMB}._HAS_FASTEMBED", True), patch(f"{_EMB}._HAS_MODEL2VEC", True):
            provider = get_embedding_provider(config)
        assert isinstance(provider, Model2VecEmbeddingProvider)

    def test_default_fastembed_model_carries_the_query_prefix(self):
        assert DEFAULT_FASTEMBED_MODEL == "snowflake/snowflake-arctic-embed-s"
        assert FASTEMBED_ALLOWLIST[DEFAULT_FASTEMBED_MODEL]["query_prefix"] == (
            "Represent this sentence for searching relevant passages: "
        )

    def test_auto_builds_no_cloud_client_and_loads_no_model(self):
        config = LearnConfig(openai_api_key="sk-test", embedding_backend="auto")
        with (
            patch(f"{_EMB}._HAS_FASTEMBED", False),
            patch(f"{_EMB}._HAS_MODEL2VEC", True),
            patch(f"{_EMB}._HAS_OPENAI", True),
            patch(f"{_EMB}.AsyncOpenAI") as openai_cls,
            patch("model2vec.StaticModel.from_pretrained") as loader,
        ):
            assert get_embedding_provider(config) is None
        openai_cls.assert_not_called()
        loader.assert_not_called()

    async def test_explicitly_pinned_potion_model_is_the_one_loaded(self):
        inst = MagicMock()
        inst.encode.return_value = [np.array([0.1, 0.2])]
        with (
            patch(f"{_EMB}._HAS_FASTEMBED", False),
            patch(f"{_EMB}._HAS_MODEL2VEC", True),
            patch("model2vec.StaticModel.from_pretrained", return_value=inst) as loader,
        ):
            prov = get_embedding_provider(
                LearnConfig(embedding_backend="model2vec", embedding_model="minishlab/potion-base-8M")
            )
            assert prov is not None
            await prov.embed_query("q")
        loader.assert_called_once_with("minishlab/potion-base-8M")

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


# ── Query vs document encoding ─────────────────────────────────────────────

EXACT_ARCTIC_PREFIX = "Represent this sentence for searching relevant passages: "
_UNSORTED_VEC = [0.8, -0.2, 0.1]  # deliberately unsorted so a coordinate shuffle is caught


def _fake_fastembed(seen: list[list[str]]) -> MagicMock:
    def _embed(texts):
        seen.append(list(texts))
        return (np.array(_UNSORTED_VEC) for _ in texts)

    model = MagicMock()
    model.embed.side_effect = _embed
    return model


class TestQueryEmbedding:
    """Queries and documents are encoded asymmetrically where the model card requires it."""

    @pytest.mark.parametrize("model_name", sorted(FASTEMBED_ALLOWLIST))
    async def test_query_prefix_matches_model_card_and_documents_stay_bare(self, model_name: str):
        seen: list[list[str]] = []
        with patch(f"{_EMB}._HAS_FASTEMBED", True), patch(f"{_EMB}.TextEmbedding", return_value=_fake_fastembed(seen)):
            prov = FastEmbedEmbeddingProvider(model_name=model_name)
            docs = await prov.embed_texts(["a learning"])
            query = await prov.embed_query("find it")
        expected_query = EXACT_ARCTIC_PREFIX + "find it" if model_name.startswith("snowflake/") else "find it"
        assert seen == [["a learning"], [expected_query]]
        assert docs == [_UNSORTED_VEC]
        assert query == _UNSORTED_VEC

    async def test_model2vec_query_is_the_single_document_embedding(self):
        inst = MagicMock()
        inst.encode.return_value = [np.array(_UNSORTED_VEC)]
        with patch(f"{_EMB}._HAS_MODEL2VEC", True), patch("model2vec.StaticModel.from_pretrained", return_value=inst):
            prov = Model2VecEmbeddingProvider()
            assert await prov.embed_query("q") == _UNSORTED_VEC
        inst.encode.assert_called_once_with(["q"])

    async def test_openai_query_delegates_to_the_attested_document_path(self):
        """No second network call site: embed_query goes through embed_texts, which records egress."""
        with patch(f"{_EMB}._HAS_OPENAI", True), patch(f"{_EMB}.AsyncOpenAI"):
            prov = OpenAIEmbeddingProvider(api_key="sk-test")
        prov.embed_texts = AsyncMock(return_value=[_UNSORTED_VEC])  # type: ignore[method-assign]
        assert await prov.embed_query("q") == _UNSORTED_VEC
        prov.embed_texts.assert_awaited_once_with(["q"])

    async def test_helper_dispatches_to_a_provider_query_method(self):
        from trace_mcp.extensions.learn.embeddings import embed_query

        prov = MagicMock()
        prov.embed_query = AsyncMock(return_value=_UNSORTED_VEC)
        prov.embed_texts = AsyncMock(return_value=[[9.0, 9.0, 9.0]])
        assert await embed_query(prov, "find it") == _UNSORTED_VEC
        prov.embed_query.assert_awaited_once_with("find it")
        prov.embed_texts.assert_not_awaited()

    async def test_helper_falls_back_for_a_duck_typed_provider_without_embed_query(self):
        from trace_mcp.extensions.learn.embeddings import embed_query

        class DocsOnly:
            model_name = "docs-only"
            dimensions = 3

            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            async def embed_texts(self, texts: list[str]) -> list[list[float]]:
                self.calls.append(list(texts))
                return [[1.0, 2.0, 3.0] for _ in texts]

        prov = DocsOnly()
        assert await embed_query(prov, "q") == [1.0, 2.0, 3.0]
        assert prov.calls == [["q"]]

    async def test_explicit_protocol_subclass_inherits_symmetric_default(self):
        from trace_mcp.extensions.learn.embeddings import EmbeddingProvider

        class Legacy(EmbeddingProvider):
            model_name = "legacy"

            @property
            def dimensions(self) -> int:
                return 3

            async def embed_texts(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0, float(len(t))] for t in texts]

        assert await Legacy().embed_query("abc") == [1.0, 0.0, 3.0]


class TestDefaultModel2VecModel:
    def test_default_is_retrieval_tuned(self):
        from trace_mcp.extensions.learn.embeddings import DEFAULT_MODEL2VEC_MODEL

        assert DEFAULT_MODEL2VEC_MODEL == "minishlab/potion-retrieval-32M"
        with patch(f"{_EMB}._HAS_MODEL2VEC", True):
            assert Model2VecEmbeddingProvider().model_name == DEFAULT_MODEL2VEC_MODEL

    def test_explicit_selection_uses_default_unless_a_potion_model_is_configured(self):
        from trace_mcp.extensions.learn.embeddings import DEFAULT_MODEL2VEC_MODEL

        with patch(f"{_EMB}._HAS_FASTEMBED", False), patch(f"{_EMB}._HAS_MODEL2VEC", True):
            default = get_embedding_provider(LearnConfig(embedding_backend="model2vec"))
            assert default is not None and default.model_name == DEFAULT_MODEL2VEC_MODEL
            pinned = get_embedding_provider(
                LearnConfig(embedding_backend="model2vec", embedding_model="minishlab/potion-base-8M")
            )
            assert pinned is not None and pinned.model_name == "minishlab/potion-base-8M"
            assert get_embedding_provider(LearnConfig(embedding_backend="auto")) is None

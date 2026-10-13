"""Recall resilience: keyword fallback when the query embedding fails, the per-model
similarity gate behind `matched`, and the durable fastembed model cache.

Why these exist (measured 2026-10-13 on fifteen real stores): a provider that cannot embed
the query made `recall_ranked` raise and the tool answer "Failed to recall learnings" in
either mode; the relative floor flagged 98 to 100% of returned items `matched` on 200
off-topic queries; and fastembed kept its model files in the temporary directory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trace_mcp.extensions.learn import embeddings, recall_notice
from trace_mcp.extensions.learn.config import ApiKeyRejectedError, LearnConfig, LLMFallbackError, load_config
from trace_mcp.extensions.learn.matching import (
    BM25_GATE,
    GATE_MIN_STORE,
    SIMILARITY_GATES,
    UNMATCHED_CAP,
    BM25Backend,
    DecayParams,
    EmbeddingBackend,
    get_default_backend,
    recall_ranked,
    resolve_gate,
)
from trace_mcp.extensions.learn.models import Learning

_EMB = "trace_mcp.extensions.learn.embeddings"
ARCTIC = "snowflake/snowflake-arctic-embed-s"
DECAY_OFF = DecayParams(enabled=False)


def _provider(query_vec: list[float], model_name: str = "test-model", fail: BaseException | list | None = None):
    prov = AsyncMock()
    prov.model_name = model_name
    prov.dimensions = len(query_vec)
    prov.embed_texts = AsyncMock(return_value=[query_vec])
    if fail is None:
        prov.embed_query = AsyncMock(return_value=query_vec)
    else:
        prov.embed_query = AsyncMock(side_effect=fail)
    return prov


def _lrn(i: int, content: str, vec: list[float] | None) -> Learning:
    return Learning(id=f"lrn_{i:03d}", content=content, embedding=vec, embedding_model="test-model" if vec else None)


def _store(n: int = 12) -> list[Learning]:
    """lrn_000 is lexically on-topic, lrn_001 semantically on-topic, the rest cooking filler."""
    out = [
        _lrn(0, "Set TRACE_EMBEDDING_MODEL to pick the embedding model for recall", [0.6, 0.8, 0.0, 0.0]),
        _lrn(1, "Pick the vector model used when searching past notes", [0.95, 0.3, 0.0, 0.0]),
    ]
    for i in range(2, n):
        out.append(_lrn(i, f"Unrelated cooking note number {i} about pasta and basil", [0.0, 0.0, 1.0, 0.0]))
    return out


# ── 1. query-time embedding failure ──────────────────────────────────────


class TestQueryEmbeddingFallback:
    async def test_permissive_mode_falls_back_to_keywords_and_says_so(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0, 0, 0], fail=ConnectionError("boom")), strict=False)
        out = await recall_ranked(
            _store(), "embedding model for recall", backend=backend, limit=5, decay_config=DECAY_OFF
        )
        assert out.results, "a failed query embedding must still produce a keyword ranking"
        assert out.results[0]["learning"]["id"] == "lrn_000"  # the lexical hit leads under keyword ranking
        assert out.results[0]["matched"] is True
        assert out.summary.degraded is not None and "ConnectionError" in out.summary.degraded
        assert out.summary.backend.startswith("bm25")
        # no vector evidence is used: the cooking filler shares no words and cannot be matched
        assert not any(r["matched"] for r in out.results if r["learning"]["id"] not in {"lrn_000", "lrn_001"})

    async def test_strict_mode_raises_instead_of_degrading(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0, 0, 0], fail=ConnectionError("boom")), strict=True)
        with pytest.raises(LLMFallbackError):
            await recall_ranked(_store(), "embedding model for recall", backend=backend, decay_config=DECAY_OFF)

    async def test_refused_key_propagates_even_in_permissive_mode(self):
        backend = EmbeddingBackend(
            provider=_provider([1.0, 0, 0, 0], fail=ApiKeyRejectedError("refused")), strict=False
        )
        with pytest.raises(ApiKeyRejectedError):
            await recall_ranked(_store(), "embedding model for recall", backend=backend, decay_config=DECAY_OFF)

    async def test_fallback_is_per_call_not_sticky(self):
        prov = _provider([1.0, 0, 0, 0], fail=[ConnectionError("once"), [1.0, 0, 0, 0]])
        backend = EmbeddingBackend(provider=prov, strict=False)
        first = await recall_ranked(_store(), "embedding model for recall", backend=backend, decay_config=DECAY_OFF)
        second = await recall_ranked(_store(), "embedding model for recall", backend=backend, decay_config=DECAY_OFF)
        assert first.summary.degraded is not None
        assert second.summary.degraded is None
        assert second.summary.backend.startswith("hybrid")

    def test_default_backend_carries_strict_mode(self):
        on = get_default_backend(LearnConfig(strict_llm=True), provider=_provider([1.0, 0, 0, 0]))
        off = get_default_backend(LearnConfig(strict_llm=False), provider=_provider([1.0, 0, 0, 0]))
        assert isinstance(on, EmbeddingBackend) and on.strict is True
        assert isinstance(off, EmbeddingBackend) and off.strict is False

    async def test_notice_names_the_degradation(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0, 0, 0], fail=ConnectionError("boom")), strict=False)
        out = await recall_ranked(_store(), "embedding model for recall", backend=backend, decay_config=DECAY_OFF)
        notice = recall_notice(out.summary, original_query="embedding model for recall")
        assert notice is not None and "keyword" in notice and "ConnectionError" in notice


# ── 2. per-model similarity gate behind `matched` ────────────────────────


class TestSimilarityGate:
    async def test_off_topic_query_is_gated_for_a_calibrated_model(self):
        backend = EmbeddingBackend(provider=_provider([0, 0, 0, 1.0], model_name=ARCTIC))
        out = await recall_ranked(
            _store(), "braise short ribs for a dinner party", backend=backend, limit=10, decay_config=DECAY_OFF
        )
        assert out.summary.gated is True
        assert out.summary.gate == SIMILARITY_GATES[ARCTIC]
        assert out.summary.matched == 0
        assert out.results and all(r["matched"] is False for r in out.results)
        assert len(out.results) <= UNMATCHED_CAP

    async def test_on_topic_query_passes_the_gate(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0, 0, 0], model_name=ARCTIC))
        out = await recall_ranked(
            _store(), "embedding model for recall", backend=backend, limit=10, decay_config=DECAY_OFF
        )
        assert out.summary.gated is False
        assert {r["learning"]["id"] for r in out.results if r["matched"]} >= {"lrn_000", "lrn_001"}

    async def test_strong_lexical_hit_overrides_a_low_cosine(self):
        """An identifier-shaped query can embed poorly yet match a learning word for word."""
        backend = EmbeddingBackend(provider=_provider([0, 0, 0, 1.0], model_name=ARCTIC))
        query = "Set TRACE_EMBEDDING_MODEL to pick the embedding model for recall"
        out = await recall_ranked(_store(), query, backend=backend, limit=10, decay_config=DECAY_OFF)
        assert out.summary.gated is False
        assert out.results[0]["learning"]["id"] == "lrn_000" and out.results[0]["matched"] is True

    async def test_unknown_model_has_no_gate_and_keeps_the_old_behaviour(self):
        backend = EmbeddingBackend(provider=_provider([0, 0, 0, 1.0], model_name="test-model"))
        out = await recall_ranked(
            _store(), "braise short ribs for a dinner party", backend=backend, limit=10, decay_config=DECAY_OFF
        )
        assert out.summary.gated is False and out.summary.gate is None

    async def test_explicit_gate_setting_off_and_numeric(self):
        prov = _provider([1.0, 0, 0, 0], model_name=ARCTIC)  # cosine 0.95 to lrn_001, no shared words
        store = _store()
        off = await recall_ranked(
            store, "zzz qqq", backend=EmbeddingBackend(provider=prov), gate="off", decay_config=DECAY_OFF
        )
        high = await recall_ranked(
            store, "zzz qqq", backend=EmbeddingBackend(provider=prov), gate=0.99, decay_config=DECAY_OFF
        )
        low = await recall_ranked(
            store, "zzz qqq", backend=EmbeddingBackend(provider=prov), gate=0.5, decay_config=DECAY_OFF
        )
        assert off.summary.gated is False and off.summary.gate is None
        assert high.summary.gated is True and high.summary.gate == 0.99
        assert low.summary.gated is False and low.summary.gate == 0.5

    def test_resolve_gate_table(self):
        assert (
            resolve_gate(EmbeddingBackend(provider=_provider([1.0], model_name=ARCTIC)), "auto")
            == SIMILARITY_GATES[ARCTIC]
        )
        assert resolve_gate(EmbeddingBackend(provider=_provider([1.0], model_name="someone/else")), "auto") is None
        assert resolve_gate(EmbeddingBackend(provider=_provider([1.0], model_name=ARCTIC)), "off") is None
        assert resolve_gate(EmbeddingBackend(provider=_provider([1.0], model_name=ARCTIC)), "0.4") == 0.4
        assert resolve_gate(BM25Backend(), "auto") == BM25_GATE
        with pytest.raises(ValueError):
            resolve_gate(BM25Backend(), "loud")

    async def test_keyword_backend_gates_off_topic_queries_on_a_calibrated_size_store(self):
        store = _store(GATE_MIN_STORE + 10)
        gated = await recall_ranked(
            store, "braise short ribs for a dinner party", backend=BM25Backend(), decay_config=DECAY_OFF
        )
        kept = await recall_ranked(
            store,
            "Set TRACE_EMBEDDING_MODEL to pick the embedding model for recall",
            backend=BM25Backend(),
            decay_config=DECAY_OFF,
        )
        assert gated.summary.gated is True and gated.summary.matched == 0
        assert kept.summary.gated is False and kept.summary.matched >= 1

    async def test_keyword_gate_is_not_applied_below_the_calibrated_store_size(self):
        """Normalised BM25 depends on the store's IDF scale; on a dozen learnings the gate would misfire."""
        out = await recall_ranked(
            _store(12), "braise short ribs for a dinner party", backend=BM25Backend(), decay_config=DECAY_OFF
        )
        assert out.summary.gated is False and out.summary.gate is None

    async def test_small_store_returns_everything_but_nothing_matched_when_gated(self):
        store = _store(5)
        backend = EmbeddingBackend(provider=_provider([0, 0, 0, 1.0], model_name=ARCTIC))
        out = await recall_ranked(
            store, "braise short ribs for a dinner party", backend=backend, limit=10, decay_config=DECAY_OFF
        )
        assert len(out.results) == 5
        assert out.summary.gated is True and all(r["matched"] is False for r in out.results)

    async def test_notice_says_nothing_resembles_the_query(self):
        backend = EmbeddingBackend(provider=_provider([0, 0, 0, 1.0], model_name=ARCTIC))
        out = await recall_ranked(
            _store(), "braise short ribs for a dinner party", backend=backend, decay_config=DECAY_OFF
        )
        notice = recall_notice(out.summary, original_query="braise short ribs for a dinner party")
        assert notice is not None and "resembles" in notice


# ── 3. durable fastembed model cache ─────────────────────────────────────


class TestDurableModelCache:
    def test_default_is_under_the_trace_home(self, monkeypatch):
        monkeypatch.delenv("TRACE_MODEL_CACHE_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        assert embeddings.fastembed_cache_dir() == Path.home() / ".trace" / "models"

    def test_env_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fe"))
        monkeypatch.delenv("TRACE_MODEL_CACHE_DIR", raising=False)
        assert embeddings.fastembed_cache_dir() == tmp_path / "fe"
        monkeypatch.setenv("TRACE_MODEL_CACHE_DIR", str(tmp_path / "trace"))
        assert embeddings.fastembed_cache_dir() == tmp_path / "trace"

    async def test_model_is_loaded_into_that_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRACE_MODEL_CACHE_DIR", str(tmp_path / "models"))
        model = MagicMock()
        model.embed.return_value = iter([__import__("numpy").array([0.1, 0.2], dtype="float32")])
        with patch(f"{_EMB}._HAS_FASTEMBED", True), patch(f"{_EMB}.TextEmbedding", return_value=model) as ctor:
            provider = embeddings.FastEmbedEmbeddingProvider(model_name=ARCTIC)
            await provider.embed_texts(["hello"])
        ctor.assert_called_once_with(model_name=ARCTIC, cache_dir=str(tmp_path / "models"))


# ── config plumbing ──────────────────────────────────────────────────────


class TestGateSetting:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch, tmp_path):
        from trace_mcp.extensions.learn import config as _cfg

        for var in ("OPENAI_API_KEY", "TRACE_LLM_ENABLED", "TRACE_LOCAL_ONLY", "TRACE_RECALL_GATE"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(_cfg, "_TRACE_ENV_PATH", Path("/nonexistent/.env"))
        monkeypatch.chdir(tmp_path)

    def test_default_is_auto_and_env_overrides(self, monkeypatch):
        assert load_config().recall_gate == "auto"
        monkeypatch.setenv("TRACE_RECALL_GATE", "off")
        assert load_config().recall_gate == "off"

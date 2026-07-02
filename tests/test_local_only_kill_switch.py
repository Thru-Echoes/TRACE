"""Tests for the unified TRACE_LOCAL_ONLY kill switch.

A single opt-in flag must force ALL three trace-learn egress paths off-machine:
- embeddings   (``embedding_backend`` must not resolve to ``openai``)
- LLM matching (gated by ``llm_enabled``)
- LLM extraction (gated by ``llm_enabled``)

This closes the "off-switch trap" where ``TRACE_LLM_ENABLED=false`` alone still
egressed content via the embedding path (and ``TRACE_EMBEDDING_BACKEND=none``
alone still egressed via LLM matching/extraction). The switch is enforced at
config load so every downstream reader honors it with no per-site logic.
"""

from __future__ import annotations

from unittest.mock import patch

from trace_mcp.extensions.learn.config import LearnConfig, load_config
from trace_mcp.extensions.learn.embeddings import OpenAIEmbeddingProvider, get_embedding_provider

_EMB = "trace_mcp.extensions.learn.embeddings"


class TestLocalOnlyKillSwitch:
    def test_env_sets_local_only_and_disables_cloud(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "openai")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "true")
        monkeypatch.setenv("TRACE_LOCAL_ONLY", "1")
        cfg = load_config()
        assert cfg.local_only is True
        assert cfg.llm_enabled is False  # LLM matching + extraction forced off
        assert cfg.embedding_backend != "openai"  # embedding egress forced off

    def test_local_only_off_by_default(self, monkeypatch):
        monkeypatch.delenv("TRACE_LOCAL_ONLY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        cfg = load_config()
        assert cfg.local_only is False

    def test_local_only_provider_never_openai_even_if_backend_openai(self):
        """A directly-constructed local-only config must never yield the cloud provider."""
        cfg = LearnConfig(openai_api_key="sk-x", embedding_backend="openai", local_only=True)
        with patch(f"{_EMB}._HAS_OPENAI", True):
            with patch(f"{_EMB}.AsyncOpenAI"):
                with patch(f"{_EMB}._HAS_FASTEMBED", False):
                    with patch(f"{_EMB}._HAS_MODEL2VEC", False):
                        provider = get_embedding_provider(cfg)
        assert not isinstance(provider, OpenAIEmbeddingProvider)

    def test_local_only_still_allows_local_backend(self):
        """Local-only forbids OpenAI but still uses an installed local backend."""
        cfg = LearnConfig(openai_api_key="sk-x", embedding_backend="openai", local_only=True)
        from unittest.mock import MagicMock

        from trace_mcp.extensions.learn.embeddings import Model2VecEmbeddingProvider

        inst = MagicMock()
        inst.dim = 256
        with patch(f"{_EMB}._HAS_FASTEMBED", False):
            with patch(f"{_EMB}._HAS_MODEL2VEC", True):
                with patch("model2vec.StaticModel.from_pretrained", return_value=inst):
                    provider = get_embedding_provider(cfg)
        assert isinstance(provider, Model2VecEmbeddingProvider)

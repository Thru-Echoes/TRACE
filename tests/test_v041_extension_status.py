"""User-facing TRACE extension-status banner.

Brief, obvious, consistent notification of which learning-extension mode
is active (none / LLM embeddings / local-no-LLM / no-embeddings), plus a
short upgrade hint. Surfaced in the trace_start_session response. The
probe must be fail-safe — it must never break session start.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trace_mcp.extension_status import get_extension_status
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import session_tools


class _OpenAIEmbeddingProvider:  # name-matched stub
    pass


class _Model2VecEmbeddingProvider:  # name-matched stub
    pass


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    import trace_mcp.extensions.learn.embeddings as e

    monkeypatch.setattr(e, "get_embedding_provider", lambda *a, **k: provider)


def test_status_openai_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _OpenAIEmbeddingProvider()
    prov.__class__.__name__ = "OpenAIEmbeddingProvider"
    _patch_provider(monkeypatch, prov)
    s = get_extension_status()
    assert "TRACE active" in s
    assert "LLM" in s and "OpenAI" in s


def test_status_model2vec_local_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _Model2VecEmbeddingProvider()
    prov.__class__.__name__ = "Model2VecEmbeddingProvider"
    _patch_provider(monkeypatch, prov)
    s = get_extension_status()
    assert "TRACE active" in s
    assert "local embeddings" in s and "no LLM" in s


def test_status_no_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider(monkeypatch, None)
    s = get_extension_status()
    assert "TRACE active" in s
    assert "no embeddings" in s or "keyword recall only" in s


def test_status_no_learning_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the learn extension is unimportable, say so explicitly + hint."""
    for name in (
        "trace_mcp.extensions.learn.embeddings",
        "trace_mcp.extensions.learn.config",
        "trace_mcp.extensions.learn",
    ):
        monkeypatch.setitem(sys.modules, name, None)
    s = get_extension_status()
    assert "TRACE active" in s
    assert "no learning extension" in s.lower()


def test_status_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-safe contract: even if the probe blows up, return a string."""
    import trace_mcp.extensions.learn.embeddings as e

    def _boom(*a: object, **k: object) -> object:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(e, "get_embedding_provider", _boom)
    s = get_extension_status()
    assert isinstance(s, str) and "TRACE active" in s


async def test_start_session_response_includes_status(
    tmp_path: Path,
) -> None:
    storage = JsonFileStorage(directory=str(tmp_path))
    active: dict[str, Session] = {}
    result = await session_tools.start_session(storage, active, project="status-test", description="d")
    assert "TRACE active" in result
    # Original content still present (additive, not replacing)
    assert "TRACE audit logging is now active." in result
    assert "Session: " in result

"""Acceptance check with a real static model (opt-in: needs model2vec and a model download).

Run with TRACE_REAL_MODEL_TESTS=1. Mirrors the failure that motivated hybrid ranking: a chain
of sessions whose own learnings were never recalled under the local embedding backend.
"""

from __future__ import annotations

import math
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_REAL_MODEL_TESTS") != "1", reason="set TRACE_REAL_MODEL_TESTS=1 to run with a real model"
)

from trace_mcp.extensions.learn.embeddings import EmbeddingProvider, Model2VecEmbeddingProvider  # noqa: E402
from trace_mcp.extensions.learn.matching import EmbeddingBackend, recall_ranked  # noqa: E402
from trace_mcp.extensions.learn.models import Learning  # noqa: E402

CHAIN = [
    "Scope 2 totals on page 3 are stated in tCO2e but the units row is missing; read the digest footnote.",
    "The assurance statement sits after the appendix, not with the emissions table.",
    "Page breaks split the scope 1/scope 2 table; the restated base year is on the second half.",
]
FILLER = [f"Unrelated cooking note {i} about pasta, basil and tomato sauce timing." for i in range(9)]
SMALL_MODEL = "minishlab/potion-base-8M"  # the 30 MB model keeps the download small


async def _store(texts: list[str], provider: EmbeddingProvider) -> list[Learning]:
    vecs = await provider.embed_texts(texts)
    return [
        Learning(id=f"lrn_{i:03d}", content=t, embedding=v, embedding_model=provider.model_name)
        for i, (t, v) in enumerate(zip(texts, vecs, strict=True))
    ]


async def test_chain_second_session_sees_first_sessions_notes():
    pytest.importorskip("model2vec")
    provider = Model2VecEmbeddingProvider(SMALL_MODEL)
    backend = EmbeddingBackend(provider=provider)
    out = await recall_ranked(await _store(CHAIN, provider), "task A norrell photonics", backend=backend, limit=5)
    assert {r["learning"]["id"] for r in out.results} == {"lrn_000", "lrn_001", "lrn_002"}  # small store: all back
    assert out.summary.small_store and out.summary.returned == 3
    assert all(r["matched"] is False for r in out.results)  # no lexical or tag signal: honestly flagged weak


async def test_on_topic_query_ranks_chain_notes_first_in_a_larger_store():
    pytest.importorskip("model2vec")
    provider = Model2VecEmbeddingProvider(SMALL_MODEL)
    backend = EmbeddingBackend(provider=provider)
    store = await _store(CHAIN + FILLER, provider)
    out = await recall_ranked(store, "emissions report scope totals layout gotcha", backend=backend, limit=3)
    assert len(out.results) == 3 and out.summary.matched >= 1
    assert out.results[0]["learning"]["id"] in {"lrn_000", "lrn_001", "lrn_002"} and out.results[0]["matched"]
    assert all(r["learning"]["id"] in {"lrn_000", "lrn_001", "lrn_002"} for r in out.results if r["matched"])


async def test_explicit_model2vec_default_model_loads_and_embeds_queries():
    pytest.importorskip("model2vec")
    provider = Model2VecEmbeddingProvider()  # the explicit tier's default
    vec = await provider.embed_query("scope totals")
    assert provider.model_name == "minishlab/potion-retrieval-32M" and len(vec) == 512
    assert all(math.isfinite(v) for v in vec) and any(v != 0.0 for v in vec)
    assert vec != await provider.embed_query("pasta and basil")


async def test_auto_selected_fastembed_default_ranks_chain_notes_first():
    """The configuration users actually receive: auto -> fastembed (arctic-s with its query prefix)."""
    pytest.importorskip("fastembed")
    from trace_mcp.extensions.learn.config import LearnConfig
    from trace_mcp.extensions.learn.embeddings import FastEmbedEmbeddingProvider, get_embedding_provider

    provider = get_embedding_provider(LearnConfig(embedding_backend="auto"))
    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.model_name == "snowflake/snowflake-arctic-embed-s"
    backend = EmbeddingBackend(provider=provider)
    store = await _store(CHAIN + FILLER, provider)
    out = await recall_ranked(store, "emissions report scope totals layout gotcha", backend=backend, limit=3)
    assert len(out.results) == 3
    assert {r["learning"]["id"] for r in out.results} <= {"lrn_000", "lrn_001", "lrn_002"}
    assert all(r["matched"] for r in out.results)


async def test_default_fastembed_through_registration_and_tools(tmp_path, monkeypatch):
    """The configuration users receive, end to end: register with auto, add, recall through the tools."""
    pytest.importorskip("fastembed")
    import json

    from _mcp_helpers import call_tool_json
    from mcp.server.mcpserver import MCPServer

    import trace_mcp.project_identity as pident
    from trace_mcp.extensions.learn import register
    from trace_mcp.storage.json_file import JsonFileStorage

    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "auto")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
    for var in ("TRACE_PROJECT", "OPENAI_API_KEY", "TRACE_LOCAL_ONLY", "TRACE_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    pident._reset_registry_cache()
    mcp = MCPServer("learn-real-default")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))
    try:
        for text in CHAIN + FILLER:
            await call_tool_json(mcp, "trace_learn_add", {"project": "proj-real", "content": text})
        result = await call_tool_json(
            mcp,
            "trace_learn_recall",
            {"project": "proj-real", "context": "emissions report scope totals layout gotcha", "limit": 3},
        )
    finally:
        pident._reset_registry_cache()
    assert result["backend"] == "hybrid:embedding:snowflake/snowflake-arctic-embed-s+bm25", result
    assert len(result["results"]) == 3 and result["matched"] >= 3
    contents = [r["learning"]["content"] for r in result["results"]]
    assert all(c in CHAIN for c in contents), contents
    stored = json.loads((tmp_path / "knowledge" / "proj-real.json").read_text())
    assert all(lrn["embedding_model"] == "snowflake/snowflake-arctic-embed-s" for lrn in stored["learnings"])

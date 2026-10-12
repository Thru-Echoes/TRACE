"""Lazy (re-)embedding must not hold the knowledge-store lock while the model runs.

A transformer tier embeds a few hundred learnings in tens of seconds on first use; the
project lock times out after 15 s by default. Here the embedding is blocked on a gate while
a concurrent add to the same project must succeed under a short lock timeout; releasing the
gate lets the recall finish and merge its vectors onto the learnings that still match.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from _mcp_helpers import call_tool_json
from mcp.server.mcpserver import MCPServer

import trace_mcp.project_identity as pident
from trace_mcp.extensions.learn import embeddings as emb
from trace_mcp.extensions.learn import register, store
from trace_mcp.storage.json_file import JsonFileStorage


@pytest.fixture()
def gated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not emb._HAS_MODEL2VEC:
        pytest.skip("model2vec not installed")
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "model2vec")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
    monkeypatch.setenv("TRACE_STRICT_LLM", "false")
    monkeypatch.setenv("TRACE_LOCK_TIMEOUT", "1")
    for var in ("TRACE_PROJECT", "OPENAI_API_KEY", "TRACE_LOCAL_ONLY", "TRACE_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)

    class _Fake:
        dim = 4

        def encode(self, texts: list[str]) -> list[Any]:
            return [np.array([1.0, 0.0, 0.0, float(len(t) % 3)]) for t in texts]

    import model2vec

    monkeypatch.setattr(model2vec.StaticModel, "from_pretrained", MagicMock(return_value=_Fake()))

    gate = asyncio.Event()
    entered = asyncio.Event()
    real_embed = emb.Model2VecEmbeddingProvider.embed_texts

    async def gated_embed(self, texts: list[str]) -> list[list[float]]:
        if len(texts) >= 2:  # the backfill of several stale learnings, not a single add or a query
            entered.set()
            await gate.wait()
        return await real_embed(self, texts)

    monkeypatch.setattr(emb.Model2VecEmbeddingProvider, "embed_texts", gated_embed)
    pident._reset_registry_cache()
    mcp = MCPServer("learn-lock-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))
    yield mcp, gate, entered
    pident._reset_registry_cache()


async def test_concurrent_add_succeeds_while_recall_embeds_and_vectors_merge_afterwards(gated_env) -> None:
    mcp, gate, entered = gated_env
    ks = store.load_store("proj-l")
    for i in range(3):  # stale: no vectors yet
        store.add_learning(ks, content=f"scope two totals on page {i}", category="gotcha")
    store.save_store(ks)

    recall = asyncio.create_task(
        call_tool_json(mcp, "trace_learn_recall", {"project": "proj-l", "context": "scope totals"})
    )
    await asyncio.wait_for(entered.wait(), timeout=5)  # the backfill is running, lock released

    added = await asyncio.wait_for(
        call_tool_json(mcp, "trace_learn_add", {"project": "proj-l", "content": "assurance statement location"}),
        timeout=5,
    )
    assert "added" in added, added  # would be a lock-timeout error if recall held the lock

    gate.set()
    result = await asyncio.wait_for(recall, timeout=10)
    assert "error" not in result, result

    again = store.load_store("proj-l")
    assert len(again.learnings) == 4
    by_content = {lrn.content: lrn for lrn in again.learnings}
    for i in range(3):
        lrn = by_content[f"scope two totals on page {i}"]
        assert (
            lrn.embedding is not None and len(lrn.embedding) == 4 and lrn.embedding_model == emb.DEFAULT_MODEL2VEC_MODEL
        )
    assert by_content["assurance statement location"].embedding is not None  # the add embedded its own learning


async def test_edited_learning_is_not_given_a_stale_vector(gated_env) -> None:
    """Content changed between snapshot and merge: no stale vector is attached, and the next recall backfills it."""
    mcp, gate, entered = gated_env
    ks = store.load_store("proj-m")
    for i in range(3):
        store.add_learning(ks, content=f"note {i} about scope", category="gotcha")
    store.save_store(ks)

    recall = asyncio.create_task(call_tool_json(mcp, "trace_learn_recall", {"project": "proj-m", "context": "scope"}))
    await asyncio.wait_for(entered.wait(), timeout=5)
    with store.project_lock("proj-m"):
        live = store.load_store("proj-m")
        live.learnings[0].content = "note 0 rewritten while the embedding ran"
        store.save_store(live)
    gate.set()
    result = await asyncio.wait_for(recall, timeout=10)
    assert "error" not in result, result

    again = store.load_store("proj-m")
    edited = next(lrn for lrn in again.learnings if lrn.content.startswith("note 0 rewritten"))
    assert edited.embedding is None  # nothing is embedded under the lock; the stale vector was not attached
    others = [lrn for lrn in again.learnings if lrn is not edited]
    assert all(lrn.embedding is not None for lrn in others)

    gate.set()  # stays open: the backfill of the single residue must not block
    second = await asyncio.wait_for(
        call_tool_json(mcp, "trace_learn_recall", {"project": "proj-m", "context": "scope"}), 10
    )
    assert "error" not in second, second
    final = store.load_store("proj-m")
    edited = next(lrn for lrn in final.learnings if lrn.content.startswith("note 0 rewritten"))
    assert edited.embedding is not None and edited.embedding_model == emb.DEFAULT_MODEL2VEC_MODEL
    # The fake encodes len(text) % 3 into the last coordinate, so a vector for the OLD text would differ.
    assert edited.embedding[3] == float(len(edited.content) % 3)

"""A learning embedded by a previous default model is re-embedded on its next recall.

The default static model changed from potion-base-8M (256-dim) to potion-retrieval-32M
(512-dim). Stores written under the old model must migrate lazily: recall notices the
model-name mismatch, re-embeds through the active provider, and persists the new vector
and model name. Exercised end to end through the registered tool, with model2vec's
loader replaced by a fake (the real one downloads weights).
"""

from __future__ import annotations

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

FAKE_DIM = 4


@pytest.fixture()
def model2vec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not emb._HAS_MODEL2VEC:
        pytest.skip("model2vec not installed")
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "model2vec")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
    monkeypatch.setenv("TRACE_STRICT_LLM", "false")
    for var in ("TRACE_PROJECT", "OPENAI_API_KEY", "TRACE_LOCAL_ONLY", "TRACE_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)

    loaded = MagicMock(name="from_pretrained")

    class _Fake:
        dim = FAKE_DIM

        def encode(self, texts: list[str]) -> list[Any]:
            return [np.array([1.0, 0.0, 0.0, float(len(t) % 3)]) for t in texts]

    loaded.return_value = _Fake()
    import model2vec

    monkeypatch.setattr(model2vec.StaticModel, "from_pretrained", loaded)
    pident._reset_registry_cache()
    mcp = MCPServer("learn-reembed-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))
    yield mcp, loaded
    pident._reset_registry_cache()


async def test_recall_reembeds_learnings_from_the_old_default_model(model2vec_env) -> None:
    mcp, loaded = model2vec_env
    ks = store.load_store("proj-m")
    lrn = store.add_learning(ks, content="scope two totals on page three", category="gotcha")
    lrn.embedding = [0.1] * 256
    lrn.embedding_model = "minishlab/potion-base-8M"
    store.save_store(ks)
    assert loaded.call_count == 0  # nothing loaded at registration or on store writes

    result = await call_tool_json(mcp, "trace_learn_recall", {"project": "proj-m", "context": "scope totals"})

    assert "error" not in result, result
    assert emb.DEFAULT_MODEL2VEC_MODEL in result["backend"]
    # The advertised model is the one actually loaded, and it is loaded once: the extension and
    # its matching backend share one provider instead of each loading their own copy.
    loaded.assert_called_once_with(emb.DEFAULT_MODEL2VEC_MODEL)
    again = store.load_store("proj-m").learnings[0]
    assert again.embedding_model == emb.DEFAULT_MODEL2VEC_MODEL
    assert again.embedding is not None and len(again.embedding) == FAKE_DIM


async def test_recall_leaves_current_model_vectors_alone(model2vec_env) -> None:
    mcp, loaded = model2vec_env
    ks = store.load_store("proj-n")
    lrn = store.add_learning(ks, content="assurance statement sits after the appendix", category="gotcha")
    lrn.embedding = [0.5, 0.5, 0.0, 0.0]
    lrn.embedding_model = emb.DEFAULT_MODEL2VEC_MODEL
    store.save_store(ks)

    await call_tool_json(mcp, "trace_learn_recall", {"project": "proj-n", "context": "assurance"})

    again = store.load_store("proj-n").learnings[0]
    assert again.embedding == [0.5, 0.5, 0.0, 0.0]  # not re-embedded
    assert loaded.call_count == 1  # loaded once, for the query only

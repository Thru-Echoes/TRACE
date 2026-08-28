"""Behavior of the learn tools against a store whose learning ids are aliased.

INV-12 refuses WRITES to such a store while leaving READS working, so the store
stays recoverable, exportable, and searchable until
``trace-mcp identity repair-ids`` renumbers it. Two things make that split real
rather than aspirational, and both are pinned here:

* Recall is not a pure read — it persists recall counts and any embeddings it
  computed lazily. That bookkeeping must degrade to "not persisted" rather than
  failing the recall, or the guarantee is inverted for exactly the stores the
  guard exists to protect.
* The refusal composes a message naming the repair command. A bare
  ``except Exception`` in the tool would discard it and hand back a generic
  failure, so the first person to meet the guard would have no idea a fix exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

import trace_mcp.project_identity as pident
from trace_mcp.extensions.learn import register
from trace_mcp.storage.json_file import JsonFileStorage

PROJECT = "aliased-proj"

# Two learnings share lrn_002 — the live shape this guard was written for.
ALIASED_STORE: dict[str, Any] = {
    "project": PROJECT,
    "learnings": [
        {"id": "lrn_001", "content": "peregrine falcon telemetry sentinel", "category": "learning"},
        {"id": "lrn_002", "content": "kept first occurrence about falcons", "category": "learning"},
        {"id": "lrn_002", "content": "later occurrence about falcons", "category": "gotcha"},
    ],
}


@pytest.fixture()
def learn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated dirs and an offline keyword backend, with the aliased store planted."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / f"{PROJECT}.json").write_text(json.dumps(ALIASED_STORE, indent=2), encoding="utf-8")

    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "none")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
    monkeypatch.setenv("TRACE_STRICT_LLM", "false")
    for var in ("TRACE_PROJECT", "OPENAI_API_KEY", "TRACE_LOCAL_ONLY"):
        monkeypatch.delenv(var, raising=False)
    pident._reset_registry_cache()
    mcp = FastMCP("learn-aliased-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))
    yield mcp, knowledge
    pident._reset_registry_cache()


async def _call(mcp: FastMCP, tool: str, args: dict[str, Any]) -> dict:
    out = await mcp.call_tool(tool, args)
    if isinstance(out, tuple):
        out = out[0]
    return json.loads(out[0].text)  # type: ignore[union-attr]


class TestReadsStayUsable:
    async def test_recall_returns_hits_and_warns_instead_of_failing(self, learn_env) -> None:
        mcp, _ = learn_env
        payload = await _call(mcp, "trace_learn_recall", {"context": "falcon telemetry", "project": PROJECT})

        assert "error" not in payload, f"recall must not fail on an aliased store: {payload}"
        assert payload["total"] >= 1, "recall returned no hits, so the read guarantee is not real"
        warnings = " ".join(payload.get("warnings", []))
        assert "repair-ids" in warnings, "the operator is never told why bookkeeping was not persisted"

    async def test_recall_does_not_rewrite_the_aliased_store(self, learn_env) -> None:
        """Bookkeeping is skipped, not forced through — the file is left as it was."""
        mcp, knowledge = learn_env
        before = (knowledge / f"{PROJECT}.json").read_bytes()
        await _call(mcp, "trace_learn_recall", {"context": "falcon telemetry", "project": PROJECT})
        assert (knowledge / f"{PROJECT}.json").read_bytes() == before

    async def test_list_still_enumerates_every_learning(self, learn_env) -> None:
        mcp, _ = learn_env
        payload = await _call(mcp, "trace_learn_list", {"project": PROJECT})
        assert "error" not in payload
        assert payload["total"] == 3


class TestWritesRefuseWithGuidance:
    async def test_add_names_the_repair_command(self, learn_env) -> None:
        mcp, _ = learn_env
        payload = await _call(mcp, "trace_learn_add", {"content": "new insight", "project": PROJECT})
        assert payload.get("error") == "duplicate_learning_ids", payload
        assert "repair-ids" in payload.get("detail", "")

    async def test_forget_names_the_repair_command(self, learn_env) -> None:
        """`forget` is the natural reaction to a bad learning, so it is the likely first contact."""
        mcp, _ = learn_env
        payload = await _call(mcp, "trace_learn_forget", {"learning_id": "lrn_001", "project": PROJECT})
        assert payload.get("error") == "duplicate_learning_ids", payload
        assert "repair-ids" in payload.get("detail", "")

    async def test_a_refused_write_leaves_the_store_untouched(self, learn_env) -> None:
        mcp, knowledge = learn_env
        before = (knowledge / f"{PROJECT}.json").read_bytes()
        await _call(mcp, "trace_learn_add", {"content": "new insight", "project": PROJECT})
        assert (knowledge / f"{PROJECT}.json").read_bytes() == before

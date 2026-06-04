"""E2E tests for embedding-enhanced learn extension via MCP protocol.

Tests: server with embedding config → add learnings → recall with semantic
queries.  Uses subprocess server and JSON-RPC, following test_e2e_server.py
patterns.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from test_e2e_server import (
    _call_tool,
    _initialize_server,
    _shutdown_server,
    _start_server,
)

# ── E2E subprocess tests ─────────────────────────────────────────────────


class TestEmbeddingE2ESubprocess:
    """Test embedding features through the full MCP server."""

    async def test_add_and_recall_through_mcp(self):
        """Add learnings through MCP, then recall the relevant one by overlapping query.

        The e2e harness forces the deterministic offline BM25 backend
        (TRACE_EMBEDDING_BACKEND=none), so the recall query shares vocabulary
        ("conda environment") with the target content, and a distractor learning
        gives BM25 a real two-document corpus. This asserts recall returns the
        right learning through the full MCP server. Embedding/semantic ranking is
        covered offline by the dedicated test_learn_matching / test_learn_embeddings
        suites.
        """
        with tempfile.TemporaryDirectory() as sessions_dir:
            proc = await _start_server(sessions_dir)
            try:
                await _initialize_server(proc)

                # Add the target learning + a distractor (BM25 needs a corpus)
                result = await _call_tool(proc, "trace_learn_add", {
                    "project": "e2e-embed-test",
                    "content": "Always use the ml-dev conda environment, not base",
                    "category": "gotcha",
                    "tags": ["conda", "env"],
                }, request_id=10)

                response_text = result["result"]["content"][0]["text"]
                data = json.loads(response_text)
                assert "added" in data or "duplicate" in data

                await _call_tool(proc, "trace_learn_add", {
                    "project": "e2e-embed-test",
                    "content": "Fresh pasta needs semolina flour and eggs",
                    "category": "gotcha",
                    "tags": ["food"],
                }, request_id=11)

                # Recall — an overlapping query must surface the conda learning first
                recall_result = await _call_tool(proc, "trace_learn_recall", {
                    "project": "e2e-embed-test",
                    "context": "which conda environment should I use",
                    "threshold": 0.05,
                    "limit": 5,
                }, request_id=12)

                recall_text = recall_result["result"]["content"][0]["text"]
                recall_data = json.loads(recall_text)
                assert recall_data["total"] >= 1
                assert "conda" in recall_data["results"][0]["learning"]["content"].lower()

            finally:
                await _shutdown_server(proc)

    async def test_recall_returns_scores(self):
        """Recall results include numeric scores."""
        with tempfile.TemporaryDirectory() as sessions_dir:
            proc = await _start_server(sessions_dir)
            try:
                await _initialize_server(proc)

                # Add two learnings with different content
                await _call_tool(proc, "trace_learn_add", {
                    "project": "e2e-scores",
                    "content": "BM25 uses term frequency and inverse document frequency",
                    "tags": ["bm25", "search"],
                }, request_id=20)
                await _call_tool(proc, "trace_learn_add", {
                    "project": "e2e-scores",
                    "content": "Fresh pasta needs semolina flour and eggs",
                    "tags": ["food"],
                }, request_id=21)

                # Recall — search-related query with low threshold
                result = await _call_tool(proc, "trace_learn_recall", {
                    "project": "e2e-scores",
                    "context": "BM25 term frequency search ranking",
                    "threshold": 0.05,
                    "limit": 5,
                }, request_id=22)

                data = json.loads(result["result"]["content"][0]["text"])
                assert data["total"] >= 1
                for r in data["results"]:
                    assert "score" in r
                    assert isinstance(r["score"], float)

            finally:
                await _shutdown_server(proc)



# ── Real data E2E tests ──────────────────────────────────────────────────

_REAL_STORE = Path.home() / ".trace" / "knowledge" / "TRACE.json"


@pytest.mark.skipif(not _REAL_STORE.exists(), reason="No real TRACE knowledge store")
class TestRealDataE2E:
    """E2E tests using real TRACE knowledge data."""

    async def test_recall_real_knowledge(self):
        """Recall against real TRACE knowledge store via MCP server."""
        with tempfile.TemporaryDirectory() as sessions_dir:
            # Copy real knowledge store to temp for isolation
            knowledge_dir = Path(sessions_dir) / "knowledge"
            knowledge_dir.mkdir()
            import shutil
            shutil.copy2(_REAL_STORE, knowledge_dir / "TRACE.json")

            env_override = {"TRACE_KNOWLEDGE_DIR": str(knowledge_dir)}
            proc = await _start_server(sessions_dir, env_extra=env_override)
            assert proc.stdin is not None

            try:
                await _initialize_server(proc)

                result = await _call_tool(proc, "trace_learn_recall", {
                    "project": "TRACE",
                    "context": "schema validation rules for actor types",
                    "limit": 5,
                }, request_id=50)

                data = json.loads(result["result"]["content"][0]["text"])
                assert data["total"] >= 1

            finally:
                await _shutdown_server(proc)

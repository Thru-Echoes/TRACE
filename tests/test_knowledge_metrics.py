"""Tests for knowledge store metrics in project_summary.

Tests: knowledge key presence, empty store, category counts,
most_surfaced sorting, never_surfaced count.
"""

from __future__ import annotations

import pytest

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import add_learning, save_store
from trace_mcp.tools.query_tools import _compute_knowledge_metrics


class TestComputeKnowledgeMetrics:
    def test_empty_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        metrics = _compute_knowledge_metrics("nonexistent-project")
        assert metrics["total"] == 0
        assert metrics["by_category"] == {}
        assert metrics["most_surfaced"] == []
        assert metrics["never_surfaced"] == 0
        assert metrics["avg_recall_count"] == 0.0

    def test_total_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a")
        add_learning(ks, content="b")
        add_learning(ks, content="c")
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        assert metrics["total"] == 3

    def test_category_breakdown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        add_learning(ks, content="b", category="correction")
        add_learning(ks, content="c", category="correction")
        add_learning(ks, content="d", category="gotcha")
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        assert metrics["by_category"] == {
            "learning": 1,
            "correction": 2,
            "gotcha": 1,
        }

    def test_most_surfaced_sorted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        ks.learnings = [
            Learning(id="lrn_001", content="low recall", recall_count=1),
            Learning(id="lrn_002", content="high recall", recall_count=10),
            Learning(id="lrn_003", content="medium recall", recall_count=5),
            Learning(id="lrn_004", content="never recalled", recall_count=0),
        ]
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        surfaced = metrics["most_surfaced"]
        assert len(surfaced) == 3  # lrn_004 excluded (recall_count=0)
        assert surfaced[0]["id"] == "lrn_002"
        assert surfaced[0]["recall_count"] == 10
        assert surfaced[1]["id"] == "lrn_003"

    def test_never_surfaced_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        ks.learnings = [
            Learning(id="lrn_001", content="surfaced", recall_count=3),
            Learning(id="lrn_002", content="never", recall_count=0),
            Learning(id="lrn_003", content="never2", recall_count=0),
        ]
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        assert metrics["never_surfaced"] == 2

    def test_avg_recall_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        ks.learnings = [
            Learning(id="lrn_001", content="a", recall_count=4),
            Learning(id="lrn_002", content="b", recall_count=6),
        ]
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        assert metrics["avg_recall_count"] == pytest.approx(5.0)

    def test_most_surfaced_caps_at_five(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path))
        ks = KnowledgeStore(project="test")
        ks.learnings = [Learning(id=f"lrn_{i:03d}", content=f"learning {i}", recall_count=i) for i in range(1, 10)]
        save_store(ks)
        metrics = _compute_knowledge_metrics("test")
        assert len(metrics["most_surfaced"]) == 5
        # Highest recall_count should be first
        assert metrics["most_surfaced"][0]["recall_count"] == 9

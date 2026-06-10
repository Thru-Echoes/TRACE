"""Tests for the trace-learn extension."""

from __future__ import annotations

import json
from typing import Literal

from trace_mcp.extensions.learn.extraction import extract_from_session
from trace_mcp.extensions.learn.matching import (
    jaccard_similarity,
    recall_learnings,
    score_learning,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import (
    add_learning,
    list_learnings,
    load_store,
    remove_learning,
    save_store,
)
from trace_mcp.schema import Session
from trace_mcp.schema.events import (
    AnnotationData,
    DecisionData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor, SessionMetadata

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_session(events: list[TraceEvent] | None = None) -> Session:
    """Create a minimal test session."""
    return Session(
        id="test_session_001",
        metadata=SessionMetadata(
            project="test-project",
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events or [],
    )


def _make_annotation_event(
    event_id: str,
    category: Literal["learning", "gotcha", "observation", "correction", "todo", "question", "other"],
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="test_session_001",
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(
            category=category,
            content=content,
            tags=tags or [],
            corrects_event_ids=corrects_event_ids or [],
        ),
    )


def _make_decision_event(
    event_id: str,
    description: str,
    disposition: Literal["proposed", "accepted", "revised", "rejected"] = "proposed",
    revision_note: str | None = None,
    tags: list[str] | None = None,
) -> TraceEvent:
    resolved_by = Actor(type="human", id="researcher") if disposition != "proposed" else None
    return TraceEvent(
        id=event_id,
        session_id="test_session_001",
        type="decision",
        actor=Actor(type="ai", id="ai-assistant"),
        decision=DecisionData(
            description=description,
            proposed_by=Actor(type="ai", id="ai-assistant"),
            disposition=disposition,
            resolved_by=resolved_by,
            revision_note=revision_note,
            tags=tags or [],
        ),
    )


# ── TestKnowledgeStoreModel ─────────────────────────────────────────────────


class TestKnowledgeStoreModel:
    def test_empty_store(self):
        ks = KnowledgeStore(project="test")
        assert ks.project == "test"
        assert ks.learnings == []
        assert ks.version == "0.4"

    def test_id_generation_empty(self):
        ks = KnowledgeStore(project="test")
        assert ks.next_learning_id() == "lrn_001"

    def test_id_generation_sequential(self):
        ks = KnowledgeStore(
            project="test",
            learnings=[
                Learning(id="lrn_001", content="first"),
                Learning(id="lrn_002", content="second"),
            ],
        )
        assert ks.next_learning_id() == "lrn_003"

    def test_id_generation_with_gap(self):
        ks = KnowledgeStore(
            project="test",
            learnings=[
                Learning(id="lrn_001", content="first"),
                Learning(id="lrn_005", content="fifth"),
            ],
        )
        assert ks.next_learning_id() == "lrn_006"

    def test_json_roundtrip(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="test learning", category="correction", tags=["env"])
        data = json.loads(ks.model_dump_json())
        ks2 = KnowledgeStore.model_validate(data)
        assert len(ks2.learnings) == 1
        assert ks2.learnings[0].content == "test learning"
        assert ks2.learnings[0].tags == ["env"]


# ── TestStoreCRUD ────────────────────────────────────────────────────────────


class TestStoreCRUD:
    def test_load_nonexistent(self, tmp_path):
        ks = load_store("nonexistent", directory=str(tmp_path))
        assert ks.project == "nonexistent"
        assert ks.learnings == []

    def test_save_load_roundtrip(self, tmp_path):
        ks = KnowledgeStore(project="roundtrip")
        add_learning(ks, content="persisted", tags=["test"])
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("roundtrip", directory=str(tmp_path))
        assert len(loaded.learnings) == 1
        assert loaded.learnings[0].content == "persisted"

    def test_directory_creation(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        ks = KnowledgeStore(project="test")
        save_store(ks, directory=str(nested))
        assert (nested / "test.json").exists()

    def test_add_learning(self):
        ks = KnowledgeStore(project="test")
        lrn = add_learning(ks, content="new insight", category="gotcha", tags=["data"])
        assert lrn.id == "lrn_001"
        assert lrn.content == "new insight"
        assert lrn.category == "gotcha"
        assert len(ks.learnings) == 1

    def test_remove_learning(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="to remove")
        assert remove_learning(ks, "lrn_001") is True
        assert len(ks.learnings) == 0

    def test_remove_nonexistent(self):
        ks = KnowledgeStore(project="test")
        assert remove_learning(ks, "lrn_999") is False

    def test_list_all(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        add_learning(ks, content="b", category="correction")
        results = list_learnings(ks)
        assert len(results) == 2

    def test_list_filtered(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        add_learning(ks, content="b", category="correction")
        results = list_learnings(ks, category="correction")
        assert len(results) == 1
        assert results[0]["content"] == "b"


# ── TestMatching ─────────────────────────────────────────────────────────────


class TestMatching:
    def test_identical_texts(self):
        score = jaccard_similarity("hello world", "hello world")
        assert score == 1.0

    def test_disjoint_texts(self):
        score = jaccard_similarity("hello world", "foo bar")
        assert score == 0.0

    def test_partial_overlap(self):
        score = jaccard_similarity("hello world", "hello there")
        assert 0.0 < score < 1.0

    def test_empty_text(self):
        assert jaccard_similarity("", "hello") == 0.0
        assert jaccard_similarity("hello", "") == 0.0

    def test_case_insensitive(self):
        score = jaccard_similarity("Hello World", "hello world")
        assert score == 1.0

    def test_score_with_tags(self):
        lrn = Learning(id="lrn_001", content="use conda env ml-dev", tags=["conda", "env"])
        score_no_tags = score_learning(lrn, "conda environment setup")
        score_with_tags = score_learning(lrn, "conda environment setup", context_tags=["conda"])
        assert score_with_tags > score_no_tags

    async def test_recall_threshold_and_limit(self):
        learnings = [
            Learning(id="lrn_001", content="use conda env ml-dev for ML tasks"),
            Learning(id="lrn_002", content="completely unrelated topic about cooking"),
            Learning(id="lrn_003", content="conda activate ml-dev before running"),
        ]
        results = await recall_learnings(learnings, "conda ml-dev environment", threshold=0.1, limit=2)
        # Should find relevant ones but not the cooking one
        ids = [r["learning"]["id"] for r in results]
        assert "lrn_002" not in ids
        assert len(results) <= 2


# ── TestExtraction ───────────────────────────────────────────────────────────


class TestExtraction:
    def test_extract_learning_annotation(self):
        events = [_make_annotation_event("evt_001", "learning", "Always check data types")]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].content == "Always check data types"
        assert ks.learnings[0].source_session == "test_session_001"
        assert ks.learnings[0].source_event == "evt_001"

    def test_extract_correction_annotation(self):
        events = [
            _make_annotation_event(
                "evt_001",
                "correction",
                "Wrong conda env — use ml-dev",
                corrects_event_ids=["evt_000"],
            )
        ]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].category == "correction"

    def test_extract_rejected_decision(self):
        events = [
            _make_decision_event(
                "evt_001",
                "Use base conda env",
                disposition="rejected",
                revision_note="Always use ml-dev for this project",
            )
        ]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert "ml-dev" in ks.learnings[0].content
        assert ks.learnings[0].category == "decision"

    def test_idempotent_extraction(self):
        events = [_make_annotation_event("evt_001", "learning", "Check types")]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        ids1 = extract_from_session(ks, session)
        ids2 = extract_from_session(ks, session)
        assert len(ids1) == 1
        assert len(ids2) == 0
        assert len(ks.learnings) == 1

    def test_skip_observation(self):
        events = [_make_annotation_event("evt_001", "observation", "Just noting something")]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 0
        assert len(ks.learnings) == 0

    def test_skip_accepted_decision(self):
        events = [_make_decision_event("evt_001", "Use pandas for analysis", disposition="accepted")]
        session = _make_session(events)
        ks = KnowledgeStore(project="test")

        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 0


# ── TestLearnIntegration ────────────────────────────────────────────────────


class TestLearnIntegration:
    async def test_full_workflow(self, tmp_path):
        """End-to-end: session → corrections → extract → recall → persist."""
        # 1. Create a session with extractable events
        events = [
            _make_annotation_event(
                "evt_001",
                "correction",
                "Use ml-dev conda env, not base",
                tags=["conda", "env"],
            ),
            _make_annotation_event(
                "evt_002",
                "learning",
                "Always activate env before pip install",
                tags=["conda", "pip"],
            ),
            _make_annotation_event(
                "evt_003",
                "observation",
                "Pipeline took 45 minutes",
            ),
            _make_decision_event(
                "evt_004",
                "Use GPU instance for training",
                disposition="revised",
                revision_note="Use CPU — GPU quota exhausted",
                tags=["compute"],
            ),
        ]
        session = _make_session(events)

        # 2. Extract learnings
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 3  # correction + learning + revised decision (not observation)

        # 3. Save and reload
        save_store(ks, directory=str(tmp_path))
        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 3

        # 4. Recall relevant learnings
        results = await recall_learnings(
            loaded.learnings,
            context="which conda environment should I use",
            context_tags=["conda"],
            threshold=0.05,
        )
        assert len(results) > 0
        # The conda-related learnings should score higher
        top_content = results[0]["learning"]["content"]
        assert "conda" in top_content.lower() or "env" in top_content.lower()

        # 5. Idempotent re-extraction
        new_ids2 = extract_from_session(loaded, session)
        assert len(new_ids2) == 0

"""Tests for trace-learn content deduplication.

Tests: find_duplicate, add_learning_dedup, DedupResult, dedup in extraction,
threshold configurability, dedup disabled behavior.
"""

from __future__ import annotations

from typing import Literal

from trace_mcp.extensions.learn.extraction import extract_from_session
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import (
    DedupResult,
    add_learning,
    add_learning_dedup,
    find_duplicate,
)
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_session(events: list[TraceEvent] | None = None) -> Session:
    return Session(
        id="test_session_dedup",
        metadata=SessionMetadata(
            project="test-project",
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events or [],
    )


def _make_annotation_event(
    event_id: str,
    category: Literal["learning", "gotcha", "correction"],
    content: str,
    tags: list[str] | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="test_session_dedup",
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(
            category=category,
            content=content,
            tags=tags or [],
        ),
    )


# ── find_duplicate tests ────────────────────────────────────────────────


class TestFindDuplicate:
    def test_exact_match(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use ml-dev conda environment")
        result = find_duplicate(ks, "Always use ml-dev conda environment")
        assert result is not None
        assert result.id == "lrn_001"

    def test_near_match(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use ml-dev conda environment for ML tasks")
        result = find_duplicate(
            ks, "Always use the ml-dev conda environment for ML tasks", threshold=0.7
        )
        assert result is not None

    def test_no_match(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use ml-dev conda environment")
        result = find_duplicate(ks, "Best pasta recipe uses fresh tomatoes")
        assert result is None

    def test_empty_store(self):
        ks = KnowledgeStore(project="test")
        assert find_duplicate(ks, "anything") is None

    def test_empty_content(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="some learning")
        assert find_duplicate(ks, "") is None
        assert find_duplicate(ks, "   ") is None

    def test_threshold_controls_sensitivity(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="use conda environment ml-dev for ML")
        # Very high threshold — should not match slightly different content
        result_strict = find_duplicate(
            ks, "conda environment setup for data science", threshold=0.95
        )
        # Lower threshold — should match
        result_lenient = find_duplicate(
            ks, "conda environment setup for data science", threshold=0.1
        )
        assert result_strict is None
        assert result_lenient is not None


# ── add_learning_dedup tests ────────────────────────────────────────────


class TestAddLearningDedup:
    def test_novel_content_added(self):
        ks = KnowledgeStore(project="test")
        result = add_learning_dedup(ks, content="brand new insight")
        assert not result.is_duplicate
        assert result.learning.id == "lrn_001"
        assert len(ks.learnings) == 1

    def test_duplicate_content_skipped(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use ml-dev conda environment")
        result = add_learning_dedup(
            ks, content="Always use ml-dev conda environment", dedup_threshold=0.5
        )
        assert result.is_duplicate
        assert result.duplicate_of == "lrn_001"
        assert len(ks.learnings) == 1  # No new learning added

    def test_result_is_dedup_result(self):
        ks = KnowledgeStore(project="test")
        result = add_learning_dedup(ks, content="test")
        assert isinstance(result, DedupResult)

    def test_preserves_all_fields(self):
        ks = KnowledgeStore(project="test")
        result = add_learning_dedup(
            ks,
            content="test insight",
            category="correction",
            source_session="sess_001",
            source_event="evt_001",
            corrects_event_ids=["evt_000"],
            tags=["tag1"],
        )
        lrn = result.learning
        assert lrn.category == "correction"
        assert lrn.source_session == "sess_001"
        assert lrn.tags == ["tag1"]

    def test_different_content_not_deduplicated(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always use ml-dev conda environment")
        result = add_learning_dedup(
            ks, content="Best pasta recipe uses fresh tomatoes"
        )
        assert not result.is_duplicate
        assert len(ks.learnings) == 2


# ── Dedup in extraction ─────────────────────────────────────────────────


class TestDedupInExtraction:
    def test_extraction_with_dedup_skips_duplicate(self):
        """If a learning already exists with similar content, extraction skips it."""
        ks = KnowledgeStore(project="test")
        # Pre-populate with an existing learning
        add_learning(ks, content="Always check data types before processing")

        # Session with a very similar annotation
        events = [
            _make_annotation_event(
                "evt_001", "learning", "Always check data types before processing"
            )
        ]
        session = _make_session(events)

        new_ids = extract_from_session(ks, session, dedup_threshold=0.5)
        assert len(new_ids) == 0  # Duplicate skipped
        assert len(ks.learnings) == 1  # Still just the original

    def test_extraction_without_dedup_adds_all(self):
        """Without dedup_threshold, extraction adds even similar content."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Always check data types before processing")

        events = [
            _make_annotation_event(
                "evt_001", "learning", "Always check data types before processing"
            )
        ]
        session = _make_session(events)

        new_ids = extract_from_session(ks, session, dedup_threshold=None)
        assert len(new_ids) == 1  # Added despite similarity
        assert len(ks.learnings) == 2

    def test_extraction_event_idempotency_still_works(self):
        """Event-level idempotency should work alongside content dedup."""
        ks = KnowledgeStore(project="test")
        events = [
            _make_annotation_event("evt_001", "learning", "Check types"),
        ]
        session = _make_session(events)

        ids1 = extract_from_session(ks, session, dedup_threshold=0.85)
        assert len(ids1) == 1
        # Second run — event already extracted
        ids2 = extract_from_session(ks, session, dedup_threshold=0.85)
        assert len(ids2) == 0
        assert len(ks.learnings) == 1

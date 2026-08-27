"""Standalone tests for trace-learn models (models.py).

Tests: Learning model, KnowledgeStore model, category validation,
ID generation, timestamp behavior, serialization.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trace_mcp.extensions.learn.models import (
    KnowledgeStore,
    Learning,
    LearningCategory,
)


class TestLearningModel:
    """Tests for the Learning Pydantic model."""

    def test_minimal_learning(self):
        lrn = Learning(content="test insight")
        assert lrn.content == "test insight"
        assert lrn.category == "learning"
        assert lrn.id == ""
        assert lrn.tags == []
        assert lrn.corrects_event_ids == []
        assert lrn.source_session is None
        assert lrn.source_event is None

    def test_full_learning(self):
        lrn = Learning(
            id="lrn_042",
            content="Always use ml-dev conda env",
            category="correction",
            source_session="trace_20260301_abc123",
            source_event="evt_003",
            corrects_event_ids=["evt_001", "evt_002"],
            tags=["conda", "env", "critical"],
        )
        assert lrn.id == "lrn_042"
        assert lrn.category == "correction"
        assert len(lrn.corrects_event_ids) == 2
        assert "conda" in lrn.tags

    def test_all_valid_categories(self):
        """Every LearningCategory literal is accepted."""
        valid: list[LearningCategory] = [
            "learning",
            "gotcha",
            "correction",
            "decision",
            "observation",
            "todo",
            "question",
            "other",
        ]
        for cat in valid:
            lrn = Learning(content="test", category=cat)
            assert lrn.category == cat

    def test_invalid_category_rejected(self):
        """Pydantic rejects categories not in the Literal union."""
        with pytest.raises(ValidationError, match="category"):
            Learning(content="test", category="invalid_category")  # pyright: ignore[reportArgumentType]

    def test_timestamp_auto_generated(self):
        before = datetime.now(UTC)
        lrn = Learning(content="test")
        after = datetime.now(UTC)
        assert before <= lrn.created <= after

    def test_json_roundtrip_preserves_all_fields(self):
        original = Learning(
            id="lrn_001",
            content="test content",
            category="gotcha",
            source_session="sess_001",
            source_event="evt_001",
            corrects_event_ids=["evt_000"],
            tags=["tag1", "tag2"],
        )
        data = json.loads(original.model_dump_json())
        restored = Learning.model_validate(data)
        assert restored.id == original.id
        assert restored.content == original.content
        assert restored.category == original.category
        assert restored.corrects_event_ids == original.corrects_event_ids
        assert restored.tags == original.tags

    def test_model_dump_mode_json(self):
        """model_dump(mode='json') produces JSON-serializable dict."""
        lrn = Learning(id="lrn_001", content="test")
        d = lrn.model_dump(mode="json")
        assert isinstance(d["created"], str)  # datetime → ISO string
        json.dumps(d)  # Should not raise


class TestKnowledgeStoreModel:
    """Tests for the KnowledgeStore Pydantic model."""

    def test_empty_store(self):
        ks = KnowledgeStore(project="my-project")
        assert ks.project == "my-project"
        assert ks.version == "0.4"
        assert ks.learnings == []

    def test_store_with_learnings(self):
        ks = KnowledgeStore(
            project="test",
            learnings=[
                Learning(id="lrn_001", content="first"),
                Learning(id="lrn_002", content="second"),
            ],
        )
        assert len(ks.learnings) == 2

    def test_updated_timestamp_auto(self):
        before = datetime.now(UTC)
        ks = KnowledgeStore(project="test")
        after = datetime.now(UTC)
        assert before <= ks.updated <= after


class TestIDGeneration:
    """Tests for KnowledgeStore.next_learning_id()."""

    def test_empty_store_starts_at_001(self):
        ks = KnowledgeStore(project="test")
        assert ks.next_learning_id() == "lrn_001"

    def test_sequential_increment(self):
        ks = KnowledgeStore(
            project="test",
            learnings=[Learning(id=f"lrn_{i:03d}", content=f"l{i}") for i in range(1, 4)],
        )
        assert ks.next_learning_id() == "lrn_004"

    def test_gap_handling(self):
        """Gaps in IDs are fine — next ID is max + 1."""
        ks = KnowledgeStore(
            project="test",
            learnings=[
                Learning(id="lrn_001", content="a"),
                Learning(id="lrn_010", content="b"),
            ],
        )
        assert ks.next_learning_id() == "lrn_011"

    def test_non_standard_ids_ignored(self):
        """IDs that don't match lrn_NNN pattern are skipped."""
        ks = KnowledgeStore(
            project="test",
            learnings=[
                Learning(id="custom_id", content="a"),
                Learning(id="lrn_003", content="b"),
            ],
        )
        assert ks.next_learning_id() == "lrn_004"

    def test_large_id_numbers(self):
        ks = KnowledgeStore(
            project="test",
            learnings=[Learning(id="lrn_999", content="big")],
        )
        assert ks.next_learning_id() == "lrn_1000"

    def test_v01_store_loads_as_v02(self):
        """A store saved with version 0.1 loads fine — Pydantic uses defaults for new fields."""
        raw = {
            "project": "legacy",
            "version": "0.1",
            "updated": "2026-02-24T00:00:00Z",
            "learnings": [
                {
                    "id": "lrn_001",
                    "content": "old learning without corrects_event_ids",
                    "category": "learning",
                    "tags": [],
                    "created": "2026-02-24T00:00:00Z",
                }
            ],
        }
        ks = KnowledgeStore.model_validate(raw)
        assert len(ks.learnings) == 1
        assert ks.learnings[0].corrects_event_ids == []  # New field gets default


class TestRecallTrackingFields:
    """Tests for recall_count and last_surfaced fields on Learning."""

    def test_defaults(self):
        lrn = Learning(content="test")
        assert lrn.recall_count == 0
        assert lrn.last_surfaced is None

    def test_explicit_values(self):
        now = datetime.now(UTC)
        lrn = Learning(content="test", recall_count=5, last_surfaced=now)
        assert lrn.recall_count == 5
        assert lrn.last_surfaced == now

    def test_backward_compat_loading(self):
        """Old JSON without recall_count/last_surfaced loads fine."""
        raw = {
            "id": "lrn_001",
            "content": "old learning",
            "category": "learning",
            "tags": [],
            "created": "2026-02-24T00:00:00Z",
        }
        lrn = Learning.model_validate(raw)
        assert lrn.recall_count == 0
        assert lrn.last_surfaced is None

    def test_json_roundtrip_with_recall_fields(self):
        now = datetime.now(UTC)
        lrn = Learning(
            id="lrn_001",
            content="test",
            recall_count=3,
            last_surfaced=now,
        )
        data = json.loads(lrn.model_dump_json())
        restored = Learning.model_validate(data)
        assert restored.recall_count == 3
        assert restored.last_surfaced is not None


class TestMonotonicIds:
    """``next_id`` is a persisted counter, so a forgotten id is never reused (INV-12)."""

    def test_repeated_calls_return_distinct_ids(self):
        """The counter advances on call, not on append — two calls can never collide."""
        ks = KnowledgeStore(project="test")
        assert ks.next_learning_id() == "lrn_001"
        assert ks.next_learning_id() == "lrn_002"

    def test_legacy_store_initializes_the_counter_above_the_max(self):
        ks = KnowledgeStore.model_validate(
            {
                "project": "legacy",
                "learnings": [
                    {"id": "lrn_001", "content": "a"},
                    {"id": "lrn_007", "content": "b"},
                ],
            }
        )
        assert ks.next_id == 8
        assert ks.next_learning_id() == "lrn_008"

    def test_counter_survives_a_json_roundtrip(self):
        ks = KnowledgeStore(project="test")
        ks.next_learning_id()
        restored = KnowledgeStore.model_validate(json.loads(ks.model_dump_json()))
        assert restored.next_id == 2

    def test_counter_self_heals_upward(self):
        """A stale counter below the highest existing id is raised, never trusted."""
        ks = KnowledgeStore.model_validate(
            {
                "project": "test",
                "next_id": 2,
                "learnings": [{"id": "lrn_009", "content": "a"}],
            }
        )
        assert ks.next_id == 10

    def test_counter_is_never_lowered(self):
        """A counter ahead of the learnings (the forget case) is preserved as-is."""
        ks = KnowledgeStore.model_validate(
            {
                "project": "test",
                "next_id": 50,
                "learnings": [{"id": "lrn_001", "content": "a"}],
            }
        )
        assert ks.next_id == 50
        assert ks.next_learning_id() == "lrn_050"

    def test_duplicate_ids_are_reported_not_raised(self):
        """Loading a duplicated store must succeed — reads keep working; writes refuse."""
        ks = KnowledgeStore.model_validate(
            {
                "project": "test",
                "learnings": [
                    {"id": "lrn_002", "content": "a"},
                    {"id": "lrn_002", "content": "b"},
                    {"id": "lrn_003", "content": "c"},
                ],
            }
        )
        assert ks.duplicate_learning_ids() == ["lrn_002"]

    def test_clean_store_reports_no_duplicates(self):
        ks = KnowledgeStore(project="test", learnings=[Learning(id="lrn_001", content="a")])
        assert ks.duplicate_learning_ids() == []

    def test_unset_ids_are_not_reported_as_aliases(self):
        """Several not-yet-added learnings legitimately carry no id at all."""
        ks = KnowledgeStore(
            project="test",
            learnings=[Learning(content="a"), Learning(content="b")],
        )
        assert ks.duplicate_learning_ids() == []

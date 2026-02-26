"""Tests for the trace-evolve extension."""

from __future__ import annotations

import json
from typing import Literal

from trace_mcp.extensions.evolve.fitness import (
    express_adaptations,
    fitness_score,
    jaccard_similarity,
)
from trace_mcp.extensions.evolve.models import Adaptation, Genome
from trace_mcp.extensions.evolve.selection import select_from_session
from trace_mcp.extensions.evolve.store import (
    add_adaptation,
    list_adaptations,
    load_genome,
    remove_adaptation,
    save_genome,
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
    disposition: str = "proposed",
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


# ── TestGenomeModel ──────────────────────────────────────────────────────────


class TestGenomeModel:
    def test_empty_genome(self):
        genome = Genome(project="test")
        assert genome.project == "test"
        assert genome.adaptations == []
        assert genome.version == "0.1"

    def test_id_generation_empty(self):
        genome = Genome(project="test")
        assert genome.next_adaptation_id() == "adp_001"

    def test_id_generation_sequential(self):
        genome = Genome(
            project="test",
            adaptations=[
                Adaptation(id="adp_001", content="first"),
                Adaptation(id="adp_002", content="second"),
            ],
        )
        assert genome.next_adaptation_id() == "adp_003"

    def test_id_generation_with_gap(self):
        genome = Genome(
            project="test",
            adaptations=[
                Adaptation(id="adp_001", content="first"),
                Adaptation(id="adp_005", content="fifth"),
            ],
        )
        assert genome.next_adaptation_id() == "adp_006"

    def test_json_roundtrip(self):
        genome = Genome(project="test")
        add_adaptation(genome, content="test adaptation", category="correction", tags=["env"])
        data = json.loads(genome.model_dump_json())
        genome2 = Genome.model_validate(data)
        assert len(genome2.adaptations) == 1
        assert genome2.adaptations[0].content == "test adaptation"
        assert genome2.adaptations[0].tags == ["env"]


# ── TestStoreCRUD ────────────────────────────────────────────────────────────


class TestStoreCRUD:
    def test_load_nonexistent(self, tmp_path):
        genome = load_genome("nonexistent", directory=str(tmp_path))
        assert genome.project == "nonexistent"
        assert genome.adaptations == []

    def test_save_load_roundtrip(self, tmp_path):
        genome = Genome(project="roundtrip")
        add_adaptation(genome, content="persisted", tags=["test"])
        save_genome(genome, directory=str(tmp_path))

        loaded = load_genome("roundtrip", directory=str(tmp_path))
        assert len(loaded.adaptations) == 1
        assert loaded.adaptations[0].content == "persisted"

    def test_directory_creation(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        genome = Genome(project="test")
        save_genome(genome, directory=str(nested))
        assert (nested / "test.json").exists()

    def test_add_adaptation(self):
        genome = Genome(project="test")
        adp = add_adaptation(genome, content="new insight", category="gotcha", tags=["data"])
        assert adp.id == "adp_001"
        assert adp.content == "new insight"
        assert adp.category == "gotcha"
        assert len(genome.adaptations) == 1

    def test_remove_adaptation(self):
        genome = Genome(project="test")
        add_adaptation(genome, content="to remove")
        assert remove_adaptation(genome, "adp_001") is True
        assert len(genome.adaptations) == 0

    def test_remove_nonexistent(self):
        genome = Genome(project="test")
        assert remove_adaptation(genome, "adp_999") is False

    def test_list_all(self):
        genome = Genome(project="test")
        add_adaptation(genome, content="a", category="learning")
        add_adaptation(genome, content="b", category="correction")
        results = list_adaptations(genome)
        assert len(results) == 2

    def test_list_filtered(self):
        genome = Genome(project="test")
        add_adaptation(genome, content="a", category="learning")
        add_adaptation(genome, content="b", category="correction")
        results = list_adaptations(genome, category="correction")
        assert len(results) == 1
        assert results[0]["content"] == "b"


# ── TestFitness ──────────────────────────────────────────────────────────────


class TestFitness:
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

    def test_fitness_with_tags(self):
        adp = Adaptation(id="adp_001", content="use conda env ml-dev", tags=["conda", "env"])
        score_no_tags = fitness_score(adp, "conda environment setup")
        score_with_tags = fitness_score(adp, "conda environment setup", context_tags=["conda"])
        assert score_with_tags > score_no_tags

    def test_express_threshold_and_limit(self):
        adaptations = [
            Adaptation(id="adp_001", content="use conda env ml-dev for ML tasks"),
            Adaptation(id="adp_002", content="completely unrelated topic about cooking"),
            Adaptation(id="adp_003", content="conda activate ml-dev before running"),
        ]
        results = express_adaptations(adaptations, "conda ml-dev environment", threshold=0.1, limit=2)
        # Should find relevant ones but not the cooking one
        ids = [r["adaptation"]["id"] for r in results]
        assert "adp_002" not in ids
        assert len(results) <= 2


# ── TestSelection ────────────────────────────────────────────────────────────


class TestSelection:
    def test_select_learning_annotation(self):
        events = [_make_annotation_event("evt_001", "learning", "Always check data types")]
        session = _make_session(events)
        genome = Genome(project="test")

        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 1
        assert genome.adaptations[0].content == "Always check data types"
        assert genome.adaptations[0].source_session == "test_session_001"
        assert genome.adaptations[0].source_event == "evt_001"

    def test_select_correction_annotation(self):
        events = [
            _make_annotation_event(
                "evt_001",
                "correction",
                "Wrong conda env — use ml-dev",
                corrects_event_ids=["evt_000"],
            )
        ]
        session = _make_session(events)
        genome = Genome(project="test")

        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 1
        assert genome.adaptations[0].category == "correction"

    def test_select_rejected_decision(self):
        events = [
            _make_decision_event(
                "evt_001",
                "Use base conda env",
                disposition="rejected",
                revision_note="Always use ml-dev for this project",
            )
        ]
        session = _make_session(events)
        genome = Genome(project="test")

        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 1
        assert "ml-dev" in genome.adaptations[0].content
        assert genome.adaptations[0].category == "decision"

    def test_idempotent_selection(self):
        events = [_make_annotation_event("evt_001", "learning", "Check types")]
        session = _make_session(events)
        genome = Genome(project="test")

        ids1 = select_from_session(genome, session)
        ids2 = select_from_session(genome, session)
        assert len(ids1) == 1
        assert len(ids2) == 0
        assert len(genome.adaptations) == 1

    def test_skip_observation(self):
        events = [_make_annotation_event("evt_001", "observation", "Just noting something")]
        session = _make_session(events)
        genome = Genome(project="test")

        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 0
        assert len(genome.adaptations) == 0

    def test_skip_accepted_decision(self):
        events = [_make_decision_event("evt_001", "Use pandas for analysis", disposition="accepted")]
        session = _make_session(events)
        genome = Genome(project="test")

        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 0


# ── TestEvolveIntegration ────────────────────────────────────────────────────


class TestEvolveIntegration:
    def test_full_workflow(self, tmp_path):
        """End-to-end: session -> corrections -> select -> express -> persist."""
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

        # 2. Select adaptations (natural selection)
        genome = Genome(project="test")
        new_ids = select_from_session(genome, session)
        assert len(new_ids) == 3  # correction + learning + revised decision (not observation)

        # 3. Save and reload
        save_genome(genome, directory=str(tmp_path))
        loaded = load_genome("test", directory=str(tmp_path))
        assert len(loaded.adaptations) == 3

        # 4. Express relevant adaptations (gene expression)
        results = express_adaptations(
            loaded.adaptations,
            context="which conda environment should I use",
            context_tags=["conda"],
            threshold=0.05,
        )
        assert len(results) > 0
        # The conda-related adaptations should score higher
        top_content = results[0]["adaptation"]["content"]
        assert "conda" in top_content.lower() or "env" in top_content.lower()

        # 5. Idempotent re-selection
        new_ids2 = select_from_session(loaded, session)
        assert len(new_ids2) == 0

"""Tests for TRACE Pydantic schema models."""

from __future__ import annotations

import json
import warnings
from datetime import datetime

import pytest

from trace_mcp.schema import (
    SCHEMA_VERSION,
    Actor,
    AnnotationData,
    ContributionData,
    DecisionData,
    Environment,
    Session,
    SessionMetadata,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)

# ── Actor ────────────────────────────────────────────────────────────────────


class TestActor:
    def test_valid_actor(self) -> None:
        a = Actor(type="human", id="researcher-jane", role="lead")
        assert a.type == "human"
        assert a.id == "researcher-jane"
        assert a.role == "lead"

    def test_actor_no_role(self) -> None:
        a = Actor(type="ai", id="claude-sonnet-4")
        assert a.role is None


# ── Environment ──────────────────────────────────────────────────────────────


class TestEnvironment:
    def test_defaults(self) -> None:
        e = Environment()
        assert e.mcp_servers == []
        assert e.custom == {}


# ── Session ──────────────────────────────────────────────────────────────────


class TestSession:
    def test_create_session(self) -> None:
        s = Session(
            id="trace_20260205_abc123",
            metadata=SessionMetadata(project="test-project"),
        )
        assert s.id == "trace_20260205_abc123"
        assert s.status == "active"
        assert s.trace_version == SCHEMA_VERSION
        assert isinstance(s.created, datetime)

    def test_trace_version_tracks_schema_not_package(self) -> None:
        """trace_version is the wire/schema format version, intentionally decoupled
        from the package version (a wire-compatible release may run ahead)."""
        import trace_mcp

        s = Session(id="t", metadata=SessionMetadata(project="p"))
        assert s.trace_version == SCHEMA_VERSION
        # Same major.minor family, but the package may be ahead for a
        # wire-compatible release (e.g. package 0.4.2 / schema 0.4.1).
        assert trace_mcp.__version__.split(".")[:2] == SCHEMA_VERSION.split(".")[:2]

    def test_next_event_id(self) -> None:
        s = Session(
            id="trace_test",
            metadata=SessionMetadata(project="test"),
        )
        assert s.next_event_id() == "evt_001"
        # Simulate adding an event
        s.events.append(
            TraceEvent(
                id="evt_001",
                session_id="trace_test",
                type="annotation",
                actor=Actor(type="ai", id="test"),
                annotation=AnnotationData(category="observation", content="test"),
            )
        )
        assert s.next_event_id() == "evt_002"


# ── TraceEvent ───────────────────────────────────────────────────────────────


class TestTraceEvent:
    def test_valid_tool_call(self) -> None:
        evt = TraceEvent(
            id="evt_001",
            session_id="s001",
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(
                server="corpus-search",
                name="search_passages",
                input={"query": "test"},
            ),
        )
        assert evt.type == "tool_call"
        assert evt.tool_call is not None
        assert evt.tool_call.name == "search_passages"

    def test_valid_decision(self) -> None:
        evt = TraceEvent(
            id="evt_002",
            session_id="s001",
            type="decision",
            actor=Actor(type="ai", id="claude"),
            decision=DecisionData(
                description="Use threshold 0.85",
                proposed_by=Actor(type="ai", id="claude"),
            ),
        )
        assert evt.decision is not None
        assert evt.decision.disposition == "proposed"

    def test_valid_annotation(self) -> None:
        evt = TraceEvent(
            id="evt_003",
            session_id="s001",
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="gotcha",
                content="Unicode issues in PDF",
            ),
        )
        assert evt.annotation is not None
        assert evt.annotation.category == "gotcha"

    def test_valid_state_change(self) -> None:
        evt = TraceEvent(
            id="evt_004",
            session_id="s001",
            type="state_change",
            actor=Actor(type="ai", id="claude"),
            state_change=StateChangeData(
                description="Switched embedding model",
                old_value="MiniLM",
                new_value="BGE-large",
            ),
        )
        assert evt.state_change is not None

    def test_valid_contribution(self) -> None:
        evt = TraceEvent(
            id="evt_005",
            session_id="s001",
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Implemented cosine similarity function",
                artifact="src/similarity.py",
                direction="human",
                execution="ai",
                related_decision_ids=["evt_002"],
                tags=["implementation"],
            ),
        )
        assert evt.contribution is not None
        assert evt.contribution.direction == "human"
        assert evt.contribution.execution == "ai"
        assert evt.contribution.artifact == "src/similarity.py"

    def test_type_data_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="requires 'tool_call' to be populated"):
            TraceEvent(
                id="evt_bad",
                session_id="s001",
                type="tool_call",
                actor=Actor(type="ai", id="claude"),
                # tool_call is None but type says "tool_call"
            )

    def test_extra_data_field_raises(self) -> None:
        with pytest.raises(ValueError, match="must not have 'annotation' populated"):
            TraceEvent(
                id="evt_bad",
                session_id="s001",
                type="tool_call",
                actor=Actor(type="ai", id="claude"),
                tool_call=ToolCallData(server="test", name="test", input={}),
                annotation=AnnotationData(category="observation", content="extra"),
            )

    def test_auto_timestamp(self) -> None:
        evt = TraceEvent(
            session_id="s001",
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(category="observation", content="test"),
        )
        assert evt.timestamp.tzinfo is not None


# ── DecisionData ─────────────────────────────────────────────────────────────


class TestDecisionData:
    def test_proposed_no_resolved_by(self) -> None:
        d = DecisionData(
            description="Test",
            proposed_by=Actor(type="ai", id="claude"),
            disposition="proposed",
        )
        assert d.resolved_by is None

    def test_accepted_requires_resolved_by(self) -> None:
        with pytest.raises(ValueError, match="resolved_by must be set"):
            DecisionData(
                description="Test",
                proposed_by=Actor(type="ai", id="claude"),
                disposition="accepted",
                # missing resolved_by
            )

    def test_revised_warns_without_note(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            DecisionData(
                description="Test",
                proposed_by=Actor(type="ai", id="claude"),
                disposition="revised",
                resolved_by=Actor(type="human", id="researcher"),
                # missing revision_note
            )
            assert len(w) == 1
            assert "revision_note" in str(w[0].message)


# ── ContributionData ─────────────────────────────────────────────────────────


class TestContributionData:
    def test_minimal(self) -> None:
        c = ContributionData(
            description="Wrote analysis script",
            direction="human",
            execution="ai",
        )
        assert c.direction == "human"
        assert c.execution == "ai"
        assert c.artifact is None
        assert c.related_decision_ids == []
        assert c.tags == []

    def test_full(self) -> None:
        c = ContributionData(
            description="Refactored embedding pipeline",
            artifact="src/embeddings.py",
            direction="ai",
            execution="collaborative",
            related_decision_ids=["evt_002", "evt_003"],
            tags=["refactor", "embeddings"],
        )
        assert c.artifact == "src/embeddings.py"
        assert len(c.related_decision_ids) == 2
        assert c.tags == ["refactor", "embeddings"]

    def test_roundtrip_json(self) -> None:
        c = ContributionData(
            description="Test",
            direction="collaborative",
            execution="human",
            artifact="test.py",
        )
        data = c.model_dump(mode="json")
        restored = ContributionData.model_validate(data)
        assert restored.direction == "collaborative"
        assert restored.execution == "human"


class TestDecisionSuggestionType:
    def test_suggestion_type_none_by_default(self) -> None:
        d = DecisionData(
            description="Test",
            proposed_by=Actor(type="ai", id="claude"),
        )
        assert d.suggestion_type is None

    def test_suggestion_type_proactive(self) -> None:
        d = DecisionData(
            description="Use BGE embeddings",
            proposed_by=Actor(type="ai", id="claude"),
            suggestion_type="proactive",
        )
        assert d.suggestion_type == "proactive"

    def test_suggestion_type_roundtrip(self) -> None:
        d = DecisionData(
            description="Test",
            proposed_by=Actor(type="ai", id="claude"),
            suggestion_type="requested",
        )
        data = d.model_dump(mode="json")
        restored = DecisionData.model_validate(data)
        assert restored.suggestion_type == "requested"


# ── Round-trip ───────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_session_roundtrip(self) -> None:
        session = Session(
            id="trace_test_roundtrip",
            metadata=SessionMetadata(
                project="test",
                participants=[Actor(type="human", id="jane", role="lead")],
                environment=Environment(
                    mcp_servers=["corpus-search"],
                    client="claude-code",
                    os="Darwin",
                ),
                tags=["test"],
            ),
            events=[
                TraceEvent(
                    id="evt_001",
                    session_id="trace_test_roundtrip",
                    type="tool_call",
                    actor=Actor(type="ai", id="claude"),
                    tool_call=ToolCallData(
                        server="test-server",
                        name="test_tool",
                        input={"key": "value"},
                        output={"result": 42},
                        duration_ms=100,
                    ),
                ),
            ],
        )
        dumped = session.model_dump(mode="json")
        json_str = json.dumps(dumped)
        restored = Session.model_validate(json.loads(json_str))
        assert restored.id == session.id
        assert len(restored.events) == 1
        assert restored.events[0].tool_call is not None
        assert restored.events[0].tool_call.name == "test_tool"


# ── JSON Schema Generation ──────────────────────────────────────────────────


class TestCorrectionAnnotation:
    def test_correction_category(self) -> None:
        evt = TraceEvent(
            id="evt_010",
            session_id="s001",
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="AI was using wrong conda env; correct env is ml-dev",
                corrects_event_ids=["evt_007", "evt_008", "evt_009"],
            ),
        )
        assert evt.annotation is not None
        assert evt.annotation.category == "correction"
        assert len(evt.annotation.corrects_event_ids) == 3

    def test_correction_empty_corrects(self) -> None:
        a = AnnotationData(
            category="correction",
            content="Minor fix, no linked events",
        )
        assert a.corrects_event_ids == []

    def test_corrects_event_ids_on_non_correction(self) -> None:
        """corrects_event_ids can be set on any category (not restricted)."""
        a = AnnotationData(
            category="gotcha",
            content="Found a bug",
            corrects_event_ids=["evt_001"],
        )
        assert a.corrects_event_ids == ["evt_001"]

    def test_correction_roundtrip_json(self) -> None:
        a = AnnotationData(
            category="correction",
            content="Wrong env used",
            corrects_event_ids=["evt_001", "evt_002"],
            tags=["env", "conda"],
        )
        data = a.model_dump(mode="json")
        restored = AnnotationData.model_validate(data)
        assert restored.category == "correction"
        assert restored.corrects_event_ids == ["evt_001", "evt_002"]


class TestToolCallRetries:
    def test_retries_event_id(self) -> None:
        tc = ToolCallData(
            server="bash",
            name="run_command",
            input={"command": "conda activate ml-dev"},
            status="error",
            error_message="CondaError: environment not found",
            retries_event_id="evt_001",
        )
        assert tc.retries_event_id == "evt_001"

    def test_retries_event_id_none_by_default(self) -> None:
        tc = ToolCallData(
            server="bash",
            name="run_command",
            input={"command": "echo hello"},
        )
        assert tc.retries_event_id is None

    def test_retries_roundtrip_json(self) -> None:
        tc = ToolCallData(
            server="bash",
            name="run_command",
            input={"command": "test"},
            retries_event_id="evt_005",
        )
        data = tc.model_dump(mode="json")
        restored = ToolCallData.model_validate(data)
        assert restored.retries_event_id == "evt_005"


class TestJsonSchema:
    def test_generate_session_schema(self) -> None:
        schema = Session.model_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "id" in schema["properties"]

    def test_generate_event_schema(self) -> None:
        schema = TraceEvent.model_json_schema()
        assert "type" in schema["properties"]

    def test_json_schema_includes_decision_warnings(self) -> None:
        """DecisionData schema must include the warnings field."""
        schema = Session.model_json_schema()
        defs = schema.get("$defs", {})
        decision_schema = defs.get("DecisionData", {})
        props = decision_schema.get("properties", {})
        assert "warnings" in props, "DecisionData schema missing 'warnings' field"
        # Should be an array type
        warnings_schema = props["warnings"]
        assert warnings_schema.get("type") == "array" or "items" in warnings_schema

    def test_decision_data_warnings_roundtrip(self) -> None:
        """DecisionData with warnings survives dump → validate."""
        d = DecisionData(
            description="Use threshold 0.9",
            proposed_by=Actor(type="ai", id="claude"),
            warnings=["Previously rejected at 0.9"],
        )
        data = d.model_dump(mode="json")
        restored = DecisionData.model_validate(data)
        assert restored.warnings == ["Previously rejected at 0.9"]


# ── Dead Field Removal (Phase 1) ────────────────────────────────────────


class TestDeadFieldRemoval:
    def test_trace_event_has_no_verification_field(self) -> None:
        """verification field was removed from TraceEvent."""
        assert "verification" not in TraceEvent.model_fields

    def test_event_context_has_no_parent_event_id(self) -> None:
        """parent_event_id field was removed from EventContext."""
        from trace_mcp.schema.events import EventContext
        assert "parent_event_id" not in EventContext.model_fields

    def test_old_json_with_extra_fields_still_loads(self) -> None:
        """Backward compat: old JSON with removed fields can still be loaded."""
        raw = {
            "id": "evt_001",
            "session_id": "s001",
            "type": "annotation",
            "actor": {"type": "ai", "id": "claude"},
            "annotation": {"category": "observation", "content": "test"},
            "verification": {"hash": "abc123"},
            "context": {"parent_event_id": "evt_000"},
        }
        # model_validate with extra fields should not raise by default
        # (Pydantic v2 ignores extra fields unless model is configured to forbid)
        evt = TraceEvent.model_validate(raw)
        assert evt.id == "evt_001"
        assert evt.type == "annotation"

"""Tests for TRACE Pydantic schema models."""

from __future__ import annotations

import json
import warnings
from datetime import datetime

import pytest

from trace_mcp.schema import (
    Actor,
    AnnotationData,
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
        assert s.trace_version == "0.1.0"
        assert isinstance(s.created, datetime)

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
                    trace_version="0.1.0",
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


class TestJsonSchema:
    def test_generate_session_schema(self) -> None:
        schema = Session.model_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "id" in schema["properties"]

    def test_generate_event_schema(self) -> None:
        schema = TraceEvent.model_json_schema()
        assert "type" in schema["properties"]

"""Tests for the Gents run-timeline importer (`trace-mcp import gents`).

The fixture `tests/fixtures/gents_timeline_v0_14_0.json` is a real
`gents trace timeline` export from Gents v0.14.0, trimmed to keep every tool
call plus a sample of the bulk event kinds (messages, inference calls, rendered
requests). It pins the mapping against the shape the runtime actually emits.

Side effects: none beyond pytest tmp dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_mcp.importers.gents import (
    GentsTimeline,
    UnknownEventKindError,
    import_timeline,
    load_timeline,
    main,
)
from trace_mcp.schema import Session

FIXTURE = Path(__file__).parent / "fixtures" / "gents_timeline_v0_14_0.json"


@pytest.fixture
def timeline() -> GentsTimeline:
    return load_timeline(FIXTURE)


class TestFixtureShape:
    """The fixture is a real export; guard the properties the mapping relies on."""

    def test_fixture_loads_and_carries_tool_calls(self, timeline: GentsTimeline) -> None:
        assert timeline.request_id
        assert timeline.agent_did and timeline.agent_did.startswith("did:key:")
        kinds = {e.get("kind") for e in timeline.events}
        assert {"tool_call", "request", "response"} <= kinds

    def test_fixture_carries_no_credentials(self) -> None:
        """A fixture captured from a live run must not ship key material."""
        blob = FIXTURE.read_text()
        assert "sk-" not in blob
        assert '"api_key": "' not in blob


class TestMapping:
    def test_produces_a_valid_session(self, timeline: GentsTimeline) -> None:
        session = import_timeline(timeline, project="gents-import-test")
        assert isinstance(session, Session)
        assert session.metadata.project == "gents-import-test"
        assert session.status == "completed"
        assert session.events, "expected at least one mapped event"

    def test_event_ids_are_sequential_and_ordered(self, timeline: GentsTimeline) -> None:
        session = import_timeline(timeline, project="p")
        assert [e.id for e in session.events] == [f"evt_{i:03d}" for i in range(1, len(session.events) + 1)]
        timestamps = [e.timestamp for e in session.events]
        assert timestamps == sorted(timestamps)

    def test_mcp_calls_carry_service_and_target_tool(self, timeline: GentsTimeline) -> None:
        """A call routed to a registered MCP service records that service, not the meta tool."""
        session = import_timeline(timeline, project="p")
        mcp_calls = [e for e in session.events if e.type == "tool_call" and e.tool_call and e.tool_call.host == "mcp"]
        assert mcp_calls, "fixture should contain calls routed to an MCP service"
        for event in mcp_calls:
            assert event.tool_call is not None
            assert event.tool_call.server == "trace"
            # The recorded name is the tool actually invoked, not `call_tool`.
            assert event.tool_call.name.startswith("trace_")

    def test_native_tools_are_host_internal(self, timeline: GentsTimeline) -> None:
        session = import_timeline(timeline, project="p")
        internal = [
            e for e in session.events if e.type == "tool_call" and e.tool_call and e.tool_call.host == "internal"
        ]
        assert internal, "fixture should contain runtime-native tool calls"
        for event in internal:
            assert event.tool_call is not None
            assert event.tool_call.server == "gents"

    def test_tool_arguments_are_decoded(self, timeline: GentsTimeline) -> None:
        session = import_timeline(timeline, project="p")
        start = [
            e
            for e in session.events
            if e.type == "tool_call" and e.tool_call and e.tool_call.name == "trace_start_session"
        ]
        assert start, "fixture should contain the session-start call"
        assert isinstance(start[0].tool_call.input, dict) if start[0].tool_call else False

    def test_no_decisions_are_ever_synthesized(self, timeline: GentsTimeline) -> None:
        """The load-bearing guarantee: an execution trace yields no decisions."""
        session = import_timeline(timeline, project="p")
        assert all(e.type != "decision" for e in session.events)

    def test_bulk_kinds_are_counted_but_not_copied(self, timeline: GentsTimeline) -> None:
        """Message and prompt bodies stay in the runtime's store, not in the TRACE record."""
        session = import_timeline(timeline, project="p")
        counts = session.metadata.custom["timeline_event_counts"]
        assert counts.get("message", 0) > 0
        assert "message" in session.metadata.custom["not_imported"]
        assert all(e.type in {"tool_call", "state_change"} for e in session.events)

    def test_provenance_of_the_import_is_recorded(self, timeline: GentsTimeline) -> None:
        custom = import_timeline(timeline, project="p").metadata.custom
        assert custom["source"] == "gents-run-timeline"
        assert custom["importer"].startswith("trace-mcp ")
        assert custom["gents_request_id"] == timeline.request_id

    def test_session_id_can_be_overridden(self, timeline: GentsTimeline) -> None:
        session = import_timeline(timeline, project="p", session_id="trace_custom_id")
        assert session.id == "trace_custom_id"
        assert all(e.session_id == "trace_custom_id" for e in session.events)

    def test_roundtrips_through_the_session_schema(self, timeline: GentsTimeline) -> None:
        payload = import_timeline(timeline, project="p").model_dump(mode="json")
        assert Session.model_validate(payload).id == payload["id"]


class TestDriftPolicy:
    def test_unknown_event_kind_fails_loud(self, timeline: GentsTimeline) -> None:
        """A future Gents event type must raise, never be silently dropped."""
        drifted = timeline.model_copy(deep=True)
        drifted.events.append({"kind": "quantum_entanglement", "timestamp": "2026-09-02T16:28:29Z"})
        with pytest.raises(UnknownEventKindError, match="quantum_entanglement"):
            import_timeline(drifted, project="p")

    def test_unknown_fields_are_tolerated(self, timeline: GentsTimeline) -> None:
        """Additive fields must not break the importer; Gents ships weekly."""
        drifted = timeline.model_copy(deep=True)
        for event in drifted.events:
            event["some_new_field_from_a_later_release"] = "value"
        assert import_timeline(drifted, project="p").events

    def test_missing_required_field_is_rejected(self) -> None:
        """A renamed or removed required field must fail, not map to an empty value."""
        raw = json.loads(FIXTURE.read_text())
        for event in raw["events"]:
            if event.get("kind") == "tool_call":
                del event["tool_name"]
        with pytest.raises(ValueError):
            import_timeline(GentsTimeline.model_validate(raw), project="p")


class TestApprovalMapping:
    """Tool approvals become state changes with the identity caveat attached."""

    def _timeline_with_approval(self, timeline: GentsTimeline) -> GentsTimeline:
        drifted = timeline.model_copy(deep=True)
        drifted.events.append(
            {
                "kind": "tool_approval",
                "approval_id": "appr-1",
                "tool_call_id": "tc-1",
                "decision": "approved",
                "approver_did": "did:key:zHuman",
                "reason": "looks fine",
                "created_at": "2026-09-02T16:29:00Z",
            }
        )
        return drifted

    def test_approval_never_becomes_a_decision(self, timeline: GentsTimeline) -> None:
        session = import_timeline(self._timeline_with_approval(timeline), project="p")
        assert all(e.type != "decision" for e in session.events)

    def test_approval_records_the_unverified_caveat(self, timeline: GentsTimeline) -> None:
        session = import_timeline(self._timeline_with_approval(timeline), project="p")
        approvals = [
            e
            for e in session.events
            if e.type == "state_change" and e.state_change and e.state_change.field == "tool_approval"
        ]
        assert len(approvals) == 1
        description = approvals[0].state_change.description if approvals[0].state_change else ""
        assert "not verified" in description
        assert "authorization gate" in description


class TestCli:
    def test_writes_a_session_to_a_file(self, tmp_path: Path) -> None:
        out = tmp_path / "session.json"
        code = main([str(FIXTURE), "--project", "cli-test", "--output", str(out)])
        assert code == 0
        payload = json.loads(out.read_text())
        assert Session.model_validate(payload).metadata.project == "cli-test"

    def test_unknown_kind_exits_two(self, tmp_path: Path) -> None:
        raw = json.loads(FIXTURE.read_text())
        raw["events"].append({"kind": "brand_new_kind"})
        drifted = tmp_path / "drifted.json"
        drifted.write_text(json.dumps(raw))
        assert main([str(drifted), "--project", "p", "--output", str(tmp_path / "o.json")]) == 2

    def test_missing_file_exits_one(self, tmp_path: Path) -> None:
        assert main([str(tmp_path / "nope.json"), "--project", "p"]) == 1

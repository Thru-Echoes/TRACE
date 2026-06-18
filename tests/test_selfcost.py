"""E2E tests for the offline self-cost report (trace_mcp.selfcost).

Uses real schema models and a real Session round-trip through disk — no mocks.
Schema-surface assertions use inequalities (>=) so they are robust to the
FastMCP singleton retaining extension tools once loaded in-process.
"""

from __future__ import annotations

import json

from trace_mcp import selfcost
from trace_mcp.schema import (
    Actor,
    AnnotationData,
    DecisionData,
    Session,
    SessionMetadata,
    ToolCallData,
    TraceEvent,
)

AI = Actor(type="ai", id="claude")


def _make_session() -> Session:
    sid = "trace_selfcost_test"
    events = [
        TraceEvent(
            session_id=sid,
            type="decision",
            actor=AI,
            decision=DecisionData(description="Use approach X for the thing", proposed_by=AI),
        ),
        TraceEvent(
            session_id=sid,
            type="tool_call",
            actor=AI,
            tool_call=ToolCallData(server="domain", name="run_query", input={"q": "select 1"}, host="mcp"),
        ),
        TraceEvent(
            session_id=sid,
            type="tool_call",
            actor=AI,
            tool_call=ToolCallData(server="claude-code", name="dispatch", input={}, host="internal"),
        ),
        TraceEvent(
            session_id=sid,
            type="annotation",
            actor=AI,
            annotation=AnnotationData(category="discovery", content="A non-trivial finding worth recording."),
        ),
    ]
    return Session(id=sid, metadata=SessionMetadata(project="selfcost-test"), events=events)


def test_chars_to_tokens() -> None:
    assert selfcost.chars_to_tokens(0) == 0
    assert selfcost.chars_to_tokens(4) == 1
    assert selfcost.chars_to_tokens(5) == 2  # ceil
    assert selfcost.chars_to_tokens(-3) == 0


def test_estimate_schema_surface_core() -> None:
    surface = selfcost.estimate_schema_surface(include_extensions=False)
    assert surface.tool_count >= 17  # core registers 17 MCP tools
    assert surface.schema_chars > 0
    assert surface.tokens_est > 0
    # Caching regimes: warm cache read must be cheaper than cold cache write.
    assert surface.warm_read_tokens_est < surface.cold_write_tokens_est
    assert surface.included_extensions is False


def test_estimate_schema_surface_with_extensions_is_superset() -> None:
    core = selfcost.estimate_schema_surface(include_extensions=False)
    full = selfcost.estimate_schema_surface(include_extensions=True)
    assert full.tool_count >= core.tool_count
    assert full.included_extensions is True


def test_estimate_session_cost() -> None:
    cost = selfcost.estimate_session_cost(_make_session())
    assert cost.event_count == 4
    assert cost.authored_tokens_est > 0
    assert "decision" in cost.by_event_type
    assert "annotation" in cost.by_event_type
    # Both tool_call events counted, separated by host.
    assert cost.tool_call_host_breakdown.get("mcp") == 1
    assert cost.tool_call_host_breakdown.get("internal") == 1


def test_build_and_format_report(tmp_path) -> None:
    session = _make_session()
    p = tmp_path / "session.json"
    p.write_text(json.dumps(session.model_dump(mode="json")), encoding="utf-8")
    report = selfcost.build_report(session_path=str(p), include_extensions=False)
    assert report.session is not None
    assert report.session.event_count == 4
    text = selfcost.format_report(report).lower()
    # Honesty caveats must be present in the rendered report.
    assert "estimate" in text
    assert "cache" in text
    assert "opentelemetry" in text or "otel" in text
    assert "self-cost" in text


def test_main_smoke(capsys, tmp_path) -> None:
    session = _make_session()
    p = tmp_path / "session.json"
    p.write_text(json.dumps(session.model_dump(mode="json")), encoding="utf-8")
    rc = selfcost.main([str(p), "--no-extensions"])
    assert rc == 0
    assert "Schema surface" in capsys.readouterr().out


def test_estimate_reuse_signal_absent() -> None:
    # A project with no knowledge store returns None (honest absence), not a crash.
    assert selfcost.estimate_reuse_signal("nonexistent-project-xyz-selfcost") is None

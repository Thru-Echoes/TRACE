"""v0.4.2: cheap-bootstrap behavior for the API-400 crash-trigger surface.

Background: the documented 2-call session bootstrap expanded, in practice, into
13-17 trace_* calls in a single interleaved-thinking assistant turn (e.g. eager
trace_list_sessions x8 + trace_get_events x3 + trace_health_check x2 to
"orient"). A large single-turn content-block count is the controlling variable
behind the Claude Code thinking-block re-serialization 400. TRACE cannot fix the
client bug, but it can stop *inviting* the fan-out.

These tests pin the v0.4.2 contract:
  1. recall_learnings defaults OFF (no auto-recall in the opening turn).
  2. start_session returns a sequential-cadence steering note.
  3. start_session returns a bounded orientation (prior-session pointer) so the
     model has no reason to call list_sessions/get_events/health_check at start.
  4. session_brief reads a BOUNDED number of files (never the whole history).
"""

from __future__ import annotations

import inspect

from trace_mcp import server
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import session_tools


def _isolate(monkeypatch, tmp_path):
    """Point the server's module-global storage + knowledge dir at tmp dirs."""
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setattr(server, "storage", JsonFileStorage(str(tmp_path / "sessions")))
    monkeypatch.setattr(server, "_current_session_id", None)


def test_recall_learnings_defaults_off():
    sig = inspect.signature(server.trace_start_session)
    assert sig.parameters["recall_learnings"].default is False


async def test_bootstrap_has_sequential_cadence_steering(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    out = await server.trace_start_session(project="P", description="do a thing")
    assert "TRACE audit logging is now active" in out  # back-compat
    assert "1-2 trace calls per turn" in out
    # No auto-recall block by default.
    assert "Relevant learnings from past sessions" not in out


async def test_bootstrap_reports_prior_session_orientation(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    first = await server.trace_start_session(project="P", description="one")
    assert "No prior TRACE sessions" in first
    second = await server.trace_start_session(project="P", description="two")
    assert "most recent" in second.lower()


def test_format_bootstrap_message_is_pure_and_complete():
    msg = session_tools.format_bootstrap_message(
        session_id="trace_x",
        project="P",
        path="/tmp/trace_x.json",
        brief={
            "matched": 2,
            "capped": False,
            "most_recent": {"id": "trace_prev", "event_count": 5, "created": "2026-05-30T00:00:00"},
        },
        recalled_block="",
    )
    assert "trace_x" in msg and "P" in msg
    assert "most recent: trace_prev" in msg
    assert "1-2 trace calls per turn" in msg
    assert msg.rstrip().endswith("will be recorded.")


async def test_session_brief_is_bounded(tmp_path):
    st = JsonFileStorage(str(tmp_path))
    for i in range(8):
        await st.create_session(Session(id=f"trace_20260101_{i:06d}", metadata=SessionMetadata(project="P")))
    brief = await st.session_brief("P", scan_cap=3)
    assert brief["scanned"] <= 3
    assert brief["matched"] <= 3
    assert brief["capped"] is True
    assert brief["most_recent"] is not None


async def test_session_brief_no_match_when_project_absent(tmp_path):
    st = JsonFileStorage(str(tmp_path))
    await st.create_session(Session(id="trace_20260101_000000", metadata=SessionMetadata(project="OTHER")))
    brief = await st.session_brief("NOPE", scan_cap=25)
    assert brief["matched"] == 0
    assert brief["most_recent"] is None
    assert brief["capped"] is False

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

from trace_mcp import extension_hooks as hooks
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


# ── recall_query, notice and echo on the session banner ───────────────────


async def test_recall_query_overrides_description_and_is_echoed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    seen: list[tuple] = []

    async def fake(project, context, tags, limit):
        seen.append((project, context, tags, limit))
        return hooks.RecallResult(results=[], notice="no learning cleared the floor (z>=1): 2 scored")

    hooks.clear_hooks()
    hooks.register_recall_hook(fake)
    try:
        out = await server.trace_start_session(
            project="P",
            description="RUN_TOKEN=abc task A",
            tags=["t"],
            recall_learnings=True,
            recall_limit=4,
            recall_query="scope 2 units",
        )
    finally:
        hooks.clear_hooks()
    assert seen == [("P", "scope 2 units", ["t"], 4)]
    assert "Recall: no learning cleared the floor (z>=1): 2 scored" in out
    assert "Recall query: scope 2 units" in out
    assert "Relevant learnings from past sessions" not in out
    # The recall query is a search text only: the persisted session keeps the real description and tags.
    assert server._current_session_id is not None
    saved = await server.storage.get_session(server._current_session_id)
    assert saved is not None
    assert saved.metadata.description == "RUN_TOKEN=abc task A" and saved.metadata.tags == ["t"]


async def test_banner_echoes_recall_query_even_without_a_hook(tmp_path, monkeypatch):
    """Visible even when no extension answers, so a client can tell the argument was not dropped."""
    _isolate(monkeypatch, tmp_path)
    hooks.clear_hooks()
    out = await server.trace_start_session(project="P", recall_learnings=True, recall_query="only a query")
    assert "Recall query: only a query" in out
    assert "Relevant learnings from past sessions" not in out


async def test_recall_query_is_echoed_but_not_recalled_when_recall_is_off(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    calls: list[tuple] = []

    async def fake(project, context, tags, limit):
        calls.append((project, context))
        return hooks.RecallResult()

    hooks.clear_hooks()
    hooks.register_recall_hook(fake)
    try:
        out = await server.trace_start_session(project="P", description="d", recall_query="q")
    finally:
        hooks.clear_hooks()
    assert calls == []  # recall stays opt-in
    assert "Recall query: q (recall_learnings is off; nothing was recalled)" in out
    assert "Relevant learnings" not in out and "Recall:" not in out


async def test_blank_recall_query_is_acknowledged_and_the_description_is_used(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    seen: list[str] = []

    async def fake(project, context, tags, limit):
        seen.append(context)
        return hooks.RecallResult()

    hooks.clear_hooks()
    hooks.register_recall_hook(fake)
    try:
        out = await server.trace_start_session(
            project="P", description="scope 2 units", recall_learnings=True, recall_query="   "
        )
        off = await server.trace_start_session(project="P", description="scope 2 units", recall_query="")
    finally:
        hooks.clear_hooks()
    assert seen == ["scope 2 units"]
    assert "Recall query: (blank, used the description)" in out
    assert "Recall query: (blank)" in off


async def test_banner_renders_results_and_notice_together(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)

    async def fake(project, context, tags, limit):
        assert (project, context, tags, limit) == ("P", "RUN_TOKEN=abc task A", None, 5)  # the description, as given
        return hooks.RecallResult(
            results=[
                {"learning": {"category": "gotcha", "content": "x"}, "score": 0.5, "matched": True},
                {"learning": {"category": "learning", "content": "y"}, "score": 0.1, "matched": False},
            ],
            notice="query cleaned to 'task A'",
        )

    hooks.clear_hooks()
    hooks.register_recall_hook(fake)
    try:
        out = await server.trace_start_session(project="P", description="RUN_TOKEN=abc task A", recall_learnings=True)
    finally:
        hooks.clear_hooks()
    assert "  - [gotcha] x (relevance: 50%)" in out
    assert "  - [learning] y (relevance: 10%, weak match)" in out
    assert "Recall: query cleaned to 'task A'" in out
    assert "Recall query:" not in out  # the description was used; nothing to echo
    assert out.rstrip().endswith("All tool calls, decisions, and annotations will be recorded.")

"""v0.4.2 Phase 3: hard payload caps on query tools.

Unbounded query results are the primary CONTEXT-bloat surface that compounds the
API-400 crash: trace_search had NO limit (~250 KB worst case), trace_get_events
defaulted to 100 (~84 KB), and health_check/project_summary scanned up to
10000/1000 session files per call. Caps must be HARD — they must bind even when
the caller requests more — because in the crash transcript the model OVERRODE the
list_sessions default (it passed limit=30/40). Query output is also emitted
compact (no indent) to cut ~20-30% of serialized bytes.
"""

from __future__ import annotations

import inspect
import json

import pytest

from trace_mcp import server
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import logging_tools, query_tools, session_tools


@pytest.fixture
def storage(tmp_path):
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active():
    return {}


async def _populate(storage, active, n, project="caps"):
    """Create a session with n 'observation' annotations all containing 'widgets'."""
    session = await session_tools.create_session(storage, active, project=project)
    for i in range(n):
        await logging_tools.log_annotation(
            storage, session, category="observation", content=f"note number {i} about widgets"
        )
    return session


# ── get_events: hard clamp + small default ───────────────────────────────────


async def test_get_events_clamps_to_hard_max(storage, active):
    session = await _populate(storage, active, 1)
    # Inflate to > MAX_EVENTS_LIMIT in-memory (fast, no I/O).
    base = session.events[0]
    session.events = [base.model_copy(deep=True) for _ in range(query_tools.MAX_EVENTS_LIMIT + 50)]
    # Even when the caller asks for far more, the clamp binds.
    events = query_tools.get_events(session, limit=10_000)
    assert len(events) == query_tools.MAX_EVENTS_LIMIT


def test_trace_get_events_default_is_small():
    sig = inspect.signature(server.trace_get_events)
    assert sig.parameters["limit"].default == query_tools.DEFAULT_EVENTS_LIMIT
    assert query_tools.DEFAULT_EVENTS_LIMIT <= 25


# ── search: caps, truncation signal, core stays list ─────────────────────────


async def test_trace_search_caps_and_reports_truncation(monkeypatch, storage, active):
    monkeypatch.setattr(server, "storage", storage)
    monkeypatch.setattr(server, "_current_session_id", None)
    session = await _populate(storage, active, 60)
    out = await server.trace_search(session_id=session.id, query="widgets")
    data = json.loads(out)
    assert isinstance(data, dict)
    assert data["total_matched"] == 60
    assert data["returned"] <= query_tools.MAX_SEARCH_LIMIT
    assert data["returned"] <= 25  # default cap
    assert data["truncated"] is True
    assert len(data["results"]) == data["returned"]


async def test_search_events_core_still_returns_list(storage, active):
    """Backward-compat: the core helper returns a plain list (used in-process)."""
    session = await _populate(storage, active, 3)
    results = query_tools.search_events(session, query="widgets")
    assert isinstance(results, list)
    assert len(results) == 3


# ── health_check: bounded scan ───────────────────────────────────────────────


async def test_health_check_caps_scan(storage):
    for i in range(5):
        await storage.create_session(Session(id=f"trace_2026010{i}_000000", metadata=SessionMetadata(project="hc")))
    result = await query_tools.health_check(storage, scan_cap=2)
    assert result["sessions_scanned"] <= 2
    assert result["scan_truncated"] is True


# ── compact output ───────────────────────────────────────────────────────────


async def test_query_output_is_compact(monkeypatch, storage, active):
    monkeypatch.setattr(server, "storage", storage)
    monkeypatch.setattr(server, "_current_session_id", None)
    session = await _populate(storage, active, 2)
    out = await server.trace_get_events(session_id=session.id)
    assert "\n  " not in out  # no 2-space indentation blocks

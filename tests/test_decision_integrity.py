"""Regression tests for the decision-integrity cluster (PR #10).

Covers the 2026-06-10 status-review findings:

- C1: an invalid disposition must be rejected before any write, and the
  on-disk session file must remain loadable afterwards (previously a raw
  string like "approved" was assigned past Pydantic validation, permanently
  bricking the session file on the next load).
- H2: re-resolving an already-resolved decision must be refused with
  guidance to propose a superseding decision via ``revises_event_id``.
- H1: resolution of a still-proposed decision is the single permitted
  post-completion mutation — allowed on completed sessions but stamped with
  an audit warning, written back to the freshest disk object (a stale
  in-memory copy must not resurrect a completed session).
- Pointer guard: an explicit ``session_id`` pointing at a completed session
  must not move the server's current-session pointer.
- Literal sweep: enum-ish MCP tool params are ``Literal``-typed so invalid
  values are rejected at the protocol edge.
"""

from __future__ import annotations

import typing
from pathlib import Path

import pytest

from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, session_tools


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


async def _session_with_proposal(storage: JsonFileStorage, active: dict[str, Session]) -> tuple[Session, str]:
    """Create a session containing one proposed decision; return (session, event_id)."""
    session = await session_tools.create_session(
        storage, active, project="integrity-test", description="decision integrity"
    )
    event_id = await decision_tools.propose_decision(
        storage,
        session,
        description="Use approach X",
        rationale="It is simplest",
        proposed_by_type="ai",
        proposed_by_id="claude",
        suggestion_type="proactive",
    )
    return session, event_id


class TestC1InvalidDisposition:
    async def test_invalid_disposition_rejected(self, storage, active):
        session, event_id = await _session_with_proposal(storage, active)
        with pytest.raises(ValueError, match="Invalid disposition 'approved'"):
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id=event_id,
                disposition="approved",
                resolved_by_type="human",
                resolved_by_id="human",
            )

    async def test_proposed_is_not_a_resolution(self, storage, active):
        session, event_id = await _session_with_proposal(storage, active)
        with pytest.raises(ValueError, match="'proposed' is the initial state"):
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id=event_id,
                disposition="proposed",
                resolved_by_type="human",
                resolved_by_id="human",
            )

    async def test_session_file_loadable_after_rejected_write(self, storage, active):
        """The C1 data-loss scenario: file must stay loadable after the attempt."""
        session, event_id = await _session_with_proposal(storage, active)
        with pytest.raises(ValueError):
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id=event_id,
                disposition="approved",
                resolved_by_type="human",
                resolved_by_id="human",
            )
        reloaded = await storage.get_session(session.id)  # must not raise
        decision_evt = next(e for e in reloaded.events if e.id == event_id)
        assert decision_evt.decision is not None
        assert decision_evt.decision.disposition == "proposed"


class TestH2ReResolution:
    async def test_re_resolution_refused(self, storage, active):
        session, event_id = await _session_with_proposal(storage, active)
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="human",
        )
        with pytest.raises(ValueError, match="already resolved.*revises_event_id"):
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id=event_id,
                disposition="rejected",
                resolved_by_type="human",
                resolved_by_id="human",
                revision_note="changed my mind",
            )
        reloaded = await storage.get_session(session.id)
        decision_evt = next(e for e in reloaded.events if e.id == event_id)
        assert decision_evt.decision is not None
        assert decision_evt.decision.disposition == "accepted"


class TestH1PostCompletionResolution:
    async def test_resolve_proposed_in_completed_session_allowed_with_warning(self, storage, active):
        """Cross-session resolution (the documented decision lifecycle) keeps working."""
        session, event_id = await _session_with_proposal(storage, active)
        await session_tools.end_session(storage, active, session_id=session.id)

        # Fresh load, as a later session/process would see it.
        later = await storage.get_session(session.id)
        result = await decision_tools.resolve_decision(
            storage,
            later,
            event_id=event_id,
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="human",
        )
        assert "resolved: accepted" in result

        reloaded = await storage.get_session(session.id)
        assert reloaded.status == "completed"  # resolution must not resurrect it
        decision_evt = next(e for e in reloaded.events if e.id == event_id)
        assert decision_evt.decision is not None
        assert decision_evt.decision.disposition == "accepted"
        assert any("after session completion" in w for w in decision_evt.decision.warnings)

    async def test_stale_in_memory_copy_cannot_resurrect_completed_session(self, storage, active):
        """The write must target the disk object, not the caller's stale copy."""
        session, event_id = await _session_with_proposal(storage, active)
        # Another process completes the session; our in-memory copy still says active.
        await session_tools.end_session(storage, {}, session_id=session.id)
        assert session.status == "active"  # stale by construction

        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="human",
            revision_note="not needed",
        )
        reloaded = await storage.get_session(session.id)
        assert reloaded.status == "completed"


class TestWarningsPreserved:
    async def test_resolution_merges_existing_warnings(self, storage, active):
        """Warnings already on the decision must survive resolution, not be clobbered."""
        session, event_id = await _session_with_proposal(storage, active)
        # Simulate a proposal-time warning persisted by an earlier writer.
        disk = await storage.get_session(session.id)
        evt = next(e for e in disk.events if e.id == event_id)
        assert evt.decision is not None
        evt.decision.warnings = ["pre-existing proposal warning"]
        await storage.update_session(disk)

        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="human",
            revision_note="not needed",
        )
        reloaded = await storage.get_session(session.id)
        warnings = next(e for e in reloaded.events if e.id == event_id).decision.warnings
        assert "pre-existing proposal warning" in warnings
        assert any("Decision rejected" in w for w in warnings)  # FM31 added too


class TestCurrentSessionPointerGuard:
    async def test_completed_session_does_not_become_current(self, storage, active, monkeypatch):
        """Explicit session_id to a completed session must not move the pointer."""
        from trace_mcp import server

        session, _ = await _session_with_proposal(storage, active)
        await session_tools.end_session(storage, active, session_id=session.id)

        monkeypatch.setattr(server, "storage", storage)
        monkeypatch.setattr(server, "active_sessions", active)
        monkeypatch.setattr(server, "_current_session_id", "sentinel-current")

        looked_up, _ = await server._ensure_session(session.id)
        assert looked_up.id == session.id
        assert server._current_session_id == "sentinel-current"

    async def test_active_session_does_become_current(self, storage, active, monkeypatch):
        from trace_mcp import server

        session, _ = await _session_with_proposal(storage, active)

        monkeypatch.setattr(server, "storage", storage)
        monkeypatch.setattr(server, "active_sessions", active)
        monkeypatch.setattr(server, "_current_session_id", None)

        looked_up, _ = await server._ensure_session(session.id)
        assert looked_up.id == session.id
        assert server._current_session_id == session.id

    async def test_stale_cached_active_but_disk_completed_does_not_move_pointer(self, storage, active, monkeypatch):
        """Pointer guard must trust disk status, not the in-memory cache."""
        from trace_mcp import server

        session, _ = await _session_with_proposal(storage, active)
        # Another process completes the session on disk; this process's cache
        # still holds the stale 'active' object.
        await session_tools.end_session(storage, {}, session_id=session.id)
        assert active[session.id].status == "active"  # stale by construction

        monkeypatch.setattr(server, "storage", storage)
        monkeypatch.setattr(server, "active_sessions", active)
        monkeypatch.setattr(server, "_current_session_id", "sentinel-current")

        looked_up, _ = await server._ensure_session(session.id)
        assert looked_up.id == session.id
        assert server._current_session_id == "sentinel-current"

    async def test_completed_current_session_falls_through_to_autocreate(self, storage, active, monkeypatch):
        """A completed current session must not wedge pointer-less calls."""
        from trace_mcp import server

        session, _ = await _session_with_proposal(storage, active)
        await session_tools.end_session(storage, active, session_id=session.id)

        monkeypatch.setattr(server, "storage", storage)
        monkeypatch.setattr(server, "active_sessions", active)
        monkeypatch.setattr(server, "_current_session_id", session.id)

        async def _fake_infer_project() -> str:
            return "integrity-test"

        async def _no_recall(*args, **kwargs):
            return []

        monkeypatch.setattr(server, "_infer_project", _fake_infer_project)
        monkeypatch.setattr(server.hooks, "recall_if_available", _no_recall)

        fresh, auto_msg = await server._ensure_session(None)
        assert fresh.id != session.id
        assert fresh.status == "active"
        assert "Auto-created TRACE session" in auto_msg
        assert server._current_session_id == fresh.id


class TestLiteralSignatureSweep:
    """Enum-ish MCP tool params must be Literal-typed at the protocol edge."""

    @pytest.mark.parametrize(
        ("tool_name", "param", "expected_values"),
        [
            ("trace_resolve_decision", "disposition", {"accepted", "revised", "rejected"}),
            ("trace_resolve_decision", "resolved_by_type", {"human", "ai", "system"}),
            ("trace_propose_decision", "proposed_by_type", {"human", "ai", "system"}),
            (
                "trace_propose_decision",
                "suggestion_type",
                {"proactive", "requested", "collaborative", None},
            ),
            (
                "trace_log_annotation",
                "category",
                {
                    "learning",
                    "gotcha",
                    "observation",
                    "correction",
                    "todo",
                    "question",
                    "discovery",
                    "other",
                },
            ),
            ("trace_log_contribution", "direction", {"human", "ai", "collaborative"}),
            ("trace_log_contribution", "execution", {"human", "ai", "collaborative"}),
            ("trace_log_tool_call", "status", {"success", "error", "timeout"}),
            ("trace_log_tool_call", "host", {"mcp", "internal", "external"}),
            ("trace_export", "format", {"json", "markdown", "prov-jsonld"}),
        ],
    )
    def test_param_is_literal(self, tool_name: str, param: str, expected_values: set):
        from trace_mcp import server

        fn = getattr(server, tool_name)
        fn = getattr(fn, "fn", fn)  # unwrap FastMCP tool object if needed
        hints = typing.get_type_hints(fn)
        assert param in hints, f"{tool_name} has no parameter '{param}'"
        literal_values: set = set()
        if typing.get_origin(hints[param]) is typing.Literal:
            literal_values = set(typing.get_args(hints[param]))
        else:  # Literal[...] | None (or other unions of Literals)
            for arg in typing.get_args(hints[param]):
                if arg is type(None):
                    literal_values.add(None)
                else:
                    assert typing.get_origin(arg) is typing.Literal, (
                        f"{tool_name}.{param} union member {arg!r} is not a Literal"
                    )
                    literal_values.update(typing.get_args(arg))
        assert literal_values == expected_values, (
            f"{tool_name}.{param}: expected Literal{sorted(map(str, expected_values))}, got {hints[param]!r}"
        )

"""Tests for the resolution time and resolution quote on decisions.

A resolved decision records when it was resolved (``resolved_at``, stamped by
the recorder) and the person's words that resolved it (``resolution_snippet``,
quoted by the resolving session).
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from trace_mcp import project_identity as pident
from trace_mcp import server
from trace_mcp.schema import Actor, DecisionData, Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, session_tools
from trace_mcp.tools.decision_tools import UNQUOTED_RESOLUTION_NOTE

DOCSTRING_SENTENCE = (
    "When a person resolved it, pass their words in conversation_snippet: what they typed, "
    "or for a question dialog the option they picked or the answer they typed."
)


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


async def _session_with_proposal(storage: JsonFileStorage, active: dict[str, Session]) -> tuple[Session, str]:
    """Create a session containing one proposed decision; return (session, event_id)."""
    session = await session_tools.create_session(storage, active, project="collab-a", description="resolution time")
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


async def _reloaded_decision(storage: JsonFileStorage, session_id: str, event_id: str) -> DecisionData:
    event = next(e for e in (await storage.get_session(session_id)).events if e.id == event_id)
    assert event.decision is not None
    return event.decision


def test_a_proposed_decision_cannot_carry_a_resolution_time() -> None:
    with pytest.raises(ValidationError, match="resolved_at"):
        DecisionData(description="d", proposed_by=Actor(type="ai", id="a"), resolved_at=datetime.now(UTC))


def test_a_proposed_decision_cannot_carry_a_resolution_quote() -> None:
    with pytest.raises(ValidationError, match="resolution_snippet"):
        DecisionData(description="d", proposed_by=Actor(type="ai", id="a"), resolution_snippet="yes, do that")


def test_a_record_resolved_before_the_fields_existed_still_loads() -> None:
    decision = DecisionData.model_validate(
        {
            "description": "d",
            "proposed_by": {"type": "ai", "id": "a"},
            "disposition": "accepted",
            "resolved_by": {"type": "human", "id": "alice"},
        }
    )
    assert decision.resolved_at is None
    assert decision.resolution_snippet is None


async def test_resolution_records_when_it_happened(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    before = datetime.now(UTC)
    await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    after = datetime.now(UTC)
    event = next(e for e in (await storage.get_session(session.id)).events if e.id == event_id)
    assert event.decision is not None
    assert event.decision.resolved_at is not None
    assert event.decision.resolved_at.tzinfo is not None
    assert event.timestamp <= before <= event.decision.resolved_at <= after


async def test_a_decision_left_proposed_has_no_resolution_time(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    decision = await _reloaded_decision(storage, session.id, event_id)
    assert decision.resolved_at is None
    assert decision.resolution_snippet is None


async def test_resolution_after_the_session_ended_is_stamped_after_the_end(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    await session_tools.end_session(storage, active, session_id=session.id, summary="done")
    await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    reloaded = await storage.get_session(session.id)
    decision = await _reloaded_decision(storage, session.id, event_id)
    assert reloaded.ended is not None
    assert decision.resolved_at is not None
    assert decision.resolved_at > reloaded.ended


async def test_a_refused_second_resolution_leaves_the_record_unchanged(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet="yes, do that",
    )
    first = await _reloaded_decision(storage, session.id, event_id)
    with pytest.raises(ValueError):
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="alice",
            revision_note="changed my mind",
            conversation_snippet="no, undo that",
        )
    second = await _reloaded_decision(storage, session.id, event_id)
    assert second.resolved_at == first.resolved_at
    assert second.resolution_snippet == first.resolution_snippet == "yes, do that"


async def test_the_time_is_written_in_the_same_form_as_the_event_timestamp(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    raw = json.loads(Path(storage.session_location(session.id)).read_text())
    raw_event = next(e for e in raw["events"] if e["id"] == event_id)
    raw_decision = raw_event["decision"]
    assert raw_decision["resolved_at"].endswith("Z") == raw_event["timestamp"].endswith("Z")
    assert datetime.fromisoformat(raw_decision["resolved_at"]).tzinfo is not None


async def test_the_persons_words_are_stored_with_the_resolution(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    before = next(e for e in (await storage.get_session(session.id)).events if e.id == event_id)
    snippet_before = before.context.conversation_snippet if before.context else None
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet="  yes, go with three retries \n",
    )
    after = next(e for e in (await storage.get_session(session.id)).events if e.id == event_id)
    assert after.decision is not None
    assert after.decision.resolution_snippet == "yes, go with three retries"
    assert (after.context.conversation_snippet if after.context else None) == snippet_before


async def test_a_human_resolution_without_a_quote_is_recorded_and_noted(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    result = await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    decision = await _reloaded_decision(storage, session.id, event_id)
    assert decision.disposition == "accepted"
    assert decision.resolution_snippet is None
    assert UNQUOTED_RESOLUTION_NOTE in result


async def test_a_blank_quote_is_stored_as_none(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    result = await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet="   ",
    )
    decision = await _reloaded_decision(storage, session.id, event_id)
    assert decision.resolution_snippet is None
    assert UNQUOTED_RESOLUTION_NOTE in result


@pytest.mark.parametrize("quote", ["yes, do that", "<autonomous-stretch>"])
async def test_a_quoted_human_resolution_carries_no_note(storage, active, quote: str) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    result = await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet=quote,
    )
    decision = await _reloaded_decision(storage, session.id, event_id)
    assert decision.resolution_snippet == quote
    assert UNQUOTED_RESOLUTION_NOTE not in result


async def test_an_ai_resolution_carries_no_note(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    result = await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="ai", resolved_by_id="reviewer"
    )
    assert UNQUOTED_RESOLUTION_NOTE not in result


async def test_the_note_is_not_stored_on_the_record(storage, active) -> None:
    session, event_id = await _session_with_proposal(storage, active)
    await decision_tools.resolve_decision(
        storage, session, event_id=event_id, disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    assert UNQUOTED_RESOLUTION_NOTE not in Path(storage.session_location(session.id)).read_text()


def test_the_tool_takes_the_quote_as_its_last_argument() -> None:
    params = inspect.signature(server.trace_resolve_decision).parameters
    assert list(params)[-1] == "conversation_snippet"
    assert params["conversation_snippet"].default is None
    doc = " ".join((server.trace_resolve_decision.__doc__ or "").split())
    assert DOCSTRING_SENTENCE in doc


@pytest.fixture
def isolated_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JsonFileStorage:
    storage = JsonFileStorage(directory=str(tmp_path / "sessions"))
    monkeypatch.setattr(server, "storage", storage)
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_PROJECT", "collab-a")
    pident._reset_registry_cache()
    monkeypatch.setattr(server, "_current_session_id", None)
    monkeypatch.setattr(server, "active_sessions", {})
    return storage


async def test_the_tool_passes_the_quote_through(isolated_server: JsonFileStorage) -> None:
    await server.trace_start_session(description="quote pass-through")
    proposed = await server.trace_propose_decision(
        description="Use approach X", proposed_by_type="ai", proposed_by_id="claude"
    )
    match = re.search(r"evt_\d+", proposed)
    assert match, proposed
    session_id = server._current_session_id
    assert session_id is not None
    result = await server.trace_resolve_decision(
        event_id=match.group(0),
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet="yes, do that",
    )
    assert "Error" not in result, result
    decision = await _reloaded_decision(isolated_server, session_id, match.group(0))
    assert decision.resolution_snippet == "yes, do that"

"""Decision workflow tools: propose and resolve decisions."""

from __future__ import annotations

from trace_mcp.schema import Actor, DecisionData, Session, TraceEvent
from trace_mcp.storage.base import TraceStorage
from trace_mcp.tools.session_tools import append_event


async def propose_decision(
    storage: TraceStorage,
    session: Session,
    *,
    description: str,
    rationale: str | None = None,
    proposed_by_type: str,
    proposed_by_id: str,
    revises_event_id: str | None = None,
    suggestion_type: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Propose a methodological decision for the workflow."""
    event = TraceEvent(
        session_id=session.id,
        type="decision",
        actor=Actor(type=proposed_by_type, id=proposed_by_id),  # type: ignore[arg-type]
        decision=DecisionData(
            description=description,
            rationale=rationale,
            proposed_by=Actor(type=proposed_by_type, id=proposed_by_id),  # type: ignore[arg-type]
            disposition="proposed",
            revises_event_id=revises_event_id,
            suggestion_type=suggestion_type,  # type: ignore[arg-type]
            tags=tags or [],
        ),
    )
    event_id = await append_event(storage, session, event)
    return event_id


async def resolve_decision(
    storage: TraceStorage,
    session: Session,
    *,
    event_id: str,
    disposition: str,
    resolved_by_type: str,
    resolved_by_id: str,
    revision_note: str | None = None,
) -> str:
    """Resolve a previously proposed decision."""
    # Find the decision event
    target = None
    for evt in session.events:
        if evt.id == event_id and evt.type == "decision":
            target = evt
            break

    if target is None:
        return f"Error: Decision event '{event_id}' not found in session."

    if target.decision is None:
        return f"Error: Event '{event_id}' has no decision data."

    resolver = Actor(type=resolved_by_type, id=resolved_by_id)  # type: ignore[arg-type]
    target.decision.disposition = disposition  # type: ignore[assignment]
    target.decision.resolved_by = resolver
    target.decision.revision_note = revision_note

    await storage.update_session(session)
    return f"Decision {event_id} resolved: {disposition}"

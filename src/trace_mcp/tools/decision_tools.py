"""Decision workflow tools: propose and resolve decisions."""

from __future__ import annotations

import os
from datetime import UTC, datetime

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
    conversation_snippet: str | None = None,
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
    if conversation_snippet is not None:
        event.context.conversation_snippet = conversation_snippet
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
        raise ValueError(f"Decision event '{event_id}' not found in session '{session.id}'")

    if target.decision is None:
        raise ValueError(f"Event '{event_id}' has no decision data")

    # --- Guard rails ---
    guard_warnings: list[str] = []
    suppress = os.environ.get("TRACE_SUPPRESS_SELF_RESOLVE_WARNING", "").lower() == "true"

    # FM1: Same-instance self-resolution
    # v0.4.1: generalized from ai-only to any same-instance pair per spec §3.6
    # Proposer Identity Rule + Attribution rule. Detects when the same Actor
    # instance (type AND id) proposes and resolves the decision.
    proposer = target.decision.proposed_by
    is_self_resolution = (
        proposer.type == resolved_by_type
        and proposer.id == resolved_by_id
    )

    if is_self_resolution and not suppress:
        if resolved_by_type == "ai":
            # Backward-compat message preserved for ai→ai (the original v0.3 case).
            guard_warnings.append(
                "AI resolved its own proposal. Decisions proposed by AI "
                "should normally be resolved by a human."
            )
        else:
            # v0.4.1: catches the evt_025 pattern — human→human (or system→system)
            # same-instance self-resolution that the original FM1 silently allowed.
            guard_warnings.append(
                "Same actor instance proposed and resolved this decision. "
                "Per spec §3.6, in multi-actor workflows the proposer should "
                "differ from the resolver."
            )

    # FM25: Suspiciously fast resolution (propose + resolve <5s by same instance)
    # v0.4.1: generalized from ai-only to any same-instance pair.
    elapsed = (datetime.now(UTC) - target.timestamp).total_seconds()
    if elapsed < 5.0 and is_self_resolution and not suppress:
        guard_warnings.append(
            f"Decision proposed and self-resolved in {elapsed:.1f}s. "
            "Was the other actor consulted before resolving?"
        )

    # FM31: Rejection -> suggest correction annotation
    if disposition == "rejected":
        guard_warnings.append(
            "Decision rejected. Consider logging a correction annotation "
            "(category='correction') linking to this decision, to capture "
            "the reasoning in the knowledge store."
        )

    resolver = Actor(type=resolved_by_type, id=resolved_by_id)  # type: ignore[arg-type]
    target.decision.disposition = disposition  # type: ignore[assignment]
    target.decision.resolved_by = resolver
    target.decision.revision_note = revision_note
    target.decision.warnings = guard_warnings

    await storage.update_session(session)

    result = f"Decision {event_id} resolved: {disposition}"
    if guard_warnings:
        result += "\n" + "\n".join(f"  \u26a0\ufe0f {w}" for w in guard_warnings)
    return result

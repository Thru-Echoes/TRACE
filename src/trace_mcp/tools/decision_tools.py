"""Decision workflow tools: propose and resolve decisions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import get_args

from trace_mcp.schema import Actor, DecisionData, DecisionDisposition, Session, TraceEvent
from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.locked import locked_disk_session
from trace_mcp.tools.session_tools import append_event

# Terminal dispositions a proposed decision may transition to. Derived from
# the schema's canonical Literal (minus the initial state) so the two can
# never drift. Validated here — not only via the Literal in the MCP
# signature — so direct library callers can never write an invalid
# disposition that bricks the session file on the next load.
VALID_RESOLUTIONS = tuple(v for v in get_args(DecisionDisposition) if v != "proposed")


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
    """Resolve a previously proposed decision.

    Integrity guarantees:
    - *disposition* must be one of ``VALID_RESOLUTIONS`` — rejected up front,
      and the updated decision is re-validated through Pydantic before any
      write, so an invalid value can never reach disk.
    - Only ``proposed`` decisions can be resolved; re-resolving raises with
      guidance to propose a superseding decision via ``revises_event_id``.
    - Resolution is the single permitted post-completion mutation: resolving
      a still-proposed decision in a completed session succeeds but stamps an
      audit warning on the decision (cross-session resolution is part of the
      documented decision lifecycle). All writes go to the freshest on-disk
      session object under the per-session lock.
    """
    if disposition not in VALID_RESOLUTIONS:
        raise ValueError(
            f"Invalid disposition '{disposition}'. Must be one of {', '.join(VALID_RESOLUTIONS)}. "
            "Note: 'proposed' is the initial state, not a resolution."
        )

    resolver = Actor(type=resolved_by_type, id=resolved_by_id)  # type: ignore[arg-type]
    suppress = os.environ.get("TRACE_SUPPRESS_SELF_RESOLVE_WARNING", "").lower() == "true"

    # Concurrency-safe write (symmetry with append_event / end_session): all
    # reads, guard-rail computation, and the write happen under the per-session
    # lock against the authoritative on-disk session, so a concurrent
    # append/resolve persisted since the caller loaded its Session is neither
    # clobbered nor judged from stale state (e.g. is_multi_actor on a copy
    # that is missing a concurrently-appended actor).
    # The helper yields the disk-loaded object (not the caller's in-memory copy)
    # so a stale in-memory status/metadata can't clobber disk state — e.g.
    # resurrect a session completed by another process — and acquires the
    # fail-closed per-session lock (INV-1, docs/INVARIANTS.md).
    async with locked_disk_session(storage, session.id, fallback=session) as write_session:
        target = next(
            (e for e in write_session.events if e.id == event_id and e.type == "decision"),
            None,
        )
        if target is None:
            raise ValueError(f"Decision event '{event_id}' not found in session '{session.id}'")
        if target.decision is None:
            raise ValueError(f"Event '{event_id}' has no decision data")
        if target.decision.disposition != "proposed":
            raise ValueError(
                f"Decision '{event_id}' is already resolved "
                f"(disposition='{target.decision.disposition}'), possibly by a concurrent "
                "or earlier call. Re-resolution is not allowed: to supersede it, propose "
                f"a new decision with revises_event_id pointing at '{event_id}' and "
                "resolve that one instead."
            )

        # --- Guard rails (computed from the authoritative disk state) ---
        guard_warnings: list[str] = []

        # FM1: Same-instance self-resolution
        # v0.4.1 + Round-3 A1 / evt_016: generalized from ai-only per spec §3.6
        # Proposer Identity Rule. Detects when the same Actor instance (type AND
        # id) proposes and resolves. The ai→ai case warns unconditionally; the
        # generalized non-ai case is gated to multi-actor sessions (see below).
        proposer = target.decision.proposed_by
        is_self_resolution = (
            proposer.type == resolved_by_type
            and proposer.id == resolved_by_id
        )

        if is_self_resolution and not suppress:
            if resolved_by_type == "ai":
                # Backward-compat message preserved for ai→ai (the original v0.3
                # case). Fires unconditionally — AI must not resolve its own
                # proposal regardless of how many actor types the session has.
                guard_warnings.append(
                    "AI resolved its own proposal. Decisions proposed by AI "
                    "should normally be resolved by a human."
                )
            elif write_session.is_multi_actor():
                # v0.4.1 + Round-3 A1 / evt_016: the evt_025 pattern —
                # human→human (or system→system) same-instance self-resolution.
                # Gated to multi-actor sessions (≥2 actor types): in a
                # single-actor session this is legitimate, not an attribution
                # concern (the false positive A1 named with production data).
                guard_warnings.append(
                    "Same actor instance proposed and resolved this decision. "
                    "Per spec §3.6, in multi-actor workflows the proposer should "
                    "differ from the resolver."
                )

        # FM25: Suspiciously fast resolution (propose + resolve <5s by same
        # instance). ai→ai always warns (fast AI self-resolution is suspicious
        # regardless of actor count); the general non-ai case is gated to
        # multi-actor sessions, mirroring FM1 (Round-3 amendment A-R3-1 — without
        # this split, gating FM25 wholesale would silently drop the §3-correct
        # ai→ai FM25 warning).
        if is_self_resolution and not suppress:
            elapsed = (datetime.now(UTC) - target.timestamp).total_seconds()
            if elapsed < 5.0 and (resolved_by_type == "ai" or write_session.is_multi_actor()):
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

        if write_session.status == "completed":
            guard_warnings.append(
                "Resolved after session completion. Cross-session resolution of a "
                "proposed decision is the only permitted post-completion mutation; "
                "it is recorded here for audit transparency."
            )

        # Single validated construction (model_copy skips validation in
        # Pydantic v2, so the model_validate round-trip IS the C1 guarantee:
        # an invalid resolution state can never reach disk and brick the
        # session file on the next load). Existing warnings are preserved,
        # not clobbered.
        target.decision = DecisionData.model_validate(
            {
                **target.decision.model_dump(),
                "disposition": disposition,
                "resolved_by": resolver.model_dump(),
                "revision_note": revision_note,
                "warnings": [*target.decision.warnings, *guard_warnings],
            }
        )
        await storage.update_session(write_session)
        # Keep the caller's in-memory view coherent with what was persisted.
        session.events = write_session.events

    result = f"Decision {event_id} resolved: {disposition}"
    if guard_warnings:
        result += "\n" + "\n".join(f"  \u26a0\ufe0f {w}" for w in guard_warnings)
    return result

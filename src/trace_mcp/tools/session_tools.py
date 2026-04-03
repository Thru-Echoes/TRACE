"""Session management tools: start and end TRACE sessions."""

from __future__ import annotations

import platform
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

import trace_mcp
from trace_mcp.schema import Actor, Environment, Session, SessionMetadata, TraceEvent
from trace_mcp.storage.base import TraceStorage


# ── Attribution Audit Models ─────────────────────────────────────────────


class ContributionSummary(BaseModel):
    """Summary of a single contribution for the attribution audit."""

    event_id: str
    direction: str
    execution: str
    artifact: str | None = None
    description_preview: str


class DecisionSummary(BaseModel):
    """Summary of a single decision for the attribution audit."""

    event_id: str
    proposed_by_type: str
    suggestion_type: str | None = None
    disposition: str
    description_preview: str


class AttributionAudit(BaseModel):
    """Structured attribution audit returned at session end."""

    contributions: list[ContributionSummary] = Field(default_factory=list)
    decisions: list[DecisionSummary] = Field(default_factory=list)
    correction_count: int = 0
    corrected_event_ids: list[str] = Field(default_factory=list)
    revision_count: int = 0
    rejection_count: int = 0
    intervention_count: int = 0

    # Guard rail audit fields
    unresolved_decision_count: int = 0
    unresolved_decision_ids: list[str] = Field(default_factory=list)
    self_resolution_count: int = 0
    self_resolution_ids: list[str] = Field(default_factory=list)
    unlinked_correction_count: int = 0
    warnings: list[str] = Field(default_factory=list)

    def render(self) -> str:
        """Render the audit as a human-readable string."""
        lines = ["\n--- Attribution Audit ---"]

        if self.contributions:
            lines.append(f"Contributions ({len(self.contributions)}):")
            for c in self.contributions:
                artifact = f", artifact={c.artifact}" if c.artifact else ""
                lines.append(
                    f"  {c.event_id}: direction={c.direction}, "
                    f"execution={c.execution}{artifact} — \"{c.description_preview}\""
                )

        if self.decisions:
            lines.append(f"Decisions ({len(self.decisions)}):")
            for d in self.decisions:
                stype = f", suggestion={d.suggestion_type}" if d.suggestion_type else ""
                lines.append(
                    f"  {d.event_id}: proposed_by={d.proposed_by_type}{stype}, "
                    f"disposition={d.disposition} — \"{d.description_preview}\""
                )

        if self.correction_count:
            corrected = ", ".join(self.corrected_event_ids) if self.corrected_event_ids else "none linked"
            lines.append(f"Corrections: {self.correction_count} (corrects: {corrected})")

        if self.intervention_count:
            parts: list[str] = []
            if self.correction_count:
                parts.append(f"{self.correction_count} correction{'s' if self.correction_count != 1 else ''}")
            if self.revision_count:
                parts.append(f"{self.revision_count} revision{'s' if self.revision_count != 1 else ''}")
            if self.rejection_count:
                parts.append(f"{self.rejection_count} rejection{'s' if self.rejection_count != 1 else ''}")
            lines.append(f"Human interventions: {self.intervention_count} ({', '.join(parts)})")

        # Guard rail warnings
        if self.unresolved_decision_count:
            ids = ", ".join(self.unresolved_decision_ids)
            lines.append(
                f"Unresolved decisions: {self.unresolved_decision_count} ({ids})"
            )

        if self.self_resolution_count:
            ids = ", ".join(self.self_resolution_ids)
            lines.append(
                f"AI self-resolutions: {self.self_resolution_count} ({ids})"
            )

        if self.unlinked_correction_count:
            lines.append(
                f"Unlinked corrections: {self.unlinked_correction_count} "
                "(missing corrects_event_ids)"
            )

        if self.warnings:
            for w in self.warnings:
                lines.append(f"  \u26a0\ufe0f {w}")

        if (
            not self.contributions
            and not self.decisions
            and not self.correction_count
            and not self.warnings
        ):
            lines.append("No contributions, decisions, or corrections to review.")

        return "\n".join(lines)


def _build_attribution_audit(session: Session) -> AttributionAudit:
    """Build an attribution review summary for session-end verification."""
    contribs: list[ContributionSummary] = []
    decs: list[DecisionSummary] = []
    corrections = []
    rejected = []
    revised = []

    unresolved_ids: list[str] = []
    self_resolved_ids: list[str] = []
    unlinked_correction_count = 0
    audit_warnings: list[str] = []

    for e in session.events:
        if e.type == "contribution" and e.contribution:
            c = e.contribution
            desc = c.description[:80] + ("..." if len(c.description) > 80 else "")
            contribs.append(ContributionSummary(
                event_id=e.id,
                direction=c.direction,
                execution=c.execution,
                artifact=c.artifact,
                description_preview=desc,
            ))
        elif e.type == "decision" and e.decision:
            d = e.decision
            desc = d.description[:80] + ("..." if len(d.description) > 80 else "")
            decs.append(DecisionSummary(
                event_id=e.id,
                proposed_by_type=d.proposed_by.type,
                suggestion_type=d.suggestion_type,
                disposition=d.disposition,
                description_preview=desc,
            ))
            if d.disposition == "rejected":
                rejected.append(e)
            elif d.disposition == "revised":
                revised.append(e)

            # FM1/FM9: Track unresolved and self-resolved decisions
            if d.disposition == "proposed":
                unresolved_ids.append(e.id)
            elif d.resolved_by and d.proposed_by.type == d.resolved_by.type == "ai":
                self_resolved_ids.append(e.id)

        elif e.type == "annotation" and e.annotation and e.annotation.category == "correction":
            corrections.append(e)
            # FM17: Correction without corrects_event_ids
            if not e.annotation.corrects_event_ids:
                unlinked_correction_count += 1

    corrected_ids: list[str] = []
    for c in corrections:
        if c.annotation:
            corrected_ids.extend(c.annotation.corrects_event_ids)

    intervention_count = len(corrections) + len(rejected) + len(revised)

    # Build aggregate warnings
    if unresolved_ids:
        audit_warnings.append(
            f"{len(unresolved_ids)} decision(s) still in 'proposed' state — "
            "were they reviewed by the human?"
        )
    if self_resolved_ids:
        audit_warnings.append(
            f"{len(self_resolved_ids)} decision(s) were proposed and resolved by AI — "
            "verify human was consulted."
        )
    if unlinked_correction_count:
        audit_warnings.append(
            f"{unlinked_correction_count} correction(s) lack corrects_event_ids — "
            "link them for full provenance."
        )

    return AttributionAudit(
        contributions=contribs,
        decisions=decs,
        correction_count=len(corrections),
        corrected_event_ids=corrected_ids,
        revision_count=len(revised),
        rejection_count=len(rejected),
        intervention_count=intervention_count,
        unresolved_decision_count=len(unresolved_ids),
        unresolved_decision_ids=unresolved_ids,
        self_resolution_count=len(self_resolved_ids),
        self_resolution_ids=self_resolved_ids,
        unlinked_correction_count=unlinked_correction_count,
        warnings=audit_warnings,
    )


def _generate_session_id() -> str:
    now = datetime.now(UTC)
    short_hex = uuid.uuid4().hex[:6]
    return f"trace_{now.strftime('%Y%m%d')}_{short_hex}"


def _auto_environment() -> Environment:
    return Environment(
        client="Claude Code",
        os=f"{platform.system()} {platform.release()}",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        trace_version=trace_mcp.__version__,
        custom={"arch": platform.machine()},
    )


async def create_session(
    storage: TraceStorage,
    active_sessions: dict[str, Session],
    *,
    project: str,
    experiment_id: str | None = None,
    description: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
) -> Session:
    """Create and register a new TRACE session. Returns the Session object.

    This is the low-level creation function used by both ``start_session``
    (explicit) and the server's auto-session mechanism.
    """
    session_id = _generate_session_id()
    actor_list = [Actor(**p) for p in participants] if participants else []
    env = _auto_environment()

    session = Session(
        id=session_id,
        metadata=SessionMetadata(
            project=project,
            experiment_id=experiment_id,
            description=description,
            participants=actor_list,
            environment=env,
            tags=tags or [],
        ),
    )
    await storage.create_session(session)
    active_sessions[session_id] = session
    return session


async def start_session(
    storage: TraceStorage,
    active_sessions: dict[str, Session],
    *,
    project: str,
    experiment_id: str | None = None,
    description: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Start a new TRACE audit session."""
    session = await create_session(
        storage,
        active_sessions,
        project=project,
        experiment_id=experiment_id,
        description=description,
        participants=participants,
        tags=tags,
    )

    path = storage._session_path(session.id) if hasattr(storage, "_session_path") else "disk"  # type: ignore[attr-defined]
    return (
        f"TRACE audit logging is now active.\n"
        f"Session: {session.id}\n"
        f"Project: {project}\n"
        f"File: {path}\n"
        f"All tool calls, decisions, and annotations will be recorded."
    )


async def end_session(
    storage: TraceStorage,
    active_sessions: dict[str, Session],
    *,
    session_id: str,
    summary: str | None = None,
) -> str:
    """End a TRACE audit session."""
    session = active_sessions.get(session_id)
    if session is None:
        try:
            session = await storage.get_session(session_id)
        except FileNotFoundError:
            return f"Error: Session '{session_id}' not found."

    # Guard: prevent double-ending — completed sessions are immutable
    if session.status == "completed":
        return (
            f"Error: Session '{session_id}' already ended at {session.ended}. "
            f"Completed sessions are immutable and cannot be ended again."
        )

    session.ended = datetime.now(UTC)
    session.status = "completed"
    session.summary = summary

    await storage.update_session(session)
    active_sessions.pop(session_id, None)

    # Count events by type
    counts: dict[str, int] = {}
    for evt in session.events:
        counts[evt.type] = counts.get(evt.type, 0) + 1
    total = len(session.events)
    parts = [f"{v} {k.replace('_', ' ')}s" for k, v in sorted(counts.items())]
    detail = ", ".join(parts) if parts else "no events"

    audit = _build_attribution_audit(session)
    return f"Session ended: {session_id}\n{total} events: {detail}{audit.render()}"


async def get_or_load_session(
    storage: TraceStorage,
    active_sessions: dict[str, Session],
    session_id: str,
) -> Session:
    """Get session from memory or load from disk. Raises FileNotFoundError."""
    session = active_sessions.get(session_id)
    if session is not None:
        return session
    session = await storage.get_session(session_id)
    active_sessions[session_id] = session
    return session


def _check_referential_integrity(
    session: Session,
    event: TraceEvent,
) -> list[str]:
    """Check that event ID references point to existing events. Returns warnings."""
    warnings: list[str] = []
    existing_ids = {e.id for e in session.events}

    ids_to_check: list[tuple[str, str]] = []

    # Collect all referenced IDs from the event
    if event.annotation and event.annotation.corrects_event_ids:
        for ref_id in event.annotation.corrects_event_ids:
            ids_to_check.append((ref_id, "corrects_event_ids"))

    if event.decision and event.decision.revises_event_id:
        ids_to_check.append((event.decision.revises_event_id, "revises_event_id"))

    if event.tool_call and event.tool_call.retries_event_id:
        ids_to_check.append((event.tool_call.retries_event_id, "retries_event_id"))

    if event.contribution and event.contribution.related_decision_ids:
        for ref_id in event.contribution.related_decision_ids:
            ids_to_check.append((ref_id, "related_decision_ids"))

    for ref_id, field_name in ids_to_check:
        if ref_id not in existing_ids:
            warnings.append(
                f"Dangling reference: {field_name} contains '{ref_id}' "
                f"which does not exist in this session."
            )

    return warnings


async def append_event(
    storage: TraceStorage,
    session: Session,
    event: TraceEvent,
) -> str:
    """Append an event to a session and flush to disk. Returns event ID.

    Raises ValueError if the session is already completed (immutability guard).
    Raises ValueError if event references point to nonexistent events (FM13/FM16).
    """
    # Guard: prevent logging to completed sessions — immutability guarantee
    if session.status == "completed":
        raise ValueError(
            f"Cannot append events to completed session '{session.id}'. "
            f"Session ended at {session.ended}. Start a new session instead."
        )

    if not event.id:
        event.id = session.next_event_id()
    event.session_id = session.id

    # FM13/FM16/FM17: Validate referential integrity — reject invalid references
    ref_errors = _check_referential_integrity(session, event)
    if ref_errors:
        raise ValueError(
            f"Invalid event references in {event.id}:\n"
            + "\n".join(f"  - {e}" for e in ref_errors)
        )

    session.events.append(event)
    await storage.update_session(session)

    return event.id

"""Session management tools: start and end TRACE sessions."""

from __future__ import annotations

import platform
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from trace_mcp.schema import Actor, Environment, Session, SessionMetadata, TraceEvent
from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.locked import locked_disk_session

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


# v0.4.1: explicit absence markers for conversation_snippet per spec §3.4.1.
# Documented allow-list; producers MAY define additional markers using the
# `<...>` convention but these are the canonical ones surfaced in the audit.
_EXPLICIT_ABSENCE_MARKERS = frozenset({
    "<autonomous-stretch>",
    "<no recent user message>",
})


def _is_explicit_absence(s: str | None) -> bool:
    """True if s is an explicit absence marker per spec §3.4.1.

    Used by the AttributionAudit to distinguish:
      (a) null / missing snippet (controller forgot — a violation)
      (b) explicit absence marker (honest "no user message" — acceptable)
      (c) real user-message snippet (the normal case)

    Allow-list semantics prevent false-positives on real user text that
    happens to be angle-bracketed (e.g., `<script>` for code review).
    Whitespace-tolerant via .strip() so leading/trailing whitespace in
    the marker doesn't cause silent misclassification.
    """
    if s is None:
        return False
    return s.strip() in _EXPLICIT_ABSENCE_MARKERS


# v0.4.1: phrase list for orphan-discovery hint detector (spec §3.7 + §8.1).
# Tightened in the v0.4.1 remediation (P4 / A8): dropped the over-broad
# "turned out" (false-positive on routine prose like "turned out cleaner").
# The remaining phrases are technical-discovery markers that strongly
# suggest a load-bearing finding that should have been its own
# discovery/correction/gotcha event.
_DISCOVERY_PHRASES = (
    "discovered",
    "found a bug",
    "load-bearing fix",
)


class AttributionAudit(BaseModel):
    """Structured attribution audit returned at session end."""

    contributions: list[ContributionSummary] = Field(default_factory=list)
    decisions: list[DecisionSummary] = Field(default_factory=list)
    correction_count: int = 0
    corrected_event_ids: list[str] = Field(default_factory=list)
    revision_count: int = 0
    rejection_count: int = 0
    intervention_count: int = 0

    # Guard rail audit fields (v0.3.x)
    unresolved_decision_count: int = 0
    unresolved_decision_ids: list[str] = Field(default_factory=list)
    self_resolution_count: int = 0  # ai-only (backward compat)
    self_resolution_ids: list[str] = Field(default_factory=list)
    unlinked_correction_count: int = 0
    warnings: list[str] = Field(default_factory=list)

    # v0.4.1: extended audit fields surfacing the silent-warning failures
    # from the waggle audit. Default 0 keeps the model backward-compatible.
    missing_snippet_contribution_count: int = 0
    missing_snippet_correction_count: int = 0
    explicit_absence_snippet_count: int = 0
    orphan_discovery_hint_count: int = 0
    attribution_warning_count: int = 0  # same-instance self-resolution (any type)
    attribution_warning_ids: list[str] = Field(default_factory=list)
    orphan_discovery_event_ids: list[str] = Field(default_factory=list)

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

        # v0.4.1: generalized same-instance self-resolution count (any actor
        # type, per spec §3.6 Proposer Identity Rule). May overlap with
        # self_resolution_count for ai→ai events — kept separate so v0.3
        # consumers reading self_resolution_count don't see a behavior change.
        if self.attribution_warning_count:
            ids = ", ".join(self.attribution_warning_ids)
            lines.append(
                f"Attribution warnings (v0.4.1 same-instance self-resolution): "
                f"{self.attribution_warning_count} ({ids}) — per spec §3.6"
            )

        if self.unlinked_correction_count:
            lines.append(
                f"Unlinked corrections: {self.unlinked_correction_count} "
                "(missing corrects_event_ids)"
            )

        if self.orphan_discovery_hint_count:
            ids = ", ".join(self.orphan_discovery_event_ids)
            lines.append(
                f"Orphan-discovery hints (v0.4.1): {self.orphan_discovery_hint_count} "
                f"contribution(s) describing discoveries with no near-in-time "
                f"discovery/correction/gotcha annotation ({ids}) — "
                "consider logging at the moment of discovery per spec §8.1"
            )

        if (
            self.missing_snippet_contribution_count
            or self.missing_snippet_correction_count
        ):
            parts2: list[str] = []
            if self.missing_snippet_contribution_count:
                parts2.append(f"{self.missing_snippet_contribution_count} contribution(s)")
            if self.missing_snippet_correction_count:
                parts2.append(f"{self.missing_snippet_correction_count} correction(s)")
            lines.append(
                f"Missing conversation_snippet (v0.4.1, spec §3.4.1 MUST): "
                f"{', '.join(parts2)} — set the user message or use "
                "'<autonomous-stretch>' / '<no recent user message>'"
            )

        if self.explicit_absence_snippet_count:
            lines.append(
                f"Explicit-absence snippet markers: {self.explicit_absence_snippet_count} "
                "(honest absences — not warnings)"
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
    """Build an attribution review summary for session-end verification.

    v0.4.1: extended with snippet coverage counts, structural attribution
    warning, orphan-discovery hint, and dispatch-visibility hint. All new
    metrics surface the silent-warning failures the waggle audit identified.
    """
    from datetime import timedelta

    contribs: list[ContributionSummary] = []
    decs: list[DecisionSummary] = []
    corrections = []
    rejected = []
    revised = []

    unresolved_ids: list[str] = []
    self_resolved_ids: list[str] = []  # ai-only (backward compat)
    unlinked_correction_count = 0
    audit_warnings: list[str] = []

    # v0.4.1 metrics
    missing_snippet_contribution = 0
    missing_snippet_correction = 0
    explicit_absence_snippet = 0
    attribution_warning_ids: list[str] = []
    orphan_discovery_ids: list[str] = []
    contribution_count = 0
    tool_call_count = 0

    # Pre-index discovery / correction / gotcha annotation timestamps for
    # the orphan-discovery scan. This lets each contribution check the
    # 30-minute pre-window in O(K) rather than O(N²).
    discovery_anchors: list[tuple[str, datetime]] = []  # (event_id, timestamp)
    for e in session.events:
        if e.type == "annotation" and e.annotation and e.annotation.category in (
            "discovery",
            "correction",
            "gotcha",
        ):
            discovery_anchors.append((e.id, e.timestamp))

    discovery_window = timedelta(minutes=30)

    for e in session.events:
        if e.type == "tool_call":
            tool_call_count += 1
        if e.type == "contribution" and e.contribution:
            contribution_count += 1
            c = e.contribution
            desc = c.description[:80] + ("..." if len(c.description) > 80 else "")
            contribs.append(ContributionSummary(
                event_id=e.id,
                direction=c.direction,
                execution=c.execution,
                artifact=c.artifact,
                description_preview=desc,
            ))

            # v0.4.1 L5.3: count contributions missing conversation_snippet.
            # explicit_absence markers count separately so honest absences
            # (no user message during autonomous stretch) aren't penalized.
            snip = e.context.conversation_snippet
            if snip is None:
                missing_snippet_contribution += 1
            elif _is_explicit_absence(snip):
                explicit_absence_snippet += 1

            # v0.4.1 L5.5: orphan-discovery hint. If this contribution's
            # description contains a discovery phrase but no
            # discovery/correction/gotcha annotation exists within 30 min
            # before this contribution, the discovery was likely logged
            # post-hoc and lost as a discrete provenance event.
            desc_lower = c.description.lower()
            if any(phrase in desc_lower for phrase in _DISCOVERY_PHRASES):
                window_start = e.timestamp - discovery_window
                has_anchor = any(
                    window_start <= ts <= e.timestamp
                    for _, ts in discovery_anchors
                )
                if not has_anchor:
                    orphan_discovery_ids.append(e.id)

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

            # FM1/FM9: Track unresolved and (ai-only) self-resolved decisions
            if d.disposition == "proposed":
                unresolved_ids.append(e.id)
            elif d.resolved_by:
                # Existing ai-only self-resolution count (backward compat).
                if d.proposed_by.type == d.resolved_by.type == "ai":
                    self_resolved_ids.append(e.id)

                # v0.4.1 L5.4 + Round-3 A1 / evt_016: STRUCTURAL
                # attribution-warning detector. Same-instance self-resolution
                # (type AND id), gated to MULTI-ACTOR sessions (≥2 actor
                # types). Catches the evt_025 pattern (human-proposes plan,
                # human-accepts) in a real multi-actor workflow, while not
                # false-firing on single-actor sessions (solo human, ai→ai
                # solo, system→system) — the false positive A1 named with
                # production data. ai→ai is still surfaced separately and
                # unconditionally via self_resolved_ids above.
                if (
                    d.proposed_by.type == d.resolved_by.type
                    and d.proposed_by.id == d.resolved_by.id
                    and session.is_multi_actor()
                ):
                    attribution_warning_ids.append(e.id)

        elif e.type == "annotation" and e.annotation and e.annotation.category == "correction":
            corrections.append(e)
            # FM17: Correction without corrects_event_ids
            if not e.annotation.corrects_event_ids:
                unlinked_correction_count += 1

            # v0.4.1 L5.3: count corrections missing conversation_snippet.
            snip = e.context.conversation_snippet
            if snip is None:
                missing_snippet_correction += 1
            elif _is_explicit_absence(snip):
                explicit_absence_snippet += 1

    corrected_ids: list[str] = []
    for c in corrections:
        if c.annotation:
            corrected_ids.extend(c.annotation.corrects_event_ids)

    intervention_count = len(corrections) + len(rejected) + len(revised)

    # Build aggregate warnings (severity-ordered to match render())
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
    if attribution_warning_ids:
        audit_warnings.append(
            f"{len(attribution_warning_ids)} decision(s) with same-instance "
            "self-resolution (any actor type) — per spec §3.6 Proposer Identity "
            "Rule, proposer should differ from resolver in multi-actor workflows."
        )
    if unlinked_correction_count:
        audit_warnings.append(
            f"{unlinked_correction_count} correction(s) lack corrects_event_ids — "
            "link them for full provenance."
        )
    # P4 / A8: orphan-discovery is surfaced ONLY as the low-severity
    # `orphan_discovery_hint_count` (rendered separately). It is deliberately
    # NOT pushed into `audit_warnings` — duplicating a heuristic signal at
    # warning severity over-weighted it (Round-1/2/3 finding).
    if missing_snippet_contribution or missing_snippet_correction:
        parts3: list[str] = []
        if missing_snippet_contribution:
            parts3.append(f"{missing_snippet_contribution} contribution(s)")
        if missing_snippet_correction:
            parts3.append(f"{missing_snippet_correction} correction(s)")
        audit_warnings.append(
            f"Missing conversation_snippet (spec §3.4.1 MUST): {', '.join(parts3)}. "
            "Set to the user message or use '<autonomous-stretch>' to mark explicit absence."
        )

    # v0.4.1 L5.6: dispatch-visibility hint (advisory only, no counter).
    # Raised threshold per Round 3 amendment A3 — production sessions
    # routinely have 0 tool_call events, so a low threshold would generate
    # permanent noise. Guard against missing environment for legacy sessions.
    env = session.metadata.environment
    if (
        contribution_count >= 10
        and tool_call_count == 0
        and env is not None
        and env.client == "Claude Code"
    ):
        audit_warnings.append(
            f"[hint] Session has {contribution_count} contributions and 0 tool_call "
            "events. If subagent dispatches occurred, consider logging them as "
            "tool_call(host='internal', server='claude-code', parent_event_id=...) "
            "per spec §3.5. Advisory only."
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
        # v0.4.1 fields
        missing_snippet_contribution_count=missing_snippet_contribution,
        missing_snippet_correction_count=missing_snippet_correction,
        explicit_absence_snippet_count=explicit_absence_snippet,
        orphan_discovery_hint_count=len(orphan_discovery_ids),
        orphan_discovery_event_ids=orphan_discovery_ids,
        attribution_warning_count=len(attribution_warning_ids),
        attribution_warning_ids=attribution_warning_ids,
    )


def _generate_session_id() -> str:
    now = datetime.now(UTC)
    short_hex = uuid.uuid4().hex[:6]
    return f"trace_{now.strftime('%Y%m%d')}_{short_hex}"


def _auto_environment() -> Environment:
    # v0.4.1: trace_version removed from Environment — single source of truth
    # is Session.trace_version (see schema/session.py docstring).
    return Environment(
        client="Claude Code",
        os=f"{platform.system()} {platform.release()}",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        custom={"arch": platform.machine()},
    )


# v0.4.2: sequential-cadence steering for the session bootstrap. The Claude
# Code thinking-block 400 fires when one interleaved-thinking assistant turn
# accumulates a large content-block count; the opening TRACE bootstrap was the
# most automatic recurring inflator (eager list/get/health fan-out). This note
# tells the model it already has what it needs, so it logs sequentially rather
# than batching many trace_* calls into the first turn.
_BOOTSTRAP_CADENCE = (
    "Log sequentially: record events as they happen (1-2 trace calls per turn; "
    "do not batch many trace_* calls into a single turn). You have what you need "
    "to begin — no need to enumerate prior sessions to orient."
)


def format_bootstrap_message(
    *,
    session_id: str,
    project: str,
    path: str,
    brief: dict[str, Any] | None = None,
    recalled_block: str = "",
) -> str:
    """Build the start_session bootstrap message (pure function, no I/O).

    Inputs:
      session_id / project / path — identity of the new session.
      brief — bounded orientation from ``storage.session_brief`` (or None).
      recalled_block — pre-rendered learnings block (empty unless the caller
        explicitly opted into recall; recall is OFF by default in v0.4.2).

    Output: the full human/agent-facing activation message, including a bounded
    prior-session orientation line and the sequential-cadence steering note.
    """
    if brief and brief.get("most_recent"):
        mr = brief["most_recent"]
        plus = "+" if brief.get("capped") else ""
        created = (mr.get("created") or "")[:10]
        orientation = (
            f"Prior context: {brief.get('matched', 0)}{plus} recent session(s) for "
            f"'{project}'; most recent: {mr['id']} ({mr.get('event_count', 0)} events"
            f"{', ' + created if created else ''})."
        )
    else:
        orientation = f"Prior context: No prior TRACE sessions recorded for '{project}'."

    lines = [
        "TRACE audit logging is now active.",
        f"Session: {session_id}",
        f"Project: {project}",
        f"File: {path}",
        orientation,
        _BOOTSTRAP_CADENCE,
    ]
    msg = "\n".join(lines)
    if recalled_block:
        msg += recalled_block
    msg += "\nAll tool calls, decisions, and annotations will be recorded."
    return msg


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
    # Core-level, fail-safe probe of the OPTIONAL trace-learn extension
    # (keeps the core/extension boundary intact — governance evt_002).
    from trace_mcp.extension_status import get_extension_status

    return (
        f"TRACE audit logging is now active.\n"
        f"Session: {session.id}\n"
        f"Project: {project}\n"
        f"File: {path}\n"
        f"All tool calls, decisions, and annotations will be recorded.\n"
        f"{get_extension_status()}"
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

    # Fast-path guard on the in-memory view (the definitive disk check is under the lock).
    if session.status == "completed":
        return (
            f"Error: Session '{session_id}' already ended at {session.ended}. "
            f"Completed sessions are immutable and cannot be ended again."
        )

    # Concurrency-safe (v0.4.2 symmetry with append_event): mutate + write under
    # the per-session lock, reloading authoritative on-disk events first, so a
    # concurrent append landing between read and write is preserved (not
    # clobbered) and the immutability guard sees disk truth.
    async with locked_disk_session(storage, session_id, fallback=session) as disk:
        if disk is not session:
            if disk.status == "completed":
                return (
                    f"Error: Session '{session_id}' already ended at {disk.ended}. "
                    f"Completed sessions are immutable and cannot be ended again."
                )
            session.events = disk.events
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


# v0.4.1: URI-form references in corrects_event_ids per spec §3.7.1.
# A string matching this pattern is treated as a URI reference (e.g.,
# "external:...", "jsonl:...", "subagent:...", "tool-result:...") and is
# exempt from in-session existence checking. Event IDs follow the
# evt_NNN convention and never contain colons.
_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9-]+:")


def is_uri_form_reference(s: str) -> bool:
    """True if s is a URI-form reference per spec §3.7.1.

    Used to exempt out-of-session anchors (subagent outputs, external
    documents) from referential-integrity checking on corrects_event_ids.
    """
    return bool(_URI_SCHEME_RE.match(s))


def _check_referential_integrity(
    session: Session,
    event: TraceEvent,
) -> list[str]:
    """Check that event ID references point to existing events. Returns warnings."""
    warnings: list[str] = []
    existing_ids = {e.id for e in session.events}

    ids_to_check: list[tuple[str, str]] = []

    # Collect all referenced IDs from the event.
    # v0.4.1: corrects_event_ids MAY contain URI-form references per spec §3.7.1;
    # those are exempt from in-session validation (the referenced item exists
    # outside the TRACE event log).
    if event.annotation and event.annotation.corrects_event_ids:
        for ref_id in event.annotation.corrects_event_ids:
            if is_uri_form_reference(ref_id):
                continue
            ids_to_check.append((ref_id, "corrects_event_ids"))

    if event.decision and event.decision.revises_event_id:
        ids_to_check.append((event.decision.revises_event_id, "revises_event_id"))

    if event.tool_call and event.tool_call.retries_event_id:
        ids_to_check.append((event.tool_call.retries_event_id, "retries_event_id"))

    # v0.4.1: parent_event_id on tool_call must point to a real event in the
    # session (the controller-side event that motivated the dispatch). Without
    # this check, PROV-LD's wasInformedBy edges could silently point at
    # nonexistent events — Verifier C correctly flagged this gap.
    if event.tool_call and event.tool_call.parent_event_id:
        ids_to_check.append((event.tool_call.parent_event_id, "parent_event_id"))

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

    Concurrency-safe (v0.4.2): the read-modify-write runs under a per-session
    lock and reloads the authoritative on-disk events before appending. This
    closes the lost-update + duplicate-evt_id defect where a second writer
    (another process, or a stale in-memory Session) overwrote the first writer's
    event and both were assigned the same positional id. Storage backends
    without a ``lock`` degrade to a no-op context (no behaviour change).

    Raises ValueError if the session is already completed (immutability guard)
    or if event references point to nonexistent events (FM13/FM16/FM17).
    """
    # Fast-path guard on the in-memory view.
    if session.status == "completed":
        raise ValueError(
            f"Cannot append events to completed session '{session.id}'. "
            f"Session ended at {session.ended}. Start a new session instead."
        )

    async with locked_disk_session(storage, session.id, fallback=session) as disk:
        # Reload authoritative on-disk state so we never clobber events another
        # writer persisted since this Session was last read. `disk is session`
        # only when nothing is persisted yet (brand-new session).
        if disk is not session:
            if disk.status == "completed":
                raise ValueError(
                    f"Cannot append events to completed session '{session.id}'. "
                    f"Session ended at {disk.ended}. Start a new session instead."
                )
            session.events = disk.events

        if not event.id:
            event.id = session.next_event_id()
        event.session_id = session.id

        # PR D #2: refuse to mint/accept an id that already exists in the
        # reloaded on-disk session. A positional next_event_id() can only
        # collide if the record is already aliased (e.g. after a pre-fix
        # unlocked write left two events sharing an id); writing another event
        # under that id would silently alias revises_event_id / parent_event_id
        # / corrects_event_ids references. Fail loudly instead of corrupting.
        if any(e.id == event.id for e in session.events):
            raise ValueError(
                f"Refusing to append event with duplicate id '{event.id}' in "
                f"session '{session.id}': that id already exists on disk. This "
                f"indicates an aliased/corrupted session record — writing under "
                f"the same id would alias references. Investigate the session "
                f"file rather than appending."
            )

        # FM13/FM16/FM17: validate referential integrity against the merged state.
        ref_errors = _check_referential_integrity(session, event)
        if ref_errors:
            raise ValueError(
                f"Invalid event references in {event.id}:\n"
                + "\n".join(f"  - {e}" for e in ref_errors)
            )

        session.events.append(event)
        await storage.update_session(session)

    return event.id

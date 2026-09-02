"""Build a TRACE session from a Gents run timeline.

[Gents](https://github.com/source-inc/gents) is an agent runtime that persists
every request, message, inference call, and tool call as a document and can
reconstruct one request's event stream with
``gents trace timeline --request-id <id>``. This module maps that timeline into
a TRACE session so a run driven by that runtime can be read with the same tools
as any other TRACE record.

What the mapping does and does not claim:

- A Gents timeline records **what happened**: which tools ran, with what
  arguments, under which agent identity. That maps cleanly onto TRACE
  ``tool_call`` and ``state_change`` events.
- It does **not** record who proposed a step, or whether a human accepted,
  revised, or rejected it. So this importer never synthesizes a TRACE
  ``decision``. A tool approval is an authorization gate, not a design
  decision, and inventing decisions from execution traces is exactly the
  fabrication TRACE exists to prevent. Decisions come from an agent that logged
  them at the time (see ``docs/integrations/gents.md``).

Drift policy. Gents is pre-1.0 and ships frequently, so this importer
distinguishes two kinds of change:

- **Unknown event kinds fail loud.** A new ``kind`` means provenance this
  mapping would silently drop, so ``import_timeline`` raises
  ``UnknownEventKindError`` naming it.
- **Unknown fields are tolerated.** Additive fields cannot corrupt a mapping
  that reads by name. Required fields are declared as required, so a rename or
  removal fails validation rather than producing empty values.

Exports: ``import_timeline``, ``load_timeline``, ``main``,
``UnknownEventKindError``, ``GentsTimeline``, ``GentsToolCall``.

Side effects: none in the mapping functions. ``main`` reads a file and writes
JSON to stdout or to ``--output``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trace_mcp import __version__
from trace_mcp.schema import (
    Actor,
    Session,
    SessionMetadata,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)

# Event kinds a Gents timeline can carry, as of v0.14.0. Kinds outside this set
# raise rather than being skipped: silence here would mean lost provenance.
_MAPPED_KINDS = {"request", "response", "tool_call", "tool_approval", "goal_transition"}
_METADATA_ONLY_KINDS = {"message", "inference_call", "rendered_request", "compaction", "provider_context_reduction"}
_KNOWN_KINDS = _MAPPED_KINDS | _METADATA_ONLY_KINDS

# Gents lifecycle_state values that mean the call did not succeed.
_FAILED_STATES = {"failed", "timedOut", "cancelled", "denied"}


class UnknownEventKindError(ValueError):
    """A timeline carried an event kind this importer does not map.

    Raised instead of skipping, so a Gents release that adds an event type is
    reported rather than silently dropped from the imported record.
    """


class _GentsModel(BaseModel):
    """Base for timeline rows: required fields are enforced, extra fields kept.

    ``extra="allow"`` is deliberate. Forbidding unknown fields would break this
    importer on any additive Gents release, which is frequent, without
    protecting anything: a mapping that reads fields by name cannot be
    corrupted by a field it never reads.
    """

    model_config = ConfigDict(extra="allow")


class GentsToolCall(_GentsModel):
    """One ``tool_call`` row of a Gents run timeline."""

    kind: Literal["tool_call"]
    tool_name: str
    tool_call_id: str | None = None
    args: str | None = None
    result: str | None = None
    status: str | None = None
    lifecycle_state: str | None = None
    latency_ms: str | int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    message_sequence: str | int | None = None
    # Present only when the call went out to a registered MCP service.
    selected_service_id: str | None = None
    selected_tool_name: str | None = None
    # Present only when the call spawned a child request.
    child_request_id: str | None = None


class GentsToolApproval(_GentsModel):
    """One ``tool_approval`` row: an operator decision on a held tool call."""

    kind: Literal["tool_approval"]
    approval_id: str | None = None
    tool_call_id: str | None = None
    decision: str | None = None
    approver_did: str | None = None
    reason: str | None = None
    created_at: str | None = None


class GentsLifecycleEvent(_GentsModel):
    """A ``request``, ``response``, or ``goal_transition`` row."""

    kind: Literal["request", "response", "goal_transition"]
    status: str | None = None
    lifecycle_state: str | None = None
    timestamp: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    failure_reason: str | None = None
    execution_origin: str | None = None


class GentsSession(_GentsModel):
    """The ``session`` block of a timeline."""

    session_id: str | None = None
    behavior_id: str | None = None
    agent_name: str | None = None
    started: str | None = None
    status: str | None = None


class GentsTimeline(_GentsModel):
    """A whole ``gents trace timeline`` document."""

    request_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    agent_did: str | None = None
    behavior_id: str | None = None
    session_id: str | None = None
    request_doc_id: str | None = None
    session: GentsSession | None = None
    child_request_ids: list[str] = Field(default_factory=list)


def load_timeline(path: Path) -> GentsTimeline:
    """Read and validate a timeline JSON file. Side effect: reads *path*."""
    return GentsTimeline.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse a Gents timestamp, returning None when absent or unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_args(raw: str | None) -> dict[str, Any]:
    """Decode a tool call's ``args`` string into a dict.

    Gents stores arguments as a JSON string. A payload that is absent, invalid,
    or not an object is preserved under a ``raw`` key rather than dropped, so
    the imported record never claims arguments it could not read.
    """
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _duration_ms(value: str | int | None) -> int | None:
    """Coerce Gents' latency field (a string in current builds) to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_call_event(row: GentsToolCall, actor: Actor, session_id: str) -> TraceEvent:
    """Map one Gents tool call onto a TRACE ``tool_call`` event.

    ``host`` follows where the call actually went: ``mcp`` when the runtime
    routed it to a registered MCP service, ``internal`` otherwise (the
    runtime's own file, shell, and subagent tools).
    """
    is_mcp = bool(row.selected_service_id)
    failed = (row.lifecycle_state in _FAILED_STATES) or (row.status in _FAILED_STATES)
    return TraceEvent(
        session_id=session_id,
        type="tool_call",
        actor=actor,
        timestamp=_parse_timestamp(row.started_at) or datetime.now(UTC),
        tool_call=ToolCallData(
            server=row.selected_service_id or "gents",
            name=row.selected_tool_name or row.tool_name,
            input=_parse_args(row.args),
            output=row.result,
            status="error" if failed else "success",
            duration_ms=_duration_ms(row.latency_ms),
            host="mcp" if is_mcp else "internal",
        ),
    )


def _approval_events(row: GentsToolApproval, actor: Actor, session_id: str) -> list[TraceEvent]:
    """Map a tool approval onto a ``state_change``, never onto a decision.

    A Gents approval authorizes one held tool call; it does not record a design
    choice, and its ``approver_did`` is a claimed identity that the runtime does
    not yet bind to a signature. Both facts are stamped on the event so a reader
    of the imported record cannot mistake it for a resolved TRACE decision or
    for verified human sign-off.
    """
    decision = row.decision or "unknown"
    return [
        TraceEvent(
            session_id=session_id,
            type="state_change",
            actor=actor,
            timestamp=_parse_timestamp(row.created_at) or datetime.now(UTC),
            state_change=StateChangeData(
                description=(
                    f"Gents tool-call authorization: {decision}"
                    f" (tool_call_id={row.tool_call_id or 'unknown'}). "
                    "Approver identity is claimed by the runtime, not verified, and this is "
                    "an authorization gate rather than a TRACE decision."
                ),
                field="tool_approval",
                old_value="awaitingApproval",
                new_value=decision,
                reason=row.reason,
            ),
        )
    ]


def _lifecycle_event(row: GentsLifecycleEvent, actor: Actor, session_id: str) -> TraceEvent:
    """Map a request, response, or goal transition onto a ``state_change``."""
    state = row.lifecycle_state or row.status or "unknown"
    return TraceEvent(
        session_id=session_id,
        type="state_change",
        actor=actor,
        timestamp=_parse_timestamp(row.timestamp) or datetime.now(UTC),
        state_change=StateChangeData(
            description=f"Gents {row.kind} reached state '{state}'",
            field=f"gents.{row.kind}",
            new_value=state,
            reason=row.failure_reason or None,
        ),
    )


def import_timeline(
    timeline: GentsTimeline,
    *,
    project: str,
    session_id: str | None = None,
) -> Session:
    """Build a TRACE `Session` from a validated Gents timeline. Pure.

    Inputs: *timeline* as returned by `load_timeline`; *project*, the TRACE
    project label to record; *session_id*, an override for the generated id.

    Output: an unsaved `Session`. Message bodies, rendered prompts, and
    inference-call payloads are counted in metadata but never copied: they are
    model input and output, not provenance, and copying them would move a large
    amount of free text into a second store.

    Raises `UnknownEventKindError` when the timeline carries an event kind this
    mapping does not know.
    """
    actor = Actor(type="ai", id=timeline.agent_did or "gents-agent", role="agent-runtime")
    sid = session_id or f"gents_{timeline.request_id}"

    kinds_seen: dict[str, int] = {}
    events: list[TraceEvent] = []

    for raw in timeline.events:
        kind = raw.get("kind")
        if kind not in _KNOWN_KINDS:
            raise UnknownEventKindError(
                f"unrecognized Gents timeline event kind {kind!r} in request "
                f"{timeline.request_id}. This importer was written against Gents v0.14.0; "
                "a newer runtime may have added an event type that carries provenance "
                "this mapping would drop."
            )
        kinds_seen[kind] = kinds_seen.get(kind, 0) + 1
        if kind in _METADATA_ONLY_KINDS:
            continue
        if kind == "tool_call":
            events.append(_tool_call_event(GentsToolCall.model_validate(raw), actor, sid))
        elif kind == "tool_approval":
            events.extend(_approval_events(GentsToolApproval.model_validate(raw), actor, sid))
        else:
            events.append(_lifecycle_event(GentsLifecycleEvent.model_validate(raw), actor, sid))

    events.sort(key=lambda e: e.timestamp)
    for index, event in enumerate(events, start=1):
        event.id = f"evt_{index:03d}"

    started = _parse_timestamp(timeline.session.started if timeline.session else None)
    return Session(
        id=sid,
        created=started or (events[0].timestamp if events else datetime.now(UTC)),
        ended=events[-1].timestamp if events else None,
        status="completed",
        metadata=SessionMetadata(
            project=project,
            description=(
                f"Imported from a Gents run timeline for request {timeline.request_id}. "
                "Execution record only: it carries no decisions, because a run timeline "
                "does not record who proposed a step or whether a human accepted it."
            ),
            participants=[actor],
            tags=["imported", "gents"],
            custom={
                "source": "gents-run-timeline",
                "importer": f"trace-mcp {__version__}",
                "gents_request_id": timeline.request_id,
                "gents_session_id": timeline.session_id,
                "gents_agent_did": timeline.agent_did,
                "gents_behavior_id": timeline.behavior_id,
                "gents_child_request_ids": timeline.child_request_ids,
                "timeline_event_counts": kinds_seen,
                "not_imported": sorted(k for k in kinds_seen if k in _METADATA_ONLY_KINDS),
            },
        ),
        events=events,
        summary=(
            f"Gents request {timeline.request_id}: "
            f"{sum(1 for e in events if e.type == 'tool_call')} tool calls imported."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``trace-mcp import gents``. Returns an exit code.

    Side effects: reads the timeline file; writes the session JSON to stdout,
    or to ``--output`` when given. It deliberately does not write into the
    session store: an imported record should be reviewed before it becomes part
    of a project's provenance.
    """
    parser = argparse.ArgumentParser(
        prog="trace-mcp import gents",
        description="Build a TRACE session from a `gents trace timeline` JSON export.",
    )
    parser.add_argument("timeline", type=Path, help="Path to the timeline JSON file.")
    parser.add_argument("--project", required=True, help="TRACE project label to record.")
    parser.add_argument("--session-id", default=None, help="Override the generated session id.")
    parser.add_argument("--output", type=Path, default=None, help="Write here instead of stdout.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        timeline = load_timeline(args.timeline)
        session = import_timeline(timeline, project=args.project, session_id=args.session_id)
    except UnknownEventKindError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: could not import {args.timeline}: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(session.model_dump(mode="json"), indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote {args.output} ({len(session.events)} events).", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

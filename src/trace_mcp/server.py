"""TRACE MCP Server — entry point.

Transparent Recording of AI-assisted Collaboration Experiments.
An MCP server that provides a standardized audit trail for AI-assisted
research workflows.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from trace_mcp import __version__
from trace_mcp import extension_hooks as hooks
from trace_mcp import project_identity as pident
from trace_mcp.schema import (
    ActorType,
    AnnotationCategory,
    ContributionAttribution,
    DecisionDisposition,
    Session,
    SuggestionType,
    ToolCallHost,
    ToolCallStatus,
)
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import (
    decision_tools,
    export_tools,
    logging_tools,
    query_tools,
    session_tools,
)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("trace-mcp")

# --- Server state ---
mcp = FastMCP("trace")
# FastMCP exposes no version parameter and defaults the low-level server's
# version to the mcp LIBRARY version, so the initialize handshake's serverInfo
# misreports what a client is talking to. Stamp trace-mcp's own version on the
# underlying server (guarded by
# tests/test_installation_health.py::TestPackageImport::test_mcp_handshake_reports_package_version).
# Defensive: `_mcp_server` is a private FastMCP attribute — if a future mcp 1.x
# release moves it, a cosmetic version misreport must degrade to a warning,
# never an import-time crash that takes the whole fleet down on its next
# cold-resolved server start (the mcp 2.0 failure shape). CI's cold-resolution
# guard still fails loudly in that case, via the test above.
try:
    mcp._mcp_server.version = __version__
except AttributeError:  # pragma: no cover — depends on the installed mcp internals
    logger.warning("could not stamp trace-mcp's version on the MCP server; serverInfo will report the mcp library's")
storage = JsonFileStorage()
active_sessions: dict[str, Session] = {}
_current_session_id: str | None = None
_unpinned_warned = False


def _compact(obj: Any) -> str:
    """Serialize a query-tool result compactly.

    Query/retrieval results land directly in the model's context window, where
    indentation is pure token waste (~20-30% overhead) and compounds the
    context bloat behind the API-400 crash. The human/artifact-facing path
    (``trace_export``) uses pretty JSON instead.
    """
    return json.dumps(obj, separators=(",", ":"), default=str)


# ── Project-Pin Enforcement (ADR-006) ──────────────────────────────────────


def _bound_project() -> pident.BoundProject | None:
    """The process's ``TRACE_PROJECT`` pin (None if unpinned).

    Warns once per process when unpinned so an operator running without
    isolation sees it, without spamming every call.
    """
    global _unpinned_warned
    bound = pident.get_bound_project()
    if bound is None and not _unpinned_warned:
        _unpinned_warned = True
        logger.warning(
            "TRACE_PROJECT is not set — this server is UNPINNED and cross-project isolation is "
            "NOT enforced. Set TRACE_PROJECT in the server's .mcp.json env to enforce it."
        )
    return bound


def _require_pin_error() -> str | None:
    """Return an error string when ``TRACE_REQUIRE_PIN=1`` but the process is unpinned, else None.

    Default off; flipped on fleet-wide only after the migration sweep (ADR-006 S9).
    """
    if os.environ.get("TRACE_REQUIRE_PIN") == "1" and pident.get_bound_project() is None:
        return (
            "Error: TRACE_REQUIRE_PIN=1 but this server is not pinned (TRACE_PROJECT unset). "
            "Set TRACE_PROJECT in the server's .mcp.json env, or unset TRACE_REQUIRE_PIN."
        )
    return None


def _resolve_start_project(project: str | None, bound: pident.BoundProject | None) -> str:
    """Return the display label to record as ``metadata.project`` for a new session.

    Pinned: ``None`` resolves to the pin's display label; a supplied label MUST
    resolve to the pinned key. Unpinned: a non-empty label is required. A label
    resolving to a reserved key (``auto``/``shared``) is rejected.

    Raises ``ProjectKeyError`` on any violation (the caller surfaces it).
    Delegates to ``project_identity.resolve_scoped_project`` — the single
    scope-resolution rule shared with the learn tools (INV-9).
    """
    return pident.resolve_scoped_project(project, bound)


def _pinned_project_key(bound: pident.BoundProject | None) -> str | None:
    """The canonical key to stamp on a new session, or None to leave it unset.

    Only a pinned process stamps ``metadata.project_key``: it is the only
    configuration that carries an authoritative answer. An unpinned process
    would be guessing, and a guess written here is exactly what ADR-005 must
    never hash into a genesis record — an unstamped session stays resolvable
    through the alias table, which is repairable; a wrongly stamped one is not.
    """
    if bound is None or bound.key in pident.RESERVED_KEYS:
        return None
    return bound.key


def check_read_scope(session: Session) -> None:
    """Fail-closed cross-project read guard for the id-only read/export tools.

    When the process is pinned and *session* belongs to a different project,
    raise ``ProjectMismatchError`` — unless ``TRACE_ALLOW_CROSS_PROJECT_READS=1``
    (operator escape hatch). Cross-project WRITES are blocked separately (pointer
    promotion + learn coherence), so a permitted read cannot be re-persisted into
    the pinned store.
    """
    bound = pident.get_bound_project()
    if bound is None or pident.session_matches_project(session.metadata, bound.key):
        return
    if os.environ.get("TRACE_ALLOW_CROSS_PROJECT_READS") == "1":
        return
    other = pident.session_project_key(session.metadata) or "<unknown>"
    raise pident.ProjectMismatchError(
        f"session belongs to project '{other}' but this server is pinned to '{bound.key}'. "
        "Cross-project reads are denied; set TRACE_ALLOW_CROSS_PROJECT_READS=1 to override."
    )


# ── Auto-Session Infrastructure ────────────────────────────────────────────


async def _infer_project() -> str:
    """Resolve the project for an auto-created session (no explicit start).

    Uses ``TRACE_DEFAULT_PROJECT`` when set; otherwise returns the stable
    ``"auto"`` sentinel. It deliberately does NOT infer from the most-recent
    session on disk: that global fallback read the newest session across the
    SHARED store and, when it belonged to a different project, silently misrouted
    the auto-created session (and any learnings later extracted from it) into
    that unrelated project's knowledge store. A stable sentinel keeps
    unattributed sessions out of a real project's provenance.

    Precedence (ADR-006): the ``TRACE_PROJECT`` pin (its display label) >
    ``TRACE_DEFAULT_PROJECT`` > the stable ``"auto"`` sentinel. A pinned process
    therefore never falls into the ``"auto"`` pool.
    """
    bound = pident.get_bound_project()
    if bound is not None and bound.key not in pident.RESERVED_KEYS:
        return bound.display_label
    return os.environ.get("TRACE_DEFAULT_PROJECT") or "auto"


async def _ensure_session(session_id: str | None) -> tuple[Session, str]:
    """Get an existing session or auto-create one.

    Returns (session, auto_message). ``auto_message`` is non-empty only
    when a session was auto-created, so the caller can prepend it to the
    tool response.

    Raises ``FileNotFoundError`` when an explicit *session_id* is given
    but does not exist (preserving existing error behaviour), and
    ``ProjectMismatchError`` when the session belongs to another project's
    pin OR when ``TRACE_REQUIRE_PIN=1`` on an unpinned process would force
    auto-creation (every caller already surfaces this as its error string).
    """
    global _current_session_id

    # 1. Explicit session_id provided — look it up (may raise)
    if session_id:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        # Pointer-capture guard (ADR-006 bleed path 1): a pinned process must
        # never use another project's session — otherwise pointer-less events
        # would append into it. Denied regardless of status (fail closed).
        bound = _bound_project()
        if bound is not None and not pident.session_matches_project(session.metadata, bound.key):
            other = pident.session_project_key(session.metadata) or "<unknown>"
            raise pident.ProjectMismatchError(
                f"session '{session_id}' belongs to project '{other}' but this server is pinned to "
                f"'{bound.key}'. Refusing to use another project's session."
            )
        # Only an ACTIVE session may become the new current session. An
        # explicit session_id targeting a completed session (e.g. resolving
        # a decision proposed in a prior, now-closed session) must not move
        # the pointer — otherwise every subsequent pointer-less call tries
        # to append to the completed session and fails. The cached copy may
        # be stale (another process may have completed the session), so the
        # status is confirmed against disk before the pointer moves.
        status = session.status
        if status == "active":
            try:
                status = (await storage.get_session(session_id)).status
            except FileNotFoundError:
                pass  # not yet persisted — in-memory status is authoritative
        if status == "active":
            _current_session_id = session_id
        return session, ""

    # 2. Re-use the current session from earlier in this server process
    if _current_session_id:
        try:
            session = await session_tools.get_or_load_session(storage, active_sessions, _current_session_id)
            if session.status == "active":
                return session, ""
            # The current session was completed/abandoned (e.g. ended from
            # another process): clear the pointer and fall through to
            # auto-create rather than wedging every subsequent pointer-less
            # call on an immutable session.
            _current_session_id = None
        except FileNotFoundError:
            _current_session_id = None

    # 3. Auto-create a new session
    # TRACE_REQUIRE_PIN closes BOTH session-creation paths. It previously gated
    # only trace_start_session, so on a require-pin fleet an unpinned stray
    # process still auto-created quarantine sessions on its first logging call —
    # the exact capture the operator opted to fail closed on. Capture-over-
    # attribution is the DEFAULT posture; this flag is the explicit opt-out,
    # and a hole in an explicit opt-out is a broken promise, not a kindness.
    pin_error = _require_pin_error()
    if pin_error:
        raise pident.ProjectMismatchError(pin_error)
    project = await _infer_project()
    session = await session_tools.create_session(
        storage,
        active_sessions,
        project=project,
        project_key=_pinned_project_key(_bound_project()),
        description="Auto-created session (no explicit trace_start_session call)",
        tags=["auto-session"],
    )
    _current_session_id = session.id

    auto_msg = (
        f"⚠️ Auto-created TRACE session: {session.id} (project: {project}). "
        f"Call trace_start_session with a proper description for better provenance."
    )

    # Recall learnings for the auto-session — but NOT for the reserved 'auto'
    # quarantine pool, whose store commingles projects (ADR-006 D14).
    if pident.key_for_label(project) not in pident.RESERVED_KEYS:
        recalled = await hooks.recall_if_available(project, "", None, 3)
        if recalled:
            auto_msg += hooks.format_recalled_learnings(recalled)

    return session, auto_msg


# ── Session Management ──────────────────────────────────────────────────────


@mcp.tool()
async def trace_start_session(
    project: str | None = None,
    experiment_id: str | None = None,
    description: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    recall_learnings: bool = False,
    recall_limit: int = 5,
) -> str:
    """Start a new TRACE audit session.

    Call this ONCE at the beginning of a multi-step workflow. It returns a
    session id plus a bounded prior-session orientation, so you do NOT need to
    follow it with trace_list_sessions / trace_get_events / trace_health_check
    to get your bearings — that opening fan-out inflates a single assistant
    turn and can trip the Claude Code thinking-block API-400. Log events
    sequentially as they happen (1-2 trace calls per turn).

    recall_learnings defaults to False to keep session start cheap and quiet.
    Set recall_learnings=True only when past learnings are likely relevant; it
    surfaces up to recall_limit (default 5) learnings based on description/tags.

    project is OPTIONAL when the server is pinned (TRACE_PROJECT set): omit it to
    use the pin. When unpinned, project is required. A supplied label must
    resolve to the pinned project, and reserved keys (auto/shared) are rejected.
    """
    global _current_session_id
    pin_error = _require_pin_error()
    if pin_error:
        return pin_error
    bound = _bound_project()
    try:
        resolved = _resolve_start_project(project, bound)
    except pident.ProjectKeyError as e:
        return f"Error: {e}"
    try:
        # Bounded orientation (reads <=25 files) on PRIOR state, computed before
        # creating the new session so it never counts the session being started.
        # This gives the model its bearings so it need not fan out to the query
        # tools in the opening interleaved-thinking turn.
        brief = await storage.session_brief(resolved)

        session = await session_tools.create_session(
            storage,
            active_sessions,
            project=resolved,
            project_key=_pinned_project_key(bound),
            experiment_id=experiment_id,
            description=description,
            participants=participants,
            tags=tags,
        )
        _current_session_id = session.id

        path = storage.session_location(session.id)

        # Recall is OFF by default (v0.4.2): opt-in only, rendered once.
        recalled_block = ""
        if recall_learnings and description:
            recalled = await hooks.recall_if_available(resolved, description, tags, recall_limit)
            if recalled:
                recalled_block = hooks.format_recalled_learnings(recalled)

        return session_tools.format_bootstrap_message(
            session_id=session.id,
            project=resolved,
            path=str(path),
            brief=brief,
            recalled_block=recalled_block,
        )
    except Exception as e:
        logger.exception("Error starting session")
        return f"Error starting session: {e}"


@mcp.tool()
async def trace_end_session(
    session_id: str,
    summary: str | None = None,
    extract_learnings: bool = True,
    write_scratchpad: bool = True,
) -> str:
    """End a TRACE audit session.

    Call this when the workflow is complete.
    Optionally provide a summary of what was accomplished.

    When extract_learnings is True (default), automatically extracts
    learnings from the session's annotations and decisions into the
    project's knowledge store.

    When write_scratchpad is True (default), appends a human-readable
    summary to .claude/SCRATCHPAD.md for context restoration in the
    next session.
    """
    global _current_session_id
    try:
        # Grab project before end_session pops the session from memory
        project: str | None = None
        if extract_learnings:
            try:
                session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
                # Do NOT extract from the reserved 'auto' quarantine pool — its
                # store commingles projects (ADR-006 D14). Leaving project None
                # skips extraction below.
                if pident.session_project_key(session.metadata) not in pident.RESERVED_KEYS:
                    project = session.metadata.project
            except FileNotFoundError:
                pass

        result = await session_tools.end_session(
            storage,
            active_sessions,
            session_id=session_id,
            summary=summary,
        )

        # Clear current session if it's the one being ended
        if _current_session_id == session_id:
            _current_session_id = None

        # Auto-extract learnings from the completed session
        if extract_learnings and project:
            extraction = await hooks.extract_if_available(project, session_id)
            if extraction.error:
                result += (
                    f"\n⚠️ Learning extraction failed: {extraction.error}"
                    "\nLearnings were NOT extracted from this session. "
                    "Run trace_learn_extract manually to retry."
                )
            elif extraction.new_ids:
                result += f"\nExtracted {len(extraction.new_ids)} new learnings: {', '.join(extraction.new_ids)}"

        # Write SCRATCHPAD.md with session summary
        if write_scratchpad:
            try:
                # Re-load the completed session for SCRATCHPAD generation
                completed = await storage.get_session(session_id)
                from trace_mcp.scratchpad import write_scratchpad as _write_sp

                sp_path = _write_sp(completed)
                result += f"\nContext saved: {sp_path}"
            except Exception as e:
                logger.warning("SCRATCHPAD write failed: %s", e, exc_info=True)
                result += f"\n⚠️ SCRATCHPAD write failed: {e}"

        return result
    except Exception as e:
        logger.exception("Error ending session")
        return f"Error ending session: {e}"


# ── Event Logging ────────────────────────────────────────────────────────────


@mcp.tool()
async def trace_log_tool_call(
    server: str,
    tool_name: str,
    input: dict[str, Any],
    output: Any = None,
    duration_ms: int | None = None,
    status: ToolCallStatus = "success",
    error_message: str | None = None,
    retries_event_id: str | None = None,
    actor_type: ActorType = "ai",
    actor_id: str = "ai-assistant",
    reasoning: str | None = None,
    conversation_turn: int | None = None,
    host: ToolCallHost = "mcp",
    parent_event_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Log a tool call made to another tool or host.

    Call this AFTER each tool invocation to record what was called,
    with what inputs, and what was returned.

    v0.4.1: `host` distinguishes "mcp" (default — external MCP server),
    "external" (non-MCP external tool), and "internal" (host-internal
    helper such as a subagent dispatch from Claude Code). `parent_event_id`
    links a dispatched child call to the controller event that motivated
    it (spec §3.5); the value MUST be an in-session event ID.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        event_id = await logging_tools.log_tool_call(
            storage,
            session,
            server=server,
            tool_name=tool_name,
            input=input,
            output=output,
            duration_ms=duration_ms,
            status=status,
            error_message=error_message,
            retries_event_id=retries_event_id,
            actor_type=actor_type,
            actor_id=actor_id,
            reasoning=reasoning,
            conversation_turn=conversation_turn,
            host=host,
            parent_event_id=parent_event_id,
        )
        return f"{prefix}Logged tool call: {event_id}"
    except Exception as e:
        logger.exception("Error logging tool call")
        return f"Error logging tool call: {e}"


@mcp.tool()
async def trace_log_annotation(
    category: AnnotationCategory,
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
    related_event_ids: list[str] | None = None,
    actor_type: ActorType = "ai",
    actor_id: str = "ai-assistant",
    conversation_snippet: str | None = None,
    session_id: str | None = None,
) -> str:
    """Log an observation, learning, gotcha, correction, or note.

    Use this whenever you encounter something surprising, learn something
    useful about the data or tools, or want to record a note for future reference.
    Use category='correction' with corrects_event_ids when a human catches and
    fixes an AI mistake.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        event_id = await logging_tools.log_annotation(
            storage,
            session,
            category=category,
            content=content,
            tags=tags,
            corrects_event_ids=corrects_event_ids,
            related_event_ids=related_event_ids,
            actor_type=actor_type,
            actor_id=actor_id,
            conversation_snippet=conversation_snippet,
        )
        return f"{prefix}Logged annotation: {event_id}"
    except Exception as e:
        logger.exception("Error logging annotation")
        return f"Error logging annotation: {e}"


@mcp.tool()
async def trace_log_contribution(
    description: str,
    direction: ContributionAttribution,
    execution: ContributionAttribution,
    artifact: str | None = None,
    related_decision_ids: list[str] | None = None,
    tags: list[str] | None = None,
    actor_type: ActorType = "ai",
    actor_id: str = "ai-assistant",
    conversation_snippet: str | None = None,
    session_id: str | None = None,
) -> str:
    """Log a contribution with direction-vs-execution attribution.

    Records who had the idea (direction) vs who did the work (execution).
    Use 'human', 'ai', or 'collaborative' for each.
    Optionally link to the decision(s) that motivated this contribution.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        event_id = await logging_tools.log_contribution(
            storage,
            session,
            description=description,
            direction=direction,
            execution=execution,
            artifact=artifact,
            related_decision_ids=related_decision_ids,
            tags=tags,
            actor_type=actor_type,
            actor_id=actor_id,
            conversation_snippet=conversation_snippet,
        )
        return f"{prefix}Logged contribution: {event_id}"
    except Exception as e:
        logger.exception("Error logging contribution")
        return f"Error logging contribution: {e}"


@mcp.tool()
async def trace_log_state_change(
    description: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    actor_type: ActorType = "ai",
    actor_id: str = "ai-assistant",
    session_id: str | None = None,
) -> str:
    """Log a change in environment, configuration, or tools.

    Use when switching models, changing parameters, updating dependencies,
    or any shift in the working context.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        event_id = await logging_tools.log_state_change(
            storage,
            session,
            description=description,
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        return f"{prefix}Logged state change: {event_id}"
    except Exception as e:
        logger.exception("Error logging state change")
        return f"Error logging state change: {e}"


# ── Decision Workflow ────────────────────────────────────────────────────────


@mcp.tool()
async def trace_propose_decision(
    description: str,
    proposed_by_type: ActorType,
    proposed_by_id: str,
    rationale: str | None = None,
    revises_event_id: str | None = None,
    suggestion_type: SuggestionType | None = None,
    tags: list[str] | None = None,
    conversation_snippet: str | None = None,
    session_id: str | None = None,
) -> str:
    """Propose a methodological decision for the workflow.

    Use this BEFORE making significant choices: which method to use, which
    parameters to set, which data to include/exclude, how to handle messy data,
    how to interpret ambiguous results. The decision stays in 'proposed' state
    until resolved.

    suggestion_type can be 'proactive' (AI volunteered), 'requested' (human asked),
    or 'collaborative' (emerged from discussion).

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description=description,
            rationale=rationale,
            proposed_by_type=proposed_by_type,
            proposed_by_id=proposed_by_id,
            revises_event_id=revises_event_id,
            suggestion_type=suggestion_type,
            tags=tags,
            conversation_snippet=conversation_snippet,
        )
        result = f"{prefix}Decision proposed: {event_id}"

        # Layer 3: Auto-recall related learnings for this decision
        project = session.metadata.project
        related = await hooks.recall_if_available(project, description, tags, limit=3)
        if related:
            result += hooks.format_decision_warnings(related)

        return result
    except Exception as e:
        logger.exception("Error proposing decision")
        return f"Error proposing decision: {e}"


@mcp.tool()
async def trace_resolve_decision(
    event_id: str,
    disposition: Literal["accepted", "revised", "rejected"],
    resolved_by_type: ActorType,
    resolved_by_id: str,
    revision_note: str | None = None,
    session_id: str | None = None,
) -> str:
    """Resolve a previously proposed decision.

    Mark it as accepted, revised, or rejected. Always include a revision_note
    when revising or rejecting — explain why.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    prefix = f"{auto_msg}\n" if auto_msg else ""
    try:
        result = await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition=disposition,
            resolved_by_type=resolved_by_type,
            resolved_by_id=resolved_by_id,
            revision_note=revision_note,
        )
        return f"{prefix}{result}" if prefix else result
    except Exception as e:
        logger.exception("Error resolving decision")
        return f"Error resolving decision: {e}"


# ── Query & Retrieval ────────────────────────────────────────────────────────


@mcp.tool()
async def trace_get_session(session_id: str) -> str:
    """Get the full data for a TRACE session (excluding event details)."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    summary = query_tools.get_session_summary(session)
    return _compact(summary)


@mcp.tool()
async def trace_get_events(
    session_id: str,
    type: str | None = None,
    limit: int = query_tools.DEFAULT_EVENTS_LIMIT,
) -> str:
    """List events in a session, optionally filtered by type."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    events = query_tools.get_events(session, type_filter=type, limit=limit)
    return _compact(events)


@mcp.tool()
async def trace_get_decisions(
    session_id: str,
    disposition: DecisionDisposition | None = None,
    proposed_by_type: ActorType | None = None,
) -> str:
    """List all decisions in a session, optionally filtered by disposition status and/or proposer type."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    decisions = query_tools.get_decisions(session, disposition=disposition, proposed_by_type=proposed_by_type)
    return _compact(decisions)


@mcp.tool()
async def trace_get_decision_chain(
    event_id: str,
    session_id: str,
) -> str:
    """Get the full chain of linked decisions starting from any decision in the chain.

    Follows revises_event_id links to assemble the full provenance chain.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    chain = query_tools.get_decision_chain(session, event_id=event_id)
    if not chain:
        return f"Error: Decision '{event_id}' not found."
    return _compact(chain)


@mcp.tool()
async def trace_search(
    session_id: str,
    query: str,
    limit: int = query_tools.DEFAULT_SEARCH_LIMIT,
) -> str:
    """Search events in a session by text content (case-insensitive).

    Returns at most `limit` matching events (clamped to a hard ceiling). The
    response reports total_matched / returned / truncated so a capped result is
    explicit rather than a silently-truncated list.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    all_results = query_tools.search_events(session, query=query)
    cap = max(1, min(limit, query_tools.MAX_SEARCH_LIMIT))
    returned = all_results[:cap]
    return _compact(
        {
            "query": query,
            "total_matched": len(all_results),
            "returned": len(returned),
            "truncated": len(all_results) > len(returned),
            "results": returned,
        }
    )


@mcp.tool()
async def trace_project_summary(
    project: str,
) -> str:
    """Get aggregated metrics across all sessions for a project.

    Returns counts of events by type, decisions by disposition (with AI vs human
    proposer breakdown), contributions by direction/execution, annotations by
    category, and unique participants. Useful for paper-ready statistics.
    """
    try:
        summary = await query_tools.project_summary(storage, project=project)
        return _compact(summary)
    except Exception as e:
        logger.exception("Error generating project summary")
        return f"Error generating project summary: {e}"


@mcp.tool()
async def trace_health_check(
    project: str | None = None,
    session_id: str | None = None,
) -> str:
    """Return system health info and event-level statistics.

    Reports TRACE version, storage paths, session count, and event breakdown
    (total, by type, by actor type). Optionally scoped to a project or session.
    """
    try:
        result = await query_tools.health_check(storage, project=project, session_id=session_id)
        return _compact(result)
    except Exception as e:
        logger.exception("Error running health check")
        return f"Error running health check: {e}"


# ── Export ───────────────────────────────────────────────────────────────────


@mcp.tool()
async def trace_export(
    session_id: str,
    format: Literal["json", "markdown", "prov-jsonld"],
    pretty: bool = True,
) -> str:
    """Export a session in a specific format.

    Supported formats: 'json', 'markdown', 'prov-jsonld'. This is the
    human/artifact-facing path, so JSON is indented (pretty) by default — pass
    pretty=False for a compact JSON artifact. (The query/retrieval tools emit
    compact JSON unconditionally because their output goes into the model's
    context window.)
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
        check_read_scope(session)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."
    except pident.ProjectMismatchError as e:
        return f"Error: {e}"

    try:
        return export_tools.export_session(session, format=format, pretty=pretty)
    except Exception as e:
        logger.exception("Error exporting session")
        return f"Error exporting session: {e}"


@mcp.tool()
async def trace_list_sessions(
    project: str | None = None,
    limit: int = 20,
) -> str:
    """List all TRACE sessions, optionally filtered by project name."""
    try:
        summaries = await storage.list_sessions(project=project, limit=limit)
        return _compact(summaries)
    except Exception as e:
        logger.exception("Error listing sessions")
        return f"Error listing sessions: {e}"


# ── Extensions ───────────────────────────────────────────────────────────────


def _load_extensions() -> None:
    """Discover and load TRACE extensions from trace_mcp.extensions package."""
    import importlib
    import pkgutil

    try:
        import trace_mcp.extensions as ext_pkg
    except ImportError:
        return
    for _finder, name, _is_pkg in pkgutil.iter_modules(ext_pkg.__path__):
        fqn = f"trace_mcp.extensions.{name}"
        try:
            mod = importlib.import_module(fqn)
            if hasattr(mod, "register"):
                mod.register(mcp, storage)
                logger.info("Loaded extension: %s", name)
        except Exception:
            logger.exception("Failed to load extension: %s", name)


# ── Entry Point ──────────────────────────────────────────────────────────────


def main() -> None:
    """Run the TRACE MCP server (or handle subcommands like 'init' or 'validate')."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        # Delegate to init's own argument parser rather than re-deriving one
        # here: the previous hand-rolled dispatch read argv[2] as the directory,
        # so every flag (`--project`, `--dry-run`, `--client`) was silently
        # swallowed as a path. init's parser already strips the leading "init".
        from trace_mcp.init_project import main as init_main

        init_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "project-key":
        from trace_mcp.init_project import print_project_key

        raise SystemExit(print_project_key(sys.argv[2] if len(sys.argv) > 2 else None))

    if len(sys.argv) > 1 and sys.argv[1] == "identity":
        from trace_mcp.identity_cli import main as identity_main

        raise SystemExit(identity_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        # Imported lazily like the other subcommands: the conformance layer
        # pulls in the adapter assets, which the running server must never
        # depend on (adapters are pure installers).
        from trace_mcp.conformance.cli import main as doctor_main

        raise SystemExit(doctor_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "fleet-check":
        from trace_mcp.conformance.cli import main_fleet_check

        raise SystemExit(main_fleet_check(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        # In-package validator (schema ships as package data) — the previous
        # repo-relative load of scripts/validate_session.py crashed on any
        # installed package, where scripts/ doesn't exist.
        from trace_mcp.validate import main as validate_main

        raise SystemExit(validate_main(sys.argv[2:]))

    args = _parse_server_args(sys.argv[1:])

    _load_extensions()

    if args.transport == "streamable-http":
        _LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
        if args.host not in _LOOPBACK_HOSTS:
            # Loud, not fatal: TRACE tools carry no authentication layer, so a
            # non-loopback bind hands session write access to the network. An
            # operator doing this deliberately (e.g. inside a container) should
            # see exactly what they opted into.
            logger.warning(
                "Binding to %s exposes TRACE tools to the network WITHOUT authentication; "
                "prefer 127.0.0.1 and a local consumer unless this host is otherwise isolated.",
                args.host,
            )
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        logger.info(
            "Starting TRACE MCP server v%s at http://%s:%d%s (transport=streamable-http)",
            __version__,
            args.host,
            args.port,
            mcp.settings.streamable_http_path,
        )
        mcp.run(transport="streamable-http")
        return

    logger.info("Starting TRACE MCP server v%s (transport=stdio)", __version__)
    mcp.run(transport="stdio")


def _parse_server_args(argv: list[str]) -> argparse.Namespace:
    """Parse server-mode CLI flags for the default (serve) invocation.

    Inputs: ``argv`` without the program name, after the named subcommands
    (``init``, ``identity``, ``doctor``, ...) have been dispatched. Outputs: a
    namespace with ``transport``, ``host``, and ``port``. Side effects: none on
    valid input; argparse prints usage and exits non-zero on an unknown flag,
    which is the fail-loud behavior we want: a typo'd invocation must never
    silently start a stdio server that a Streamable HTTP consumer then waits on.

    ``--host`` and ``--port`` only take effect with
    ``--transport streamable-http``; the stdio transport has no socket. The
    HTTP path is FastMCP's ``streamable_http_path`` default (``/mcp``).
    """
    parser = argparse.ArgumentParser(
        prog="trace-mcp",
        description=(
            "Run the TRACE MCP server. With no flags, serves over stdio for a "
            "spawning MCP client. --transport streamable-http serves HTTP at "
            "http://HOST:PORT/mcp for clients that connect over the network, "
            "such as an agent runtime's MCP service registry."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport to serve (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for streamable-http (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port for streamable-http (default: 8765).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()

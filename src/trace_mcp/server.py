"""TRACE MCP Server — entry point.

Transparent Recording of AI-assisted Collaboration Experiments.
An MCP server that provides a standardized audit trail for AI-assisted
research workflows.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from trace_mcp import __version__
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp import hooks
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
storage = JsonFileStorage()
active_sessions: dict[str, Session] = {}
_current_session_id: str | None = None


# ── Auto-Session Infrastructure ────────────────────────────────────────────


async def _infer_project() -> str:
    """Infer project name from env var or most recent session."""
    project = os.environ.get("TRACE_DEFAULT_PROJECT")
    if project:
        return project
    try:
        sessions = await storage.list_sessions(limit=1)
        if sessions:
            return sessions[0].get("project", "auto")
    except Exception:
        pass
    return "auto"


async def _ensure_session(session_id: str | None) -> tuple[Session, str]:
    """Get an existing session or auto-create one.

    Returns (session, auto_message). ``auto_message`` is non-empty only
    when a session was auto-created, so the caller can prepend it to the
    tool response.

    Raises ``FileNotFoundError`` when an explicit *session_id* is given
    but does not exist (preserving existing error behaviour).
    """
    global _current_session_id

    # 1. Explicit session_id provided — look it up (may raise)
    if session_id:
        session = await session_tools.get_or_load_session(
            storage, active_sessions, session_id
        )
        _current_session_id = session_id
        return session, ""

    # 2. Re-use the current session from earlier in this server process
    if _current_session_id:
        try:
            session = await session_tools.get_or_load_session(
                storage, active_sessions, _current_session_id
            )
            return session, ""
        except FileNotFoundError:
            _current_session_id = None

    # 3. Auto-create a new session
    project = await _infer_project()
    session = await session_tools.create_session(
        storage,
        active_sessions,
        project=project,
        description="Auto-created session (no explicit trace_start_session call)",
        tags=["auto-session"],
    )
    _current_session_id = session.id

    auto_msg = (
        f"⚠️ Auto-created TRACE session: {session.id} (project: {project}). "
        f"Call trace_start_session with a proper description for better provenance."
    )

    # Try to recall learnings for the auto-session
    recalled = await hooks.recall_if_available(project, "", None, 3)
    if recalled:
        auto_msg += hooks.format_recalled_learnings(recalled)

    return session, auto_msg


# ── Session Management ──────────────────────────────────────────────────────


@mcp.tool()
async def trace_start_session(
    project: str,
    experiment_id: str | None = None,
    description: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    recall_learnings: bool = True,
    recall_limit: int = 5,
) -> str:
    """Start a new TRACE audit session.

    Call this at the beginning of any scientific workflow or experiment.
    Returns a session ID to use in all subsequent TRACE calls.

    When recall_learnings is True (default), automatically surfaces the
    most relevant past learnings based on the session description and tags.
    Only the top recall_limit results are returned (default 5).
    """
    global _current_session_id
    try:
        session = await session_tools.create_session(
            storage,
            active_sessions,
            project=project,
            experiment_id=experiment_id,
            description=description,
            participants=participants,
            tags=tags,
        )
        _current_session_id = session.id

        path = storage._session_path(session.id) if hasattr(storage, "_session_path") else "disk"  # type: ignore[attr-defined]
        result = (
            f"TRACE audit logging is now active.\n"
            f"Session: {session.id}\n"
            f"Project: {project}\n"
            f"File: {path}\n"
            f"All tool calls, decisions, and annotations will be recorded."
        )

        # Layer 1: Auto-recall relevant learnings at session start
        if recall_learnings and description:
            recalled = await hooks.recall_if_available(
                project, description, tags, recall_limit
            )
            if recalled:
                result += hooks.format_recalled_learnings(recalled)

        return result
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
                session = await session_tools.get_or_load_session(
                    storage, active_sessions, session_id
                )
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
    status: str = "success",
    error_message: str | None = None,
    retries_event_id: str | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
    reasoning: str | None = None,
    conversation_turn: int | None = None,
    session_id: str | None = None,
) -> str:
    """Log a tool call made to another MCP server.

    Call this AFTER each tool invocation to record what was called,
    with what inputs, and what was returned.

    session_id is optional — if omitted, uses the current session or
    auto-creates one.
    """
    try:
        session, auto_msg = await _ensure_session(session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        )
        return f"{prefix}Logged tool call: {event_id}"
    except Exception as e:
        logger.exception("Error logging tool call")
        return f"Error logging tool call: {e}"


@mcp.tool()
async def trace_log_annotation(
    category: str,
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
    related_event_ids: list[str] | None = None,
    actor_type: str = "ai",
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
    direction: str,
    execution: str,
    artifact: str | None = None,
    related_decision_ids: list[str] | None = None,
    tags: list[str] | None = None,
    actor_type: str = "ai",
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
    actor_type: str = "ai",
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
    proposed_by_type: str,
    proposed_by_id: str,
    rationale: str | None = None,
    revises_event_id: str | None = None,
    suggestion_type: str | None = None,
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
        related = await hooks.recall_if_available(
            project, description, tags, limit=3
        )
        if related:
            result += hooks.format_decision_warnings(related)

        return result
    except Exception as e:
        logger.exception("Error proposing decision")
        return f"Error proposing decision: {e}"


@mcp.tool()
async def trace_resolve_decision(
    event_id: str,
    disposition: str,
    resolved_by_type: str,
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
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    summary = query_tools.get_session_summary(session)
    return json.dumps(summary, indent=2, default=str)


@mcp.tool()
async def trace_get_events(
    session_id: str,
    type: str | None = None,
    limit: int = 100,
) -> str:
    """List events in a session, optionally filtered by type."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    events = query_tools.get_events(session, type_filter=type, limit=limit)
    return json.dumps(events, indent=2, default=str)


@mcp.tool()
async def trace_get_decisions(
    session_id: str,
    disposition: str | None = None,
    proposed_by_type: str | None = None,
) -> str:
    """List all decisions in a session, optionally filtered by disposition status and/or proposer type."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    decisions = query_tools.get_decisions(session, disposition=disposition, proposed_by_type=proposed_by_type)
    return json.dumps(decisions, indent=2, default=str)


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
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    chain = query_tools.get_decision_chain(session, event_id=event_id)
    if not chain:
        return f"Error: Decision '{event_id}' not found."
    return json.dumps(chain, indent=2, default=str)


@mcp.tool()
async def trace_search(
    session_id: str,
    query: str,
) -> str:
    """Search events in a session by text content (case-insensitive)."""
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    results = query_tools.search_events(session, query=query)
    return json.dumps(results, indent=2, default=str)


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
        return json.dumps(summary, indent=2, default=str)
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
        result = await query_tools.health_check(
            storage, project=project, session_id=session_id
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        logger.exception("Error running health check")
        return f"Error running health check: {e}"


# ── Export ───────────────────────────────────────────────────────────────────


@mcp.tool()
async def trace_export(
    session_id: str,
    format: str,
) -> str:
    """Export a session in a specific format.

    Supported formats: 'json', 'markdown', 'prov-jsonld'.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    try:
        return export_tools.export_session(session, format=format)
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
        return json.dumps(summaries, indent=2, default=str)
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
        from trace_mcp.init_project import init_project

        directory = sys.argv[2] if len(sys.argv) > 2 else None
        init_project(directory)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        import importlib.util
        from pathlib import Path as _Path

        script = _Path(__file__).resolve().parent.parent.parent / "scripts" / "validate_session.py"
        spec = importlib.util.spec_from_file_location("validate_session", script)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raise SystemExit(mod.main(sys.argv[2:]))

    _load_extensions()

    # Version pin check: warn if the calling project expects a different version
    pinned = os.environ.get("TRACE_PINNED_VERSION")
    if pinned and pinned != __version__:
        logger.warning(
            "Version mismatch: project expects TRACE %s but server is %s. "
            "Update .mcp.json TRACE_PINNED_VERSION or upgrade TRACE.",
            pinned,
            __version__,
        )

    logger.info("Starting TRACE MCP server v%s", __version__)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

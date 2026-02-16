"""TRACE MCP Server — entry point.

Transparent Recording of AI-assisted Collaboration Experiments.
An MCP server that provides a standardized audit trail for AI-assisted
research workflows.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from trace_mcp import __version__
from trace_mcp.schema import Session
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
storage = JsonFileStorage()
active_sessions: dict[str, Session] = {}


# ── Session Management ──────────────────────────────────────────────────────


@mcp.tool()
async def trace_start_session(
    project: str,
    experiment_id: str | None = None,
    description: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Start a new TRACE audit session.

    Call this at the beginning of any scientific workflow or experiment.
    Returns a session ID to use in all subsequent TRACE calls.
    """
    try:
        return await session_tools.start_session(
            storage,
            active_sessions,
            project=project,
            experiment_id=experiment_id,
            description=description,
            participants=participants,
            tags=tags,
        )
    except Exception as e:
        logger.exception("Error starting session")
        return f"Error starting session: {e}"


@mcp.tool()
async def trace_end_session(
    session_id: str,
    summary: str | None = None,
) -> str:
    """End a TRACE audit session.

    Call this when the workflow is complete.
    Optionally provide a summary of what was accomplished.
    """
    try:
        return await session_tools.end_session(
            storage,
            active_sessions,
            session_id=session_id,
            summary=summary,
        )
    except Exception as e:
        logger.exception("Error ending session")
        return f"Error ending session: {e}"


# ── Event Logging ────────────────────────────────────────────────────────────


@mcp.tool()
async def trace_log_tool_call(
    session_id: str,
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
) -> str:
    """Log a tool call made to another MCP server.

    Call this AFTER each tool invocation to record what was called,
    with what inputs, and what was returned.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        return f"Logged tool call: {event_id}"
    except Exception as e:
        logger.exception("Error logging tool call")
        return f"Error logging tool call: {e}"


@mcp.tool()
async def trace_log_annotation(
    session_id: str,
    category: str,
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
    related_event_ids: list[str] | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log an observation, learning, gotcha, correction, or note.

    Use this whenever you encounter something surprising, learn something
    useful about the data or tools, or want to record a note for future reference.
    Use category='correction' with corrects_event_ids when a human catches and
    fixes an AI mistake.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        )
        return f"Logged annotation: {event_id}"
    except Exception as e:
        logger.exception("Error logging annotation")
        return f"Error logging annotation: {e}"


@mcp.tool()
async def trace_log_contribution(
    session_id: str,
    description: str,
    direction: str,
    execution: str,
    artifact: str | None = None,
    related_decision_ids: list[str] | None = None,
    tags: list[str] | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log a contribution with direction-vs-execution attribution.

    Records who had the idea (direction) vs who did the work (execution).
    Use 'human', 'ai', or 'collaborative' for each.
    Optionally link to the decision(s) that motivated this contribution.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        )
        return f"Logged contribution: {event_id}"
    except Exception as e:
        logger.exception("Error logging contribution")
        return f"Error logging contribution: {e}"


@mcp.tool()
async def trace_log_state_change(
    session_id: str,
    description: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log a change in environment, configuration, or tools.

    Use when switching models, changing parameters, updating dependencies,
    or any shift in the working context.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        return f"Logged state change: {event_id}"
    except Exception as e:
        logger.exception("Error logging state change")
        return f"Error logging state change: {e}"


# ── Decision Workflow ────────────────────────────────────────────────────────


@mcp.tool()
async def trace_propose_decision(
    session_id: str,
    description: str,
    proposed_by_type: str,
    proposed_by_id: str,
    rationale: str | None = None,
    revises_event_id: str | None = None,
    suggestion_type: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Propose a methodological decision for the workflow.

    Use this BEFORE making significant choices: which method to use, which
    parameters to set, which data to include/exclude, how to handle messy data,
    how to interpret ambiguous results. The decision stays in 'proposed' state
    until resolved.

    suggestion_type can be 'proactive' (AI volunteered), 'requested' (human asked),
    or 'collaborative' (emerged from discussion).
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

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
        )
        return f"Decision proposed: {event_id}"
    except Exception as e:
        logger.exception("Error proposing decision")
        return f"Error proposing decision: {e}"


@mcp.tool()
async def trace_resolve_decision(
    event_id: str,
    session_id: str,
    disposition: str,
    resolved_by_type: str,
    resolved_by_id: str,
    revision_note: str | None = None,
) -> str:
    """Resolve a previously proposed decision.

    Mark it as accepted, revised, or rejected. Always include a revision_note
    when revising or rejecting — explain why.
    """
    try:
        session = await session_tools.get_or_load_session(storage, active_sessions, session_id)
    except FileNotFoundError:
        return f"Error: Session '{session_id}' not found."

    try:
        return await decision_tools.resolve_decision(
            storage,
            session,
            event_id=event_id,
            disposition=disposition,
            resolved_by_type=resolved_by_type,
            resolved_by_id=resolved_by_id,
            revision_note=revision_note,
        )
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
    """Run the TRACE MCP server (or handle subcommands like 'init')."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from trace_mcp.init_project import init_project

        directory = sys.argv[2] if len(sys.argv) > 2 else None
        init_project(directory)
        return

    _load_extensions()
    logger.info("Starting TRACE MCP server v%s", __version__)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

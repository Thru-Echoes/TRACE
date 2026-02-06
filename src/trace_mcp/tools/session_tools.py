"""Session management tools: start and end TRACE sessions."""

from __future__ import annotations

import platform
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import trace_mcp
from trace_mcp.schema import Actor, Environment, Session, SessionMetadata, TraceEvent
from trace_mcp.storage.base import TraceStorage


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

    path = storage._session_path(session_id) if hasattr(storage, "_session_path") else "disk"  # type: ignore[attr-defined]
    return (
        f"TRACE audit logging is now active.\n"
        f"Session: {session_id}\n"
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

    return f"Session ended: {session_id}\n{total} events: {detail}"


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


async def append_event(
    storage: TraceStorage,
    session: Session,
    event: TraceEvent,
) -> str:
    """Append an event to a session and flush to disk. Returns event ID."""
    if not event.id:
        event.id = session.next_event_id()
    event.session_id = session.id
    session.events.append(event)
    await storage.update_session(session)
    return event.id

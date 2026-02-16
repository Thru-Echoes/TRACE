"""Event logging tools: tool calls, annotations, state changes."""

from __future__ import annotations

from typing import Any

from trace_mcp.schema import (
    Actor,
    AnnotationData,
    ContributionData,
    EventContext,
    Session,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.storage.base import TraceStorage
from trace_mcp.tools.session_tools import append_event


async def log_tool_call(
    storage: TraceStorage,
    session: Session,
    *,
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
    """Log a tool call made to another MCP server."""
    event = TraceEvent(
        session_id=session.id,
        type="tool_call",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        tool_call=ToolCallData(
            server=server,
            name=tool_name,
            input=input,
            output=output,
            duration_ms=duration_ms,
            status=status,  # type: ignore[arg-type]
            error_message=error_message,
            retries_event_id=retries_event_id,
        ),
        context=EventContext(
            reasoning_summary=reasoning,
            conversation_turn=conversation_turn,
        ),
    )
    event_id = await append_event(storage, session, event)
    return event_id


async def log_annotation(
    storage: TraceStorage,
    session: Session,
    *,
    category: str,
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
    related_event_ids: list[str] | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log an observation, learning, gotcha, correction, or note."""
    event = TraceEvent(
        session_id=session.id,
        type="annotation",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        annotation=AnnotationData(
            category=category,  # type: ignore[arg-type]
            content=content,
            tags=tags or [],
            corrects_event_ids=corrects_event_ids or [],
            related_event_ids=related_event_ids or [],
        ),
    )
    event_id = await append_event(storage, session, event)
    return event_id


async def log_contribution(
    storage: TraceStorage,
    session: Session,
    *,
    description: str,
    direction: str,
    execution: str,
    artifact: str | None = None,
    related_decision_ids: list[str] | None = None,
    tags: list[str] | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log a contribution with direction-vs-execution attribution."""
    event = TraceEvent(
        session_id=session.id,
        type="contribution",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        contribution=ContributionData(
            description=description,
            artifact=artifact,
            direction=direction,  # type: ignore[arg-type]
            execution=execution,  # type: ignore[arg-type]
            related_decision_ids=related_decision_ids or [],
            tags=tags or [],
        ),
    )
    event_id = await append_event(storage, session, event)
    return event_id


async def log_state_change(
    storage: TraceStorage,
    session: Session,
    *,
    description: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    actor_type: str = "ai",
    actor_id: str = "ai-assistant",
) -> str:
    """Log a change in environment, configuration, or tools."""
    event = TraceEvent(
        session_id=session.id,
        type="state_change",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        state_change=StateChangeData(
            description=description,
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        ),
    )
    event_id = await append_event(storage, session, event)
    return event_id

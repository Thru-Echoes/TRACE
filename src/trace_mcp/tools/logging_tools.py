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
    warnings: list[str] = []

    # FM22: Block logging TRACE's own tool calls
    server_lower = server.lower()
    tool_lower = tool_name.lower()
    if "trace" in server_lower or tool_lower.startswith("trace_"):
        warnings.append(
            "TRACE protocol says to never log TRACE's own tool calls. "
            "This event was logged but should be avoided."
        )

    # FM23: Hint about exploratory tool calls
    exploratory_tools = {"read", "glob", "grep", "bash", "cat", "ls", "find", "head", "tail"}
    if tool_lower in exploratory_tools:
        warnings.append(
            "Consider whether this exploratory call needs TRACE logging. "
            "File reads, greps, and directory listings are usually not logged."
        )

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

    if warnings:
        return event_id + "\n" + "\n".join(f"  \u26a0\ufe0f {w}" for w in warnings)
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
    conversation_snippet: str | None = None,
) -> str:
    """Log an observation, learning, gotcha, correction, or note."""
    warnings: list[str] = []
    effective_corrects = corrects_event_ids or []

    # FM17: Correction without corrects_event_ids
    if category == "correction" and not effective_corrects:
        warnings.append(
            "Correction logged without corrects_event_ids. Link to the "
            "event(s) being corrected for full provenance."
        )

    # FM5: Correction without conversation_snippet
    if category == "correction" and conversation_snippet is None:
        warnings.append(
            "Correction logged without conversation_snippet. Include the "
            "relevant user message (~200 chars) for attribution."
        )

    # FM26: Gotcha with corrects_event_ids suggests reclassification
    if category == "gotcha" and effective_corrects:
        warnings.append(
            "Gotcha logged with corrects_event_ids — consider whether this "
            "should be category='correction' instead. Gotchas are surprises "
            "where nobody was wrong; corrections are for actual mistakes."
        )

    event = TraceEvent(
        session_id=session.id,
        type="annotation",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        annotation=AnnotationData(
            category=category,  # type: ignore[arg-type]
            content=content,
            tags=tags or [],
            corrects_event_ids=effective_corrects,
            related_event_ids=related_event_ids or [],
        ),
    )
    if conversation_snippet is not None:
        event.context.conversation_snippet = conversation_snippet
    event_id = await append_event(storage, session, event)

    if warnings:
        return event_id + "\n" + "\n".join(f"  \u26a0\ufe0f {w}" for w in warnings)
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
    conversation_snippet: str | None = None,
) -> str:
    """Log a contribution with direction-vs-execution attribution."""
    warnings: list[str] = []
    effective_decision_ids = related_decision_ids or []

    # FM5: Missing conversation_snippet
    if conversation_snippet is None:
        warnings.append(
            "Contribution logged without conversation_snippet. Include the "
            "relevant user message (~200 chars) for attribution."
        )

    # FM3 (partial): No related_decision_ids
    if not effective_decision_ids:
        warnings.append(
            "Contribution logged without related_decision_ids. Consider "
            "linking to the decision(s) that motivated this work."
        )

    event = TraceEvent(
        session_id=session.id,
        type="contribution",
        actor=Actor(type=actor_type, id=actor_id),  # type: ignore[arg-type]
        contribution=ContributionData(
            description=description,
            artifact=artifact,
            direction=direction,  # type: ignore[arg-type]
            execution=execution,  # type: ignore[arg-type]
            related_decision_ids=effective_decision_ids,
            tags=tags or [],
        ),
    )
    if conversation_snippet is not None:
        event.context.conversation_snippet = conversation_snippet
    event_id = await append_event(storage, session, event)

    if warnings:
        return event_id + "\n" + "\n".join(f"  \u26a0\ufe0f {w}" for w in warnings)
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

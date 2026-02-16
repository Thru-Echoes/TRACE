"""TRACE schema definitions — Pydantic models for sessions and events."""

from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    EventContext,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.schema.session import (
    Actor,
    Environment,
    Session,
    SessionMetadata,
)

# Resolve forward reference: Session.events uses TraceEvent which is in a
# separate module. Pydantic needs model_rebuild() after both classes are defined.
Session.model_rebuild()

__all__ = [
    "Actor",
    "AnnotationData",
    "ContributionData",
    "DecisionData",
    "Environment",
    "EventContext",
    "Session",
    "SessionMetadata",
    "StateChangeData",
    "ToolCallData",
    "TraceEvent",
]

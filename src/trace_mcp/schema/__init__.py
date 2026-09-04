"""TRACE schema definitions — Pydantic models for sessions and events."""

from trace_mcp.schema.events import (
    AnnotationCategory,
    AnnotationData,
    ContributionAttribution,
    ContributionData,
    DecisionConfidence,
    DecisionData,
    DecisionDisposition,
    EventContext,
    EvidenceRef,
    MeasurementDirection,
    MeasurementInterval,
    MeasurementMethod,
    StateChangeData,
    SuggestionType,
    ToolCallData,
    ToolCallHost,
    ToolCallStatus,
    TraceEvent,
)
from trace_mcp.schema.session import (
    SCHEMA_VERSION,
    Actor,
    ActorType,
    Environment,
    Session,
    SessionMetadata,
)

# Resolve forward reference: Session.events uses TraceEvent which is in a
# separate module. Pydantic needs model_rebuild() after both classes are defined.
Session.model_rebuild()

__all__ = [
    "SCHEMA_VERSION",
    "Actor",
    "ActorType",
    "AnnotationCategory",
    "ContributionAttribution",
    "DecisionDisposition",
    "MeasurementDirection",
    "SuggestionType",
    "ToolCallHost",
    "ToolCallStatus",
    "AnnotationData",
    "ContributionData",
    "DecisionConfidence",
    "DecisionData",
    "Environment",
    "EventContext",
    "EvidenceRef",
    "MeasurementInterval",
    "MeasurementMethod",
    "Session",
    "SessionMetadata",
    "StateChangeData",
    "ToolCallData",
    "TraceEvent",
]

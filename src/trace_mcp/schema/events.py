"""TRACE event type definitions.

Events are the core unit of TRACE — each one records a single auditable action
(tool call, decision, annotation, or state change) with full attribution.
"""

import warnings
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from trace_mcp.schema.session import Actor


class EventContext(BaseModel):
    """Shared context attached to any event."""

    conversation_turn: int | None = None
    parent_event_id: str | None = None
    reasoning_summary: str | None = None
    related_event_ids: list[str] = Field(default_factory=list)


class ToolCallData(BaseModel):
    """Records an MCP tool invocation on another server."""

    server: str
    method: str = "tools/call"
    name: str
    input: dict[str, Any]
    output: Any = None
    output_truncated: bool | None = None
    output_hash: str | None = None
    duration_ms: int | None = None
    status: Literal["success", "error", "timeout"] = "success"
    error_message: str | None = None


class DecisionData(BaseModel):
    """Records a decision with full attribution and resolution status."""

    description: str
    rationale: str | None = None
    proposed_by: Actor
    disposition: Literal["proposed", "accepted", "revised", "rejected"] = "proposed"
    resolved_by: Actor | None = None
    revision_note: str | None = None
    revises_event_id: str | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_resolution(self) -> Self:
        if self.disposition != "proposed" and self.resolved_by is None:
            raise ValueError(f"resolved_by must be set when disposition is '{self.disposition}'")
        if self.disposition in ("revised", "rejected") and not self.revision_note:
            warnings.warn(
                f"revision_note should be set when disposition is '{self.disposition}'",
                UserWarning,
                stacklevel=2,
            )
        return self


class AnnotationData(BaseModel):
    """Free-form observations, learnings, gotchas, todos."""

    category: Literal["learning", "gotcha", "observation", "todo", "question", "other"]
    content: str
    related_event_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class StateChangeData(BaseModel):
    """Records a change in environment, configuration, or tools."""

    description: str
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None


class TraceEvent(BaseModel):
    """A single audit event. The core unit of TRACE."""

    id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    type: Literal["tool_call", "decision", "annotation", "state_change"]
    actor: Actor

    tool_call: ToolCallData | None = None
    decision: DecisionData | None = None
    annotation: AnnotationData | None = None
    state_change: StateChangeData | None = None

    context: EventContext = Field(default_factory=EventContext)

    verification: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_type_data_match(self) -> Self:
        """Ensure exactly one data field is populated and matches type."""
        type_to_field: dict[str, str] = {
            "tool_call": "tool_call",
            "decision": "decision",
            "annotation": "annotation",
            "state_change": "state_change",
        }
        expected_field = type_to_field[self.type]
        if getattr(self, expected_field) is None:
            raise ValueError(f"Event type '{self.type}' requires '{expected_field}' to be populated")
        for field_name in type_to_field.values():
            if field_name != expected_field and getattr(self, field_name) is not None:
                raise ValueError(f"Event type '{self.type}' must not have '{field_name}' populated")
        return self

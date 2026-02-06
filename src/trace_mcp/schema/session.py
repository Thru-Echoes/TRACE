"""TRACE session and actor definitions.

A Session is the top-level audit document — one session = one TRACE JSON file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from trace_mcp.schema.events import TraceEvent


class Actor(BaseModel):
    """Who performed an action."""

    type: Literal["human", "ai", "system"]
    id: str
    role: str | None = None


class Environment(BaseModel):
    """Execution context for reproducibility."""

    mcp_servers: list[str] = Field(default_factory=list)
    client: str = ""
    os: str | None = None
    python_version: str | None = None
    trace_version: str = ""
    custom: dict[str, Any] = Field(default_factory=dict)


class SessionMetadata(BaseModel):
    """Descriptive metadata for a session."""

    project: str
    experiment_id: str | None = None
    description: str | None = None
    participants: list[Actor] = Field(default_factory=list)
    environment: Environment | None = None
    tags: list[str] = Field(default_factory=list)
    doi: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """Top-level audit session. One session = one TRACE JSON file."""

    context: str = "https://trace-protocol.org/v0.1"
    trace_version: str = "0.1.0"
    id: str
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended: datetime | None = None
    status: Literal["active", "completed", "abandoned"] = "active"
    metadata: SessionMetadata
    summary: str | None = None
    events: list[TraceEvent] = Field(default_factory=list)

    def next_event_id(self) -> str:
        """Generate the next sequential event ID."""
        return f"evt_{len(self.events) + 1:03d}"

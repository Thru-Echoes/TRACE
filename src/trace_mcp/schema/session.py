"""TRACE session and actor definitions.

A Session is the top-level audit document — one session = one TRACE JSON file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from trace_mcp.schema.events import TraceEvent


class TraceModel(BaseModel):
    """Base for TRACE schema models that PRESERVE unknown fields.

    Forward-compatibility: Pydantic v2's default
    ``extra='ignore'`` silently DROPS unknown fields, so an older server that
    loads a newer-schema session and rewrites it durably DELETES every field it
    did not recognize — the documented upgrade path's silent-data-loss mode.
    ``extra='allow'`` round-trips unknown fields through ``model_dump`` so a
    version-skewed read+write is non-destructive.

    Tradeoff (accepted): preservation is unconditional — a typo'd optional-field
    key survives as a ghost extra (the real field keeps its default) and any
    unknown key in a session file is retained through ``model_dump`` and the
    ``format="json"`` export. That is acceptable for a forward-compatible audit
    record; it is NOT a guarantee the extras are meaningful. (PROV-LD export is
    unaffected — it projects only known fields.)

    ``Environment`` is intentionally NOT a ``TraceModel``: it relies on
    ``extra='ignore'`` to drop the legacy ``environment.trace_version`` field
    (the v0.4.1 single-source-of-truth decision). It is therefore a closed
    forward-compat dead zone — future/extension environment data MUST go in
    ``Environment.custom`` (a real field), not as ad-hoc extra keys.
    """

    model_config = ConfigDict(extra="allow")


SCHEMA_VERSION = "0.4.1"
"""TRACE wire/schema format version that session documents conform to.

Intentionally decoupled from the package version (``trace_mcp.__version__``):
a wire-compatible release that adds no schema fields (e.g. 0.4.2) keeps this at
the last format-changing version. Bump it only when the on-disk session format
changes — never for a packaging/patch release. The spec namespace URL in
``Session.context`` is separately pinned at v0.3 per ADR-002 D6.
"""


# Canonical enum value-sets. Single source of truth — the MCP tool
# signatures in server.py import these so the protocol edge can never
# drift from the schema.
ActorType = Literal["human", "ai", "system"]


class Actor(TraceModel):
    """Who performed an action."""

    type: ActorType
    id: str
    role: str | None = None


class Environment(BaseModel):
    """Execution context for reproducibility.

    v0.4.1: `trace_version` removed from Environment to eliminate the
    two-source-of-truth problem. The single canonical version lives on
    `Session.trace_version`. Existing v0.3.x/v0.4.0 session files that
    have `environment.trace_version` set load cleanly via Pydantic v2
    default `extra='ignore'` (the field is silently dropped).
    """

    mcp_servers: list[str] = Field(default_factory=list)
    client: str = ""
    os: str | None = None
    python_version: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class SessionMetadata(TraceModel):
    """Descriptive metadata for a session."""

    project: str
    experiment_id: str | None = None
    description: str | None = None
    participants: list[Actor] = Field(default_factory=list)
    environment: Environment | None = None
    tags: list[str] = Field(default_factory=list)
    doi: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class Session(TraceModel):
    """Top-level audit session. One session = one TRACE JSON file."""

    context: str = "https://trace-protocol.org/v0.3"
    trace_version: str = Field(
        default=SCHEMA_VERSION,
        description=(
            "Wire/schema format version this record conforms to. Decoupled from "
            "the package version (trace_mcp.__version__): wire-compatible releases "
            "keep this at the schema version. See SCHEMA_VERSION."
        ),
    )
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

    def distinct_actor_types(self) -> set[str]:
        """Unique actor types across declared participants ∪ event actors.

        Per Round-3 amendment A1 / decision evt_016: the union of declared
        participants and observed event actors. When participants is empty
        this naturally falls back to the event actors alone (Round-3 A7).
        """
        types: set[str] = {p.type for p in self.metadata.participants}
        types.update(e.actor.type for e in self.events)
        return types

    def is_multi_actor(self) -> bool:
        """True when the session involves ≥2 distinct actor *types*.

        Gates the general-case (non-ai) same-instance self-resolution
        warning (spec §3.6 Proposer Identity Rule). In a single-actor
        session the same actor proposing and resolving is legitimate, not
        an attribution concern — flagging it is the false positive Round-3
        amendment A1 identified with production data.
        """
        return len(self.distinct_actor_types()) >= 2

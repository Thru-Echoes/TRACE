"""TRACE event type definitions.

Events are the core unit of TRACE — each one records a single auditable action
(tool call, decision, annotation, or state change) with full attribution.
"""

import warnings
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from trace_mcp.schema.session import Actor, TraceModel

# Canonical enum value-sets (single source of truth; server.py imports these
# for the MCP tool signatures so the protocol edge can never drift from the
# schema).
ToolCallStatus = Literal["success", "error", "timeout"]
ToolCallHost = Literal["mcp", "internal", "external"]
DecisionDisposition = Literal["proposed", "accepted", "revised", "rejected"]
SuggestionType = Literal["proactive", "requested", "collaborative"]
AnnotationCategory = Literal[
    "learning", "gotcha", "observation", "correction", "todo", "question", "discovery", "other"
]
ContributionAttribution = Literal["human", "ai", "collaborative"]
MeasurementDirection = Literal["higher", "lower"]


class EventContext(TraceModel):
    """Shared context attached to any event."""

    conversation_turn: int | None = None
    reasoning_summary: str | None = None
    conversation_snippet: str | None = None
    related_event_ids: list[str] = Field(default_factory=list)


class ToolCallData(TraceModel):
    """Records a tool or service invocation (MCP, external non-MCP, or host-internal)."""

    server: str
    method: str = "tools/call"
    name: str
    input: dict[str, Any]
    output: Any = None
    output_truncated: bool | None = None
    output_hash: str | None = None
    duration_ms: int | None = None
    status: ToolCallStatus = "success"
    error_message: str | None = None
    retries_event_id: str | None = None
    host: ToolCallHost = "mcp"
    parent_event_id: str | None = None


_HEX_DIGEST = r"^[0-9a-f]{64}$"
_PREFIXED_DIGEST = r"^sha256:[0-9a-f]{64}$"
# Identifier-like strings: non-empty, no control characters (so a value can never break a rendered
# line or an identifier built from it). Everything else about them is the producer's business.
_TEXT = r"^[^\x00-\x1f\x7f]+$"
Text = Annotated[str, StringConstraints(pattern=_TEXT)]
PrefixedDigest = Annotated[str, StringConstraints(pattern=_PREFIXED_DIGEST)]


class MeasurementInterval(TraceModel):
    """An interval on a measured effect: lower and upper bounds and the nominal coverage (v0.5.1)."""

    lower: float = Field(allow_inf_nan=False)
    upper: float = Field(allow_inf_nan=False)
    level: float = Field(gt=0.0, lt=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("interval lower bound must not exceed the upper bound")
        return self


class MeasurementMethod(TraceModel):
    """How an interval was computed (v0.5.1).

    ``name`` is a free identifier; consumers MUST NOT reject an unknown one. ``algorithm`` names the
    exact procedure when the producer has one.
    """

    name: Text
    algorithm: Text | None = None
    resamples: int | None = Field(default=None, ge=1)
    seed: int | None = None


class EvidenceRef(TraceModel):
    """A file a measurement rests on, by role, locator and SHA-256 (v0.5.1).

    A matching digest shows that a holder's bytes match the recorded digest. It does not show that
    the measurement was computed from those bytes, who produced the file, or that it is authentic.
    """

    role: Text
    locator: Text
    sha256: str = Field(pattern=_HEX_DIGEST)


class DecisionConfidence(TraceModel):
    """The producer-recorded measurement that motivated a decision (v0.5.1): the estimated effect,
    its interval, the method, the sample size, and the files it rests on.

    A positive ``estimate`` favours the option the decision describes; the producer orients the
    statistic that way. ``direction`` records the native sense of the underlying raw metric
    (``"lower"`` means the raw metric is better when smaller) so a reader can relate the estimate
    back to it. This object carries the measurement only. A producer's decision rule (a minimum
    effect, a verdict, a held-out check, a confirmation policy) travels as preserved extra keys
    identified by ``contract``; TRACE stores those untouched and never interprets them. Nothing
    here says whether the decision was right.

    Field order follows the contract's section 2 listing.
    """

    interval: MeasurementInterval
    method: MeasurementMethod
    sample_size: int = Field(ge=1)
    evidence_digests: dict[str, PrefixedDigest] | None = None
    contract: Text | None = None
    statistic: Text
    unit: Text | None = None
    direction: MeasurementDirection
    estimate: float = Field(allow_inf_nan=False)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _digests_match_evidence(self) -> Self:
        if self.evidence_digests is None:
            return self
        by_role: dict[str, str] = {}
        for ref in self.evidence:
            if ref.role in by_role:
                raise ValueError(f"evidence role {ref.role!r} appears twice; evidence_digests is keyed by role")
            by_role[ref.role] = ref.sha256
        if set(self.evidence_digests) != set(by_role):
            raise ValueError("evidence_digests keys must equal the set of evidence roles")
        for role, digest in self.evidence_digests.items():
            if digest != f"sha256:{by_role[role]}":
                raise ValueError(f"evidence_digests[{role!r}] does not match the evidence entry's sha256")
        return self


class DecisionData(TraceModel):
    """Records a decision with full attribution and resolution status."""

    description: str
    rationale: str | None = None
    proposed_by: Actor
    disposition: DecisionDisposition = "proposed"
    resolved_by: Actor | None = None
    revision_note: str | None = None
    revises_event_id: str | None = None
    suggestion_type: SuggestionType | None = None
    tags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: DecisionConfidence | None = None

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


class AnnotationData(TraceModel):
    """Free-form observations, learnings, gotchas, corrections, todos."""

    category: AnnotationCategory
    content: str
    corrects_event_ids: list[str] = Field(default_factory=list)
    related_event_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ContributionData(TraceModel):
    """Records a contribution with direction-vs-execution attribution.

    Captures who had the idea (direction) vs who did the work (execution),
    linking back to the decisions that motivated this contribution.
    """

    description: str
    artifact: str | None = None
    direction: ContributionAttribution
    execution: ContributionAttribution
    related_decision_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class StateChangeData(TraceModel):
    """Records a change in environment, configuration, or tools."""

    description: str
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None


class TraceEvent(TraceModel):
    """A single audit event. The core unit of TRACE."""

    id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    type: Literal["tool_call", "decision", "annotation", "state_change", "contribution"]
    actor: Actor

    tool_call: ToolCallData | None = None
    decision: DecisionData | None = None
    annotation: AnnotationData | None = None
    state_change: StateChangeData | None = None
    contribution: ContributionData | None = None

    context: EventContext = Field(default_factory=EventContext)

    @model_validator(mode="after")
    def _validate_type_data_match(self) -> Self:
        """Ensure exactly one data field is populated and matches type."""
        type_to_field: dict[str, str] = {
            "tool_call": "tool_call",
            "decision": "decision",
            "annotation": "annotation",
            "state_change": "state_change",
            "contribution": "contribution",
        }
        expected_field = type_to_field[self.type]
        if getattr(self, expected_field) is None:
            raise ValueError(f"Event type '{self.type}' requires '{expected_field}' to be populated")
        for field_name in type_to_field.values():
            if field_name != expected_field and getattr(self, field_name) is not None:
                raise ValueError(f"Event type '{self.type}' must not have '{field_name}' populated")
        return self

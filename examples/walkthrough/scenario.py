"""The golden-walkthrough scenario: one ordered list of steps, two consumers.

This module is the single source of truth for the walkthrough. It is consumed
by:

- ``render_walkthrough.py`` → the committed ``WALKTHROUGH.md`` a human reads.
- ``tests/test_walkthrough_e2e.py`` → the same steps driven over MCP stdio.

A test re-renders this scenario and diffs it against the committed markdown, so
the manual doc and the automated run can never drift apart.

Each :class:`Step` is pure data: the tool to call, the arguments to call it
with, the substrings its response must contain, and a one-line narration for
the doc. The one dynamic value — the session id minted by ``start_session`` —
is written as :data:`SESSION_ID_PLACEHOLDER` in the arguments; the E2E runner
substitutes the captured id, and the renderer prints the placeholder verbatim.

Exports:
    PROJECT                  the hermetic project the walkthrough pins to
    SESSION_ID_PLACEHOLDER   sentinel replaced with the live session id
    Step                     one walkthrough step (frozen Pydantic model)
    STEPS                    the ordered scenario
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The walkthrough pins a hermetic project so the golden doc is self-contained
# and the cross-project denial step has a concrete pinned key to name.
PROJECT = "walkthrough"

# A distinctive phrase planted by the learn_add step and recalled verbatim, so
# the recall step returns a scored hit deterministically — independent of what
# offline rule-based extraction happens to produce from the session's events.
SENTINEL = "peregrine falcon telemetry sentinel"

# Written wherever a step needs the id that start_session mints at run time. The
# renderer prints it literally; the E2E runner replaces it with the real id.
SESSION_ID_PLACEHOLDER = "$SESSION_ID"

# A label that is not the pinned project, used to show cross-project denial.
FOREIGN_PROJECT = "some-other-project"


class Step(BaseModel):
    """One walkthrough step: a tool call, what to expect, and how to narrate it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Short title for this step, shown as the doc heading.")
    tool: str = Field(description="The MCP tool to call.")
    arguments: dict[str, Any] = Field(description="Arguments for the call; may hold SESSION_ID_PLACEHOLDER.")
    expect_substrings: tuple[str, ...] = Field(description="Substrings the response must contain.")
    narration: str = Field(description="One or two sentences on what this step shows and what to look for.")
    capture_session_id: bool = Field(default=False, description="Capture the session id from this step's response.")
    expect_session_id: bool = Field(
        default=False, description="Assert the captured session id appears in the response."
    )


STEPS: tuple[Step, ...] = (
    Step(
        name="Start a session",
        tool="trace_start_session",
        arguments={
            "description": "Golden walkthrough of the TRACE provenance loop.",
            "participants": [
                {"id": "human", "type": "human", "role": "researcher"},
                {"id": "claude", "type": "ai", "role": "assistant"},
            ],
            "tags": ["walkthrough"],
        },
        expect_substrings=("TRACE audit logging is now active", f"Project: {PROJECT}", "Session:"),
        narration=(
            "Open a session at the start of a workflow. The server is pinned, so `project` is "
            "omitted and resolves to the pinned key. The banner reports the session id used below."
        ),
        capture_session_id=True,
    ),
    Step(
        name="Propose a decision (AI)",
        tool="trace_propose_decision",
        arguments={
            "description": "Use median imputation for the three missing station readings.",
            "proposed_by_type": "ai",
            "proposed_by_id": "claude",
            "suggestion_type": "proactive",
            "rationale": "Median resists the two outliers a mean would chase.",
            "session_id": SESSION_ID_PLACEHOLDER,
        },
        expect_substrings=("Decision proposed: evt_001",),
        narration=(
            "Log the decision BEFORE acting. The AI authored the proposal, so `proposed_by` is the "
            "AI — regardless of who later accepts it. It stays in the proposed state until resolved. "
            "It is the first event of the session, `evt_001`, which the next steps reference."
        ),
    ),
    Step(
        name="Resolve the decision (human accepts)",
        tool="trace_resolve_decision",
        arguments={
            "event_id": "evt_001",
            "disposition": "accepted",
            "resolved_by_type": "human",
            "resolved_by_id": "human",
            "session_id": SESSION_ID_PLACEHOLDER,
        },
        expect_substrings=("Decision evt_001 resolved: accepted",),
        narration=(
            "The human accepts the AI's proposal. Proposer stays the AI, resolver is the human — the "
            "attribution the record is built to keep. The proposal was `evt_001` in this session."
        ),
    ),
    Step(
        name="Log a contribution",
        tool="trace_log_contribution",
        arguments={
            "description": "Imputation applied to the station-readings table.",
            "direction": "human",
            "execution": "ai",
            "artifact": "data/stations.csv",
            "related_decision_ids": ["evt_001"],
            "conversation_snippet": "use median imputation for the missing readings",
            "session_id": SESSION_ID_PLACEHOLDER,
        },
        expect_substrings=("Logged contribution:",),
        narration=(
            "One contribution per artifact, splitting who had the idea (direction) from who did the "
            "work (execution), and linked to the decision that motivated it."
        ),
    ),
    Step(
        name="Log a correction",
        tool="trace_log_annotation",
        arguments={
            "category": "correction",
            "content": "The mean was used at first; the human caught it and the median was applied.",
            "corrects_event_ids": ["evt_001"],
            "conversation_snippet": "that's wrong, use the median not the mean",
            "session_id": SESSION_ID_PLACEHOLDER,
        },
        expect_substrings=("Logged annotation:",),
        narration=(
            "A correction records a caught mistake and links to what it corrects. This is how the "
            "record keeps the mistakes, not just the tidy final answer."
        ),
    ),
    Step(
        name="End the session",
        tool="trace_end_session",
        arguments={
            "session_id": SESSION_ID_PLACEHOLDER,
            "summary": "Walkthrough complete: one decision proposed, accepted, applied, and corrected.",
            "write_scratchpad": False,
        },
        expect_substrings=(
            "Session ended:",
            "--- Attribution Audit ---",
            "Contributions (1):",
            "direction=human, execution=ai",
            "artifact=data/stations.csv",
            "Decisions (1):",
            "proposed_by=ai",
            "disposition=accepted",
            "Corrections: 1 (corrects: evt_001)",
        ),
        expect_session_id=True,
        narration=(
            "Ending the session prints the Attribution Audit, and this is where the attribution is "
            "read back: the contribution's direction and execution, the decision proposed by the AI "
            "and accepted, and the correction linked to `evt_001`. The audit names the session id."
        ),
    ),
    Step(
        name="Read the decision back",
        tool="trace_get_decisions",
        arguments={"session_id": SESSION_ID_PLACEHOLDER},
        expect_substrings=(
            '"proposed_by":{"type":"ai"',
            '"resolved_by":{"type":"human"',
            '"disposition":"accepted"',
        ),
        narration=(
            "Query the completed session's decisions. The record kept both halves of the "
            "attribution: proposed by the AI, resolved by the human, accepted. That proposer-"
            "vs-resolver split is the distinction the whole system exists to preserve."
        ),
    ),
    Step(
        name="Add a learning",
        tool="trace_learn_add",
        arguments={
            "content": f"{SENTINEL}: median imputation beat the mean on the station readings.",
            "category": "learning",
        },
        expect_substrings=('"added"', '"id": "lrn_', SENTINEL),
        narration=(
            "Learnings persist in the project's knowledge store on disk. `project` is omitted and "
            "resolves to the pin, the same as the session tools. The response echoes the new "
            "learning's id and content."
        ),
    ),
    Step(
        name="Extract learnings from the record",
        tool="trace_learn_extract",
        arguments={},
        expect_substrings=('"new_learnings": 0',),
        narration=(
            "Extraction mines the session's decisions and annotations into durable learnings. It "
            "already ran when the session ended, so running it again here adds nothing: extraction "
            "is idempotent, and this call reports `new_learnings: 0`."
        ),
    ),
    Step(
        name="Recall a learning",
        tool="trace_learn_recall",
        arguments={"context": SENTINEL},
        expect_substrings=('"results"', '"score"', '"backend"', SENTINEL),
        narration=(
            "Recall ranks the store against a query and reports the backend that ranked it. The "
            "sentinel learning comes back with a score; the backend name makes a degraded ranker "
            "visible, never silent."
        ),
    ),
    Step(
        name="A write is denied across projects",
        tool="trace_learn_add",
        arguments={"project": FOREIGN_PROJECT, "content": "should never be written"},
        expect_substrings=('"error"', PROJECT, FOREIGN_PROJECT),
        narration=(
            "Under a pin, WRITING to another project fails closed the same way reading does — the "
            "error names both the pinned key and the label asked for, and no foreign store is created."
        ),
    ),
    Step(
        name="A read is denied across projects",
        tool="trace_learn_recall",
        arguments={"project": FOREIGN_PROJECT, "context": SENTINEL},
        expect_substrings=('"error"', PROJECT, FOREIGN_PROJECT),
        narration=(
            "And reading another project fails closed too. Cross-project reads and writes do not "
            "silently cross; a follow-up check confirms no `some-other-project` store was created."
        ),
    ),
)

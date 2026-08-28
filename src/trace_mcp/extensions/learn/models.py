"""Pydantic models for the trace-learn knowledge store.

Exports:
    LearningCategory  the closed set of categories a Learning may carry
    Learning          one extracted or manually added learning
    KnowledgeStore    a project's store, including its monotonic id counter
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

_LEARNING_ID_PREFIX = "lrn_"


def learning_id_number(learning_id: str) -> int | None:
    """The numeric suffix of a ``lrn_NNN`` id, or None for any other id shape.

    Pure. Ids that do not follow the scheme (hand-written or imported) simply do
    not participate in counter arithmetic.
    """
    if not learning_id.startswith(_LEARNING_ID_PREFIX):
        return None
    try:
        return int(learning_id[len(_LEARNING_ID_PREFIX) :])
    except ValueError:
        return None


# Categories that a Learning can have — superset of AnnotationData categories
# (includes "decision" for rejected/revised decision learnings).
# v0.4.1: added "discovery" — non-trivial findings from autonomous work
# should feed the cross-session knowledge store (spec §3.7).
LearningCategory = Literal[
    "learning",
    "gotcha",
    "correction",
    "decision",
    "observation",
    "prompt_pattern",
    "todo",
    "question",
    "discovery",
    "other",
]


class Learning(BaseModel):
    """A single extracted or manually added learning."""

    id: str = ""
    content: str
    category: LearningCategory = "learning"
    source_session: str | None = None
    source_event: str | None = None
    corrects_event_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recall_count: int = 0
    last_surfaced: datetime | None = None
    embedding: list[float] | None = None
    embedding_model: str | None = None
    # Creation-path provenance (egress-as-provenance): whether a cloud LLM,
    # the local rule-based extractor, or a human-initiated trace_learn_add
    # produced this learning's content. None on records predating the field.
    extraction_method: Literal["llm", "rule-based", "manual"] | None = None
    # Model id that generated the content when extraction_method == "llm"
    # (the content is model output, not a verbatim quote of session events).
    generated_by: str | None = None


class KnowledgeStore(BaseModel):
    """Per-project knowledge store containing accumulated learnings."""

    project: str
    version: str = "0.4"
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    next_id: int = Field(
        default=1,
        description=(
            "Monotonic counter for the next learning id. Persisted and never decreased, so an id "
            "released by `forget` is never reissued to different content."
        ),
    )
    learnings: list[Learning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _raise_counter_above_existing_ids(self) -> "KnowledgeStore":
        """Initialize or self-heal ``next_id`` — upward only, never downward.

        A store written before the counter existed carries no ``next_id``; it is
        initialized once to one past the highest id present. A counter that is
        present but too low (hand-edited, or truncated by a partial restore)
        would hand out an id that already exists, so it is raised and the
        anomaly is logged. A counter that is AHEAD of the learnings is left
        alone: that is exactly the post-``forget`` state this field exists to
        preserve, and lowering it would reintroduce the reuse defect.
        """
        highest = 0
        for lrn in self.learnings:
            num = learning_id_number(lrn.id)
            if num is not None and num > highest:
                highest = num
        if self.next_id <= highest:
            if "next_id" in self.model_fields_set:
                logger.warning(
                    "Knowledge store %r declares next_id=%d but already holds %s%03d; raising the "
                    "counter to %d so no id is reissued.",
                    self.project,
                    self.next_id,
                    _LEARNING_ID_PREFIX,
                    highest,
                    highest + 1,
                )
            self.next_id = highest + 1
        return self

    def next_learning_id(self) -> str:
        """Return the next learning id (``lrn_001``, ``lrn_002``, …) and advance the counter.

        Side effect: increments ``next_id``. The id is consumed on CALL rather
        than derived from the learnings present, so forgetting the highest
        learning cannot hand its id to different content later — the aliasing
        class that a ``max(existing) + 1`` scheme silently reintroduced on every
        delete (INV-12, docs/INVARIANTS.md).
        """
        learning_id = f"{_LEARNING_ID_PREFIX}{self.next_id:03d}"
        self.next_id += 1
        return learning_id

    def duplicate_learning_ids(self) -> list[str]:
        """Ids carried by more than one learning, sorted; empty for a healthy store.

        Pure. Reported rather than raised at load time so an already-aliased
        store stays readable and exportable; the write paths are what refuse
        (see ``store.save_store``), and ``trace-mcp identity repair-ids`` is the
        operator's fix.

        An UNSET id (the empty-string default on a not-yet-added ``Learning``) is
        not an alias — several unsaved learnings legitimately carry no id at all,
        and they are given one on add. Only assigned ids can collide, so blanks
        are skipped rather than reported as a collision with each other.
        """
        seen: set[str] = set()
        duplicates: set[str] = set()
        for lrn in self.learnings:
            if not lrn.id:
                continue
            if lrn.id in seen:
                duplicates.add(lrn.id)
            else:
                seen.add(lrn.id)
        return sorted(duplicates)

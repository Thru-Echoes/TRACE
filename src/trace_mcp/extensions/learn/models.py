"""Pydantic models for the trace-learn knowledge store."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Categories that a Learning can have — superset of AnnotationData categories
# (includes "decision" for rejected/revised decision learnings).
LearningCategory = Literal[
    "learning",
    "gotcha",
    "correction",
    "decision",
    "observation",
    "prompt_pattern",
    "todo",
    "question",
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


class KnowledgeStore(BaseModel):
    """Per-project knowledge store containing accumulated learnings."""

    project: str
    version: str = "0.4"
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    learnings: list[Learning] = Field(default_factory=list)

    def next_learning_id(self) -> str:
        """Generate the next sequential learning ID (lrn_001, lrn_002, ...)."""
        if not self.learnings:
            return "lrn_001"
        max_num = 0
        for lrn in self.learnings:
            if lrn.id.startswith("lrn_"):
                try:
                    num = int(lrn.id[4:])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"lrn_{max_num + 1:03d}"

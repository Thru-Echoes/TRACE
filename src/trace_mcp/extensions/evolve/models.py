"""Pydantic models for the trace-evolve genome store."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Adaptation(BaseModel):
    """A single evolved adaptation — an insight, correction, or pattern."""

    id: str = ""
    content: str
    category: str = "learning"
    source_session: str | None = None
    source_event: str | None = None
    tags: list[str] = Field(default_factory=list)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Genome(BaseModel):
    """Per-project genome containing accumulated adaptations."""

    project: str
    version: str = "0.1"
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    adaptations: list[Adaptation] = Field(default_factory=list)

    def next_adaptation_id(self) -> str:
        """Generate the next sequential adaptation ID (adp_001, adp_002, ...)."""
        if not self.adaptations:
            return "adp_001"
        max_num = 0
        for adp in self.adaptations:
            if adp.id.startswith("adp_"):
                try:
                    num = int(adp.id[4:])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"adp_{max_num + 1:03d}"

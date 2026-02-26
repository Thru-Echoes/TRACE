"""Natural selection: extract adaptations from TRACE session events.

Processes annotations (learning, correction, gotcha) and rejected/revised
decisions into persistent adaptations in the genome.
"""

from __future__ import annotations

from trace_mcp.extensions.evolve.models import Genome
from trace_mcp.extensions.evolve.store import add_adaptation
from trace_mcp.schema import Session

# Annotation categories that produce adaptations (selective pressure)
_EXTRACTABLE_CATEGORIES = {"learning", "correction", "gotcha"}


def _already_selected(genome: Genome, session_id: str, event_id: str) -> bool:
    """Check if an adaptation from this session+event already exists."""
    return any(adp.source_session == session_id and adp.source_event == event_id for adp in genome.adaptations)


def select_from_session(genome: Genome, session: Session) -> list[str]:
    """Select adaptations from a session's annotations and decisions.

    Idempotent: skips events that have already been selected.
    Returns list of new adaptation IDs.
    """
    new_ids: list[str] = []

    for evt in session.events:
        if _already_selected(genome, session.id, evt.id):
            continue

        # Select from annotations
        if evt.type == "annotation" and evt.annotation:
            if evt.annotation.category in _EXTRACTABLE_CATEGORIES:
                adp = add_adaptation(
                    genome,
                    content=evt.annotation.content,
                    category=evt.annotation.category,
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(evt.annotation.tags),
                )
                new_ids.append(adp.id)

        # Select from rejected/revised decisions
        elif evt.type == "decision" and evt.decision:
            d = evt.decision
            if d.disposition in ("rejected", "revised"):
                parts = [d.description]
                if d.revision_note:
                    parts.append(d.revision_note)
                content = " — ".join(parts)
                adp = add_adaptation(
                    genome,
                    content=content,
                    category="decision",
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(d.tags),
                )
                new_ids.append(adp.id)

    return new_ids

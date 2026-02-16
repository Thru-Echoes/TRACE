"""Extract learnings from TRACE session events.

Processes annotations (learning, correction, gotcha) and rejected/revised
decisions into persistent knowledge entries.
"""

from __future__ import annotations

from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning
from trace_mcp.schema import Session

# Annotation categories that produce learnings
_EXTRACTABLE_CATEGORIES = {"learning", "correction", "gotcha"}


def _already_extracted(store: KnowledgeStore, session_id: str, event_id: str) -> bool:
    """Check if a learning from this session+event already exists."""
    return any(lrn.source_session == session_id and lrn.source_event == event_id for lrn in store.learnings)


def extract_from_session(store: KnowledgeStore, session: Session) -> list[str]:
    """Extract learnings from a session's annotations and decisions.

    Idempotent: skips events that have already been extracted.
    Returns list of new learning IDs.
    """
    new_ids: list[str] = []

    for evt in session.events:
        if _already_extracted(store, session.id, evt.id):
            continue

        # Extract from annotations
        if evt.type == "annotation" and evt.annotation:
            if evt.annotation.category in _EXTRACTABLE_CATEGORIES:
                lrn = add_learning(
                    store,
                    content=evt.annotation.content,
                    category=evt.annotation.category,
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(evt.annotation.tags),
                )
                new_ids.append(lrn.id)

        # Extract from rejected/revised decisions
        elif evt.type == "decision" and evt.decision:
            d = evt.decision
            if d.disposition in ("rejected", "revised"):
                parts = [d.description]
                if d.revision_note:
                    parts.append(d.revision_note)
                content = " — ".join(parts)
                lrn = add_learning(
                    store,
                    content=content,
                    category="decision",
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(d.tags),
                )
                new_ids.append(lrn.id)

    return new_ids

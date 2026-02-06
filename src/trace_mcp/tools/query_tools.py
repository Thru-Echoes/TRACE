"""Query and retrieval tools for TRACE sessions."""

from __future__ import annotations

from typing import Any

from trace_mcp.schema import Session


def get_session_summary(session: Session) -> dict[str, Any]:
    """Get session data (excluding events for brevity)."""
    data = session.model_dump(mode="json")
    data.pop("events", None)
    data["event_count"] = len(session.events)
    return data


def get_events(
    session: Session,
    *,
    type_filter: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List events in a session, optionally filtered by type."""
    events = session.events
    if type_filter:
        events = [e for e in events if e.type == type_filter]
    events = events[:limit]
    return [e.model_dump(mode="json") for e in events]


def get_decisions(
    session: Session,
    *,
    disposition: str | None = None,
) -> list[dict[str, Any]]:
    """List all decisions in a session, optionally filtered by disposition."""
    decisions = [e for e in session.events if e.type == "decision"]
    if disposition:
        decisions = [e for e in decisions if e.decision is not None and e.decision.disposition == disposition]
    return [e.model_dump(mode="json") for e in decisions]


def get_decision_chain(
    session: Session,
    *,
    event_id: str,
) -> list[dict[str, Any]]:
    """Get the full chain of linked decisions starting from any decision.

    Walks revises_event_id links in both directions to assemble the full chain.
    """
    events_by_id = {e.id: e for e in session.events if e.type == "decision"}

    if event_id not in events_by_id:
        return []

    # Walk backward to the root
    root_id = event_id
    visited: set[str] = set()
    while True:
        if root_id in visited:
            break
        visited.add(root_id)
        evt = events_by_id.get(root_id)
        if evt is None or evt.decision is None:
            break
        parent = evt.decision.revises_event_id
        if parent and parent in events_by_id:
            root_id = parent
        else:
            break

    # Walk forward from root collecting the chain
    chain: list[dict[str, Any]] = []
    # Build reverse index: parent_id -> children
    children: dict[str, list[str]] = {}
    for eid, evt in events_by_id.items():
        if evt.decision and evt.decision.revises_event_id:
            parent = evt.decision.revises_event_id
            children.setdefault(parent, []).append(eid)

    # BFS from root
    queue = [root_id]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        evt = events_by_id.get(current)
        if evt:
            chain.append(evt.model_dump(mode="json"))
            for child_id in children.get(current, []):
                queue.append(child_id)

    return chain


def search_events(
    session: Session,
    *,
    query: str,
) -> list[dict[str, Any]]:
    """Search events by text content (case-insensitive substring match)."""
    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for evt in session.events:
        searchable: list[str] = []
        if evt.tool_call:
            searchable.append(evt.tool_call.name)
            searchable.append(str(evt.tool_call.input))
        if evt.decision:
            searchable.append(evt.decision.description)
            if evt.decision.rationale:
                searchable.append(evt.decision.rationale)
            if evt.decision.revision_note:
                searchable.append(evt.decision.revision_note)
        if evt.annotation:
            searchable.append(evt.annotation.content)
            searchable.extend(evt.annotation.tags)
        if evt.state_change:
            searchable.append(evt.state_change.description)
            if evt.state_change.reason:
                searchable.append(evt.state_change.reason)
        if evt.context.reasoning_summary:
            searchable.append(evt.context.reasoning_summary)

        combined = " ".join(searchable).lower()
        if query_lower in combined:
            results.append(evt.model_dump(mode="json"))

    return results

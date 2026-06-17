"""Query and retrieval tools for TRACE sessions."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import trace_mcp
from trace_mcp.schema import Session
from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)

# v0.4.2 Phase 3: HARD payload caps. Unbounded query results are the primary
# context-bloat surface compounding the API-400 crash. These ceilings bind even
# when the caller requests more (the crashing model overrode list_sessions to
# 30/40), so a too-large `limit` is clamped, not honoured.
DEFAULT_EVENTS_LIMIT = 25
MAX_EVENTS_LIMIT = 200
DEFAULT_SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 100
MAX_SESSION_SCAN = 500  # health_check / project_summary file-read ceiling


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
    limit: int = DEFAULT_EVENTS_LIMIT,
) -> list[dict[str, Any]]:
    """List events in a session, optionally filtered by type.

    ``limit`` is clamped to [1, MAX_EVENTS_LIMIT]; a request above the ceiling
    is silently capped rather than honoured (context-bloat guard).
    """
    limit = max(1, min(limit, MAX_EVENTS_LIMIT))
    events = session.events
    if type_filter:
        events = [e for e in events if e.type == type_filter]
    events = events[:limit]
    return [e.model_dump(mode="json") for e in events]


def get_decisions(
    session: Session,
    *,
    disposition: str | None = None,
    proposed_by_type: str | None = None,
) -> list[dict[str, Any]]:
    """List all decisions in a session, optionally filtered by disposition and/or proposer type."""
    decisions = [e for e in session.events if e.type == "decision"]
    if disposition:
        decisions = [e for e in decisions if e.decision is not None and e.decision.disposition == disposition]
    if proposed_by_type:
        decisions = [e for e in decisions if e.decision is not None and e.decision.proposed_by.type == proposed_by_type]
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
            searchable.extend(evt.annotation.corrects_event_ids)
        if evt.state_change:
            searchable.append(evt.state_change.description)
            if evt.state_change.reason:
                searchable.append(evt.state_change.reason)
        if evt.contribution:
            searchable.append(evt.contribution.description)
            if evt.contribution.artifact:
                searchable.append(evt.contribution.artifact)
            searchable.extend(evt.contribution.tags)
        if evt.context.reasoning_summary:
            searchable.append(evt.context.reasoning_summary)
        if evt.context.conversation_snippet:
            searchable.append(evt.context.conversation_snippet)

        combined = " ".join(searchable).lower()
        if query_lower in combined:
            results.append(evt.model_dump(mode="json"))

    return results


def _compute_knowledge_metrics(project: str) -> dict[str, Any]:
    """Compute knowledge store metrics for a project.

    Returns total learnings, category breakdown, most-surfaced learnings,
    never-surfaced count, and average recall count.

    Core/extension boundary (see docs/adr/003-core-extension-boundary.md):
    the optional trace-learn extension must never be a hard dependency of a
    core tool. When it is absent, this fails open with the zero sentinel so
    the core tool `trace_project_summary` stays functional — `import` must
    never break a core tool.
    """
    try:
        from trace_mcp.extensions.learn.store import load_store
    except ImportError:
        return {
            "total": 0,
            "by_category": {},
            "most_surfaced": [],
            "never_surfaced": 0,
            "avg_recall_count": 0.0,
        }

    store = load_store(project)
    if not store.learnings:
        return {
            "total": 0,
            "by_category": {},
            "most_surfaced": [],
            "never_surfaced": 0,
            "avg_recall_count": 0.0,
        }

    by_category: dict[str, int] = {}
    never_surfaced = 0
    total_recall = 0

    for lrn in store.learnings:
        by_category[lrn.category] = by_category.get(lrn.category, 0) + 1
        total_recall += lrn.recall_count
        if lrn.recall_count == 0:
            never_surfaced += 1

    # Top 5 most surfaced
    sorted_learnings = sorted(store.learnings, key=lambda lr: lr.recall_count, reverse=True)
    most_surfaced = [
        {
            "id": lrn.id,
            "content": lrn.content[:100],
            "recall_count": lrn.recall_count,
            "category": lrn.category,
        }
        for lrn in sorted_learnings[:5]
        if lrn.recall_count > 0
    ]

    return {
        "total": len(store.learnings),
        "by_category": by_category,
        "most_surfaced": most_surfaced,
        "never_surfaced": never_surfaced,
        "avg_recall_count": round(total_recall / len(store.learnings), 2),
    }


async def project_summary(
    storage: TraceStorage,
    project: str,
    scan_cap: int = MAX_SESSION_SCAN,
) -> dict[str, Any]:
    """Aggregate metrics across all sessions for a project.

    Returns counts by event type, decision disposition, contribution
    direction/execution matrix, annotation categories, unique participants,
    and knowledge store metrics.
    """
    session_summaries = await storage.list_sessions(project=project, limit=scan_cap)
    scan_truncated = len(session_summaries) >= scan_cap
    sessions: list[Session] = []
    skipped_sessions: list[str] = []
    for s in session_summaries:
        try:
            sessions.append(await storage.get_session(s["id"]))
        except FileNotFoundError:
            continue
        except (ValidationError, json.JSONDecodeError) as exc:
            # A schema-invalid / corrupt record must not abort the whole
            # aggregate. Skip it, LOG it (so the omission isn't silent), and
            # report it so the aggregate stats stay honest, not silently wrong.
            # (JSON-corrupt files are already pre-filtered by list_sessions, so
            # in practice this catches schema-invalid valid-JSON records.)
            logger.warning("project_summary(%s): skipping unreadable session %s: %s", project, s["id"], exc)
            skipped_sessions.append(s["id"])

    total_events = 0
    events_by_type: dict[str, int] = {}
    decisions_total = 0
    proposed_by_ai = 0
    proposed_by_human = 0
    accepted = 0
    revised = 0
    rejected = 0
    pending = 0
    suggestion_types: dict[str, int] = {}
    contribution_matrix: dict[str, int] = {}
    annotations_by_category: dict[str, int] = {}
    participants: set[str] = set()
    # Correction/retry metrics
    correction_count = 0
    corrections_with_links = 0
    retry_chains = 0
    retry_chain_events: set[str] = set()

    for session in sessions:
        for p in session.metadata.participants:
            participants.add(f"{p.id} ({p.type})")

        for evt in session.events:
            total_events += 1
            events_by_type[evt.type] = events_by_type.get(evt.type, 0) + 1

            if evt.type == "decision" and evt.decision:
                d = evt.decision
                decisions_total += 1
                if d.proposed_by.type == "ai":
                    proposed_by_ai += 1
                elif d.proposed_by.type == "human":
                    proposed_by_human += 1
                if d.disposition == "accepted":
                    accepted += 1
                elif d.disposition == "revised":
                    revised += 1
                elif d.disposition == "rejected":
                    rejected += 1
                elif d.disposition == "proposed":
                    pending += 1
                if d.suggestion_type:
                    suggestion_types[d.suggestion_type] = suggestion_types.get(d.suggestion_type, 0) + 1

            elif evt.type == "annotation" and evt.annotation:
                cat = evt.annotation.category
                annotations_by_category[cat] = annotations_by_category.get(cat, 0) + 1
                if cat == "correction":
                    correction_count += 1
                    if evt.annotation.corrects_event_ids:
                        corrections_with_links += 1

            elif evt.type == "contribution" and evt.contribution:
                c = evt.contribution
                key = f"{c.direction}_directed_{c.execution}_executed"
                contribution_matrix[key] = contribution_matrix.get(key, 0) + 1

            elif evt.type == "tool_call" and evt.tool_call:
                if evt.tool_call.retries_event_id:
                    # This event is a retry — track the chain
                    if evt.tool_call.retries_event_id not in retry_chain_events:
                        # New chain (the parent wasn't already part of one)
                        retry_chains += 1
                    retry_chain_events.add(evt.id)
                    retry_chain_events.add(evt.tool_call.retries_event_id)

    resolved = accepted + revised + rejected
    acceptance_rate = round(accepted / resolved, 3) if resolved > 0 else None
    human_interventions = correction_count + rejected + revised
    intervention_rate = round(human_interventions / total_events, 3) if total_events > 0 else None

    return {
        "project": project,
        "session_count": len(sessions),
        "skipped_sessions": skipped_sessions,
        "scan_truncated": scan_truncated,
        "total_events": total_events,
        "events_by_type": events_by_type,
        "decisions": {
            "total": decisions_total,
            "proposed_by_ai": proposed_by_ai,
            "proposed_by_human": proposed_by_human,
            "accepted": accepted,
            "revised": revised,
            "rejected": rejected,
            "pending": pending,
            "acceptance_rate": acceptance_rate,
            "suggestion_types": suggestion_types,
        },
        "contributions": contribution_matrix,
        "annotations_by_category": annotations_by_category,
        "human_interventions": {
            "total": human_interventions,
            "corrections": correction_count,
            "corrections_with_links": corrections_with_links,
            "decision_rejections": rejected,
            "decision_revisions": revised,
            "retry_chains": retry_chains,
            "intervention_rate": intervention_rate,
        },
        "participants": sorted(participants),
        "knowledge": _compute_knowledge_metrics(project),
    }


async def health_check(
    storage: TraceStorage,
    *,
    project: str | None = None,
    session_id: str | None = None,
    scan_cap: int = MAX_SESSION_SCAN,
) -> dict[str, Any]:
    """Return system health info and event-level statistics.

    Optionally scoped to a single project or session. When scanning all
    sessions, at most ``scan_cap`` (most-recent) session files are read so a
    health probe never becomes an unbounded full-store read; ``scan_truncated``
    flags when more sessions exist beyond the scan window.
    """
    # Storage paths (reported for diagnostics only — never control flow).
    sessions_dir = storage.location()
    sessions_dir_exists = sessions_dir != "unknown" and Path(sessions_dir).is_dir()

    # Knowledge dir is reported for diagnostics only. Compute it from the env
    # WITHOUT importing the trace-learn extension — core must not depend on the
    # extension (HARD CONSTRAINT #1; see docs/adr/003-core-extension-boundary.md).
    # This mirrors the extension's own default resolution.
    knowledge_dir = str(Path(os.environ.get("TRACE_KNOWLEDGE_DIR", "~/.trace/knowledge")).expanduser())
    knowledge_dir_exists = Path(knowledge_dir).is_dir()

    # Load sessions (bounded: read at most scan_cap most-recent files).
    sessions: list[Session] = []
    skipped_sessions: list[str] = []
    scan_truncated = False
    if session_id:
        try:
            sessions.append(await storage.get_session(session_id))
        except FileNotFoundError:
            pass
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning("health_check: skipping unreadable session %s: %s", session_id, exc)
            skipped_sessions.append(session_id)
    else:
        summaries = await storage.list_sessions(project=project, limit=scan_cap)
        scan_truncated = len(summaries) >= scan_cap
        for s in summaries:
            try:
                sessions.append(await storage.get_session(s["id"]))
            except FileNotFoundError:
                continue
            except (ValidationError, json.JSONDecodeError) as exc:
                # Skip-and-report (and log) a schema-invalid/corrupt record
                # rather than aborting the health probe entirely.
                logger.warning("health_check: skipping unreadable session %s: %s", s["id"], exc)
                skipped_sessions.append(s["id"])

    # Tally events
    total = 0
    by_type: dict[str, int] = {}
    by_actor_type: dict[str, int] = {}

    for session in sessions:
        for evt in session.events:
            total += 1
            by_type[evt.type] = by_type.get(evt.type, 0) + 1
            by_actor_type[evt.actor.type] = by_actor_type.get(evt.actor.type, 0) + 1

    return {
        "version": trace_mcp.__version__,
        "storage": {
            "sessions_dir": sessions_dir,
            "sessions_dir_exists": sessions_dir_exists,
            "knowledge_dir": knowledge_dir,
            "knowledge_dir_exists": knowledge_dir_exists,
        },
        "session_count": len(sessions),
        "sessions_scanned": len(sessions),
        "skipped_sessions": skipped_sessions,
        "scan_truncated": scan_truncated,
        "project_filter": project,
        "session_filter": session_id,
        "events": {
            "total": total,
            "by_type": by_type,
            "by_actor_type": by_actor_type,
        },
    }

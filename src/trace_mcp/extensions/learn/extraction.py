"""Extract learnings from TRACE session events.

Two extraction backends:

1. **Rule-based** (default fallback) — Processes annotations (learning,
   correction, gotcha), rejected/revised decisions, and contributions into
   persistent knowledge entries.  Preserves corrects_event_ids and decision
   rationale.
2. **LLM-enhanced** (primary when configured) — Sends session events to
   an OpenAI model for intelligent identification and synthesis of learnings.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning
from trace_mcp.schema import Session

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig

logger = logging.getLogger(__name__)

# Annotation categories that rule-based extraction processes
_EXTRACTABLE_CATEGORIES = {"learning", "correction", "gotcha"}

try:
    from openai import AsyncOpenAI

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


# ── Helpers ───────────────────────────────────────────────────────────────


def _already_extracted(store: KnowledgeStore, session_id: str, event_id: str) -> bool:
    """Check if a learning from this session+event already exists."""
    return any(
        lrn.source_session == session_id and lrn.source_event == event_id
        for lrn in store.learnings
    )


# ── Rule-based extraction (fallback) ─────────────────────────────────────


def extract_from_session(store: KnowledgeStore, session: Session) -> list[str]:
    """Extract learnings from a session's events using rule-based logic.

    Processes:
    - Annotations with category in {learning, correction, gotcha}
    - Rejected/revised decisions (with rationale and revision_note)
    - Contributions with collaborative direction (emergent insights)

    Idempotent: skips events that have already been extracted.
    Returns list of new learning IDs.
    """
    new_ids: list[str] = []

    for evt in session.events:
        if _already_extracted(store, session.id, evt.id):
            continue

        # ── Annotations ──
        if evt.type == "annotation" and evt.annotation:
            ann = evt.annotation
            if ann.category in _EXTRACTABLE_CATEGORIES:
                lrn = add_learning(
                    store,
                    content=ann.content,
                    category=ann.category,
                    source_session=session.id,
                    source_event=evt.id,
                    corrects_event_ids=list(ann.corrects_event_ids) if ann.corrects_event_ids else None,
                    tags=list(ann.tags),
                )
                new_ids.append(lrn.id)

        # ── Rejected/revised decisions ──
        elif evt.type == "decision" and evt.decision:
            d = evt.decision
            if d.disposition in ("rejected", "revised"):
                parts = [d.description]
                if d.rationale:
                    parts.append(f"Rationale: {d.rationale}")
                if d.revision_note:
                    parts.append(f"Revision: {d.revision_note}")
                content = " — ".join(parts)

                tags = list(d.tags)
                if d.suggestion_type and d.suggestion_type not in tags:
                    tags.append(d.suggestion_type)

                lrn = add_learning(
                    store,
                    content=content,
                    category="decision",
                    source_session=session.id,
                    source_event=evt.id,
                    tags=tags,
                )
                new_ids.append(lrn.id)

        # ── Contributions with collaborative direction ──
        elif evt.type == "contribution" and evt.contribution:
            contrib = evt.contribution
            if contrib.direction == "collaborative":
                content = contrib.description
                if contrib.artifact:
                    content += f" (artifact: {contrib.artifact})"
                lrn = add_learning(
                    store,
                    content=content,
                    category="observation",
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(contrib.tags),
                )
                new_ids.append(lrn.id)

    return new_ids


# ── LLM-enhanced extraction ──────────────────────────────────────────────


def _format_events_for_llm(session: Session) -> str:
    """Format session events as a text block for LLM consumption."""
    lines: list[str] = []
    for evt in session.events:
        parts = [f"[{evt.id}] type={evt.type}"]
        if evt.annotation:
            parts.append(f"category={evt.annotation.category}")
            parts.append(f'content="{evt.annotation.content}"')
            if evt.annotation.corrects_event_ids:
                parts.append(f"corrects={evt.annotation.corrects_event_ids}")
            if evt.annotation.tags:
                parts.append(f"tags={evt.annotation.tags}")
        elif evt.decision:
            parts.append(f'description="{evt.decision.description}"')
            parts.append(f"disposition={evt.decision.disposition}")
            if evt.decision.rationale:
                parts.append(f'rationale="{evt.decision.rationale}"')
            if evt.decision.revision_note:
                parts.append(f'revision_note="{evt.decision.revision_note}"')
            if evt.decision.suggestion_type:
                parts.append(f"suggestion_type={evt.decision.suggestion_type}")
            if evt.decision.tags:
                parts.append(f"tags={evt.decision.tags}")
        elif evt.contribution:
            parts.append(f'description="{evt.contribution.description}"')
            parts.append(f"direction={evt.contribution.direction}")
            parts.append(f"execution={evt.contribution.execution}")
            if evt.contribution.artifact:
                parts.append(f"artifact={evt.contribution.artifact}")
            if evt.contribution.tags:
                parts.append(f"tags={evt.contribution.tags}")
        elif evt.state_change:
            parts.append(f'description="{evt.state_change.description}"')
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _format_existing_learnings(store: KnowledgeStore) -> str:
    """Format existing learnings so the LLM can avoid duplicates."""
    if not store.learnings:
        return "(none)"
    lines: list[str] = []
    for lrn in store.learnings:
        lines.append(f'- [{lrn.id}] ({lrn.category}) "{lrn.content}"')
    return "\n".join(lines)


async def extract_from_session_llm(
    store: KnowledgeStore,
    session: Session,
    config: LearnConfig,
) -> list[str]:
    """Extract learnings from a session using an LLM.

    The model reads all session events and identifies valuable learnings,
    synthesising cross-event patterns and generating quality tags.
    Falls back to rule-based extraction on any error.
    """
    if not _HAS_OPENAI or not config.openai_api_key:
        return extract_from_session(store, session)

    events_text = _format_events_for_llm(session)
    if not events_text.strip():
        return []

    existing_text = _format_existing_learnings(store)

    prompt = (
        f"Session ID: {session.id}\n"
        f"Project: {session.metadata.project}\n\n"
        f"Events:\n{events_text}\n\n"
        f"Existing learnings (DO NOT duplicate):\n{existing_text}\n\n"
        "Extract NEW learnings from these events. For each learning:\n"
        '- "content": actionable, specific insight (1-3 sentences)\n'
        '- "category": one of learning, correction, gotcha, decision, observation\n'
        '- "tags": 2-5 relevant tags for future retrieval\n'
        '- "source_event": the event ID this learning primarily comes from\n'
        '- "corrects_event_ids": list of event IDs being corrected (for corrections only)\n\n'
        "Return a JSON object with key \"learnings\" containing an array of objects.\n"
        "If no new learnings exist, return {\"learnings\": []}."
    )

    try:
        client = AsyncOpenAI(api_key=config.openai_api_key)
        response = await client.chat.completions.create(
            model=config.llm_extraction_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a knowledge extraction system for AI-assisted workflow audits. "
                        "You identify actionable learnings from session event logs: corrections "
                        "(human caught an AI mistake), gotchas (surprising findings), decisions "
                        "(important rejected/revised choices), and general learnings. "
                        "Be selective — only extract insights that would help in future sessions. "
                        "Do NOT duplicate existing learnings."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        raw = response.choices[0].message.content or '{"learnings": []}'
        parsed = json.loads(raw)
        extracted: list[dict] = parsed.get("learnings", [])

        new_ids: list[str] = []
        for item in extracted:
            source_event = item.get("source_event", "")
            if source_event and _already_extracted(store, session.id, source_event):
                continue
            lrn = add_learning(
                store,
                content=item.get("content", ""),
                category=item.get("category", "learning"),
                source_session=session.id,
                source_event=source_event or None,
                corrects_event_ids=item.get("corrects_event_ids"),
                tags=item.get("tags", []),
            )
            new_ids.append(lrn.id)

        return new_ids

    except Exception:
        logger.warning("LLM extraction failed — falling back to rule-based", exc_info=True)
        return extract_from_session(store, session)


# ── Auto-selection ────────────────────────────────────────────────────────


async def extract_from_session_auto(
    store: KnowledgeStore,
    session: Session,
    config: LearnConfig | None = None,
) -> list[str]:
    """Extract learnings using LLM if available, else rule-based."""
    if config is None:
        from trace_mcp.extensions.learn.config import load_config

        config = load_config()

    if config.llm_enabled and config.openai_api_key and _HAS_OPENAI:
        return await extract_from_session_llm(store, session, config)
    return extract_from_session(store, session)

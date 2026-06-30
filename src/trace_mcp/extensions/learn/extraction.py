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

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning, LearningCategory
from trace_mcp.extensions.learn.store import add_learning, add_learning_dedup
from trace_mcp.schema import Session

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig

logger = logging.getLogger(__name__)

# Annotation categories that rule-based extraction processes
_EXTRACTABLE_CATEGORIES = {"learning", "correction", "gotcha"}

# Keywords that signal a decision is about prompt strategy (for auto-categorization)
_PROMPT_KEYWORDS = frozenset(
    {
        "prompt",
        "few-shot",
        "zero-shot",
        "chain-of-thought",
        "system prompt",
        "instruction",
        "few shot",
        "zero shot",
        "chain of thought",
        "cot",
        "prompting",
        "prompt engineering",
        "prompt template",
        "in-context",
    }
)

try:
    from openai import AsyncOpenAI

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


# ── Helpers ───────────────────────────────────────────────────────────────


def _already_extracted(store: KnowledgeStore, session_id: str, event_id: str) -> bool:
    """Check if a learning from this session+event already exists."""
    return any(lrn.source_session == session_id and lrn.source_event == event_id for lrn in store.learnings)


def _add_with_optional_dedup(
    store: KnowledgeStore,
    *,
    content: str,
    category: LearningCategory,
    source_session: str,
    source_event: str | None,
    corrects_event_ids: list[str] | None = None,
    tags: list[str] | None = None,
    dedup_threshold: float | None = None,
) -> Learning | None:
    """Add a learning, optionally checking for content duplicates first.

    Returns the new Learning, or None if a content duplicate was found.
    """
    if dedup_threshold is not None:
        result = add_learning_dedup(
            store,
            content=content,
            category=category,
            source_session=source_session,
            source_event=source_event,
            corrects_event_ids=corrects_event_ids,
            tags=tags,
            dedup_threshold=dedup_threshold,
        )
        if result.is_duplicate:
            logger.debug(
                "Skipping duplicate learning (similar to %s): %.60s…",
                result.duplicate_of,
                content,
            )
            return None
        return result.learning
    return add_learning(
        store,
        content=content,
        category=category,
        source_session=source_session,
        source_event=source_event,
        corrects_event_ids=corrects_event_ids,
        tags=tags,
    )


# ── Rule-based extraction (fallback) ─────────────────────────────────────


def extract_from_session(
    store: KnowledgeStore,
    session: Session,
    *,
    dedup_threshold: float | None = None,
) -> list[str]:
    """Extract learnings from a session's events using rule-based logic.

    Processes:
    - Annotations with category in {learning, correction, gotcha}
    - Rejected/revised decisions (with rationale and revision_note)
    - Contributions with collaborative direction (emergent insights)

    Idempotent: skips events that have already been extracted.
    When *dedup_threshold* is set, also skips content-duplicate learnings.
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
                lrn = _add_with_optional_dedup(
                    store,
                    content=ann.content,
                    category=ann.category,
                    source_session=session.id,
                    source_event=evt.id,
                    corrects_event_ids=list(ann.corrects_event_ids) if ann.corrects_event_ids else None,
                    tags=list(ann.tags),
                    dedup_threshold=dedup_threshold,
                )
                if lrn is not None:
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

                # Auto-categorize as prompt_pattern if description
                # mentions prompt-related keywords
                desc_lower = d.description.lower()
                cat: LearningCategory = "decision"
                if any(kw in desc_lower for kw in _PROMPT_KEYWORDS):
                    cat = "prompt_pattern"
                    if "prompt_pattern" not in tags:
                        tags.append("prompt_pattern")

                lrn = _add_with_optional_dedup(
                    store,
                    content=content,
                    category=cat,
                    source_session=session.id,
                    source_event=evt.id,
                    tags=tags,
                    dedup_threshold=dedup_threshold,
                )
                if lrn is not None:
                    new_ids.append(lrn.id)

        # ── Contributions with collaborative direction ──
        elif evt.type == "contribution" and evt.contribution:
            contrib = evt.contribution
            if contrib.direction == "collaborative":
                content = contrib.description
                if contrib.artifact:
                    content += f" (artifact: {contrib.artifact})"
                lrn = _add_with_optional_dedup(
                    store,
                    content=content,
                    category="observation",
                    source_session=session.id,
                    source_event=evt.id,
                    tags=list(contrib.tags),
                    dedup_threshold=dedup_threshold,
                )
                if lrn is not None:
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
    *,
    dedup_threshold: float | None = None,
) -> list[str]:
    """Extract learnings from a session using an LLM.

    The model reads all session events and identifies valuable learnings,
    synthesising cross-event patterns and generating quality tags.
    Falls back to rule-based extraction on any error (unless strict mode).
    """
    from trace_mcp.extensions.learn.config import LLMFallbackError

    if not _HAS_OPENAI or not config.openai_api_key:
        if config.strict_llm and config.openai_api_key:
            logger.error(
                "LLM extraction requested in strict mode but 'openai' package "
                "is not installed. Refusing to fall back to rule-based extraction."
            )
            raise LLMFallbackError(
                "LLM extraction unavailable: 'openai' package not installed. "
                "Install with: pip install 'trace-mcp[llm]'. "
                "Or set TRACE_STRICT_LLM=false to allow rule-based fallback."
            )
        return extract_from_session(store, session, dedup_threshold=dedup_threshold)

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
        '- "category": one of learning, correction, gotcha, decision, observation, prompt_pattern\n'
        '- "tags": 2-5 relevant tags for future retrieval\n'
        '- "source_event": the event ID this learning primarily comes from\n'
        '- "corrects_event_ids": list of event IDs being corrected (for corrections only)\n\n'
        'Return a JSON object with key "learnings" containing an array of objects.\n'
        'If no new learnings exist, return {"learnings": []}.'
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
                        "(important rejected/revised choices), prompt_pattern (prompt strategies "
                        "that were especially effective or ineffective — e.g., accuracy improvements "
                        "from prompt changes, failed prompting approaches, effective few-shot "
                        "patterns), and general learnings. When session events reveal a decision "
                        "chain about prompt refinement (revisions mentioning prompts, accuracy "
                        "changes, or strategy shifts), extract a prompt_pattern learning noting "
                        "what worked, what didn't, and why. "
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
            lrn = _add_with_optional_dedup(
                store,
                content=item.get("content", ""),
                category=item.get("category", "learning"),
                source_session=session.id,
                source_event=source_event or None,
                corrects_event_ids=item.get("corrects_event_ids"),
                tags=item.get("tags", []),
                dedup_threshold=dedup_threshold,
            )
            if lrn is not None:
                new_ids.append(lrn.id)

        return new_ids

    except Exception as exc:
        if config.strict_llm:
            logger.error(
                "LLM extraction failed in strict mode (model=%s) — "
                "refusing to silently fall back to rule-based extraction.",
                config.llm_extraction_model,
            )
            raise LLMFallbackError(
                f"LLM extraction failed (model={config.llm_extraction_model}): {exc}. "
                f"Strict mode is ON — set TRACE_STRICT_LLM=false to allow "
                f"rule-based fallback."
            ) from exc
        logger.warning(
            "LLM extraction failed (model=%s) — falling back to rule-based. Strict mode is OFF.",
            config.llm_extraction_model,
            exc_info=True,
        )
        return extract_from_session(store, session, dedup_threshold=dedup_threshold)


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

    dedup_threshold = config.dedup_threshold if config.dedup_enabled else None

    if config.llm_enabled and config.openai_api_key and _HAS_OPENAI:
        return await extract_from_session_llm(store, session, config, dedup_threshold=dedup_threshold)
    return extract_from_session(store, session, dedup_threshold=dedup_threshold)

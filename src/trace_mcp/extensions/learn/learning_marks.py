"""A recalled learning's standing, read from the event it was extracted from (ADR 008 D1, D9, D10).

A learning has no mark of its own. Its marks are computed, each time it is
recalled, from its source event in the session record, so a decision resolved
after the learning was written shows its current standing. The marks describe
that source event, not the learning's wording, which is the extractor's. A
learning whose source event cannot be found is kept and marked
``[source-unknown]``, and one whose source is neither a decision nor an AI's
note takes ``[ai-record]`` or ``[person-note]`` (``get_record_mark``), so no
learning is shown without a mark.

From the same source event, recall also learns:

- whether the learning steps back: a person rejected its source decision, or
  replaced it by accepting a decision that revises it (in the same session,
  following the chain of such revisions to its end);
- which corrections in that session name the source event, shown as pointers;
- whether the evergreen floor may hold it up: only when a person accepted its
  source as written (``[accepted]``, not ``[revised]``) and it does not step back.

A session is shown the base mark only. The quote-check tags depend on the
person's recorded words and a miss is often benign; they appear in exports,
the report and the review sheet, where a person reads them.

Exports: SourceReader, get_learning_tags, get_record_mark, get_replacement, get_corrections,
get_results_marks, get_display_marks

Side effects: ``SourceReader`` reads session records through the storage and,
while a comparison period runs for the project, the project's words files.
Nothing is written.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from trace_mcp.extensions.learn.matching import StepBack
from trace_mcp.extensions.learn.models import Learning
from trace_mcp.extensions.learn.shadow.state import get_project_key, is_active
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import (
    AI_RECORD_TAG,
    MARK_MEANINGS,
    PERSON_NOTE_TAG,
    SOURCE_UNKNOWN_TAG,
    WordsEntry,
    get_base_mark,
    get_event_marks,
)
from trace_mcp.schema import Session, TraceEvent
from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)

LearningLike = Learning | Mapping[str, Any]
# Base marks of a decision a person took up and did not reject.
PERSON_BACKED = ("accepted", "revised")


def _source_ids(learning: LearningLike) -> tuple[str | None, str | None]:
    if isinstance(learning, Learning):
        return learning.source_session, learning.source_event
    session_id, event_id = learning.get("source_session"), learning.get("source_event")
    return (session_id if isinstance(session_id, str) else None), (event_id if isinstance(event_id, str) else None)


def get_record_mark(event: TraceEvent) -> str:
    """Return the mark a learning shows when its source event has no base mark: who wrote that record. Pure.

    ``[person-note]`` for an event recorded with a person as its actor; ``[ai-record]`` for
    every other (an AI's contribution, a tool call, a state change).
    """
    return PERSON_NOTE_TAG if event.actor.type == "human" else AI_RECORD_TAG


def get_learning_tags(
    event: TraceEvent, words: Sequence[WordsEntry], *, session_created: datetime, as_of: datetime | None = None
) -> list[str]:
    """Return every tag a learning takes from its source event, optionally as of a past time. Pure.

    The event's own tags, led by ``get_record_mark`` when the event has no base
    mark, so that no learning is counted or shown without a mark.
    """
    tags = get_event_marks(event, words, session_created=session_created, as_of=as_of).tags()
    return tags if get_base_mark(event)[0] is not None else [get_record_mark(event), *tags]


def get_replacement(session: Session, event_id: str) -> str | None:
    """Return the id of the decision a person accepted in place of ``event_id``, following revisions; else None.

    A replacement is a decision in the same session whose ``revises_event_id`` names the
    event and that a person accepted, or revised in turn: either way the person set the
    earlier decision aside. Its own replacement, if any, wins. A
    revision an AI resolved, or a person rejected, replaces nothing. Pure.
    """
    current, seen = event_id, {event_id}
    while True:
        accepted = [
            e.id
            for e in session.events
            if e.type == "decision"
            and e.decision is not None
            and e.decision.revises_event_id == current
            and get_base_mark(e)[0] in PERSON_BACKED
        ]
        if not accepted or accepted[-1] in seen:
            return None if current == event_id else current
        current = accepted[-1]
        seen.add(current)


def get_corrections(session: Session, event_id: str) -> list[str]:
    """Return ``<session>#<event>`` of each correction in ``session`` that names ``event_id``, in order. Pure."""
    return [
        f"{session.id}#{e.id}"
        for e in session.events
        if e.type == "annotation"
        and e.annotation is not None
        and e.annotation.category == "correction"
        and event_id in e.annotation.corrects_event_ids
    ]


class SourceReader:
    """Reads recalled learnings' source events, each session once per recall.

    State: the storage, the person's recorded words (only while a comparison period
    runs for the project) and the session records read so far. One reader serves one
    recall. No method raises: a source that cannot be read reads as unknown, steps
    nothing back and earns no floor.
    """

    def __init__(self, *, project: str, storage: TraceStorage) -> None:
        self._storage = storage
        self._cache: dict[str, Session | None] = {}
        try:
            key = get_project_key(project)
            self._words: list[WordsEntry] = load_words(key) if is_active(key) else []
        except Exception:
            self._words = []

    async def _source(self, learning: LearningLike) -> tuple[Session, TraceEvent] | None:
        session_id, event_id = _source_ids(learning)
        if not session_id or not event_id:
            return None
        if session_id not in self._cache:
            try:
                self._cache[session_id] = await self._storage.get_session(session_id)
            except Exception:
                self._cache[session_id] = None
        session = self._cache[session_id]
        if session is None:
            return None
        event = next((e for e in session.events if e.id == event_id), None)
        return (session, event) if event is not None else None

    async def get_tags(self, learning: LearningLike) -> list[str]:
        """Every tag of the learning's source event, or ``[source-unknown]``."""
        try:
            found = await self._source(learning)
            if found is None:
                return [SOURCE_UNKNOWN_TAG]
            session, event = found
            return get_learning_tags(event, self._words, session_created=session.created)
        except Exception:
            logger.warning("Could not read a learning's source marks", exc_info=True)
            return [SOURCE_UNKNOWN_TAG]

    async def get_step_back(self, learning: LearningLike) -> StepBack | None:
        """Why the learning steps back from recall, or None (also when its source cannot be read)."""
        try:
            found = await self._source(learning)
            if found is None:
                return None
            session, event = found
            if (replacement := get_replacement(session, event.id)) is not None:
                return StepBack("replaced", f"{session.id}#{replacement}")
            return StepBack("rejected") if get_base_mark(event)[0] == "rejected" else None
        except Exception:
            logger.warning("Could not read whether a learning steps back; it is recalled as before", exc_info=True)
            return None

    async def is_floor_eligible(self, learning: LearningLike) -> bool:
        """True when a person accepted the learning's source and it does not step back."""
        try:
            found = await self._source(learning)
            if found is None or get_base_mark(found[1])[0] != "accepted":
                return False
            return await self.get_step_back(learning) is None
        except Exception:
            logger.warning("Could not read a learning's source; the evergreen floor does not apply", exc_info=True)
            return False

    async def set_results_marks(self, results: Sequence[dict[str, Any]]) -> list[list[str]]:
        """Set each result's ``source_marks`` (and ``source_corrected_by`` when any); return every tag per result.

        Side effects: mutates the result dicts.
        """
        out: list[list[str]] = []
        for result in results:
            learning = result.get("learning") or {}
            tags = await self.get_tags(learning)
            result["source_marks"] = get_display_marks(tags)
            try:
                found = await self._source(learning)
                corrected = get_corrections(found[0], found[1].id) if found is not None else []
            except Exception:
                corrected = []
            if corrected:
                result["source_corrected_by"] = corrected
            out.append(tags)
        return out


async def get_results_marks(
    results: Sequence[Mapping[str, Any]], *, project: str, storage: TraceStorage
) -> list[list[str]]:
    """Return every tag of each recall result's source event, in result order. Never raises.

    ``[source-unknown]`` stands for a learning with no recorded source, a source
    session that cannot be read, or an event no longer in it. The quote checks
    use the person's recorded words only while a comparison period runs for the
    project; otherwise only the tags the record alone supports are returned.
    """
    reader = SourceReader(project=project, storage=storage)
    return [await reader.get_tags(result.get("learning") or {}) for result in results]


def get_display_marks(tags: Sequence[str]) -> list[str]:
    """Return the tags a session is shown: the base mark, or ``[source-unknown]``."""
    return [tag for tag in tags if tag in MARK_MEANINGS]

"""Marks of a recalled learning: the marks of the event it was extracted from (ADR 008 D1, D9).

A learning has no mark of its own. Its marks are computed, each time it is
recalled, from its source event in the session record, so a decision resolved
after the learning was written shows its current standing. A learning whose
source event cannot be found is kept and marked ``[source-unknown]``.

A session is shown the base mark only. The quote-check tags depend on the
person's recorded words and a miss is often benign; they appear in exports,
the report and the review sheet, where a person reads them.

Exports: get_results_marks, get_display_marks, set_display_marks

Side effects: ``get_results_marks`` reads session records through the storage
and, while a comparison period runs for the project, the project's words
files. Nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from trace_mcp.extensions.learn.shadow.state import get_project_key, is_active
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import MARK_MEANINGS, SOURCE_UNKNOWN_TAG, WordsEntry, get_event_marks
from trace_mcp.schema import Session
from trace_mcp.storage.base import TraceStorage


async def _source_tags(
    learning: Mapping[str, Any], storage: TraceStorage, words: Sequence[WordsEntry], cache: dict[str, Session | None]
) -> list[str]:
    session_id, event_id = learning.get("source_session"), learning.get("source_event")
    if not (isinstance(session_id, str) and session_id and isinstance(event_id, str) and event_id):
        return [SOURCE_UNKNOWN_TAG]
    if session_id not in cache:
        try:
            cache[session_id] = await storage.get_session(session_id)
        except Exception:
            cache[session_id] = None
    session = cache[session_id]
    event = next((e for e in session.events if e.id == event_id), None) if session is not None else None
    if session is None or event is None:
        return [SOURCE_UNKNOWN_TAG]
    return get_event_marks(event, words, session_created=session.created).tags()


async def get_results_marks(
    results: Sequence[Mapping[str, Any]], *, project: str, storage: TraceStorage
) -> list[list[str]]:
    """Return every tag of each recall result's source event, in result order. Never raises.

    ``[source-unknown]`` stands for a learning with no recorded source, a source
    session that cannot be read, or an event no longer in it. The quote checks
    use the person's recorded words only while a comparison period runs for the
    project; otherwise only the tags the record alone supports are returned.
    """
    try:
        key = get_project_key(project)
        words: list[WordsEntry] = load_words(key) if is_active(key) else []
    except Exception:
        words = []
    cache: dict[str, Session | None] = {}
    out: list[list[str]] = []
    for result in results:
        try:
            out.append(await _source_tags(result.get("learning") or {}, storage, words, cache))
        except Exception:
            out.append([SOURCE_UNKNOWN_TAG])
    return out


def get_display_marks(tags: Sequence[str]) -> list[str]:
    """Return the tags a session is shown: the base mark, or ``[source-unknown]``."""
    return [tag for tag in tags if tag in MARK_MEANINGS]


def set_display_marks(results: Sequence[dict[str, Any]], tags: Sequence[Sequence[str]]) -> None:
    """Set ``result["marks"]`` on each recall result to the tags a session is shown.

    Side effects: mutates the result dicts.
    """
    for result, item_tags in zip(results, tags, strict=True):
        result["marks"] = get_display_marks(item_tags)

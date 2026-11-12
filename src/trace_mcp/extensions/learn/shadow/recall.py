"""Shadow recall: record what each recall would have shown with marks (ADR 008 D6).

At every recall the live result is returned unchanged. While the shadow period
runs for the project, ``record_recall`` appends one line to
``<shadow root>/<key>/recall.jsonl``: the recalled ids, each item's marks (from
its source event, looked up in the session record), and the rendered length of
the items without marks and with marks plus the one-line legend (D9).

Exports: RecallItem, RecallRecord, record_recall

Side effects: ``record_recall`` reads session records and the project's words
files and appends one line under the shadow root; it never raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from trace_mcp.extensions.learn.shadow import extract as shadow_extract
from trace_mcp.extensions.learn.shadow.state import append_record, get_project_dir, is_active, record_error
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import MARK_LEGEND, SOURCE_UNKNOWN_TAG, WordsEntry, get_event_marks
from trace_mcp.schema import Session
from trace_mcp.storage.base import TraceStorage


class RecallItem(BaseModel):
    """One recalled learning, with the marks it would have been shown with."""

    id: str = Field(..., description="Learning id.")
    category: str = Field(..., description="Learning category.")
    matched: bool = Field(..., description="Whether the item cleared the recall floor.")
    tags: list[str] = Field(default_factory=list, description="Provenance-mark tags of its source event.")
    content_chars: int = Field(..., description="Length of the learning's content.")


class RecallRecord(BaseModel):
    """One line of ``recall.jsonl``."""

    ts: datetime = Field(..., description="UTC time of the recall.")
    project_key: str = Field(..., description="Canonical project key.")
    limit: int = Field(..., description="The recall's result limit.")
    context_chars: int = Field(..., description="Length of the recall's context text.")
    items: list[RecallItem] = Field(default_factory=list, description="The recalled items, in order.")
    chars_without: int = Field(..., description="Rendered length of the items without marks.")
    chars_with: int = Field(..., description="Rendered length with marks and the legend line.")


def get_rendered_chars(items: Sequence[RecallItem], contents: Sequence[str]) -> tuple[int, int]:
    """Return ``(chars without marks, chars with marks plus legend)`` for the rendered recall items.

    An item renders as ``"  - [<category>] <content>"``; with marks, ``" " +
    " ".join(tags)`` is added to each item that has tags, plus one legend line
    when any item has tags.
    """
    without = sum(len(f"  - [{item.category}] {content}") for item, content in zip(items, contents, strict=True))
    tagged = [item for item in items if item.tags]
    extra = sum(len(" " + " ".join(item.tags)) for item in tagged)
    legend = len(MARK_LEGEND) + 1 if tagged else 0
    return without, without + extra + legend


async def _get_session(storage: TraceStorage, session_id: str, cache: dict[str, Session | None]) -> Session | None:
    if session_id not in cache:
        try:
            cache[session_id] = await storage.get_session(session_id)
        except Exception:
            cache[session_id] = None
    return cache[session_id]


async def _record(*, key: str, results: list[dict[str, Any]], storage: TraceStorage, limit: int, context: str) -> None:
    words: list[WordsEntry] = load_words(key)
    cache: dict[str, Session | None] = {}
    items: list[RecallItem] = []
    contents: list[str] = []
    for result in results:
        learning = result.get("learning") or {}
        content = str(learning.get("content", ""))
        source_session = learning.get("source_session")
        source_event = learning.get("source_event")
        tags = [SOURCE_UNKNOWN_TAG]
        if isinstance(source_session, str) and source_session and isinstance(source_event, str) and source_event:
            session = await _get_session(storage, source_session, cache)
            event = next((e for e in session.events if e.id == source_event), None) if session else None
            if session is not None and event is not None:
                tags = get_event_marks(event, words, session_created=session.created).tags()
        items.append(
            RecallItem(
                id=str(learning.get("id", "")),
                category=str(learning.get("category", "")),
                matched=bool(result.get("matched", False)),
                tags=tags,
                content_chars=len(content),
            )
        )
        contents.append(content)
    without, with_marks = get_rendered_chars(items, contents)
    record = RecallRecord(
        ts=datetime.now(UTC),
        project_key=key,
        limit=limit,
        context_chars=len(context),
        items=items,
        chars_without=without,
        chars_with=with_marks,
    )
    append_record(get_project_dir(key) / "recall.jsonl", record)


async def record_recall(
    *, project: str, results: list[dict[str, Any]], storage: TraceStorage, limit: int, context: str
) -> None:
    """Record what a recall would have shown with marks. Never raises; a no-op unless the period runs here.

    Call it after the recall's store lock is released, with the results the
    recall is about to return. Failures, and the shadow time limit, are appended
    to the project's ``errors.jsonl`` with step ``recall``.
    """
    key = ""
    try:
        key = shadow_extract._project_key(project)
        if not is_active(key):
            return
        await asyncio.wait_for(
            _record(key=key, results=list(results), storage=storage, limit=limit, context=context),
            shadow_extract.SHADOW_STEP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        record_error(key or "unknown", "recall", exc)

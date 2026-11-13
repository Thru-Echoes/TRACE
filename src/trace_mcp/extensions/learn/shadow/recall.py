"""Recall record: what each recall showed, by mark (ADR 008 D6).

While a comparison period runs for the project, ``record_recall`` appends one
line to ``<shadow root>/<key>/recall.jsonl``: the recalled ids, every tag of
each item's source event, and the rendered length of the items without marks
and as shown, with the mark a session sees plus the one-line legend (D9).

Exports: RecallItem, RecallRecord, get_rendered_chars, record_recall

Side effects: ``record_recall`` appends one line under the shadow root; it
never raises and never changes the recall result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from trace_mcp.extensions.learn.learning_marks import get_display_marks, get_results_marks
from trace_mcp.extensions.learn.shadow.state import (
    append_record,
    get_project_dir,
    get_project_key,
    is_active,
    record_error,
)
from trace_mcp.provenance_marks import get_mark_legend
from trace_mcp.storage.base import TraceStorage

RECORD_TIMEOUT_SECONDS = 90.0


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
    """Return ``(chars without marks, chars as shown)`` for the rendered recall items.

    An item renders as ``"  - [<category>] <content>"``. As shown, the mark a
    session sees precedes the category, and one legend line explains the marks
    in use.
    """
    without = sum(len(f"  - [{item.category}] {content}") for item, content in zip(items, contents, strict=True))
    shown = [get_display_marks(item.tags) for item in items]
    extra = sum(len(" ".join(marks) + " ") for marks in shown if marks)
    legend = get_mark_legend([tag for marks in shown for tag in marks], any_unmarked=any(not m for m in shown))
    return without, without + extra + (len(legend) + 3 if items and legend else 0)


async def _record(
    *,
    key: str,
    results: list[dict[str, Any]],
    storage: TraceStorage,
    limit: int,
    context: str,
    tags: Sequence[Sequence[str]] | None,
    project: str,
) -> None:
    if tags is None:
        tags = await get_results_marks(results, project=project, storage=storage)
    items: list[RecallItem] = []
    contents: list[str] = []
    for result, item_tags in zip(results, tags, strict=True):
        learning = result.get("learning") or {}
        content = str(learning.get("content", ""))
        items.append(
            RecallItem(
                id=str(learning.get("id", "")),
                category=str(learning.get("category", "")),
                matched=bool(result.get("matched", False)),
                tags=list(item_tags),
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
    *,
    project: str,
    results: list[dict[str, Any]],
    storage: TraceStorage,
    limit: int,
    context: str,
    tags: Sequence[Sequence[str]] | None = None,
) -> None:
    """Record one recall while a comparison period runs for the project. Never raises; otherwise a no-op.

    ``tags`` are the tags the recall already computed for each result (every
    tag of the source event); when omitted they are looked up here. Failures,
    and the time limit, are appended to the project's ``errors.jsonl`` with
    step ``recall``.
    """
    key = ""
    try:
        key = get_project_key(project)
        if not is_active(key):
            return
        await asyncio.wait_for(
            _record(
                key=key,
                results=list(results),
                storage=storage,
                limit=limit,
                context=context,
                tags=tags,
                project=project,
            ),
            RECORD_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        record_error(key or "unknown", "recall", exc)

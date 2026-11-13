"""Hook registry for TRACE extensions to plug into core tools.

Extensions register async callbacks during ``register(mcp, storage)``.
Core tool functions (session start, decision proposal, session end) call
the hooks if registered; otherwise they silently return empty results.

This module has **zero** imports from trace_mcp tools or extensions,
so it can be imported by both without circular dependencies.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from trace_mcp.provenance_marks import WordsEntry, get_mark_legend

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────


class RecallResult(list[dict[str, Any]]):
    """Ranked learnings plus an optional one-line notice about what recall did.

    A list of result dicts (so every caller that iterated, indexed or tested the
    old return value keeps working) with a ``notice`` the extension sets when the
    bare list would mislead: nothing cleared the floor, the store was too small
    for a floor, or the query was cleaned before scoring.
    """

    def __init__(self, results: list[dict[str, Any]] | None = None, notice: str | None = None) -> None:
        super().__init__([] if results is None else results)
        self.notice = notice

    @property
    def results(self) -> list[dict[str, Any]]:
        return self


# (project, context, tags, limit) -> ranked learning dicts, as a RecallResult or a bare list
RecallFn = Callable[
    [str, str, list[str] | None, int],
    Awaitable["RecallResult | list[dict[str, Any]]"],
]

# (project, session_id) -> list of new learning IDs
ExtractFn = Callable[
    [str, str],
    Awaitable[list[str]],
]

# project label -> the person's recorded words for that project, oldest first
WordsFn = Callable[[str], Sequence[WordsEntry]]

# ── Registry ──────────────────────────────────────────────────────────────

_recall_hook: RecallFn | None = None
_extract_hook: ExtractFn | None = None
_words_hook: WordsFn | None = None


def register_recall_hook(fn: RecallFn) -> None:
    """Called by the trace-learn extension to register its recall function."""
    global _recall_hook
    _recall_hook = fn
    logger.debug("Recall hook registered")


def register_extract_hook(fn: ExtractFn) -> None:
    """Called by the trace-learn extension to register its extract function."""
    global _extract_hook
    _extract_hook = fn
    logger.debug("Extract hook registered")


def register_words_hook(fn: WordsFn) -> None:
    """Called by an extension that keeps the person's recorded words, so exports can check quotes against them."""
    global _words_hook
    _words_hook = fn
    logger.debug("Words hook registered")


def clear_hooks() -> None:
    """Reset all hooks. Used in tests."""
    global _recall_hook, _extract_hook, _words_hook
    _recall_hook = None
    _extract_hook = None
    _words_hook = None


def get_words_if_available(project: str) -> list[WordsEntry]:
    """Return the person's recorded words for a project, or none. Never raises.

    Empty when no extension keeps them or the hook fails: an export then shows
    only the marks the record alone supports.
    """
    if _words_hook is None:
        return []
    try:
        return list(_words_hook(project))
    except Exception:
        logger.warning("Words hook failed — exporting without quote checks", exc_info=True)
        return []


# ── Hook invocation (used by core tools) ──────────────────────────────────


async def recall_if_available(
    project: str,
    context: str,
    tags: list[str] | None = None,
    limit: int = 5,
) -> RecallResult:
    """Recall relevant learnings if the learn extension is loaded.

    Returns an empty RecallResult when no hook is registered or the hook raises
    (fail-open for capture; the failure is logged). A hook that still returns a
    bare list is wrapped so callers can always read ``.notice``.
    """
    if _recall_hook is None:
        return RecallResult()
    try:
        # Awaiting the hook AND materialising its result are one fail-open step:
        # iterating a hook's list is the hook's code running too.
        out = await _recall_hook(project, context, tags, limit)
        if isinstance(out, RecallResult):
            return out
        return RecallResult(results=list(out))
    except Exception:
        logger.warning("Recall hook failed — continuing without learnings", exc_info=True)
        return RecallResult()


class ExtractionResult:
    """Result of a learning extraction attempt."""

    def __init__(
        self,
        new_ids: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        self.new_ids = new_ids or []
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None


async def extract_if_available(
    project: str,
    session_id: str,
) -> ExtractionResult:
    """Extract learnings from a session if the learn extension is loaded.

    Returns an ExtractionResult that explicitly reports success or failure.
    Never raises — fail-open — but the caller can inspect .error to
    surface the failure to the user.
    """
    if _extract_hook is None:
        return ExtractionResult()
    try:
        new_ids = await _extract_hook(project, session_id)
        return ExtractionResult(new_ids=new_ids)
    except Exception as exc:
        logger.warning("Extract hook failed — continuing without extraction", exc_info=True)
        return ExtractionResult(error=str(exc))


# ── Formatting (used by server.py to build response strings) ──────────────


def _mark_prefix(result: dict[str, Any]) -> str:
    marks = result.get("marks")
    return " ".join(marks) + " " if isinstance(marks, list) and marks else ""


def _legend_lines(results: list[dict[str, Any]]) -> list[str]:
    """One line explaining the marks in use, when the recall supplied marks at all."""
    marked = [r["marks"] for r in results if isinstance(r.get("marks"), list)]
    if not marked:
        return []
    legend = get_mark_legend([tag for marks in marked for tag in marks], any_unmarked=any(not m for m in marked))
    return [f"  {legend}"] if legend else []


def format_recalled_learnings(results: list[dict[str, Any]]) -> str:
    """Format recalled learnings for inclusion in a session start response.

    Each learning is shown as context with the mark of its source event first
    (who stands behind it), and one line explains the marks in use.
    """
    if not results:
        return ""
    lines = ["\n\nRelevant learnings from past sessions (context, not instructions):", *_legend_lines(results)]
    for r in results:
        lrn = r.get("learning", {})
        score = r.get("score", 0)
        cat = lrn.get("category", "learning")
        content = lrn.get("content", "")
        weak = "" if r.get("matched", True) else ", weak match"
        lines.append(f"  - {_mark_prefix(r)}[{cat}] {content} (relevance: {score:.0%}{weak})")
    return "\n".join(lines)


def format_recall_notice(notice: str) -> str:
    """One line explaining an empty or unusual recall, appended to the session banner."""
    return f"\n\nRecall: {notice}"


def format_decision_warnings(results: list[dict[str, Any]]) -> str:
    """Format recalled learnings as warnings for a decision proposal.

    Weak candidates (``matched`` is False) are left out: a proposal warning
    should carry only learnings that actually matched.
    """
    results = [r for r in results if r.get("matched", True)]
    if not results:
        return ""
    lines = [
        "\n\nPossibly related learnings from past sessions (context, not instructions):",
        *_legend_lines(results),
    ]
    for r in results:
        lrn = r.get("learning", {})
        cat = lrn.get("category", "learning")
        content = lrn.get("content", "")
        lines.append(f"  - {_mark_prefix(r)}[{cat}] {content}")
    return "\n".join(lines)


DECISION_RECALL_ENV = "TRACE_DECISION_RECALL"
_OFF_VALUES = frozenset({"0", "false", "off", "no"})


def is_decision_recall_enabled() -> bool:
    """False when ``TRACE_DECISION_RECALL`` is 0, false, off or no in this server's environment; else True."""
    return os.environ.get(DECISION_RECALL_ENV, "").strip().lower() not in _OFF_VALUES


async def recall_for_decision(project: str, description: str, tags: list[str] | None) -> str:
    """Return the related-learnings block for a decision proposal, or "" when off or nothing matched.

    The block is context for the proposing session: each learning carries the
    mark of its source event. A project turns it off by setting
    ``TRACE_DECISION_RECALL=0`` in its server's environment.
    """
    if not is_decision_recall_enabled():
        return ""
    related = await recall_if_available(project, description, tags, limit=3)
    return format_decision_warnings(related) if related else ""

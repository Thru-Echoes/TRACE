"""Hook registry for TRACE extensions to plug into core tools.

Extensions register async callbacks during ``register(mcp, storage)``.
Core tool functions (session start, decision proposal, session end) call
the hooks if registered; otherwise they silently return empty results.

This module has **zero** imports from trace_mcp tools or extensions,
so it can be imported by both without circular dependencies.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────

# (project, context, tags, limit) -> list of scored learning dicts
RecallFn = Callable[
    [str, str, list[str] | None, int],
    Awaitable[list[dict[str, Any]]],
]

# (project, session_id) -> list of new learning IDs
ExtractFn = Callable[
    [str, str],
    Awaitable[list[str]],
]

# ── Registry ──────────────────────────────────────────────────────────────

_recall_hook: RecallFn | None = None
_extract_hook: ExtractFn | None = None


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


def clear_hooks() -> None:
    """Reset all hooks. Used in tests."""
    global _recall_hook, _extract_hook
    _recall_hook = None
    _extract_hook = None


# ── Hook invocation (used by core tools) ──────────────────────────────────


async def recall_if_available(
    project: str,
    context: str,
    tags: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Recall relevant learnings if the learn extension is loaded.

    Returns an empty list if no recall hook is registered or if the
    hook raises an exception (fail-open).
    """
    if _recall_hook is None:
        return []
    try:
        return await _recall_hook(project, context, tags, limit)
    except Exception:
        logger.warning("Recall hook failed — continuing without learnings", exc_info=True)
        return []


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


def format_recalled_learnings(results: list[dict[str, Any]]) -> str:
    """Format recalled learnings for inclusion in a session start response."""
    if not results:
        return ""
    lines = ["\n\nRelevant learnings from past sessions:"]
    for r in results:
        lrn = r.get("learning", {})
        score = r.get("score", 0)
        cat = lrn.get("category", "learning")
        content = lrn.get("content", "")
        lines.append(f"  - [{cat}] {content} (relevance: {score:.0%})")
    return "\n".join(lines)


def format_decision_warnings(results: list[dict[str, Any]]) -> str:
    """Format recalled learnings as warnings for a decision proposal."""
    if not results:
        return ""
    lines = ["\n\nRelated learnings (review before proceeding):"]
    for r in results:
        lrn = r.get("learning", {})
        cat = lrn.get("category", "learning")
        content = lrn.get("content", "")
        lines.append(f"  - [{cat}] {content}")
    return "\n".join(lines)

"""SCRATCHPAD.md generator — session-end working memory for TRACE.

Generates a human-readable markdown summary of a completed session and
appends it to the project's ``.claude/SCRATCHPAD.md`` file.  This gives
the next session (or human reader) a quick-reference record of what
happened, what was decided, and what's left to do.

Unlike trace-learn (cross-session knowledge), SCRATCHPAD captures
**session-specific state**: what was accomplished, open items, and
immediate next steps.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from trace_mcp.schema import Session

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

# Where to write SCRATCHPAD.md.  Defaults to the current working directory's
# .claude/ folder, but can be overridden via env var.
_SCRATCHPAD_DIR_ENV = "TRACE_SCRATCHPAD_DIR"


def _scratchpad_path() -> Path:
    """Resolve the SCRATCHPAD.md path.

    Priority: $TRACE_SCRATCHPAD_DIR > cwd/.claude/ > ~/.trace/scratchpads/
    """
    env_dir = os.environ.get(_SCRATCHPAD_DIR_ENV)
    if env_dir:
        base = Path(env_dir)
    else:
        cwd_claude = Path.cwd() / ".claude"
        if cwd_claude.is_dir():
            base = cwd_claude
        else:
            base = Path(os.path.expanduser("~/.trace/scratchpads"))

    base.mkdir(parents=True, exist_ok=True)
    return base / "SCRATCHPAD.md"


# ── Session Summary Builder ──────────────────────────────────────────────


def _build_session_section(session: Session) -> str:
    """Build a markdown section summarizing one session."""
    lines: list[str] = []
    sid = session.id
    project = session.metadata.project
    created = session.created.strftime("%Y-%m-%d %H:%M UTC")
    ended = session.ended.strftime("%Y-%m-%d %H:%M UTC") if session.ended else "in progress"

    lines.append(f"## Session: `{sid}`")
    lines.append("")
    lines.append(f"- **Project**: {project}")
    lines.append(f"- **Started**: {created}")
    lines.append(f"- **Ended**: {ended}")

    if session.metadata.description:
        lines.append(f"- **Description**: {session.metadata.description}")

    if session.metadata.tags:
        lines.append(f"- **Tags**: {', '.join(session.metadata.tags)}")

    # ── Summary ──────────────────────────────────────────────────────
    if session.summary:
        lines.append("")
        lines.append("### Summary")
        lines.append("")
        lines.append(session.summary)

    # ── Contributions ────────────────────────────────────────────────
    contributions = [
        e for e in session.events
        if e.type == "contribution" and e.contribution
    ]
    if contributions:
        lines.append("")
        lines.append("### What Was Accomplished")
        lines.append("")
        for e in contributions:
            c = e.contribution
            assert c is not None
            artifact = f" (`{c.artifact}`)" if c.artifact else ""
            lines.append(
                f"- {c.description}{artifact} "
                f"— direction={c.direction}, execution={c.execution}"
            )

    # ── Decisions ────────────────────────────────────────────────────
    decisions = [
        e for e in session.events
        if e.type == "decision" and e.decision
    ]
    if decisions:
        lines.append("")
        lines.append("### Decisions")
        lines.append("")
        for e in decisions:
            d = e.decision
            assert d is not None
            stype = f", suggestion={d.suggestion_type}" if d.suggestion_type else ""
            note = f" — {d.revision_note}" if d.revision_note else ""
            lines.append(
                f"- **[{d.disposition}]** {d.description} "
                f"(proposed_by={d.proposed_by.type}{stype}){note}"
            )

    # ── Open items (unresolved decisions) ────────────────────────────
    unresolved = [
        e for e in decisions
        if e.decision and e.decision.disposition == "proposed"
    ]
    if unresolved:
        lines.append("")
        lines.append("### Open Items")
        lines.append("")
        for e in unresolved:
            d = e.decision
            assert d is not None
            lines.append(f"- [ ] {d.description} (`{e.id}`)")

    # ── Gotchas & Corrections ────────────────────────────────────────
    gotchas = [
        e for e in session.events
        if e.type == "annotation" and e.annotation
        and e.annotation.category in ("gotcha", "correction")
    ]
    if gotchas:
        lines.append("")
        lines.append("### Gotchas & Corrections")
        lines.append("")
        for e in gotchas:
            a = e.annotation
            assert a is not None
            prefix = "CORRECTION" if a.category == "correction" else "GOTCHA"
            linked = f" (corrects: {', '.join(a.corrects_event_ids)})" if a.corrects_event_ids else ""
            lines.append(f"- **{prefix}**: {a.content}{linked}")

    # ── Learnings ────────────────────────────────────────────────────
    learnings = [
        e for e in session.events
        if e.type == "annotation" and e.annotation
        and e.annotation.category == "learning"
    ]
    if learnings:
        lines.append("")
        lines.append("### Learnings")
        lines.append("")
        for e in learnings:
            a = e.annotation
            assert a is not None
            lines.append(f"- {a.content}")

    # ── TODOs ────────────────────────────────────────────────────────
    todos = [
        e for e in session.events
        if e.type == "annotation" and e.annotation
        and e.annotation.category == "todo"
    ]
    if todos:
        lines.append("")
        lines.append("### TODOs")
        lines.append("")
        for e in todos:
            a = e.annotation
            assert a is not None
            lines.append(f"- [ ] {a.content}")

    # ── Event Stats ──────────────────────────────────────────────────
    counts: dict[str, int] = {}
    for evt in session.events:
        counts[evt.type] = counts.get(evt.type, 0) + 1
    total = len(session.events)
    if total:
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        lines.append("")
        lines.append(f"*{total} events: {', '.join(parts)}*")

    lines.append("")
    lines.append(f"*Full session: `~/.trace/sessions/{sid}.json`*")
    lines.append("")

    return "\n".join(lines)


# ── File I/O ─────────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically using temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".scratchpad_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_scratchpad(session: Session) -> Path:
    """Write the most recent session summary to SCRATCHPAD.md.

    Replaces any previous content — the scratchpad is a working-memory
    buffer for context restoration, not an archive.  Past sessions are
    preserved in ``~/.trace/sessions/*.json`` and queryable via
    ``trace_list_sessions`` / ``trace_search``.

    Returns the path to the SCRATCHPAD.md file.
    """
    path = _scratchpad_path()

    section = _build_session_section(session)
    header = (
        f"# SCRATCHPAD — {session.metadata.project}\n\n"
        f"_Auto-generated by TRACE at session end. "
        f"Most recent session only — past sessions are in "
        f"`~/.trace/sessions/`._\n\n"
    )
    content = header + "---\n\n" + section

    _atomic_write(path, content)
    logger.info("Updated SCRATCHPAD: %s", path)
    return path

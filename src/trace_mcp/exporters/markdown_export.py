"""Markdown export for TRACE sessions.

Generates a human-readable Markdown document from a TRACE session.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trace_mcp.schema import Session

_DISPOSITION_EMOJI = {
    "accepted": "\u2705",  # check
    "revised": "\u270f\ufe0f",  # pencil
    "rejected": "\u274c",  # x
    "proposed": "\u23f3",  # hourglass
}

_ANNOTATION_EMOJI = {
    "gotcha": "\U0001f525",  # fire
    "learning": "\U0001f4a1",  # lightbulb
    "observation": "\U0001f441\ufe0f",  # eye
    "correction": "\U0001f6d1",  # stop sign
    "todo": "\U0001f4cb",  # clipboard
    "question": "\u2753",  # question mark
    "other": "\U0001f4ac",  # speech bubble
}


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%H:%M")


def _fmt_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return ""
    delta = end - start
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f" ({hours}h {minutes}m)"
    return f" ({minutes}m)"


def export_markdown(session: Session) -> str:
    """Export a TRACE session as a human-readable Markdown document."""
    lines: list[str] = []
    md = session.metadata

    # Header
    lines.append(f"# TRACE Session: {session.id}")
    lines.append("")
    lines.append(f"**Project**: {md.project}")
    if md.experiment_id:
        lines.append(f"**Experiment**: {md.experiment_id}")

    start_str = session.created.strftime("%Y-%m-%d %H:%M") if session.created else "—"
    end_str = _fmt_time(session.ended) if session.ended else "ongoing"
    duration = _fmt_duration(session.created, session.ended)
    lines.append(f"**Date**: {start_str} — {end_str}{duration}")

    if md.participants:
        parts = []
        for p in md.participants:
            role = f", {p.role}" if p.role else ""
            parts.append(f"{p.id} ({p.type}{role})")
        lines.append(f"**Participants**: {', '.join(parts)}")

    if md.tags:
        lines.append(f"**Tags**: {', '.join(md.tags)}")
    lines.append(f"**Status**: {session.status}")
    lines.append("")

    # Summary
    if session.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(session.summary)
        lines.append("")

    # Decision Log
    decisions = [e for e in session.events if e.type == "decision" and e.decision]
    if decisions:
        lines.append("## Decision Log")
        lines.append("")
        for i, evt in enumerate(decisions, 1):
            d = evt.decision
            assert d is not None
            emoji = _DISPOSITION_EMOJI.get(d.disposition, "")
            lines.append(f"### Decision {i}: {d.description} ({evt.id})")
            lines.append(
                f"- **Proposed by**: {d.proposed_by.id} ({d.proposed_by.type.upper()}) at {_fmt_time(evt.timestamp)}"
            )
            if d.rationale:
                lines.append(f"- **Rationale**: {d.rationale}")
            if d.disposition == "proposed":
                lines.append(f"- **Status**: {emoji} Proposed (unresolved)")
            elif d.resolved_by:
                lines.append(f"- **Status**: {emoji} {d.disposition.capitalize()} by {d.resolved_by.id}")
            if d.revision_note:
                lines.append(f'- **Revision**: "{d.revision_note}"')
            if d.warnings:
                for w in d.warnings:
                    lines.append(f"- **Guard rail**: {w}")
            lines.append("")

    # Tool Calls
    tool_calls = [e for e in session.events if e.type == "tool_call" and e.tool_call]
    if tool_calls:
        lines.append("## Tool Calls")
        lines.append("")
        lines.append("| # | Time | Server | Tool | Status | Duration | Retries |")
        lines.append("|---|------|--------|------|--------|----------|---------|")
        for i, evt in enumerate(tool_calls, 1):
            tc = evt.tool_call
            assert tc is not None
            time_str = _fmt_time(evt.timestamp)
            status = "\u2705" if tc.status == "success" else "\u274c"
            dur = f"{tc.duration_ms / 1000:.1f}s" if tc.duration_ms else "—"
            retries = tc.retries_event_id or "—"
            lines.append(f"| {i} | {time_str} | {tc.server} | {tc.name} | {status} | {dur} | {retries} |")
        lines.append("")

    # Contributions
    contributions = [e for e in session.events if e.type == "contribution" and e.contribution]
    if contributions:
        lines.append("## Contributions")
        lines.append("")
        lines.append("| # | Direction | Execution | Description | Artifact |")
        lines.append("|---|-----------|-----------|-------------|----------|")
        for i, evt in enumerate(contributions, 1):
            c = evt.contribution
            assert c is not None
            artifact = c.artifact or "—"
            lines.append(f"| {i} | {c.direction} | {c.execution} | {c.description} | {artifact} |")
        lines.append("")

    # Annotations
    annotations = [e for e in session.events if e.type == "annotation" and e.annotation]
    if annotations:
        lines.append("## Annotations")
        lines.append("")
        for evt in annotations:
            a = evt.annotation
            assert a is not None
            emoji = _ANNOTATION_EMOJI.get(a.category, "")
            lines.append(f"### {emoji} {a.category.capitalize()}: ({evt.id})")
            lines.append(a.content)
            if a.corrects_event_ids:
                lines.append(f"*Corrects*: {', '.join(a.corrects_event_ids)}")
            if a.related_event_ids:
                lines.append(f"*Related to*: {', '.join(a.related_event_ids)}")
            lines.append("")

    # Statistics
    type_counts: dict[str, int] = {}
    for evt in session.events:
        type_counts[evt.type] = type_counts.get(evt.type, 0) + 1
    total = len(session.events)

    tc_success = sum(1 for e in tool_calls if e.tool_call and e.tool_call.status == "success")
    tc_error = sum(1 for e in tool_calls if e.tool_call and e.tool_call.status != "success")

    dec_by_disp: dict[str, int] = {}
    for e in decisions:
        if e.decision:
            dec_by_disp[e.decision.disposition] = dec_by_disp.get(e.decision.disposition, 0) + 1

    ann_by_cat: dict[str, int] = {}
    for e in annotations:
        if e.annotation:
            ann_by_cat[e.annotation.category] = ann_by_cat.get(e.annotation.category, 0) + 1

    lines.append("## Statistics")
    lines.append(f"- **Total events**: {total}")
    if tool_calls:
        lines.append(f"- **Tool calls**: {len(tool_calls)} ({tc_success} success, {tc_error} error)")
    if decisions:
        dec_parts = [f"{v} {k}" for k, v in sorted(dec_by_disp.items())]
        lines.append(f"- **Decisions**: {len(decisions)} ({', '.join(dec_parts)})")
    if annotations:
        ann_parts = [f"{v} {k}s" for k, v in sorted(ann_by_cat.items())]
        lines.append(f"- **Annotations**: {len(annotations)} ({', '.join(ann_parts)})")
    if contributions:
        contrib_by_dir: dict[str, int] = {}
        for e in contributions:
            if e.contribution:
                key = f"{e.contribution.direction}→{e.contribution.execution}"
                contrib_by_dir[key] = contrib_by_dir.get(key, 0) + 1
        contrib_parts = [f"{v} {k}" for k, v in sorted(contrib_by_dir.items())]
        lines.append(f"- **Contributions**: {len(contributions)} ({', '.join(contrib_parts)})")
    sc_count = type_counts.get("state_change", 0)
    if sc_count:
        lines.append(f"- **State changes**: {sc_count}")
    lines.append("")

    return "\n".join(lines)

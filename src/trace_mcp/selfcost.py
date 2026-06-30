"""Offline self-cost report for TRACE — estimate TRACE's own token footprint.

This is a STANDALONE, read-only diagnostic. It adds ZERO MCP tools (so running
it does not enlarge the very tool-schema surface it measures) and never mutates
any session record. It answers "how much context does TRACE itself cost me?"
with deliberately-labeled ESTIMATES, at the only granularity an offline analyzer
can honestly speak to.

What it can and cannot see (verified against the MCP/FastMCP runtime):

  * Schema-surface — the JSON tool definitions TRACE injects into context. These
    sit at prompt prefix position 0 and, under Anthropic prompt caching, are
    written once (~1.25x input price) on the first turn of a session and then
    served from cache (~0.1x) thereafter — they are NOT re-billed in full every
    turn. So the steady-state cost is far below a naive ``chars/4 * turns``
    figure. We report a cold-write and a warm-read regime instead of one number.

  * Authored event content — for every TRACE event in a session, the model spent
    OUTPUT tokens authoring that call's arguments. We estimate that from the
    stored event payload. We do NOT see the result text TRACE returned (it is
    not stored in the session) nor the host's true tokenizer, so this is a
    labeled UNDERCOUNT of interaction cost, not a bill.

  * Reuse signals (trace-learn) — recall counts, as a WEAK benefit proxy.
    ``recall_count`` tracks SURFACING, not use, and has known tracking gaps, so
    it ships with a data-quality flag and is NEVER converted to "tokens saved".

For the authoritative, MEASURED per-tool / per-session number, ingest Claude
Code's OpenTelemetry ``claude_code.token.usage`` metric filtered to
``mcp_tool.name`` (see ``docs/adr/004-telemetry-sidecar.md``, the SOON "OTEL
importer" tier). This offline report is the no-setup approximation of that.

All token figures use a transparent ``ceil(chars / 4)`` proxy — NOT the host's
real tokenizer. Treat every number as an order-of-magnitude estimate.

Public functions: ``chars_to_tokens``, ``estimate_schema_surface``,
``estimate_session_cost``, ``estimate_reuse_signal``, ``build_report``,
``format_report``, ``main``.

Side effects: reads the local filesystem (session JSON, knowledge store) and
imports ``trace_mcp.server`` to introspect registered tool schemas (no server is
started). Nothing is written.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pydantic import BaseModel, Field

from trace_mcp.schema import Session

# Prompt-caching regime multipliers applied to the raw schema-surface token
# estimate. Anthropic caches the tools+system prefix: the first turn pays a
# cache-write premium, later turns pay a cache-read fraction. These are
# documented public multipliers, recorded so the report's assumptions are
# inspectable rather than hidden.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

CHARS_PER_TOKEN = 4


def chars_to_tokens(chars: int) -> int:
    """Estimate tokens from character count via a transparent ceil(chars/4) proxy.

    This is NOT the host's tokenizer; it is a deterministic, auditable
    approximation. Pure function.
    """
    if chars <= 0:
        return 0
    return (chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


class SchemaSurface(BaseModel):
    """Estimated cost of the TRACE tool-schema surface injected into context."""

    tool_count: int = Field(..., description="Number of registered MCP tools introspected.")
    schema_chars: int = Field(..., description="Total characters of name+description+inputSchema JSON across tools.")
    tokens_est: int = Field(..., description="Raw chars/4 token estimate of the schema surface (uncached).")
    cold_write_tokens_est: int = Field(
        ..., description="First-turn cache-write estimate (tokens_est * write multiplier)."
    )
    warm_read_tokens_est: int = Field(
        ..., description="Per-later-turn cache-read estimate (tokens_est * read multiplier)."
    )
    included_extensions: bool = Field(..., description="Whether extension tools were included in the surface.")
    note: str = Field(..., description="Honesty caveat for this measurement.")


class SessionCost(BaseModel):
    """Estimated tokens the model spent AUTHORING TRACE calls in one session."""

    session_id: str
    project: str | None = None
    event_count: int
    authored_tokens_est: int = Field(
        ..., description="chars/4 estimate of authored event payloads (model output side)."
    )
    by_event_type: dict[str, int] = Field(
        default_factory=dict, description="authored_tokens_est broken down by event type."
    )
    tool_call_host_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of logged tool_call events by host (mcp/internal/external). internal/external are logged "
        "by reference — TRACE never marshalled their payload, so they are not sizeable from this side.",
    )
    note: str = Field(..., description="Honesty caveat: excludes TRACE's returned-result tokens; chars/4 proxy.")


class ReuseSignal(BaseModel):
    """Weak benefit proxy from trace-learn. NOT a savings figure."""

    project: str
    total_learnings: int
    total_recall_count: int = Field(
        ..., description="Sum of recall_count across learnings (SURFACING, not proven use)."
    )
    never_surfaced: int
    avg_recall_count: float
    data_quality_flag: str = Field(..., description="Why these counts are a weak, possibly-undercounted signal.")


class SelfCostReport(BaseModel):
    """Top-level offline self-cost report."""

    schema_surface: SchemaSurface
    session: SessionCost | None = None
    reuse: ReuseSignal | None = None
    measured_path_pointer: str = Field(
        default=(
            "For a MEASURED per-tool/per-session number, ingest Claude Code's OpenTelemetry "
            "'claude_code.token.usage' metric filtered to mcp_tool.name (see docs/adr/004-telemetry-sidecar.md). "
            "This offline report is the no-setup approximation."
        ),
        description="Where to get the authoritative measured number.",
    )


def _list_registered_tools(include_extensions: bool) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (name, description, input_schema) for every registered MCP tool.

    Side effect: imports trace_mcp.server (which constructs the FastMCP instance
    and registers core tools at import time) and, when include_extensions is
    True, calls its extension loader. No server is started. Returns [] if the
    runtime introspection API is unavailable.
    """
    import trace_mcp.server as srv

    if include_extensions:
        try:
            srv._load_extensions()
        except Exception:  # noqa: BLE001 - extension loading is best-effort here
            pass

    tool_manager = getattr(srv.mcp, "_tool_manager", None)
    if tool_manager is not None and hasattr(tool_manager, "list_tools"):
        try:
            tools = list(tool_manager.list_tools())
            return [
                (
                    getattr(t, "name", "") or "",
                    getattr(t, "description", "") or "",
                    getattr(t, "parameters", {}) or {},
                )
                for t in tools
            ]
        except Exception:  # noqa: BLE001 - fall through to async fallback
            pass

    # Async fallback via the public list_tools() coroutine (MCPTool objects).
    try:
        import asyncio

        mcp_tools = asyncio.run(srv.mcp.list_tools())
        return [
            (
                getattr(t, "name", "") or "",
                getattr(t, "description", "") or "",
                getattr(t, "inputSchema", {}) or {},
            )
            for t in mcp_tools
        ]
    except Exception:  # noqa: BLE001 - introspection genuinely unavailable
        return []


def estimate_schema_surface(include_extensions: bool = True) -> SchemaSurface:
    """Estimate the token cost of the TRACE tool-schema surface (see module docstring)."""
    tools = _list_registered_tools(include_extensions)
    total_chars = 0
    for name, description, schema in tools:
        payload = {"name": name, "description": description, "parameters": schema}
        total_chars += len(json.dumps(payload, default=str))
    tokens_est = chars_to_tokens(total_chars)
    return SchemaSurface(
        tool_count=len(tools),
        schema_chars=total_chars,
        tokens_est=tokens_est,
        cold_write_tokens_est=round(tokens_est * CACHE_WRITE_MULTIPLIER),
        warm_read_tokens_est=round(tokens_est * CACHE_READ_MULTIPLIER),
        included_extensions=include_extensions,
        note=(
            "chars/4 proxy, not the host tokenizer. Under Anthropic prompt caching the schema surface is "
            "written once per session (~cold) and read from cache thereafter (~warm) — it is NOT re-billed in "
            "full every turn. Do not multiply tokens_est by turn count."
        ),
    )


def estimate_session_cost(session: Session) -> SessionCost:
    """Estimate the tokens the model spent authoring TRACE calls in this session.

    Pure with respect to the filesystem (operates on an in-memory Session).
    Sums a chars/4 estimate of each event's authored data payload. Excludes the
    result text TRACE returned (not stored), so it is a labeled undercount.
    """
    by_type: dict[str, int] = {}
    host_breakdown: dict[str, int] = {}
    total = 0
    for event in session.events:
        data = getattr(event, event.type, None)
        if data is not None and hasattr(data, "model_dump"):
            payload = data.model_dump(mode="json", exclude_none=True)
            chars = len(json.dumps(payload, default=str))
            tokens = chars_to_tokens(chars)
            total += tokens
            by_type[event.type] = by_type.get(event.type, 0) + tokens
        if event.type == "tool_call" and event.tool_call is not None:
            host = event.tool_call.host or "mcp"
            host_breakdown[host] = host_breakdown.get(host, 0) + 1
    return SessionCost(
        session_id=session.id,
        project=session.metadata.project,
        event_count=len(session.events),
        authored_tokens_est=total,
        by_event_type=by_type,
        tool_call_host_breakdown=host_breakdown,
        note=(
            "Authored (model-output) side only, chars/4 proxy. Excludes TRACE's returned-result tokens "
            "(not stored in the session) and the host's true tokenization — treat as a lower bound."
        ),
    )


def estimate_reuse_signal(project: str) -> ReuseSignal | None:
    """Summarize trace-learn reuse as a WEAK benefit proxy, or None if unavailable.

    Side effect: reads the project's knowledge store from disk. Returns None when
    the trace-learn extension or the store is absent (honest absence, not a zero
    masquerading as a measurement).
    """
    try:
        from trace_mcp.extensions.learn.store import load_store
    except ImportError:
        return None
    try:
        store = load_store(project)
    except Exception:  # noqa: BLE001 - no store / unreadable store is honest absence
        return None
    learnings = list(getattr(store, "learnings", []))
    if not learnings:
        return None
    recall_counts = [int(getattr(lrn, "recall_count", 0) or 0) for lrn in learnings]
    total_recall = sum(recall_counts)
    never = sum(1 for c in recall_counts if c == 0)
    return ReuseSignal(
        project=project,
        total_learnings=len(learnings),
        total_recall_count=total_recall,
        never_surfaced=never,
        avg_recall_count=round(total_recall / len(learnings), 2),
        data_quality_flag=(
            "recall_count counts SURFACING, not proven use, and trace-learn recall tracking has known gaps. "
            "This is a weak, possibly-undercounted signal — never convert it to 'tokens saved'."
        ),
    )


def build_report(session_path: str | None = None, include_extensions: bool = True) -> SelfCostReport:
    """Assemble the offline self-cost report.

    Side effect: reads the session JSON at session_path (if given) and the
    matching project's knowledge store; imports the server for schema surface.
    """
    surface = estimate_schema_surface(include_extensions=include_extensions)
    session_cost: SessionCost | None = None
    reuse: ReuseSignal | None = None
    if session_path:
        with open(session_path, encoding="utf-8") as fh:
            session = Session.model_validate(json.load(fh))
        session_cost = estimate_session_cost(session)
        if session.metadata.project:
            reuse = estimate_reuse_signal(session.metadata.project)
    return SelfCostReport(schema_surface=surface, session=session_cost, reuse=reuse)


def format_report(report: SelfCostReport) -> str:
    """Render a human-readable text report. Pure function."""
    s = report.schema_surface
    lines: list[str] = []
    lines.append("TRACE self-cost report (offline estimate — NOT a bill)")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Schema surface ({s.tool_count} tools, extensions={s.included_extensions}):")
    lines.append(f"  raw estimate      ~{s.tokens_est:,} tokens  ({s.schema_chars:,} chars / 4)")
    lines.append(f"  cold (turn 1)     ~{s.cold_write_tokens_est:,} tokens  (cache write)")
    lines.append(f"  warm (later turns)~{s.warm_read_tokens_est:,} tokens  (cache read)")
    lines.append(f"  note: {s.note}")
    lines.append("")
    if report.session is not None:
        c = report.session
        lines.append(f"Session {c.session_id} (project={c.project}, {c.event_count} events):")
        lines.append(f"  authored TRACE-call tokens ~{c.authored_tokens_est:,} (estimate)")
        if c.by_event_type:
            parts = ", ".join(f"{k}={v:,}" for k, v in sorted(c.by_event_type.items()))
            lines.append(f"  by event type: {parts}")
        if c.tool_call_host_breakdown:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(c.tool_call_host_breakdown.items()))
            lines.append(f"  logged tool_call events by host: {parts}")
        lines.append(f"  note: {c.note}")
        lines.append("")
    if report.reuse is not None:
        r = report.reuse
        lines.append(f"Reuse signal (trace-learn, project={r.project}) — WEAK benefit proxy:")
        lines.append(
            f"  {r.total_learnings} learnings, total recall_count={r.total_recall_count}, "
            f"never_surfaced={r.never_surfaced}, avg_recall={r.avg_recall_count}"
        )
        lines.append(f"  flag: {r.data_quality_flag}")
        lines.append("")
    lines.append(report.measured_path_pointer)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Usage: python -m trace_mcp.selfcost [SESSION_JSON] [--no-extensions] [--json]."""
    parser = argparse.ArgumentParser(
        prog="trace-selfcost",
        description="Estimate TRACE's own token footprint (offline, read-only). Not a bill — labeled estimates only.",
    )
    parser.add_argument("session", nargs="?", default=None, help="Path to a session JSON file (optional).")
    parser.add_argument(
        "--no-extensions",
        action="store_true",
        help="Measure only the core tool-schema surface (exclude extension tools).",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of text.")
    args = parser.parse_args(argv)

    report = build_report(session_path=args.session, include_extensions=not args.no_extensions)
    if args.json:
        print(json.dumps(report.model_dump(), indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

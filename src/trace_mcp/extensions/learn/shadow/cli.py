"""``trace-mcp shadow``: the provenance-marks capture period, its report, and the offline replay (ADR 008).

Commands: ``start``, ``stop``, ``status``, ``exclude KEY``, ``include KEY``,
``report [--from D] [--to D] [--project KEY] [--json] [--csv PATH]``,
``replay --project KEY [--limit N] [--from D] [--to D] [--session ID ...]``.

Exports: main

Side effects: ``start``, ``stop``, ``exclude`` and ``include`` rewrite the shadow
state file; ``report --csv`` writes the review sheet; ``replay`` makes two
attested model calls per replayed session and appends its records under the
shadow root; everything else only reads.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from trace_mcp.extensions.learn.shadow import replay as shadow_replay
from trace_mcp.extensions.learn.shadow import report as shadow_report
from trace_mcp.extensions.learn.shadow.state import (
    ShadowState,
    ShadowStateError,
    get_project_dir,
    get_shadow_root,
    load_state,
    set_excluded,
    start_trial,
    stop_trial,
)
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.project_identity import ProjectKeyError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-mcp shadow", description="The provenance-marks capture period, its report, and the replay."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="start the period for every project")
    sub.add_parser("stop", help="end the period")
    sub.add_parser("status", help="show the period's state and per-project counts")
    for name in ("exclude", "include"):
        cmd = sub.add_parser(name, help=f"{name} one project")
        cmd.add_argument("key")
    rep = sub.add_parser("report", help="print the comparison report")
    rep.add_argument("--from", dest="date_from", type=date.fromisoformat)
    rep.add_argument("--to", dest="date_to", type=date.fromisoformat)
    rep.add_argument("--project")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--csv", type=Path)
    replay = sub.add_parser("replay", help="replay past sessions: whole-store against nearest-50 extraction")
    replay.add_argument("--project", required=True)
    replay.add_argument("--limit", type=int, default=10, help="sessions to replay this run (default 10)")
    replay.add_argument("--from", dest="date_from", type=date.fromisoformat)
    replay.add_argument("--to", dest="date_to", type=date.fromisoformat)
    replay.add_argument("--session", dest="sessions", action="append", help="replay only this session id")
    return parser


def _excluded_line(state: ShadowState) -> str:
    return "Excluded: " + (", ".join(state.excluded) if state.excluded else "(none)")


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _status() -> int:
    try:
        state = load_state()
    except ShadowStateError as exc:
        print(f"shadow state is unreadable: {exc}", file=sys.stderr)
        return 1
    if state.started_at is None:
        print("Capture period: not started.")
    else:
        end = state.stopped_at or datetime.now(UTC)
        days = (end - state.started_at).days
        label = "stopped " + state.stopped_at.date().isoformat() if state.stopped_at else "running"
        print(f"Capture period: {label}; started {state.started_at.date().isoformat()}, day {days}.")
    print(_excluded_line(state))
    root = get_shadow_root()
    projects = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for key in projects:
        directory = get_project_dir(key)
        print(
            f"  {key}: words entries {len(load_words(key))}, replayed sessions "
            f"{_count_lines(directory / 'replay.jsonl')}, recalls {_count_lines(directory / 'recall.jsonl')}, "
            f"errors {_count_lines(directory / 'errors.jsonl')}"
        )
    return 0


def _replay(args: argparse.Namespace) -> int:
    try:
        summary = asyncio.run(
            shadow_replay.run_replay(
                project=args.project,
                limit=args.limit,
                date_from=args.date_from,
                date_to=args.date_to,
                session_ids=args.sessions,
            )
        )
    except shadow_replay.ReplayUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for rec in summary.records:
        print(
            f"  {rec.session_id}: {rec.store_size} stored, nearest arm sent {rec.selection}; "
            f"whole {rec.whole.prompt_tokens} prompt tokens, {len(rec.whole.learnings)} new; "
            f"nearest {rec.nearest.prompt_tokens} prompt tokens, {len(rec.nearest.learnings)} new"
        )
    print(
        f"Replayed {summary.replayed} of {summary.candidates} ended session(s) of {summary.project_key}: "
        f"{summary.already_done} already replayed, {summary.skipped} with nothing to extract, {summary.failed} failed."
    )
    return 1 if summary.failed else 0


def main(argv: list[str] | None = None) -> int:
    """Run one ``trace-mcp shadow`` command and return its exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "start":
            state = start_trial()
            assert state.started_at is not None
            print(
                f'Capture period started {state.started_at.isoformat()}. It runs until you run "trace-mcp shadow stop".'
            )
            return 0
        if args.command == "stop":
            state = stop_trial()
            assert state.started_at is not None and state.stopped_at is not None
            days = (state.stopped_at - state.started_at).days
            print(f"Capture period stopped {state.stopped_at.isoformat()} after {days} day(s).")
            return 0
        if args.command == "status":
            return _status()
        if args.command in ("exclude", "include"):
            state = set_excluded(args.key, args.command == "exclude")
            print(_excluded_line(state))
            return 0
        if args.command == "replay":
            return _replay(args)
        report = shadow_report.build_report(date_from=args.date_from, date_to=args.date_to, project_key=args.project)
        print(report.model_dump_json(indent=2) if args.json else shadow_report.format_report(report))
        if args.csv is not None:
            rows = shadow_report.get_review_rows(
                date_from=args.date_from, date_to=args.date_to, project_key=args.project
            )
            written = shadow_report.write_review_csv(args.csv, rows)
            print(f"Wrote {written} review row(s) to {args.csv}", file=sys.stderr if args.json else sys.stdout)
        return 0
    except ShadowStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1 if args.command in ("exclude", "include") else 2
    except ProjectKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

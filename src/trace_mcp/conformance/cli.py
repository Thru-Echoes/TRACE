"""``trace-mcp doctor`` and ``trace-mcp fleet-check`` — deployed-state CLIs.

    trace-mcp doctor [DIR] [--live] [--json]

Exit codes: 0 clean, 1 findings, 2 usage error (a bad path is not an unhealthy
project, and a caller must be able to tell them apart). Diagnostics go to
stderr so ``--json`` stdout is always a report or empty. ``--json`` prints the
``DoctorReport`` for machine consumption (stable check ids; ``ok`` is a
serialized field).

``--live`` additionally **runs the command the project's own `.mcp.json`
declares** and handshakes with it — the only way to catch a correct-looking
project whose running server is a stale build. It is opt-in for that reason:
executing a command out of a config file is an action, not an inspection.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TextIO

from trace_mcp.conformance import (
    FLEET_ROOTS_ENV,
    MAX_DEPTH,
    DoctorReport,
    discover_projects,
    planned_live_commands,
    roots_from_env,
    run_doctor,
    run_fleet_check,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-mcp doctor",
        description="Check a project directory's deployed TRACE state (config, hooks, pins, served build).",
    )
    parser.add_argument("directory", nargs="?", default=None, help="project directory (default: cwd)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="also spawn the project's configured server command and verify the build it serves",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    return parser


EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
"""Exit codes. A usage error is distinct from an unhealthy project: a sweep
shelling out to this command must be able to tell "you gave me a bad path" from
"this project is broken", and must not have to parse a diagnostic that is not
the report it asked for."""


def main(argv: list[str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Entry point for ``trace-mcp doctor``. Returns a process exit code.

    The report goes to *out* (stdout); usage diagnostics go to *err* (stderr),
    so ``--json`` stdout is always either a report or empty — never a
    human-readable error a caller would try to parse as JSON.
    """
    stream = out if out is not None else sys.stdout
    errors = err if err is not None else sys.stderr
    ns = build_parser().parse_args(argv)
    project_dir = Path(ns.directory).expanduser() if ns.directory else Path.cwd()
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=errors)
        return EXIT_USAGE

    report = run_doctor(project_dir, live=ns.live)
    print(report.model_dump_json(indent=2) if ns.json else report.render(), file=stream)
    return EXIT_OK if report.ok else EXIT_FINDINGS


def build_fleet_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-mcp fleet-check",
        description="Check every project declaring a TRACE MCP server under the given roots.",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        default=None,
        help=f"directories to sweep (default: ${FLEET_ROOTS_ENV}, {os.pathsep}-separated)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "also start each project's configured server command and verify the build it serves. "
            "This RUNS a command declared by every project found; without --yes it only lists them."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required with --live: confirm that the listed commands may be executed",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=MAX_DEPTH,
        metavar="N",
        help=f"how far below each root to look (default: {MAX_DEPTH}); un-descended branches are reported",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    return parser


def main_fleet_check(argv: list[str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Entry point for ``trace-mcp fleet-check``. Returns a process exit code.

    Roots come from the arguments, else ``TRACE_FLEET_ROOTS``. With neither, the
    command fails as a usage error rather than guessing a directory to sweep: a
    checker that assumes one machine's layout silently surveys nothing on any
    other, and reports that as a healthy fleet.
    """
    stream = out if out is not None else sys.stdout
    errors = err if err is not None else sys.stderr
    ns = build_fleet_parser().parse_args(argv)

    roots = [Path(r).expanduser() for r in (ns.roots or [])] or roots_from_env()
    if not roots:
        print(
            f"Error: no roots to sweep. Pass one or more directories, or set {FLEET_ROOTS_ENV} "
            f"to a {os.pathsep}-separated list.",
            file=errors,
        )
        return EXIT_USAGE

    if ns.live and not ns.yes:
        # Reading a config and executing what it declares are different acts, and
        # a sweep executes commands from directories the operator never named
        # one by one. List them and stop; --yes is the informed second step.
        found = discover_projects(roots, max_depth=ns.max_depth)
        print(
            f"--live would execute the server command declared by {len(found.projects)} project(s):",
            file=errors,
        )
        for project_dir, command in planned_live_commands(found.projects):
            print(f"  {project_dir}\n      {command}", file=errors)
        print("Re-run with --yes to execute them.", file=errors)
        return EXIT_USAGE

    def show_progress(report: DoctorReport, index: int, total: int) -> None:
        """Per-project line on stderr: a live sweep budgets minutes per project,
        and a silent terminal is indistinguishable from a hang."""
        status = "ok  " if report.ok else "FAIL"
        print(f"  [{index}/{total}] {status} {report.project_dir}", file=errors)

    # JSON consumers get a pure stdout; progress would still go to stderr, but a
    # machine-read sweep has no one watching it.
    progress = None if ns.json else show_progress

    report = run_fleet_check(roots, live=ns.live, max_depth=ns.max_depth, on_project=progress)
    print(report.model_dump_json(indent=2) if ns.json else report.render(), file=stream)
    return EXIT_OK if report.ok else EXIT_FINDINGS


__all__ = [
    "EXIT_FINDINGS",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_fleet_parser",
    "build_parser",
    "main",
    "main_fleet_check",
]

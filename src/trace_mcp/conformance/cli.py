"""``trace-mcp doctor`` — check one project's deployed TRACE state.

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
import sys
from pathlib import Path
from typing import TextIO

from trace_mcp.conformance import run_doctor


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


__all__ = ["EXIT_FINDINGS", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]

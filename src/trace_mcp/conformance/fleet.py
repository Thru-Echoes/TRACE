"""Fleet-wide deployed-state sweep: run the doctor over every TRACE project.

``run_doctor`` answers "is this project's deployment sound?". The failures this
codebase actually accumulates are fleet-shaped — one defect replicated across
every consumer and found months later by hand: a hook matcher that never fired
in most projects, hook copies frozen at an old release, identity pins that were
never minted. Answering that question previously meant a bespoke shell loop,
which is why it got answered about once.

Discovery walks the roots the caller supplies — command-line arguments, or
``TRACE_FLEET_ROOTS`` (``os.pathsep``-separated). **No path is ever compiled
in**: a checker that assumes one machine's layout is a checker that silently
surveys nothing on any other. The walk is depth-capped, skips dependency and
VCS directories, does not follow symlinks (a loop would otherwise never
terminate), and reports each project once however many roots reach it.

Output shape is API. ``trace-mcp fleet-check --json`` is consumed by tooling as
well as read by a person, so field names, the check ids inside each project's
report, and the path ordering are stable; rename them with the care given a
wire field.

Nothing here writes. The one action available — ``--live``, which starts each
project's configured server to see what it actually serves — is off by default
and carries a warning of its own, because running the commands declared by every
directory found in a sweep is a materially bigger act than reading their files.

Exports: ``FleetReport``, ``discover_projects``, ``run_fleet_check``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, computed_field

from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.conformance.expectations import DoctorReport, Finding

FLEET_ROOTS_ENV = "TRACE_FLEET_ROOTS"

MAX_DEPTH = 5
"""How far below a root to look. Deep enough for the nested layouts consumers
actually use (``<area>/<client>/<project>``), shallow enough that pointing this
at a home directory finishes."""

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
        "Library",
        ".Trash",
    }
)
"""Directories a sweep never needs to enter. Dependency trees are the expensive
ones: a vendored copy of some other project's config is not a deployment."""


class FleetReport(BaseModel):
    """Result of checking every discovered project under a set of roots."""

    roots: list[Path] = Field(default_factory=list, description="Roots that were swept.")
    projects: list[DoctorReport] = Field(
        default_factory=list, description="Per-project reports, ordered by project path."
    )
    unreadable_roots: list[Path] = Field(
        default_factory=list, description="Roots that do not exist or could not be read."
    )
    unreadable_configs: list[Path] = Field(
        default_factory=list,
        description="`.mcp.json` files that could not be parsed, so could not be classified as TRACE projects.",
    )
    unreadable_dirs: list[Path] = Field(
        default_factory=list,
        description="Directories that could not be read (permissions, dead mounts); anything below them is unsurveyed.",
    )
    truncated_dirs: list[Path] = Field(
        default_factory=list,
        description="Directories not descended into because the depth cap was reached; projects below are unsurveyed.",
    )

    @computed_field(description="Projects discovered and checked.")
    @property
    def total(self) -> int:
        return len(self.projects)

    @computed_field(description="Projects with no failing check.")
    @property
    def clean(self) -> int:
        return sum(1 for p in self.projects if p.ok)

    @computed_field(description="Projects with at least one failing check.")
    @property
    def with_findings(self) -> int:
        return self.total - self.clean

    @computed_field(
        description="Failing check id -> number of projects failing it. The actionable fleet view: "
        "which defect, how many consumers."
    )
    @property
    def findings_by_check(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for project in self.projects:
            for finding in project.failures():
                counts[finding.check] = counts.get(finding.check, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    @computed_field(
        description="True when every discovered project is clean AND nothing was unreadable. "
        "Depth truncation deliberately does NOT count: on a real tree it happens thousands of times, "
        "and a flag that is always false is a flag nobody reads. It is reported as a count instead."
    )
    @property
    def ok(self) -> bool:
        return (
            self.with_findings == 0
            and not self.unreadable_roots
            and not self.unreadable_configs
            and not self.unreadable_dirs
        )

    def render(self) -> str:
        """Human-readable summary: the counts, the failing projects, the rollup."""
        lines = [f"fleet-check: {self.clean}/{self.total} project(s) clean"]
        if self.total == 0:
            lines.append("  0 projects discovered — check the roots, the depth cap, and the skip list.")
        for path in self.unreadable_roots:
            lines.append(f"  UNREADABLE ROOT   {path}")
        for path in self.unreadable_configs:
            lines.append(f"  UNREADABLE CONFIG {path}")
        for path in self.unreadable_dirs:
            lines.append(f"  UNREADABLE DIR    {path} (anything below it was not surveyed)")
        if self.truncated_dirs:
            shown = ", ".join(str(p) for p in self.truncated_dirs[:3])
            more = f" (+{len(self.truncated_dirs) - 3} more)" if len(self.truncated_dirs) > 3 else ""
            lines.append(
                f"  note: {len(self.truncated_dirs)} director(ies) not descended at the depth cap — "
                f"raise --max-depth to include them. First: {shown}{more}"
            )
        for project in self.projects:
            if project.ok:
                lines.append(f"  ok   {project.project_dir}")
                continue
            failed = project.failures()
            lines.append(f"  FAIL {project.project_dir}  ({len(failed)} finding(s))")
            lines += [f"         {f.check}: {f.detail.splitlines()[0]}" for f in failed]
        if self.total == 0:
            pass
        elif self.findings_by_check:
            lines.append("fleet-check: failing checks, most widespread first")
            lines += [f"  {count:>4} project(s)  {check}" for check, count in self.findings_by_check.items()]
        else:
            lines.append("fleet-check: every discovered project matches this build's expectations.")
        return "\n".join(lines)


def roots_from_env() -> list[Path]:
    """Roots declared in ``TRACE_FLEET_ROOTS``, or [] when unset.

    Side effects: reads the environment.
    """
    raw = os.environ.get(FLEET_ROOTS_ENV, "")
    return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]


def _declares_trace_server(config_path: Path) -> bool | None:
    """True/False for a parseable config, None when it could not be read.

    None is distinct on purpose: an unparseable `.mcp.json` might be a TRACE
    project, so it is reported rather than quietly treated as somebody else's.
    """
    try:
        data = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and MCP_SERVER_KEY in servers


class Discovery(BaseModel):
    """What a walk found, and what it could not see."""

    projects: list[Path] = Field(default_factory=list)
    unreadable_roots: list[Path] = Field(default_factory=list)
    unreadable_configs: list[Path] = Field(default_factory=list)
    unreadable_dirs: list[Path] = Field(default_factory=list)
    truncated_dirs: list[Path] = Field(default_factory=list)


def discover_projects(roots: list[Path], *, max_depth: int = MAX_DEPTH) -> Discovery:
    """Find every project under *roots* declaring a TRACE MCP server.

    Every list is deterministically ordered, and a project reached through more
    than one root appears once, resolved, so overlapping arguments do not
    double-report it.

    Nothing here raises for a hostile tree. A directory the process cannot stat
    or list is recorded rather than propagated: on macOS a terminal without Full
    Disk Access gets EPERM on TCC-gated directories, and a sweep that dies on the
    first of them surveys nothing. What it could NOT see is reported, because a
    partial survey presented as a complete one is the failure this tool exists to
    prevent.

    Side effects: reads directories and `.mcp.json` files under *roots*.
    """
    found = Discovery()
    projects: set[Path] = set()
    unreadable_configs: set[Path] = set()
    unreadable_dirs: set[Path] = set()
    truncated: set[Path] = set()

    for root in roots:
        expanded = root.expanduser()
        try:
            if not expanded.is_dir():
                found.unreadable_roots.append(root)
                continue
            base = expanded.resolve(strict=True)
        except OSError:
            found.unreadable_roots.append(root)
            continue
        _walk(base, 0, max_depth, projects, unreadable_configs, unreadable_dirs, truncated)

    found.projects = sorted(projects)
    found.unreadable_configs = sorted(unreadable_configs)
    found.unreadable_dirs = sorted(unreadable_dirs)
    found.truncated_dirs = sorted(truncated)
    return found


def _walk(
    directory: Path,
    depth: int,
    max_depth: int,
    projects: set[Path],
    unreadable_configs: set[Path],
    unreadable_dirs: set[Path],
    truncated: set[Path],
) -> None:
    """Depth-first scan for `.mcp.json`, bounded, symlink-safe, and total.

    Symlinked directories are not followed: a link pointing at an ancestor
    would otherwise walk forever, and the target is either inside a root
    already or deliberately outside the sweep.
    """
    config = directory / ".mcp.json"
    try:
        is_config = config.is_file()
    except OSError:
        unreadable_dirs.add(directory)
        return
    if is_config:
        declares = _declares_trace_server(config)
        if declares is None:
            unreadable_configs.add(config)
        elif declares:
            projects.add(directory)

    try:
        entries = sorted(directory.iterdir())
    except OSError:
        unreadable_dirs.add(directory)
        return

    candidates: list[Path] = []
    for entry in entries:
        if entry.name in SKIP_DIRS or entry.name.startswith("."):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            unreadable_dirs.add(entry)
            continue
        candidates.append(entry)

    if depth >= max_depth:
        # Record the branch instead of dropping it: a project below the cap is
        # simply absent from the report, which reads as "not there".
        truncated.update(candidates)
        return
    for entry in candidates:
        _walk(entry, depth + 1, max_depth, projects, unreadable_configs, unreadable_dirs, truncated)


def planned_live_commands(project_dirs: list[Path]) -> list[tuple[Path, str]]:
    """The command each project would run under ``--live``, for review first.

    Reading a config is not the same act as executing what it declares, and a
    sweep executes commands from directories the operator never named
    individually. Listing them is what makes ``--live`` an informed choice.

    Side effects: reads each project's `.mcp.json`.
    """
    planned: list[tuple[Path, str]] = []
    for project_dir in project_dirs:
        try:
            data = json.loads((project_dir / ".mcp.json").read_text(encoding="utf-8", errors="replace"))
            entry = data["mcpServers"][MCP_SERVER_KEY]
            argv = [str(entry.get("command", "?"))] + [str(a) for a in entry.get("args", [])]
            planned.append((project_dir, " ".join(argv)))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
            planned.append((project_dir, "<unreadable server entry>"))
    return planned


def run_fleet_check(
    roots: list[Path],
    *,
    live: bool = False,
    max_depth: int = MAX_DEPTH,
    on_project: Callable[[DoctorReport, int, int], None] | None = None,
) -> FleetReport:
    """Check every TRACE project under *roots*.

    *live* is passed through to each project's doctor, which starts that
    project's own configured server command. Across a sweep that means running a
    command declared by every directory found, so it stays opt-in and the CLI
    lists the commands before executing any of them.

    *on_project* is called with each finished report plus its 1-based position
    and the total, so a caller can show progress: sequential live probes budget
    up to three minutes each, and a silent terminal for an hour is
    indistinguishable from a hang.

    Side effects: reads files under *roots*; with *live*, starts and terminates
    one subprocess per project. Never writes.

    A project whose check raises is reported as a failed project rather than
    ending the sweep: surveying two dozen directories is the entire point, and
    one pathological config must not cost the rest of the results.
    """
    from trace_mcp import conformance

    found = discover_projects(roots, max_depth=max_depth)
    reports: list[DoctorReport] = []
    total = len(found.projects)
    for index, project_dir in enumerate(found.projects, start=1):
        try:
            report = conformance.run_doctor(project_dir, live=live)
        except Exception as exc:  # noqa: BLE001 - one bad project must not end the sweep
            report = DoctorReport(
                project_dir=project_dir,
                findings=[
                    Finding(
                        check="doctor.crashed",
                        status="fail",
                        detail=f"checking this project raised {type(exc).__name__}: {exc}",
                    )
                ],
            )
        reports.append(report)
        if on_project is not None:
            on_project(report, index, total)
    return FleetReport(
        roots=list(roots),
        projects=reports,
        unreadable_roots=found.unreadable_roots,
        unreadable_configs=found.unreadable_configs,
        unreadable_dirs=found.unreadable_dirs,
        truncated_dirs=found.truncated_dirs,
    )


__all__ = [
    "FLEET_ROOTS_ENV",
    "Discovery",
    "FleetReport",
    "discover_projects",
    "planned_live_commands",
    "roots_from_env",
    "run_fleet_check",
]

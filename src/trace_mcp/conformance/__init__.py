"""Deployed-state conformance for TRACE — the `trace-mcp doctor` layer.

The unit suite proves the source tree is correct. It cannot prove that the
*deployed* system is: hook copies that predate the release, a PostToolUse
matcher that never fires, a project with no `TRACE_PROJECT` pin, a server
process built from a stale cached wheel. Every one of those has happened, and
every one was found by hand. This package turns that hand-checking into a
machine-readable check of one project directory against this build's own
expectations.

Public API:

    run_doctor(project_dir, *, live=False) -> DoctorReport

``DoctorReport.ok`` is False when any check failed; ``skip`` findings name the
upstream check that made evaluation impossible and never make a report
unhealthy on their own. ``expectations`` holds the models and the derived
constants; ``probes`` holds the individual checks; ``cli`` is the
``trace-mcp doctor`` entry point.

Nothing here writes to the checked project or to ``~/.trace``. The single
action — spawning the project's own configured server command — happens only
under ``live=True`` (the CLI's ``--live`` flag).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from trace_mcp.conformance.expectations import (
    CORE_TOOLS,
    LEARN_TOOLS,
    ConformanceAssetError,
    DoctorReport,
    ExpectedHookDeployment,
    ExpectedServedBuild,
    ExpectedToolSurface,
    Finding,
)
from trace_mcp.conformance.probes import (
    CONFIG_CHECKS,
    HOOK_CHECKS,
    LIVE_CHECKS,
    PIN_CHECKS,
    check_config,
    check_hooks,
    check_pin_coherence,
    check_served_build,
)

_OFFLINE_PROBES: tuple[tuple[str, Callable[[Path], list[Finding]], tuple[str, ...]], ...] = (
    ("config", check_config, CONFIG_CHECKS),
    ("hooks", check_hooks, HOOK_CHECKS),
    ("pin", check_pin_coherence, PIN_CHECKS),
)


def run_doctor(project_dir: Path, *, live: bool = False) -> DoctorReport:
    """Check one project directory's deployed state against this build.

    *live* additionally spawns the project's own configured server command and
    handshakes with it (see ``probes.check_served_build``); without it the
    three ``live.*`` checks report as not evaluated.

    Side effects: reads files under *project_dir*; with *live*, starts and
    terminates one subprocess. Never writes.

    A probe that raises is itself reported as a failed check rather than
    propagating: a checker that dies on one malformed project would take a
    whole fleet sweep down with it, and an unexplained crash is exactly the
    silence this layer exists to remove.
    """
    findings: list[Finding] = []
    for name, probe, owned in _OFFLINE_PROBES:
        findings += _guarded(name, owned, lambda p=probe: p(project_dir))
    findings += _guarded("live", LIVE_CHECKS, lambda: check_served_build(project_dir, live=live))
    return DoctorReport(project_dir=project_dir, findings=findings)


def _guarded(name: str, owned: tuple[str, ...], probe: Callable[[], list[Finding]]) -> list[Finding]:
    """Run one probe, converting a crash into findings that keep its check ids present.

    Check ids are consumed by tooling, so a crashing probe must not make its
    whole group vanish: the crash itself is one failed check, and every id the
    probe owns is reported as not evaluated.
    """
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - a probe crash must surface as findings, never propagate
        reason = f"the {name} probe raised {type(exc).__name__}: {exc}"
        return [
            Finding(check=f"{name}.probe_error", status="fail", detail=reason),
            *[
                Finding(check=check, status="skip", detail=f"not evaluated: {reason} (see {name}.probe_error)")
                for check in owned
            ],
        ]


__all__ = [
    "CONFIG_CHECKS",
    "CORE_TOOLS",
    "HOOK_CHECKS",
    "LIVE_CHECKS",
    "LEARN_TOOLS",
    "PIN_CHECKS",
    "ConformanceAssetError",
    "DoctorReport",
    "ExpectedHookDeployment",
    "ExpectedServedBuild",
    "ExpectedToolSurface",
    "Finding",
    "check_config",
    "check_hooks",
    "check_pin_coherence",
    "check_served_build",
    "run_doctor",
]

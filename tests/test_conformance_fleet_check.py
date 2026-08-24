"""Fleet-wide deployed-state sweep — `trace-mcp fleet-check`.

The doctor answers "is THIS project's deployment sound?". The failure this
codebase keeps hitting is fleet-shaped: one defect replicated across every
consumer, discovered months later by hand — a matcher that never fired in most
projects, hook copies frozen at an old release, pins that were never minted.
Answering that at all previously meant a bespoke shell loop, which is why it
was answered roughly once.

Two properties matter beyond "it runs the doctor N times". The output is
consumed by tooling as well as by a person, so **field names and check ids are
stable and ordering is deterministic**. And a sweep reads configuration from
directories nobody validated, so **one unreadable or malformed project must not
end the sweep** — it is reported and the walk continues.

Every test builds its own tree under tmp_path. Nothing here walks a real home
directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trace_mcp import project_identity as pident
from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.conformance import FleetReport, run_fleet_check
from trace_mcp.conformance.cli import main_fleet_check as fleet_main
from trace_mcp.init_project import init_project

LIVE_CHECKS = {"live.spawn", "live.version", "live.tool_surface"}


def _make_project(root: Path, name: str, monkeypatch: pytest.MonkeyPatch, *, client: str = "claude-code") -> Path:
    """Initialize a project under *root* exactly as `trace-mcp-init` would."""
    source = root / "TRACE-checkout"
    source.mkdir(exist_ok=True)
    monkeypatch.setenv("TRACE_SOURCE_PATH", str(source))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(root / "projects.json"))
    pident._reset_registry_cache()
    project_dir = root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    init_project(str(project_dir), client=client)
    pident._reset_registry_cache()
    return project_dir


def _break_hooks(project_dir: Path) -> None:
    """Remove a hook script — the shape most deployed projects actually fail."""
    (project_dir / ".claude" / "hooks" / "decision-audit.sh").unlink()


@pytest.fixture()
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A root holding one healthy project, one broken one, and a non-TRACE one."""
    root = tmp_path / "fleet"
    root.mkdir()
    healthy = _make_project(root, "alpha", monkeypatch)
    broken = _make_project(root, "beta", monkeypatch)
    _break_hooks(broken)
    unrelated = root / "gamma"
    unrelated.mkdir()
    (unrelated / ".mcp.json").write_text(json.dumps({"mcpServers": {"other-server": {"command": "node"}}}))
    return root, healthy, broken


# ── discovery ───────────────────────────────────────────────────────────────


def test_finds_every_trace_project_and_ignores_the_rest(fleet) -> None:
    root, healthy, broken = fleet
    report = run_fleet_check([root])

    checked = [p.project_dir for p in report.projects]
    assert checked == sorted([healthy, broken]), "projects are reported in a deterministic path order"
    assert not any("gamma" in str(p) for p in checked), "a config with no trace server is not this tool's business"


def test_walk_is_depth_capped_and_skips_heavy_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep of a home directory must not descend forever or into dependency trees."""
    root = tmp_path / "fleet"
    root.mkdir()
    shallow = _make_project(root, "shallow", monkeypatch)

    deep = root / "a" / "b" / "c" / "d" / "e" / "f" / "deep"
    deep.mkdir(parents=True)
    (deep / ".mcp.json").write_text(json.dumps({"mcpServers": {MCP_SERVER_KEY: {"command": "uvx"}}}))

    for ignored in (root / "node_modules" / "pkg", root / ".git" / "modules" / "x"):
        ignored.mkdir(parents=True)
        (ignored / ".mcp.json").write_text(json.dumps({"mcpServers": {MCP_SERVER_KEY: {"command": "uvx"}}}))

    found = [p.project_dir for p in run_fleet_check([root]).projects]
    assert shallow in found
    assert deep not in found, "the walk must be depth-capped"
    assert not any("node_modules" in str(p) or "/.git/" in str(p) for p in found)


def test_a_symlink_loop_does_not_hang_the_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "fleet"
    root.mkdir()
    _make_project(root, "alpha", monkeypatch)
    (root / "loop").symlink_to(root, target_is_directory=True)

    report = run_fleet_check([root])
    assert len(report.projects) == 1


def test_the_same_project_reached_twice_is_reported_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Overlapping roots are the normal case when a user passes several paths."""
    root = tmp_path / "fleet"
    root.mkdir()
    project = _make_project(root, "alpha", monkeypatch)

    report = run_fleet_check([root, project, root])
    assert [p.project_dir for p in report.projects] == [project]


def test_a_root_that_does_not_exist_is_reported_not_ignored(tmp_path: Path) -> None:
    """Silently sweeping nothing would report a perfectly healthy fleet."""
    report = run_fleet_check([tmp_path / "no-such-place"])
    assert report.unreadable_roots == [tmp_path / "no-such-place"]
    assert not report.ok, "a root that could not be read means the sweep is not a clean bill of health"


def test_an_unparseable_config_is_counted_rather_than_skipped(fleet) -> None:
    """It might be a trace config; it cannot be classified, so it is surfaced."""
    root, _healthy, _broken = fleet
    (root / "delta").mkdir()
    (root / "delta" / ".mcp.json").write_text("{not json")

    report = run_fleet_check([root])
    assert report.unreadable_configs == [root / "delta" / ".mcp.json"]
    assert not report.ok


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_an_unreadable_directory_is_reported_and_the_sweep_continues(fleet) -> None:
    """A directory the process cannot stat must not end the run.

    This is the ordinary case on macOS, where a terminal without Full Disk
    Access gets EPERM on TCC-gated directories — sweeping a home directory
    would otherwise traceback instead of surveying the fleet.
    """
    root, _healthy, _broken = fleet
    locked = root / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        report = run_fleet_check([root])
    finally:
        locked.chmod(0o755)

    assert report.total == 2, "the readable projects are still checked"
    assert locked in report.unreadable_dirs
    assert not report.ok, "a sweep that could not read part of the tree is not a clean bill of health"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_an_unlistable_directory_is_reported_rather_than_silently_skipped(fleet) -> None:
    """`iterdir` failing hides every project below that point; saying nothing
    would report a partial survey as a healthy fleet."""
    root, _healthy, _broken = fleet
    blind = root / "blind"
    blind.mkdir()
    (blind / "nested").mkdir()
    blind.chmod(0o111)  # traversable, not listable
    try:
        report = run_fleet_check([root])
    finally:
        blind.chmod(0o755)

    assert blind in report.unreadable_dirs
    assert not report.ok


def test_depth_truncation_is_recorded_not_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project below the cap is absent from the report, so the report says how
    many branches were cut rather than implying it surveyed everything."""
    root = tmp_path / "fleet"
    root.mkdir()
    deep = root / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    (deep / ".mcp.json").write_text(json.dumps({"mcpServers": {MCP_SERVER_KEY: {"command": "uvx"}}}))

    report = run_fleet_check([root])
    assert report.truncated_dirs, "the walk stopped at the cap and must say so"
    rendered = report.render()
    assert "depth cap" in rendered and "--max-depth" in rendered
    # Truncation is a note, not a defect: a real tree truncates thousands of
    # times, and tying `ok` to it would make the flag permanently false and
    # therefore permanently ignored.
    assert report.ok


def test_a_raised_max_depth_reaches_a_deeper_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "fleet"
    root.mkdir()
    nested = root / "a" / "b" / "c" / "d" / "e"
    nested.mkdir(parents=True)
    deep = _make_project(nested, "deep", monkeypatch)

    assert deep not in [p.project_dir for p in run_fleet_check([root]).projects]
    assert deep in [p.project_dir for p in run_fleet_check([root], max_depth=10).projects]


# ── aggregation ─────────────────────────────────────────────────────────────


def test_totals_match_the_per_project_reports(fleet) -> None:
    root, healthy, _broken = fleet
    report = run_fleet_check([root])

    assert report.total == 2
    assert report.clean == 1
    assert report.with_findings == 1
    assert {p.project_dir for p in report.projects if p.ok} == {healthy}


def test_findings_are_rolled_up_by_check(fleet) -> None:
    """The actionable view for a fleet: which defect, how many projects — that is
    how one fix gets planned across many consumers."""
    root, _healthy, _broken = fleet
    report = run_fleet_check([root])

    assert report.findings_by_check["hooks.present"] == 1
    assert all(count >= 1 for count in report.findings_by_check.values())
    assert "hooks.executable" not in report.findings_by_check, "only FAILING checks are rolled up"


def test_an_empty_fleet_says_so_instead_of_printing_an_all_clear(tmp_path: Path) -> None:
    """Nothing failed, but "every discovered project matches" over zero projects
    reads as a healthy fleet when the likely cause is a mistyped root."""
    root = tmp_path / "empty"
    root.mkdir()
    report = run_fleet_check([root])

    assert report.total == 0
    assert report.ok, "no projects is not the same as broken projects"
    rendered = report.render()
    assert "0 project" in rendered
    assert "every discovered project matches" not in rendered


def test_one_crashing_project_does_not_end_the_sweep(fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep exists to survey many directories; a single pathological one must
    not cost the operator the other twenty-three results."""
    import trace_mcp.conformance as conformance

    real = conformance.run_doctor
    root, healthy, broken = fleet

    def explode(project_dir: Path, *, live: bool = False):
        if project_dir == broken:
            raise RuntimeError("doctor exploded")
        return real(project_dir, live=live)

    monkeypatch.setattr(conformance, "run_doctor", explode)
    report = run_fleet_check([root])

    assert report.total == 2, "the crashed project is still reported"
    crashed = next(p for p in report.projects if p.project_dir == broken)
    assert not crashed.ok
    assert any("exploded" in f.detail for f in crashed.findings)
    assert next(p for p in report.projects if p.project_dir == healthy).ok


# ── report shape (consumed by tooling) ──────────────────────────────────────


def test_report_json_round_trips_with_its_totals(fleet) -> None:
    root, _healthy, _broken = fleet
    report = run_fleet_check([root])
    payload = json.loads(report.model_dump_json())

    for field in ("total", "clean", "with_findings", "ok", "projects", "findings_by_check"):
        assert field in payload, f"{field} is part of the documented output shape"
    restored = FleetReport.model_validate_json(report.model_dump_json())
    assert restored.total == report.total
    assert [p.project_dir for p in restored.projects] == [p.project_dir for p in report.projects]


def test_offline_sweep_leaves_the_live_checks_unevaluated(fleet) -> None:
    root, _healthy, _broken = fleet
    report = run_fleet_check([root])
    for project in report.projects:
        skipped = {f.check for f in project.findings if f.status == "skip"}
        assert LIVE_CHECKS <= skipped, "a sweep must not spawn servers unless asked"


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_exit_codes_distinguish_findings_from_usage(fleet, tmp_path: Path, capsys) -> None:
    root, healthy, _broken = fleet

    assert fleet_main([str(root)]) == 1, "a broken project means findings"
    assert fleet_main([str(healthy)]) == 0
    capsys.readouterr()

    assert fleet_main([]) == 2, "no roots given and none configured is a usage error"
    assert "TRACE_FLEET_ROOTS" in capsys.readouterr().err


def test_cli_reads_roots_from_the_environment(fleet, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """No personal path is ever compiled in; the operator supplies the roots."""
    root, _healthy, _broken = fleet
    monkeypatch.setenv("TRACE_FLEET_ROOTS", str(root))

    assert fleet_main([]) == 1
    assert "alpha" in capsys.readouterr().out


def test_cli_json_output_parses(fleet, capsys) -> None:
    root, _healthy, _broken = fleet
    fleet_main([str(root), "--json"])
    report = FleetReport.model_validate_json(capsys.readouterr().out)
    assert report.total == 2


def test_cli_summary_names_the_failing_projects_and_the_common_defects(fleet, capsys) -> None:
    root, _healthy, _broken = fleet
    fleet_main([str(root)])
    out = capsys.readouterr().out

    assert "beta" in out, "a failing project must be named"
    assert "hooks.present" in out, "the rollup tells the operator what to fix across the fleet"
    assert "1/2" in out or "1 of 2" in out


def test_server_cli_dispatches_fleet_check(fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """`trace-mcp fleet-check` must reach the sweep, not fall through to the server."""
    import sys

    from trace_mcp import server as srv

    _root, healthy, _broken = fleet
    monkeypatch.setattr(sys, "argv", ["trace-mcp", "fleet-check", str(healthy)])
    with pytest.raises(SystemExit) as exc:
        srv.main()
    assert exc.value.code == 0


def test_live_is_not_implied_by_a_plain_sweep(fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep runs the commands declared by every directory it finds when --live
    is given, so nothing may turn it on implicitly."""
    from trace_mcp.conformance import fleet as fleet_mod

    seen: list[bool] = []
    real = fleet_mod.discover_projects

    import trace_mcp.conformance as conformance

    real_doctor = conformance.run_doctor

    def spy(project_dir, *, live: bool = False):
        seen.append(live)
        return real_doctor(project_dir, live=live)

    monkeypatch.setattr(conformance, "run_doctor", spy)
    root, _healthy, _broken = fleet
    fleet_mod.run_fleet_check([root])
    assert seen and not any(seen)
    assert real is fleet_mod.discover_projects


# ── --live is an informed second step ───────────────────────────────────────


def test_live_without_confirmation_lists_commands_and_runs_nothing(fleet, monkeypatch, capsys) -> None:
    """A sweep executes commands from directories the operator never named one
    by one, so the first `--live` shows what would run and stops."""
    import trace_mcp.conformance as conformance

    spawned: list[bool] = []
    real = conformance.run_doctor
    monkeypatch.setattr(
        conformance,
        "run_doctor",
        lambda d, *, live=False: (spawned.append(live), real(d, live=False))[1],
    )

    root, _healthy, _broken = fleet
    assert fleet_main([str(root), "--live"]) == 2
    err = capsys.readouterr().err
    assert "would execute" in err and "uvx" in err and "--yes" in err
    assert not spawned, "nothing may be started before the operator has seen the list"


def test_live_with_confirmation_passes_through(fleet, monkeypatch, capsys) -> None:
    import trace_mcp.conformance as conformance

    seen: list[bool] = []
    real = conformance.run_doctor

    def spy(project_dir, *, live: bool = False):
        seen.append(live)
        return real(project_dir, live=False)  # do not actually spawn in tests

    monkeypatch.setattr(conformance, "run_doctor", spy)
    root, _healthy, _broken = fleet
    fleet_main([str(root), "--live", "--yes"])
    assert seen and all(seen)


def test_progress_is_streamed_so_a_long_sweep_is_not_a_silent_terminal(fleet, capsys) -> None:
    root, _healthy, _broken = fleet
    fleet_main([str(root)])
    err = capsys.readouterr().err
    assert "[1/2]" in err and "[2/2]" in err


def test_json_mode_keeps_stdout_pure(fleet, capsys) -> None:
    """Progress goes to stderr; a --json consumer must get only the report."""
    root, _healthy, _broken = fleet
    fleet_main([str(root), "--json"])
    captured = capsys.readouterr()
    FleetReport.model_validate_json(captured.out)
    assert "[1/2]" not in captured.out

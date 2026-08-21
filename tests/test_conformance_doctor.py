"""Deployed-state conformance — `trace-mcp doctor` (`trace_mcp.conformance`).

The failure class this suite exists to catch is *source tree green, deployed
system rotten*: hook copies that predate the current release, a PostToolUse
matcher that never fires because it names a bare tool name instead of the
host-namespaced one, a project carrying no `TRACE_PROJECT` pin, a served build
that is a stale wheel from a warm uv cache. None of those are visible to the
rest of the suite — every one of them was found by hand.

The load-bearing property is **INV-11: a freshly initialized project is
doctor-clean.** It ties the shipped installer to the shipped expectations, so
template rot — a dead hook matcher shipped in `settings_template.json` for
several releases — fails at PR time instead of on a consumer's machine months
later. Every other test here breaks exactly one aspect of a fresh install and
asserts exactly one check fails, which is also what pins the report's
one-fail-per-root-cause contract.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

import trace_mcp
from trace_mcp import project_identity as pident
from trace_mcp.adapters import get_adapter, list_adapters
from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.conformance import DoctorReport, expectations, run_doctor
from trace_mcp.conformance.cli import main as doctor_main
from trace_mcp.init_project import LEARN_EXTRAS, init_project

REPO_ROOT = Path(__file__).parent.parent
README = REPO_ROOT / "README.md"

# The served-build checks are only evaluated under --live; every offline run
# reports them as skipped, so assertions about "nothing unevaluated" exclude them.
LIVE_CHECKS = {"live.spawn", "live.version", "live.tool_surface"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _failed(report: DoctorReport) -> set[str]:
    return {f.check for f in report.findings if f.status == "fail"}


def _skipped(report: DoctorReport) -> set[str]:
    return {f.check for f in report.findings if f.status == "skip"}


def _detail(report: DoctorReport, check: str) -> str:
    return next(f.detail for f in report.findings if f.check == check)


def _read_mcp_json(project_dir: Path) -> dict:
    return json.loads((project_dir / ".mcp.json").read_text())


def _write_mcp_json(project_dir: Path, config: dict) -> None:
    (project_dir / ".mcp.json").write_text(json.dumps(config, indent=2) + "\n")


def _without_with_pairs(args: list[str]) -> list[str]:
    """Drop every ``--with <pkg>`` pair, leaving the rest of the launch args intact."""
    stripped: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args):
            i += 2
            continue
        stripped.append(args[i])
        i += 1
    return stripped


def _settings_path(project_dir: Path) -> Path:
    return project_dir / ".claude" / "settings.json"


def _readme_tools(heading: str) -> list[str]:
    """Tool names in the markdown table under *heading* in README.md."""
    text = README.read_text()
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = rest.find("\n## ")
    nxt = rest.find("\n### ")
    if nxt != -1 and (end == -1 or nxt < end):
        end = nxt
    section = rest if end == -1 else rest[:end]
    return re.findall(r"^\|\s*`(trace_\w+)`", section, flags=re.MULTILINE)


def _make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str) -> Path:
    """Initialize a project directory exactly as `trace-mcp-init` would.

    Side effects: writes under *tmp_path*, enrolls the project in the
    test-isolated registry, and points `TRACE_SOURCE_PATH` at a real directory
    so the written `--from` source resolves (the doctor checks that a
    filesystem source exists).
    """
    source = tmp_path / "TRACE-checkout"
    source.mkdir(exist_ok=True)
    monkeypatch.setenv("TRACE_SOURCE_PATH", str(source))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()
    project_dir = tmp_path / "demo-project"
    project_dir.mkdir(exist_ok=True)
    init_project(str(project_dir), client=client)
    pident._reset_registry_cache()
    return project_dir


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project freshly initialized with the Claude Code adapter."""
    return _make_project(tmp_path, monkeypatch, client="claude-code")


# ── INV-11: a fresh install is doctor-clean ─────────────────────────────────


@pytest.mark.parametrize("adapter_name", sorted(list_adapters()))
def test_fresh_init_is_doctor_clean(adapter_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INV-11 — every adapter TRACE actually installs must produce a clean project.

    Enumerated over the adapter registry rather than hard-coded to Claude Code:
    when a new host adapter ships, this test starts exercising it, and the
    doctor must learn that host's layout or the guard fails. That is the point
    — the dead decision-audit matcher survived four releases precisely because
    nothing compared the installer's output against the expectations.
    """
    probe = tmp_path / "adapter-probe"
    probe.mkdir()
    try:
        get_adapter(adapter_name).install(probe, dry_run=True)
    except NotImplementedError:
        pytest.skip(f"{adapter_name} adapter is a placeholder — install() is not implemented")

    project_dir = _make_project(tmp_path, monkeypatch, client=adapter_name)
    report = run_doctor(project_dir)

    assert report.ok, f"fresh {adapter_name} install is not doctor-clean: {_failed(report)}\n{report.render()}"
    assert _skipped(report) <= LIVE_CHECKS, (
        f"a fresh install should leave nothing unevaluated but the served build: {_skipped(report) - LIVE_CHECKS}"
    )


def test_report_ok_is_false_exactly_when_a_check_fails(project: Path) -> None:
    assert run_doctor(project).ok
    (project / ".claude" / "hooks" / "decision-audit.sh").unlink()
    assert not run_doctor(project).ok


# ── Expectations are derived, not restated ──────────────────────────────────


def test_expected_tool_surface_matches_registered_tools() -> None:
    """The declared surface must equal what the server actually registers."""
    from trace_mcp.selfcost import _list_registered_tools

    registered = {name for name, _desc, _schema in _list_registered_tools(include_extensions=True)}
    surface = expectations.ExpectedToolSurface()
    declared = set(surface.core_tools) | set(surface.learn_tools)
    assert declared == registered, (
        f"declared-but-unregistered: {sorted(declared - registered)}; "
        f"registered-but-undeclared: {sorted(registered - declared)}"
    )


def test_expected_tool_surface_matches_readme_tables() -> None:
    """README is the documented surface; the constants must not drift from it."""
    surface = expectations.ExpectedToolSurface()
    assert list(surface.core_tools) == _readme_tools("### Core tools (17)")
    assert list(surface.learn_tools) == _readme_tools("### Extension: trace-learn (5)")


def test_expected_tool_total_is_the_documented_22() -> None:
    surface = expectations.ExpectedToolSurface()
    assert surface.total == 22 == len(surface.core_tools) + len(surface.learn_tools)
    assert "22 total" in README.read_text()


def test_hook_expectations_derive_from_the_shipped_assets() -> None:
    """Filenames and the version stamp come from the assets, never retyped."""
    assets = REPO_ROOT / "src" / "trace_mcp" / "adapters" / "claude_code" / "assets" / "hooks"
    shipped = {p.name for p in assets.glob("*.sh")}
    hooks = expectations.ExpectedHookDeployment()

    assert set(hooks.hook_files) == shipped
    assert shipped, "positive control: the assets directory must not be empty"
    assert re.fullmatch(r"\[trace-hooks v[\d.]+\]", hooks.version_stamp)
    for name in shipped:
        assert hooks.version_stamp in (assets / name).read_text()


def test_decision_audit_matcher_derives_from_the_server_key() -> None:
    """A bare tool-name matcher never fires; the namespaced form is mandatory."""
    hooks = expectations.ExpectedHookDeployment()
    assert hooks.decision_audit_matcher == f"mcp__{MCP_SERVER_KEY}__trace_end_session"


def test_expected_served_build_reads_the_package_version() -> None:
    assert expectations.ExpectedServedBuild().version == trace_mcp.__version__
    assert expectations.ExpectedServedBuild().tool_total == expectations.ExpectedToolSurface().total


# ── .mcp.json config checks ─────────────────────────────────────────────────


def test_missing_mcp_json_fails_once_and_skips_dependents(project: Path) -> None:
    """One fail per root cause: dependents report an explicit skip, not silence."""
    (project / ".mcp.json").unlink()
    report = run_doctor(project)

    assert _failed(report) == {"config.file"}
    assert {"config.command", "config.source", "config.learn_extras", "config.refresh"} <= _skipped(report)
    assert "config.file" in _detail(report, "config.command")


def test_unparseable_mcp_json_fails_config_file(project: Path) -> None:
    (project / ".mcp.json").write_text("{not json")
    assert _failed(run_doctor(project)) == {"config.file"}


def test_absent_trace_entry_fails_server_entry(project: Path) -> None:
    _write_mcp_json(project, {"mcpServers": {"other": {"command": "node"}}})
    report = run_doctor(project)
    assert "config.server_entry" in _failed(report)
    assert MCP_SERVER_KEY in _detail(report, "config.server_entry")


def test_missing_learn_extras_fails_and_explains_the_17_tool_symptom(project: Path) -> None:
    config = _read_mcp_json(project)
    assert all(pkg in config["mcpServers"][MCP_SERVER_KEY]["args"] for pkg in LEARN_EXTRAS), (
        "positive control: a fresh init must carry the extras this test then strips"
    )
    entry = config["mcpServers"][MCP_SERVER_KEY]
    entry["args"] = _without_with_pairs(entry["args"])
    _write_mcp_json(project, config)

    report = run_doctor(project)
    assert _failed(report) == {"config.learn_extras"}
    detail = _detail(report, "config.learn_extras")
    assert "17" in detail and "22" in detail


def test_missing_refresh_flag_fails(project: Path) -> None:
    config = _read_mcp_json(project)
    entry = config["mcpServers"][MCP_SERVER_KEY]
    entry["args"] = [a for a in entry["args"] if a not in ("--refresh-package", "--refresh")]
    _write_mcp_json(project, config)
    assert _failed(run_doctor(project)) == {"config.refresh"}


def test_non_uvx_command_fails(project: Path) -> None:
    config = _read_mcp_json(project)
    config["mcpServers"][MCP_SERVER_KEY]["command"] = "python"
    _write_mcp_json(project, config)
    assert _failed(run_doctor(project)) == {"config.command"}


def _set_source(project_dir: Path, source: str) -> None:
    config = _read_mcp_json(project_dir)
    args = config["mcpServers"][MCP_SERVER_KEY]["args"]
    args[args.index("--from") + 1] = source
    _write_mcp_json(project_dir, config)


def test_relative_source_resolves_against_the_project_not_the_checker(project: Path) -> None:
    """`--from .` is what this repository's own config uses; uvx reads it relative
    to the project, so a checker that resolves it against its own cwd reports the
    same project healthy or broken depending on where it was run from."""
    _set_source(project, ".")
    assert run_doctor(project).ok


def test_bare_pypi_name_source_fails_as_dependency_confusion(project: Path) -> None:
    """`--from trace-mcp` would fetch an unrelated PyPI package and run it."""
    _set_source(project, "trace-mcp")
    report = run_doctor(project)
    assert _failed(report) == {"config.source"}
    assert "dependency confusion" in _detail(report, "config.source").lower()


def test_git_ref_source_is_accepted(project: Path) -> None:
    """The documented onboarding path pins a git ref rather than a local path."""
    _set_source(project, "git+https://github.com/Thru-Echoes/TRACE@main")
    assert run_doctor(project).ok


def test_vanished_source_checkout_fails(project: Path) -> None:
    """A `--from` path that no longer exists is a dead server, not a warning."""
    config = _read_mcp_json(project)
    args = config["mcpServers"][MCP_SERVER_KEY]["args"]
    args[args.index("--from") + 1] = str(project / "does-not-exist")
    _write_mcp_json(project, config)
    assert _failed(run_doctor(project)) == {"config.source"}


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"command": "uvx", "args": {"not": "a list"}},
        {"command": "uvx", "args": ["--from", "."], "env": ["not", "an", "object"]},
        {},
    ],
    ids=["string-entry", "dict-args", "list-env", "empty-entry"],
)
def test_malformed_config_shapes_report_findings_rather_than_crashing(project: Path, entry: object) -> None:
    """Fleet sweeps read configs nobody validated — a malformed one must not
    take the checker down, and must not be waved through either."""
    _write_mcp_json(project, {"mcpServers": {MCP_SERVER_KEY: entry}})
    report = run_doctor(project)
    assert not report.ok
    assert not [f for f in report.findings if f.check.endswith(".probe_error")], report.render()


# ── hook deployment checks ──────────────────────────────────────────────────


def test_missing_hook_file_fails(project: Path) -> None:
    (project / ".claude" / "hooks" / "pretool-guard.sh").unlink()
    report = run_doctor(project)
    assert _failed(report) == {"hooks.present"}
    assert "pretool-guard.sh" in _detail(report, "hooks.present")


def test_non_executable_hook_fails(project: Path) -> None:
    hook = project / ".claude" / "hooks" / "session-reminder.sh"
    hook.chmod(stat.S_IRUSR | stat.S_IWUSR)
    report = run_doctor(project)
    assert _failed(report) == {"hooks.executable"}
    assert "session-reminder.sh" in _detail(report, "hooks.executable")


def test_stale_hook_copy_fails_on_the_version_stamp(project: Path) -> None:
    """The 17-project stale-fleet finding, reduced to a test."""
    hook = project / ".claude" / "hooks" / "decision-audit.sh"
    stamp = expectations.ExpectedHookDeployment().version_stamp
    hook.write_text(hook.read_text().replace(stamp, "[trace-hooks v0.1]"))
    report = run_doctor(project)
    assert _failed(report) == {"hooks.stamp"}
    assert "decision-audit.sh" in _detail(report, "hooks.stamp")


def test_dead_short_decision_audit_matcher_fails(project: Path) -> None:
    """The exact defect that left 15 of 17 deployed projects with a dead hook."""
    settings = json.loads(_settings_path(project).read_text())
    for entry in settings["hooks"]["PostToolUse"]:
        entry["matcher"] = "trace_end_session"
    _settings_path(project).write_text(json.dumps(settings, indent=2))

    report = run_doctor(project)
    assert _failed(report) == {"hooks.decision_audit_matcher"}
    detail = _detail(report, "hooks.decision_audit_matcher")
    assert "trace_end_session" in detail and f"mcp__{MCP_SERVER_KEY}__trace_end_session" in detail


def test_unregistered_hook_script_fails(project: Path) -> None:
    """A hook file on disk that nothing invokes is dead weight, not a deployment."""
    settings = json.loads(_settings_path(project).read_text())
    settings["hooks"].pop("UserPromptSubmit")
    _settings_path(project).write_text(json.dumps(settings, indent=2))

    report = run_doctor(project)
    assert _failed(report) == {"hooks.registered"}
    assert "prompt-reminder.sh" in _detail(report, "hooks.registered")


def test_unparseable_settings_fails_once_and_skips_dependents(project: Path) -> None:
    _settings_path(project).write_text("{not json")
    report = run_doctor(project)
    assert _failed(report) == {"hooks.settings"}
    assert {"hooks.registered", "hooks.decision_audit_matcher"} <= _skipped(report)


def test_missing_settings_fails(project: Path) -> None:
    _settings_path(project).unlink()
    assert _failed(run_doctor(project)) == {"hooks.settings"}


def test_missing_hooks_directory_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project with `.mcp.json` but no adapter install: the 7-config population."""
    project_dir = _make_project(tmp_path, monkeypatch, client="none")
    report = run_doctor(project_dir)
    assert {"hooks.present", "hooks.settings"} <= _failed(report)


def test_hook_registered_under_the_wrong_event_fails(project: Path) -> None:
    """A hook on the wrong event is installed, current, executable — and inert.

    Moving the decision-audit registration off PostToolUse leaves it unable to
    ever fire on session end: the same defect as a matcher that never matches,
    one level up.
    """
    settings = json.loads(_settings_path(project).read_text())
    settings["hooks"].setdefault("PreToolUse", []).extend(settings["hooks"].pop("PostToolUse"))
    _settings_path(project).write_text(json.dumps(settings, indent=2))

    report = run_doctor(project)
    assert _failed(report) == {"hooks.registered"}
    detail = _detail(report, "hooks.registered")
    assert "decision-audit.sh" in detail and "PostToolUse" in detail
    assert "hooks.decision_audit_matcher" in _skipped(report)


def test_leftover_stamped_hook_from_an_older_release_fails(project: Path) -> None:
    """A hook this build no longer ships keeps running old semantics forever."""
    stamp = expectations.ExpectedHookDeployment().version_stamp
    orphan = project / ".claude" / "hooks" / "legacy-check.sh"
    orphan.write_text(f"#!/bin/bash\necho 'old behaviour {stamp}'\n")

    report = run_doctor(project)
    assert _failed(report) == {"hooks.unknown"}
    assert "legacy-check.sh" in _detail(report, "hooks.unknown")


def test_a_projects_own_unstamped_hook_is_not_flagged(project: Path) -> None:
    """Only TRACE-stamped scripts are ours to judge."""
    (project / ".claude" / "hooks" / "my-own-hook.sh").write_text("#!/bin/bash\necho hi\n")
    assert run_doctor(project).ok


def test_newer_deployed_hooks_blame_the_checker_not_the_project(project: Path) -> None:
    """A sweep run with an older trace-mcp must not tell a current project to downgrade."""
    hook = project / ".claude" / "hooks" / "decision-audit.sh"
    stamp = expectations.ExpectedHookDeployment().version_stamp
    hook.write_text(hook.read_text().replace(stamp, "[trace-hooks v99.9]"))

    detail = _detail(run_doctor(project), "hooks.stamp")
    assert "NEWER" in detail
    assert "upgrade the trace-mcp" in detail


def test_tilde_source_fails_because_only_a_shell_expands_it(project: Path) -> None:
    """The host executes uvx directly, so a `~` is taken literally and resolved
    against the working directory — verified against uv itself."""
    _set_source(project, "~/Documents/TRACE")
    report = run_doctor(project)
    assert _failed(report) == {"config.source"}
    assert "~" in _detail(report, "config.source")


@pytest.mark.parametrize("spelling", ["trace_mcp", "Trace-MCP", "trace-mcp==0.5.0", "trace.mcp"])
def test_pypi_name_variants_all_fail_as_dependency_confusion(project: Path, spelling: str) -> None:
    """PyPI normalizes names, so an exact-string check would wave three of these through."""
    _set_source(project, spelling)
    report = run_doctor(project)
    assert _failed(report) == {"config.source"}
    assert "dependency confusion" in _detail(report, "config.source").lower()


def test_source_pointing_at_a_plain_file_fails(project: Path) -> None:
    """An existing path is not automatically something uvx can build."""
    stray = project / "notes.txt"
    stray.write_text("not a project\n")
    _set_source(project, str(stray))
    report = run_doctor(project)
    assert _failed(report) == {"config.source"}


def test_launch_args_without_a_command_fail(project: Path) -> None:
    """`--refresh-package trace-mcp` ends with the same word as a correct entry
    but names no command at all, so uvx starts nothing."""
    config = _read_mcp_json(project)
    entry = config["mcpServers"][MCP_SERVER_KEY]
    entry["args"] = entry["args"][:-1]
    _write_mcp_json(project, config)

    report = run_doctor(project)
    assert _failed(report) == {"config.entrypoint"}
    assert "trace-mcp" in _detail(report, "config.entrypoint")


def test_launch_args_running_the_wrong_command_fail(project: Path) -> None:
    config = _read_mcp_json(project)
    entry = config["mcpServers"][MCP_SERVER_KEY]
    entry["args"] = [*entry["args"][:-1], "some-other-tool"]
    _write_mcp_json(project, config)
    assert _failed(run_doctor(project)) == {"config.entrypoint"}


# ── project-pin coherence ───────────────────────────────────────────────────


def test_missing_pin_file_fails_and_skips_coherence(project: Path) -> None:
    (project / ".claude" / "trace.project").unlink()
    report = run_doctor(project)
    assert _failed(report) == {"pin.trace_project_file"}
    assert "pin.coherence" in _skipped(report)


def test_missing_env_pin_fails(project: Path) -> None:
    config = _read_mcp_json(project)
    config["mcpServers"][MCP_SERVER_KEY].pop("env")
    _write_mcp_json(project, config)
    report = run_doctor(project)
    assert _failed(report) == {"pin.mcp_env"}
    assert "TRACE_PROJECT" in _detail(report, "pin.mcp_env")


def test_missing_claude_md_pin_line_fails(project: Path) -> None:
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(re.sub(r'TRACE project name\**\s*:\s*"[^"]+"', "", claude_md.read_text()))
    report = run_doctor(project)
    assert _failed(report) == {"pin.claude_md_line"}


def test_divergent_pins_fail_coherence(project: Path) -> None:
    """Three pin sites, one identity: a disagreement mints two projects."""
    (project / ".claude" / "trace.project").write_text("some-other-key\n")
    report = run_doctor(project)
    assert _failed(report) == {"pin.coherence"}
    detail = _detail(report, "pin.coherence")
    assert "some-other-key" in detail and "demo-project" in detail


def test_bold_claude_md_pin_line_is_accepted(project: Path) -> None:
    """The bold form is what this repository itself writes — it must not drift."""
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(
        re.sub(
            r'TRACE project name\**\s*:\s*"([^"]+)"',
            r'**TRACE project name**: "\1"',
            claude_md.read_text(),
        )
    )
    assert run_doctor(project).ok


def test_case_and_separator_variants_are_the_same_pin(project: Path) -> None:
    """Coherence compares canonical keys, never free-text labels (INV-4)."""
    (project / ".claude" / "trace.project").write_text("Demo_Project\n")
    assert run_doctor(project).ok


# ── report + CLI surface ────────────────────────────────────────────────────


def test_report_json_round_trips(project: Path) -> None:
    report = run_doctor(project)
    restored = DoctorReport.model_validate_json(report.model_dump_json())
    assert restored.ok == report.ok
    assert [f.check for f in restored.findings] == [f.check for f in report.findings]
    assert "ok" in json.loads(report.model_dump_json()), "ok must survive serialization for fleet-check"


def test_cli_exits_zero_on_a_clean_project(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor_main([str(project)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_exits_nonzero_on_findings(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / ".claude" / "trace.project").unlink()
    assert doctor_main([str(project)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_json_output_parses_as_a_report(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor_main([str(project), "--json"]) == 0
    report = DoctorReport.model_validate_json(capsys.readouterr().out)
    assert report.ok


def test_cli_rejects_a_non_directory_with_a_distinct_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad path is not an unhealthy project, and `--json` stdout must stay parseable."""
    assert doctor_main([str(tmp_path / "nope"), "--json"]) == 2
    captured = capsys.readouterr()
    assert "not a directory" in captured.err.lower()
    assert captured.out.strip() == "", "a usage diagnostic on stdout would break a --json consumer"


def test_offline_run_reports_the_live_probe_as_skipped(project: Path) -> None:
    """Without --live the served build is unevaluated — say so, never imply pass."""
    report = run_doctor(project)
    assert LIVE_CHECKS <= _skipped(report)
    for check in LIVE_CHECKS:
        assert "--live" in _detail(report, check)


def test_a_clean_run_emits_exactly_the_declared_check_ids(project: Path) -> None:
    """Check ids are stable API for fleet tooling — a rename or a dropped check
    must break here rather than silently in a consumer."""
    from trace_mcp.conformance import CONFIG_CHECKS, HOOK_CHECKS, LIVE_CHECKS, PIN_CHECKS

    declared = [*CONFIG_CHECKS, *HOOK_CHECKS, *PIN_CHECKS, *LIVE_CHECKS]
    emitted = [f.check for f in run_doctor(project).findings]
    assert sorted(emitted) == sorted(declared)
    assert len(emitted) == len(set(emitted)), "each check must be reported exactly once"


def test_a_crashing_probe_keeps_its_check_ids_present(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe bug must not make a whole group of ids vanish from the report."""
    import trace_mcp.conformance as conformance
    from trace_mcp.conformance import HOOK_CHECKS

    def boom(_project_dir: Path) -> list[expectations.Finding]:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(
        conformance,
        "_OFFLINE_PROBES",
        tuple((name, boom if name == "hooks" else probe, owned) for name, probe, owned in conformance._OFFLINE_PROBES),
    )
    report = run_doctor(project)

    assert not report.ok
    assert "hooks.probe_error" in _failed(report)
    assert set(HOOK_CHECKS) <= _skipped(report)
    assert "probe exploded" in _detail(report, "hooks.probe_error")


def test_server_cli_dispatches_doctor(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`trace-mcp doctor` must reach the doctor, not fall through to the server."""
    from trace_mcp import server as srv

    monkeypatch.setattr(sys, "argv", ["trace-mcp", "doctor", str(project)])
    with pytest.raises(SystemExit) as exc:
        srv.main()
    assert exc.value.code == 0


# ── --live: the served build ────────────────────────────────────────────────
#
# THE MOTIVATING INCIDENT: after a set of merges, a warm uv cache served a
# STALE wheel for ~20 minutes despite `--refresh-package` — the source tree,
# the config, and the hooks were all correct, and only the identity of the
# RUNNING build was wrong. Nothing but a handshake against the project's own
# configured command can catch that, so the probe is exercised here against
# both a real server and a deliberately stale impostor.

_FAKE_STALE_SERVER = """
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("id") is None:
        continue
    if msg.get("method") == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "trace", "version": "0.0.1"},
        }
    elif msg.get("method") == "tools/list":
        result = {"tools": [{"name": "trace_start_session"}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


def _with_server_command(project_dir: Path, command: str, args: list[str], env: dict[str, str]) -> None:
    """Point the project's trace entry at *command* so --live spawns it."""
    config = _read_mcp_json(project_dir)
    entry = config["mcpServers"][MCP_SERVER_KEY]
    entry["command"] = command
    entry["args"] = args
    entry["env"] = {**entry.get("env", {}), **env}
    _write_mcp_json(project_dir, config)


def _offline_server_env() -> dict[str, str]:
    """Deterministic, offline server env — mirrors the e2e harness."""
    return {
        "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "TRACE_EMBEDDING_BACKEND": "none",
        "TRACE_LLM_ENABLED": "false",
        "TRACE_STRICT_LLM": "false",
    }


class TestLiveProbe:
    """`--live` spawns the project's OWN configured command and handshakes with it."""

    def test_real_server_passes_version_and_tool_surface(self, project: Path) -> None:
        _with_server_command(project, sys.executable, ["-m", "trace_mcp.server"], _offline_server_env())
        report = run_doctor(project, live=True)

        live = {f.check: f for f in report.findings if f.check.startswith("live.")}
        assert live["live.spawn"].status == "pass", live["live.spawn"].detail
        assert live["live.version"].status == "pass", live["live.version"].detail
        assert live["live.tool_surface"].status == "pass", live["live.tool_surface"].detail
        assert trace_mcp.__version__ in live["live.version"].detail

    def test_stale_build_is_caught_and_the_remedy_is_named(self, project: Path) -> None:
        script = project / "stale_server.py"
        script.write_text(_FAKE_STALE_SERVER)
        _with_server_command(project, sys.executable, [str(script)], {})
        report = run_doctor(project, live=True)

        failed = _failed(report)
        assert "live.version" in failed and "live.tool_surface" in failed
        version_detail = _detail(report, "live.version")
        assert "0.0.1" in version_detail and trace_mcp.__version__ in version_detail
        assert "uv cache clean" in version_detail, "the finding must carry the remedy, not just the diagnosis"
        assert "22" in _detail(report, "live.tool_surface")

    def test_spawn_failure_is_a_finding_not_an_exception(self, project: Path) -> None:
        _with_server_command(
            project,
            sys.executable,
            ["-c", "import sys; sys.stderr.write('boom: dependency resolution failed\\n'); raise SystemExit(3)"],
            {},
        )
        report = run_doctor(project, live=True)

        assert "live.spawn" in _failed(report)
        assert "boom" in _detail(report, "live.spawn"), "a spawn failure must carry the server's stderr tail"
        assert {"live.version", "live.tool_surface"} <= _skipped(report)

    def test_a_newer_served_build_blames_the_checker(self, project: Path) -> None:
        """When the running server is ahead, telling its owner to clear a cache is
        the wrong instruction — the checker is the stale side."""
        script = project / "future_server.py"
        script.write_text(_FAKE_STALE_SERVER.replace('"0.0.1"', '"99.0.0"'))
        _with_server_command(project, sys.executable, [str(script)], {})

        detail = _detail(run_doctor(project, live=True), "live.version")
        assert "NEWER" in detail
        assert "upgrade the trace-mcp" in detail

    def test_an_oversized_response_line_is_reported_explicitly(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silently dropping an over-long line turns a healthy server into a bogus
        timeout whose suggested remedy cannot help. The real tools/list response
        is already tens of kilobytes and grows with every tool."""
        from trace_mcp.conformance import probes

        monkeypatch.setattr(probes, "_MAX_LINE", 20)
        script = project / "stale_server.py"
        script.write_text(_FAKE_STALE_SERVER)
        _with_server_command(project, sys.executable, [str(script)], {})

        report = run_doctor(project, live=True)
        detail = _detail(report, "live.spawn")
        assert "larger than 20 bytes" in detail
        assert "timeout" not in detail.lower(), "an over-long line must not be misreported as a timeout"

    def test_malformed_env_block_does_not_crash_the_probe(self, project: Path) -> None:
        """A hand-edited config can carry any shape; the probe spawns without it."""
        script = project / "stale_server.py"
        script.write_text(_FAKE_STALE_SERVER)
        config = _read_mcp_json(project)
        entry = config["mcpServers"][MCP_SERVER_KEY]
        entry["command"], entry["args"], entry["env"] = sys.executable, [str(script)], ["not", "an", "object"]
        _write_mcp_json(project, config)

        report = run_doctor(project, live=True)
        assert not [f for f in report.findings if f.check.endswith(".probe_error")], report.render()
        assert _detail(report, "live.spawn").startswith("the configured command starts")

    def test_unrunnable_config_skips_the_live_probe(self, project: Path) -> None:
        (project / ".mcp.json").unlink()
        report = run_doctor(project, live=True)
        assert "live.spawn" in _skipped(report)
        assert not report.ok  # the config failure still stands

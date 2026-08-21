"""Checks that compare one project directory's DEPLOYED state to expectations.

Each ``check_*`` function takes a project directory and returns a list of
``Finding``s; none of them raise for a defect in the target, and none of them
write anything to the project or to ``~/.trace``. The one exception to
"reads only" is ``check_served_build`` with ``live=True``, which **spawns the
project's own configured server command** — noted in its docstring and gated
behind the CLI's ``--live`` flag, because running a command out of a config
file is a real action, not an inspection.

Two contracts hold across every probe, both of them the fail-loud rule from
the handoff:

* **One fail per root cause.** When an input cannot be read, exactly one check
  fails for it and every dependent check reports ``skip`` naming that upstream
  check. A skip is never silence — it says what was not evaluated and why.
* **A probe that cannot determine health fails.** There is no
  warn-and-proceed: an unreadable settings file, a vanished ``--from`` source,
  or a server that will not start are failures, not notes.

Check ids are stable API: ``trace-mcp fleet-check`` and downstream tooling key
off them, so rename with the same care as a wire field.

The hook checks describe the Claude Code deployment — the only adapter that
installs today. A directory set up with ``--client none`` therefore fails
them, which is intended: an MCP server with no host-side enforcement is a gap
in the audit trail, not a supported configuration.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trace_mcp import project_identity as pident
from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.conformance.expectations import (
    ExpectedHookDeployment,
    ExpectedServedBuild,
    ExpectedToolSurface,
    Finding,
    extract_hook_stamp,
)

# Reused, not restated: the same `--with` extras init writes, the same
# bold-tolerant pin-line regex the hooks and init share, and the same argument
# scanner init uses to merge configs. A second copy of any of these is a second
# thing to drift.
from trace_mcp.init_project import _PIN_LINE_RE, LEARN_EXTRAS, _extract_with_packages

_LIVE_TIMEOUT = "TRACE_DOCTOR_LIVE_TIMEOUT"
_DEFAULT_LIVE_TIMEOUT = 180.0

CONFIG_CHECKS = (
    "config.file",
    "config.server_entry",
    "config.command",
    "config.source",
    "config.entrypoint",
    "config.learn_extras",
    "config.refresh",
)
HOOK_CHECKS = (
    "hooks.present",
    "hooks.executable",
    "hooks.stamp",
    "hooks.unknown",
    "hooks.settings",
    "hooks.registered",
    "hooks.decision_audit_matcher",
)
PIN_CHECKS = ("pin.trace_project_file", "pin.mcp_env", "pin.claude_md_line", "pin.coherence")
LIVE_CHECKS = ("live.spawn", "live.version", "live.tool_surface")
"""Every check id each probe owns, in emission order.

These are stable API — `trace-mcp fleet-check` and downstream tooling key off
them — so a probe must emit its whole set on every run, including when it
cannot evaluate a check and when it crashes outright. A consumer that keys on
an id must never have that id silently disappear.
"""


# ── Finding constructors ────────────────────────────────────────────────────


def _ok(check: str, detail: str) -> Finding:
    return Finding(check=check, status="pass", detail=detail)


def _bad(check: str, detail: str) -> Finding:
    return Finding(check=check, status="fail", detail=detail)


def _unevaluated(check: str, reason: str, upstream: str | None = None) -> Finding:
    suffix = f" (see {upstream})" if upstream else ""
    return Finding(check=check, status="skip", detail=f"not evaluated: {reason}{suffix}")


# ── Shared readers ──────────────────────────────────────────────────────────


def _load_config(project_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Parse ``.mcp.json``. Returns (config, error) — never both.

    Side effects: reads ``<project_dir>/.mcp.json``.
    """
    path = project_dir / ".mcp.json"
    if not path.is_file():
        return None, f"{path} does not exist — this directory has no MCP server configuration"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return None, f"{path} is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, f"{path} does not contain a JSON object"
    return data, None


def _trace_entry(config: dict[str, Any]) -> dict[str, Any] | None:
    """The ``mcpServers.<key>`` entry TRACE is configured under, if well-formed."""
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(MCP_SERVER_KEY)
    return entry if isinstance(entry, dict) else None


def _entry_or_reason(project_dir: Path) -> tuple[dict[str, Any] | None, str, str]:
    """Return (entry, reason, upstream_check) for probes that need the trace entry."""
    config, _error = _load_config(project_dir)
    if config is None:
        return None, ".mcp.json is unreadable", "config.file"
    entry = _trace_entry(config)
    if entry is None:
        return None, f"no '{MCP_SERVER_KEY}' server entry in .mcp.json", "config.server_entry"
    return entry, "", ""


def _args(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("args")
    return [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []


def _env_pin(entry: dict[str, Any]) -> str | None:
    env = entry.get("env")
    if not isinstance(env, dict):
        return None
    pin = env.get("TRACE_PROJECT")
    return pin.strip() if isinstance(pin, str) and pin.strip() else None


# ── check_config ────────────────────────────────────────────────────────────

_CONFIG_ENTRY_DEPENDENTS = ("config.command", "config.source", "config.learn_extras", "config.refresh")


def check_config(project_dir: Path) -> list[Finding]:
    """Check ``.mcp.json``: a launchable, pinned, fully-equipped trace server.

    Side effects: reads ``<project_dir>/.mcp.json`` and, for a filesystem
    ``--from`` source, stats that path.
    """
    findings: list[Finding] = []
    config, error = _load_config(project_dir)
    if config is None:
        findings.append(_bad("config.file", error or "unreadable"))
        findings.append(_unevaluated("config.server_entry", ".mcp.json is unreadable", "config.file"))
        findings += [_unevaluated(c, ".mcp.json is unreadable", "config.file") for c in _CONFIG_ENTRY_DEPENDENTS]
        return findings

    findings.append(_ok("config.file", f"{project_dir / '.mcp.json'} parses"))
    entry = _trace_entry(config)
    if entry is None:
        findings.append(
            _bad(
                "config.server_entry",
                f"no well-formed '{MCP_SERVER_KEY}' entry under mcpServers — the TRACE tools are not "
                f"configured here. Run `trace-mcp-init` in this directory.",
            )
        )
        findings += [
            _unevaluated(c, f"no '{MCP_SERVER_KEY}' server entry", "config.server_entry")
            for c in _CONFIG_ENTRY_DEPENDENTS
        ]
        return findings
    findings.append(_ok("config.server_entry", f"mcpServers.{MCP_SERVER_KEY} is present"))

    command = entry.get("command")
    findings.append(
        _ok("config.command", "launched with uvx")
        if command == "uvx"
        else _bad(
            "config.command",
            f"command is {command!r}, not 'uvx' — the supported launch path builds an isolated environment "
            "from the source checkout. A different launcher is not covered by this build's expectations.",
        )
    )

    args = _args(entry)
    findings.append(_check_source(project_dir, args))
    findings.append(_check_entrypoint(args))
    findings.append(_check_learn_extras(args))
    findings.append(_check_refresh(args))
    return findings


_DISTRIBUTION_NAME = "trace-mcp"

_ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".zip")

# uvx options that consume the following argument. Needed to tell the command
# uvx runs (the first true positional) from a flag's value — the difference
# between `... --refresh-package trace-mcp trace-mcp` (correct) and
# `... --refresh-package trace-mcp` (no command at all, but the same last word).
_UVX_VALUE_FLAGS = frozenset(
    {
        "--from",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--refresh-package",
        "--python",
        "-p",
        "--index",
        "--index-url",
        "--extra-index-url",
        "--constraint",
        "--override",
        "--exclude-newer",
        "--cache-dir",
        "--directory",
        "--project",
    }
)


def _normalized_requirement(source: str) -> str | None:
    """The PEP 503 normalized name if *source* is a bare requirement, else None.

    ``trace_mcp``, ``Trace-MCP`` and ``trace-mcp==0.5.0`` all install the same
    distribution, so an exact-string check on one spelling would wave the other
    two through.
    """
    if "://" in source or source.startswith(("git+", "~", ".", "/")) or "/" in source or os.sep in source:
        return None
    name = re.split(r"[<>=!~;\[@ ]", source, maxsplit=1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower() if name else None


def _check_source(project_dir: Path, args: list[str]) -> Finding:
    """The ``--from`` source must be something uvx can actually build.

    A relative source (this repository's own config uses ``--from .``) is
    resolved against the PROJECT directory, because that is uvx's working
    directory when the host starts the server — resolving it against the
    checker's cwd would report one project as healthy or broken depending on
    where the check was run from.
    """
    if "--from" not in args:
        return _bad("config.source", "no `--from <source>` in the launch args — uvx has nothing to build")
    idx = args.index("--from")
    if idx + 1 >= len(args):
        return _bad("config.source", "`--from` is the last argument — no source follows it")
    source = args[idx + 1]

    if "://" in source or source.startswith("git+"):
        return _ok("config.source", f"builds from the remote ref {source}")

    requirement = _normalized_requirement(source)
    if requirement == _DISTRIBUTION_NAME:
        return _bad(
            "config.source",
            f"`--from {source}` names the PyPI distribution `{_DISTRIBUTION_NAME}`, which belongs to an "
            "UNRELATED project — every server start would download and execute a third party's code "
            "(dependency confusion). Point `--from` at a TRACE checkout or a git ref instead.",
        )
    if requirement is not None:
        return _bad(
            "config.source",
            f"`--from {source}` names a package on an index rather than a checkout, and TRACE is not published "
            "under that name — this cannot resolve to this project. Use a local checkout path or a git ref.",
        )

    if source.startswith("~"):
        return _bad(
            "config.source",
            f"`--from {source}` starts with `~`, which only a shell expands. The host executes uvx directly, so "
            "the tilde is taken literally and resolved against the working directory. Write the path in full.",
        )

    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    if not candidate.exists():
        return _bad(
            "config.source",
            f"`--from {source}` does not exist (resolved to {candidate}) — every server start from this config "
            "fails. Repoint it at the TRACE checkout (or re-run `trace-mcp-init` with TRACE_SOURCE_PATH set).",
        )
    if not candidate.is_dir() and candidate.suffix not in _ARCHIVE_SUFFIXES:
        return _bad(
            "config.source",
            f"`--from {source}` is neither a project directory nor a distribution archive "
            f"({', '.join(_ARCHIVE_SUFFIXES)}) — uvx has nothing to build from it.",
        )
    return _ok("config.source", f"builds from {source}")


def _uvx_command(args: list[str]) -> str | None:
    """The command uvx would run: the first argument that is not a flag or a flag's value."""
    i = 0
    while i < len(args):
        token = args[i]
        if token in _UVX_VALUE_FLAGS:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token
    return None


def _check_entrypoint(args: list[str]) -> Finding:
    """uvx must be told which command to run, and it must be the TRACE server."""
    command = _uvx_command(args)
    if command is None:
        return _bad(
            "config.entrypoint",
            f"the launch args name no command for uvx to run — every argument is a flag or a flag's value, so "
            f"nothing starts. Append `{_DISTRIBUTION_NAME}` after the flags.",
        )
    if command != _DISTRIBUTION_NAME:
        return _bad(
            "config.entrypoint",
            f"uvx would run {command!r}, not `{_DISTRIBUTION_NAME}` — this entry does not start the TRACE server.",
        )
    return _ok("config.entrypoint", f"runs `{_DISTRIBUTION_NAME}`")


def _check_learn_extras(args: list[str]) -> Finding:
    """Missing extras is the silent 17-tool server — the symptom with no error."""
    present = _extract_with_packages(args)
    missing = [pkg for pkg in LEARN_EXTRAS if pkg not in present]
    if missing:
        return _bad(
            "config.learn_extras",
            f"missing `--with` extras {missing} — without them the trace-learn extension does not register and "
            f"the server comes up with 17 tools instead of the documented 22, with no error to explain the gap. "
            f"Add: " + " ".join(f"--with {pkg}" for pkg in missing),
        )
    return _ok("config.learn_extras", f"carries the trace-learn extras {list(LEARN_EXTRAS)} (22-tool server)")


def _check_refresh(args: list[str]) -> Finding:
    """Without a refresh flag the launcher can serve an arbitrarily old build."""
    if "--refresh-package" in args:
        idx = args.index("--refresh-package")
        target = args[idx + 1] if idx + 1 < len(args) else None
        if target != "trace-mcp":
            return _bad(
                "config.refresh",
                f"`--refresh-package {target!r}` does not name trace-mcp, so source changes are not picked up "
                "on server start. Use `--refresh-package trace-mcp`.",
            )
        return _ok("config.refresh", "refreshes trace-mcp on every server start")
    if "--refresh" in args:
        return _ok("config.refresh", "uses the blanket `--refresh` (canonical form is `--refresh-package trace-mcp`)")
    return _bad(
        "config.refresh",
        "no `--refresh-package trace-mcp` flag — uvx may serve a cached build indefinitely, so merged fixes "
        "never reach this project.",
    )


# ── check_hooks ─────────────────────────────────────────────────────────────


def check_hooks(project_dir: Path) -> list[Finding]:
    """Check the host hook deployment: files, executability, version, wiring.

    Side effects: reads ``.claude/hooks/*`` and ``.claude/settings.json`` under
    *project_dir*.
    """
    expected = ExpectedHookDeployment()
    hooks_dir = project_dir / expected.hooks_dir
    findings: list[Finding] = []

    present = [name for name in expected.hook_files if (hooks_dir / name).is_file()]
    missing = [name for name in expected.hook_files if name not in present]
    findings.append(
        _bad(
            "hooks.present",
            f"missing hook script(s) {missing} under {hooks_dir} — re-run `trace-mcp-init` in this directory.",
        )
        if missing
        else _ok("hooks.present", f"all {len(present)} shipped hook scripts are installed")
    )

    if not present:
        findings.append(_unevaluated("hooks.executable", "no hook scripts are installed", "hooks.present"))
        findings.append(_unevaluated("hooks.stamp", "no hook scripts are installed", "hooks.present"))
    else:
        findings.append(_check_hook_executable(hooks_dir, present))
        findings.append(_check_hook_stamp(hooks_dir, present, expected.version_stamp))

    findings.append(_check_unknown_hooks(hooks_dir, expected))
    findings += _check_settings(project_dir, expected)
    return findings


def _check_unknown_hooks(hooks_dir: Path, expected: ExpectedHookDeployment) -> Finding:
    """TRACE-stamped scripts this build no longer ships are left running forever.

    Only scripts carrying a `[trace-hooks vX.Y]` stamp are reported: that stamp
    identifies a copy TRACE installed, so a project's own unrelated hook scripts
    are none of this checker's business. When a release renames or drops a hook,
    the old copy stays on disk and stays registered — checking only the shipped
    names would call that deployment current.
    """
    if not hooks_dir.is_dir():
        return _unevaluated("hooks.unknown", f"{hooks_dir} does not exist", "hooks.present")
    orphans = [
        path.name
        for path in sorted(hooks_dir.glob("*.sh"))
        if path.name not in expected.hook_files
        and extract_hook_stamp(path.read_text(encoding="utf-8", errors="replace")) is not None
    ]
    if orphans:
        return _bad(
            "hooks.unknown",
            f"hook script(s) {orphans} carry a TRACE version stamp but are not shipped by this build — they are "
            "leftovers from an older release still running old semantics. Remove them.",
        )
    return _ok("hooks.unknown", "no leftover TRACE hook scripts from an older release")


def _check_hook_executable(hooks_dir: Path, present: list[str]) -> Finding:
    not_exec = [name for name in present if not os.access(hooks_dir / name, os.X_OK)]
    if not_exec:
        return _bad(
            "hooks.executable",
            f"hook script(s) {not_exec} are not executable — the host cannot run them, so the protocol "
            f"is silently unenforced here. Fix with `chmod +x {hooks_dir}/*.sh`.",
        )
    return _ok("hooks.executable", "every installed hook script is executable")


def _version_tuple(text: str) -> tuple[int, ...] | None:
    """Leading dotted integers in *text*, or None when it carries no version."""
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _which_side_is_behind(deployed: str | None, shipped: str) -> str:
    """Say which artifact is older, or decline to guess.

    Asserting "stale deployment" in both directions is wrong half the time: a
    sweep run with an older `trace-mcp` on PATH meets projects initialized from
    a newer checkout, and telling their owner to re-run init would downgrade a
    correct deployment.
    """
    left, right = _version_tuple(deployed or ""), _version_tuple(shipped)
    if left is None or right is None or left == right:
        return "differs from"
    return "is OLDER than" if left < right else "is NEWER than"


def _check_hook_stamp(hooks_dir: Path, present: list[str], stamp: str) -> Finding:
    mismatched: list[tuple[str, str]] = []
    for name in present:
        text = (hooks_dir / name).read_text(encoding="utf-8", errors="replace")
        if stamp not in text:
            mismatched.append((name, extract_hook_stamp(text) or "no stamp"))
    if not mismatched:
        return _ok("hooks.stamp", f"every installed hook carries {stamp}")
    found = sorted({found for _name, found in mismatched})
    direction = _which_side_is_behind(found[0] if len(found) == 1 else None, stamp)
    remedy = (
        "Re-run `trace-mcp-init` to refresh them."
        if direction != "is NEWER than"
        else "The CHECKER is the older side: upgrade the trace-mcp you are running rather than touching the project."
    )
    listed = ", ".join(f"{name} ({found})" for name, found in mismatched)
    return _bad(
        "hooks.stamp",
        f"installed hook version {direction} this build's {stamp}: {listed}. A copy from a different release may "
        f"detect the project differently or emit warnings that no longer apply. {remedy}",
    )


def _hook_registrations(settings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every (event, registration) pair in a settings file, malformed shapes skipped."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    pairs: list[tuple[str, dict[str, Any]]] = []
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        pairs += [(str(event), e) for e in entries if isinstance(e, dict)]
    return pairs


def _registration_commands(entry: dict[str, Any]) -> list[str]:
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return []
    return [h.get("command", "") for h in hooks if isinstance(h, dict) and isinstance(h.get("command"), str)]


def _check_settings(project_dir: Path, expected: ExpectedHookDeployment) -> list[Finding]:
    """Settings parse, every hook is wired to its own event, and the audit matcher can fire."""
    path = project_dir / expected.settings_file
    dependents = ("hooks.registered", "hooks.decision_audit_matcher")
    if not path.is_file():
        return [
            _bad("hooks.settings", f"{path} does not exist — no hook is registered, so none of them ever run."),
            *[_unevaluated(c, "settings.json is missing", "hooks.settings") for c in dependents],
        ]
    try:
        settings = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return [
            _bad("hooks.settings", f"{path} is not valid JSON ({exc}) — the host ignores it and no hook runs."),
            *[_unevaluated(c, "settings.json is unparseable", "hooks.settings") for c in dependents],
        ]
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks"), dict):
        return [
            _bad("hooks.settings", f"{path} has no 'hooks' object — nothing is registered."),
            *[_unevaluated(c, "settings.json declares no hooks", "hooks.settings") for c in dependents],
        ]

    findings = [_ok("hooks.settings", f"{path} parses and declares hooks")]
    registrations = _hook_registrations(settings)
    findings.append(_check_registrations(path, registrations, expected))
    findings.append(_check_audit_matcher(registrations, expected))
    return findings


def _events_running(registrations: list[tuple[str, dict[str, Any]]], script: str) -> set[str]:
    """Every host event under which some registration runs *script*."""
    return {
        event for event, entry in registrations if any(script in command for command in _registration_commands(entry))
    }


def _check_registrations(path: Path, registrations: list[tuple[str, dict[str, Any]]], expected) -> Finding:
    """Each shipped hook must be registered, and under ITS OWN event.

    Checking only that a script is mentioned somewhere in settings.json is the
    same defect as matching a bare tool name: a decision-audit hook registered
    under `PreToolUse` is installed, current, executable — and never fires on
    session end. The event is part of the deployment, so it is part of the check.
    """
    unregistered: list[str] = []
    misregistered: list[str] = []
    for script, event in sorted(expected.hook_events.items()):
        found = _events_running(registrations, script)
        if not found:
            unregistered.append(script)
        elif event not in found:
            misregistered.append(f"{script} (registered under {sorted(found)}, expected {event})")
    if not unregistered and not misregistered:
        return _ok("hooks.registered", "every shipped hook script is registered under its own host event")
    parts: list[str] = []
    if unregistered:
        parts.append(
            f"hook script(s) {unregistered} exist on disk but no registration in {path} runs them — an installed "
            "hook that nothing invokes is not a deployment"
        )
    if misregistered:
        parts.append(
            f"hook script(s) registered under the wrong host event: {misregistered} — a hook on the wrong event "
            "looks installed and never fires for its trigger"
        )
    return _bad("hooks.registered", ". ".join(parts) + ". Re-run `trace-mcp-init`.")


def _check_audit_matcher(registrations: list[tuple[str, dict[str, Any]]], expected) -> Finding:
    """The decision-audit hook must match the full namespaced tool name, on its own event."""
    event = expected.hook_events.get(expected.decision_audit_script)
    audit = [
        entry
        for ev, entry in registrations
        if ev == event and any(expected.decision_audit_script in cmd for cmd in _registration_commands(entry))
    ]
    if not audit:
        return _unevaluated(
            "hooks.decision_audit_matcher",
            f"the decision-audit hook is not registered under {event}",
            "hooks.registered",
        )
    wrong = [entry.get("matcher", "") for entry in audit if entry.get("matcher") != expected.decision_audit_matcher]
    if not wrong:
        return _ok("hooks.decision_audit_matcher", f"matches {expected.decision_audit_matcher} on {event}")
    return _bad(
        "hooks.decision_audit_matcher",
        f"decision-audit is registered with matcher(s) {wrong!r}, not {expected.decision_audit_matcher!r}. "
        "Claude Code matches the FULL tool name, and hosts namespace MCP tools as mcp__<server-key>__<tool>, "
        "so this matcher never fires and the attribution audit is never surfaced. Re-run `trace-mcp-init` "
        "(it rewrites the legacy short matcher in place).",
    )


# ── check_pin_coherence ─────────────────────────────────────────────────────


def _canonical(label: str) -> str | None:
    try:
        return pident.canonical_project_key(label)
    except pident.ProjectKeyError:
        return None


def check_pin_coherence(project_dir: Path) -> list[Finding]:
    """The three pin sites must exist and canonicalize to ONE project key.

    The pin file (hooks), the ``.mcp.json`` env pin (server), and the CLAUDE.md
    pin line (a model reading the repository) are three readers of one
    identity. A disagreement mints two projects out of one directory — two
    knowledge stores, two session pools — which is precisely the drift ADR-006
    closed at the schema level and this check closes at the deployment level.

    Side effects: reads the pin file, ``.mcp.json``, and ``CLAUDE.md``.
    """
    expected = ExpectedHookDeployment()
    findings: list[Finding] = []
    keys: dict[str, str] = {}

    pin_path = project_dir / expected.pin_file
    raw_pin = pin_path.read_text(encoding="utf-8", errors="replace").strip() if pin_path.is_file() else ""
    if not raw_pin:
        findings.append(
            _bad(
                "pin.trace_project_file",
                f"{pin_path} is missing or empty — the hooks fall back to guessing this project's identity "
                "from CLAUDE.md or the directory name. Re-run `trace-mcp-init`.",
            )
        )
    elif (key := _canonical(raw_pin)) is None:
        findings.append(_bad("pin.trace_project_file", f"{pin_path} holds {raw_pin!r}, which yields no usable key"))
    else:
        keys[expected.pin_file] = key
        findings.append(_ok("pin.trace_project_file", f"pins '{key}'"))

    entry, reason, upstream = _entry_or_reason(project_dir)
    if entry is None:
        findings.append(_unevaluated("pin.mcp_env", reason, upstream))
    elif (pin := _env_pin(entry)) is None:
        findings.append(
            _bad(
                "pin.mcp_env",
                "the trace server entry has no env.TRACE_PROJECT pin — an unpinned server rejects "
                "trace_start_session outright (which clients report as 'TRACE tools unavailable') and cannot "
                "fail closed on cross-project reads. Re-run `trace-mcp-init`.",
            )
        )
    elif (key := _canonical(pin)) is None:
        findings.append(_bad("pin.mcp_env", f"env.TRACE_PROJECT is {pin!r}, which yields no usable key"))
    else:
        keys[".mcp.json env.TRACE_PROJECT"] = key
        findings.append(_ok("pin.mcp_env", f"pins '{key}'"))

    claude_md = project_dir / "CLAUDE.md"
    match = (
        _PIN_LINE_RE.search(claude_md.read_text(encoding="utf-8", errors="replace")) if claude_md.is_file() else None
    )
    if match is None:
        findings.append(
            _bad(
                "pin.claude_md_line",
                f'{claude_md} declares no machine-parseable `TRACE project name: "..."` line — a model reading '
                "this repository has to guess the project. Re-run `trace-mcp-init`.",
            )
        )
    elif (key := _canonical(match.group(1))) is None:
        findings.append(
            _bad("pin.claude_md_line", f"CLAUDE.md declares {match.group(1)!r}, which yields no usable key")
        )
    else:
        keys["CLAUDE.md"] = key
        findings.append(_ok("pin.claude_md_line", f"declares '{key}'"))

    if len(keys) < 3:
        missing = [site for site in (expected.pin_file, ".mcp.json env.TRACE_PROJECT", "CLAUDE.md") if site not in keys]
        findings.append(_unevaluated("pin.coherence", f"pin site(s) {missing} unusable", "the pin.* checks above"))
        return findings

    distinct = sorted(set(keys.values()))
    findings.append(
        _bad(
            "pin.coherence",
            "the three pin sites disagree about this project's canonical key: "
            + "; ".join(f"{site} → '{key}'" for site, key in sorted(keys.items()))
            + ". One directory with two keys means two knowledge stores and two session pools. Repair with an "
            "alias in the registry (`trace-mcp identity`) — never by rewriting captured sessions.",
        )
        if len(distinct) != 1
        else _ok("pin.coherence", f"all three pin sites resolve to '{distinct[0]}'")
    )
    return findings


# ── check_served_build (--live) ─────────────────────────────────────────────


@dataclass(frozen=True)
class _LiveResult:
    """Outcome of one handshake against a project's configured server command."""

    version: str | None = None
    tool_names: tuple[str, ...] = ()
    error: str | None = None
    stderr_tail: str = ""


def check_served_build(project_dir: Path, *, live: bool = False, timeout: float | None = None) -> list[Finding]:
    """Check what the project's own configured command actually serves.

    Every other check reads files; this one is the only way to catch a
    deployment whose config, hooks, and pins are all correct while the RUNNING
    build is something else — a warm uv cache can serve a stale wheel for
    minutes after a merge, even with ``--refresh-package``.

    Side effects: with ``live=True``, **spawns the command from the project's
    ``.mcp.json``** (with that entry's ``env`` layered over the current
    environment, cwd set to *project_dir*), performs an MCP initialize +
    tools/list handshake, and terminates it. Nothing is written. With
    ``live=False`` (the default) nothing is spawned and all three checks are
    reported as not evaluated.
    """
    if not live:
        return [_unevaluated(c, "the served build is only probed when --live is given", None) for c in LIVE_CHECKS]

    entry, reason, upstream = _entry_or_reason(project_dir)
    if entry is None:
        return [_unevaluated(c, reason, upstream) for c in LIVE_CHECKS]

    expected = ExpectedServedBuild()
    result = _handshake(entry, project_dir, timeout)
    if result.error is not None:
        detail = f"could not complete an MCP handshake with the configured command: {result.error}"
        if result.stderr_tail:
            detail += f"\n        server stderr: {result.stderr_tail}"
        return [
            _bad("live.spawn", detail),
            *[_unevaluated(c, "the server did not answer", "live.spawn") for c in LIVE_CHECKS[1:]],
        ]

    findings = [_ok("live.spawn", "the configured command starts and completes an MCP handshake")]
    findings.append(_check_live_version(result.version, expected.version))
    findings.append(_check_live_tools(result.tool_names, expected.tool_total))
    return findings


def _check_live_version(found: str | None, expected: str) -> Finding:
    if found == expected:
        return _ok("live.version", f"the running server reports version {expected}")
    direction = _which_side_is_behind(found, expected)
    remedy = (
        "A warm uv cache can keep serving an old wheel even with `--refresh-package`. Remedy: "
        "`uv cache clean trace-mcp`, then restart the MCP server (in Claude Code, restart the session)."
        if direction != "is NEWER than"
        else "The CHECKER is the older side here: upgrade the trace-mcp you are running before trusting this report."
    )
    return _bad(
        "live.version",
        f"the running server reports version {found!r}, which {direction} this build's {expected!r} — the served "
        f"build is not what this checker's expectations describe. {remedy}",
    )


def _check_live_tools(found: tuple[str, ...], expected_total: int) -> Finding:
    surface = ExpectedToolSurface()
    declared = set(surface.core_tools) | set(surface.learn_tools)
    missing = sorted(declared - set(found))
    extra = sorted(set(found) - declared)
    if not missing and not extra:
        return _ok("live.tool_surface", f"serves all {expected_total} expected tools")
    detail = f"the running server serves {len(found)} tools, expected {expected_total}."
    if missing:
        detail += f" Missing: {missing}."
        if set(missing) == set(surface.learn_tools):
            detail += (
                " Exactly the trace-learn tools are absent — the launch config is missing the "
                f"`--with` extras {list(LEARN_EXTRAS)}."
            )
    if extra:
        detail += f" Unexpected: {extra}."
    return _bad("live.tool_surface", detail)


def _handshake(entry: dict[str, Any], project_dir: Path, timeout: float | None) -> _LiveResult:
    """Spawn the configured command and run initialize + tools/list over stdio.

    Returns a ``_LiveResult`` for every outcome, including failure to start:
    a broken deployment must produce a finding, never an exception.

    Side effects: starts and terminates a subprocess.
    """
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return _LiveResult(error="the trace server entry declares no command to run")
    budget = timeout if timeout is not None else float(os.environ.get(_LIVE_TIMEOUT, _DEFAULT_LIVE_TIMEOUT))
    declared_env = entry.get("env")
    overrides = declared_env if isinstance(declared_env, dict) else {}
    env = {**os.environ, **{k: str(v) for k, v in overrides.items() if isinstance(k, str)}}

    try:
        proc = subprocess.Popen(  # noqa: S603 - the command is the project's own declared server, run on request
            [command, *_args(entry)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(project_dir),
            env=env,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return _LiveResult(error=f"cannot execute {command!r}: {exc}")

    lines: queue.Queue[str | None] = queue.Queue()
    errors: list[str] = []
    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True),
        threading.Thread(target=_collect, args=(proc.stderr, errors), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + budget
    try:
        init = _request(
            proc,
            lines,
            "initialize",
            1,
            deadline,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "trace-mcp doctor", "version": ExpectedServedBuild().version},
            },
        )
        if isinstance(init, str):
            return _LiveResult(error=init, stderr_tail=_tail(errors))
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = _request(proc, lines, "tools/list", 2, deadline, {})
        if isinstance(tools, str):
            return _LiveResult(error=tools, stderr_tail=_tail(errors))
    finally:
        _terminate(proc)

    server_info = init.get("serverInfo")
    raw_version = server_info.get("version") if isinstance(server_info, dict) else None
    version = raw_version if isinstance(raw_version, str) else None
    raw_tools = tools.get("tools")
    names = (
        tuple(t["name"] for t in raw_tools if isinstance(t, dict) and isinstance(t.get("name"), str))
        if isinstance(raw_tools, list)
        else ()
    )
    return _LiveResult(version=version, tool_names=names, stderr_tail=_tail(errors))


_MAX_LINE = 4_000_000
_OVERSIZE = "\x00trace-doctor:response-line-too-long\x00"
"""Sentinel put in place of a line past the cap.

The cap exists because the probe reads output from a process it did not write.
It must sit far clear of the real protocol payload — a `tools/list` response
for the current tool surface is already tens of kilobytes and grows with every
tool and every description edit — and passing the cap must be REPORTED: a
truncated line silently fails to parse as JSON, and skipping it turns a healthy
server into a bogus timeout whose suggested remedy cannot help."""


def _pump(stream: Any, sink: queue.Queue[str | None]) -> None:
    """Feed a subprocess's stdout lines into a queue; ``None`` marks EOF."""
    try:
        for line in stream:
            sink.put(line if len(line) <= _MAX_LINE else _OVERSIZE)
    except ValueError:  # pragma: no cover - stream closed under us during teardown
        pass
    finally:
        sink.put(None)


def _collect(stream: Any, sink: list[str]) -> None:
    """Accumulate a subprocess's stderr, bounded in both line count and length."""
    try:
        for line in stream:
            if len(sink) < 200:
                sink.append(line[:_MAX_LINE])
    except ValueError:  # pragma: no cover - stream closed under us during teardown
        pass


def _tail(errors: list[str], limit: int = 800) -> str:
    return "".join(errors).strip()[-limit:]


def _send(proc: subprocess.Popen[str], message: dict[str, Any]) -> bool:
    if proc.stdin is None:
        return False
    try:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, ValueError, OSError):
        return False
    return True


def _request(
    proc: subprocess.Popen[str],
    lines: queue.Queue[str | None],
    method: str,
    request_id: int,
    deadline: float,
    params: dict[str, Any],
) -> dict[str, Any] | str:
    """Send one JSON-RPC request and return its ``result``, or an error string."""
    if not _send(proc, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}):
        return f"the server closed its input before the {method} request could be sent"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return f"no response to {method} within the timeout (raise {_LIVE_TIMEOUT} for a slow cold start)"
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            return f"no response to {method} within the timeout (raise {_LIVE_TIMEOUT} for a slow cold start)"
        if line is None:
            code = proc.poll()
            return f"the server exited (status {code}) before answering {method}"
        if line == _OVERSIZE:
            return f"the server sent a response line larger than {_MAX_LINE} bytes while answering {method}"
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # servers may emit non-protocol chatter on stdout
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            return f"{method} returned a protocol error: {message['error']}"
        result = message.get("result")
        return result if isinstance(result, dict) else {}


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Close the server down; kill it if it will not go."""
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


__all__ = [
    "CONFIG_CHECKS",
    "HOOK_CHECKS",
    "LIVE_CHECKS",
    "PIN_CHECKS",
    "check_config",
    "check_hooks",
    "check_pin_coherence",
    "check_served_build",
]

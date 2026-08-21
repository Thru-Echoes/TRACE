"""Machine-readable expectations for a correctly *deployed* TRACE project.

The unit suite proves the source tree is correct. This module states what a
correct **deployment** looks like — the tool surface a server must serve, the
hook copies a project must carry, the identity pins that must agree — so
`trace_mcp.conformance.probes` can compare a real directory against it.

Everything here is derived from a shipped artifact rather than restated:
the required hook filenames and their version stamp are read out of the
adapter's asset directory, the decision-audit matcher is built from
``adapters.base.MCP_SERVER_KEY``, the expected served version comes from
``trace_mcp.__version__``, and the expected tool total is computed from the
declared names. A restated expectation is one more copy to rot, and rot in
exactly this layer — a matcher naming a bare tool name, a hook fleet frozen at
an old release — is what the doctor exists to catch.

Exports: ``CORE_TOOLS``, ``LEARN_TOOLS``, ``ConformanceAssetError``,
``DoctorReport``, ``ExpectedHookDeployment``, ``ExpectedServedBuild``,
``ExpectedToolSurface``, ``Finding``, ``shipped_hook_files``,
``shipped_hook_stamp``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

import trace_mcp
from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.adapters.claude_code import HOOK_ASSETS_DIR, SETTINGS_TEMPLATE_PATH

# ── Tool surface ────────────────────────────────────────────────────────────

CORE_TOOLS: tuple[str, ...] = (
    "trace_start_session",
    "trace_end_session",
    "trace_log_tool_call",
    "trace_log_annotation",
    "trace_log_contribution",
    "trace_log_state_change",
    "trace_propose_decision",
    "trace_resolve_decision",
    "trace_get_session",
    "trace_get_events",
    "trace_get_decisions",
    "trace_get_decision_chain",
    "trace_search",
    "trace_export",
    "trace_list_sessions",
    "trace_project_summary",
    "trace_health_check",
)
"""The core tools every TRACE server registers, in the documented order.

This tuple is the ONE place the names live for conformance purposes; tests pin
it against both the README table and the tools the server actually registers,
so a tool added, renamed, or lost cannot pass silently. The names cannot be
introspected from the server here: importing ``trace_mcp.server`` constructs a
``JsonFileStorage`` and would make a read-only checker touch the data
directory."""

LEARN_TOOLS: tuple[str, ...] = (
    "trace_learn_recall",
    "trace_learn_add",
    "trace_learn_list",
    "trace_learn_forget",
    "trace_learn_extract",
)
"""The trace-learn extension's tools.

Kept separate from the core names because the extension is optional by
governance (ADR-003): core must work with it absent. A server missing exactly
these five is the documented symptom of a launch config without the
``--with openai/numpy/model2vec`` extras."""


# ── Shipped hook assets ─────────────────────────────────────────────────────


class ConformanceAssetError(RuntimeError):
    """The installed trace-mcp's own hook assets are missing or inconsistent.

    Raised rather than defaulted: a checker that cannot read its own reference
    copy cannot judge a deployment, and guessing would report a stale fleet as
    healthy."""


_STAMP_RE = re.compile(r"\[trace-hooks v[\d.]+\]")


def shipped_hook_files() -> tuple[str, ...]:
    """Names of the hook scripts this build ships, sorted.

    Side effects: reads the adapter's asset directory.

    Raises:
        ConformanceAssetError: the asset directory holds no hook scripts.
    """
    names = tuple(sorted(p.name for p in HOOK_ASSETS_DIR.glob("*.sh")))
    if not names:
        raise ConformanceAssetError(
            f"no hook scripts found in {HOOK_ASSETS_DIR} — this trace-mcp build is missing its adapter assets, "
            "so a deployment cannot be checked against them. Reinstall the package."
        )
    return names


def shipped_hook_stamp() -> str:
    """The single version stamp (``[trace-hooks vX.Y]``) every shipped hook emits.

    Deriving it — rather than restating it — is what makes a stamp bump
    automatically flag every deployed copy that predates it.

    Side effects: reads the adapter's hook assets.

    Raises:
        ConformanceAssetError: a hook carries no stamp, or the copies disagree.
    """
    stamps: set[str] = set()
    for name in shipped_hook_files():
        found = set(_STAMP_RE.findall((HOOK_ASSETS_DIR / name).read_text(encoding="utf-8", errors="replace")))
        if not found:
            raise ConformanceAssetError(f"shipped hook {name} carries no [trace-hooks vX.Y] version stamp")
        stamps |= found
    if len(stamps) != 1:
        raise ConformanceAssetError(f"shipped hooks disagree about their version stamp: {sorted(stamps)}")
    return stamps.pop()


def extract_hook_stamp(text: str) -> str | None:
    """The ``[trace-hooks vX.Y]`` stamp *text* carries, or None.

    Used to report what a deployed copy actually says, so a mismatch can name
    both sides instead of asserting a direction.
    """
    found = _STAMP_RE.findall(text)
    return found[0] if found else None


def shipped_hook_events() -> dict[str, str]:
    """Map each shipped hook script to the host event it must be registered under.

    Derived from the installer's own ``settings_template.json``: a hook
    registered under the wrong event is installed, executable, current, and
    completely inert — it never fires for the trigger it exists to answer. That
    is the same shape as the dead matcher, one level up, so the event belongs in
    the expectations rather than in a reviewer's memory.

    Side effects: reads the adapter's settings template.

    Raises:
        ConformanceAssetError: the template is unreadable, a shipped hook has no
            registration in it, or one hook is registered under two events.
    """
    try:
        template = json.loads(SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceAssetError(
            f"cannot read the shipped settings template {SETTINGS_TEMPLATE_PATH}: {exc}"
        ) from exc
    hooks = template.get("hooks") if isinstance(template, dict) else None
    if not isinstance(hooks, dict):
        raise ConformanceAssetError(f"{SETTINGS_TEMPLATE_PATH} declares no 'hooks' object")

    events: dict[str, set[str]] = {name: set() for name in shipped_hook_files()}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            commands = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(commands, list):
                continue
            for command in commands:
                text = command.get("command", "") if isinstance(command, dict) else ""
                for name in events:
                    if isinstance(text, str) and name in text:
                        events[name].add(str(event))

    unmapped = sorted(name for name, evs in events.items() if not evs)
    if unmapped:
        raise ConformanceAssetError(
            f"shipped hook(s) {unmapped} have no registration in {SETTINGS_TEMPLATE_PATH} — "
            "the installer would copy a script nothing ever runs"
        )
    ambiguous = sorted(name for name, evs in events.items() if len(evs) > 1)
    if ambiguous:
        raise ConformanceAssetError(f"shipped hook(s) {ambiguous} are registered under more than one event")
    return {name: next(iter(evs)) for name, evs in sorted(events.items())}


# ── Expectation models ──────────────────────────────────────────────────────


class ExpectedToolSurface(BaseModel):
    """The tools a correctly launched TRACE server serves."""

    model_config = ConfigDict(frozen=True)

    core_tools: tuple[str, ...] = Field(default=CORE_TOOLS, description="Core tool names, in documented order.")
    learn_tools: tuple[str, ...] = Field(
        default=LEARN_TOOLS, description="trace-learn extension tool names, in documented order."
    )

    @computed_field(description="Total tools a fully-equipped server serves (core + learn).")
    @property
    def total(self) -> int:
        return len(self.core_tools) + len(self.learn_tools)


class ExpectedHookDeployment(BaseModel):
    """The host-integration artifacts a correctly initialized project carries."""

    model_config = ConfigDict(frozen=True)

    hook_files: tuple[str, ...] = Field(
        default_factory=shipped_hook_files, description="Hook scripts that must exist under .claude/hooks/."
    )
    version_stamp: str = Field(
        default_factory=shipped_hook_stamp,
        description="Stamp every deployed hook must carry; an older stamp identifies a stale copy.",
    )
    decision_audit_matcher: str = Field(
        default=f"mcp__{MCP_SERVER_KEY}__trace_end_session",
        description=(
            "PostToolUse matcher for the decision-audit hook. Claude Code matches the FULL tool name, "
            "and hosts namespace MCP tools as mcp__<server-key>__<tool>, so a bare tool name never fires."
        ),
    )
    hooks_dir: str = Field(default=".claude/hooks", description="Project-relative hook script directory.")
    settings_file: str = Field(default=".claude/settings.json", description="Project-relative host settings file.")
    pin_file: str = Field(
        default=".claude/trace.project", description="Project-relative canonical-key pin file (hooks read this first)."
    )
    decision_audit_script: str = Field(
        default="decision-audit.sh", description="Hook script the decision-audit matcher must be attached to."
    )
    hook_events: dict[str, str] = Field(
        default_factory=shipped_hook_events,
        description="Host event each hook script must be registered under; a hook under the wrong event never fires.",
    )


class ExpectedServedBuild(BaseModel):
    """What the project's own configured server command must report when spawned."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(
        default=trace_mcp.__version__,
        description="Version the MCP initialize handshake must report in serverInfo.",
    )
    tool_total: int = Field(
        default_factory=lambda: ExpectedToolSurface().total, description="Tool count tools/list must return."
    )


# ── Report ──────────────────────────────────────────────────────────────────

FindingStatus = Literal["pass", "fail", "skip"]


class Finding(BaseModel):
    """One check's outcome.

    ``skip`` means *not evaluated* and always names the upstream check that
    made evaluation impossible — the doctor never stays silent about a check
    it could not run.
    """

    check: str = Field(..., description="Stable dotted check id, e.g. 'hooks.decision_audit_matcher'.")
    status: FindingStatus = Field(..., description="pass, fail, or skip (not evaluated).")
    detail: str = Field(..., description="Human-readable evidence, including the remedy on a failure.")


class DoctorReport(BaseModel):
    """Result of checking one project directory's deployed state."""

    project_dir: Path = Field(..., description="Directory that was checked.")
    findings: list[Finding] = Field(default_factory=list, description="Findings in check order.")

    @computed_field(description="True when no check failed. Skipped checks do not make a report unhealthy.")
    @property
    def ok(self) -> bool:
        return not any(f.status == "fail" for f in self.findings)

    def failures(self) -> list[Finding]:
        """The failing findings, in check order."""
        return [f for f in self.findings if f.status == "fail"]

    def render(self) -> str:
        """Human-readable report body (no trailing newline)."""
        width = max((len(f.check) for f in self.findings), default=0)
        lines = [f"doctor: {self.project_dir}"]
        lines += [f"  {f.status.upper():<4} {f.check:<{width}}  {f.detail}" for f in self.findings]
        failed = self.failures()
        lines.append(
            "doctor: clean — deployed state matches this build's expectations."
            if not failed
            else f"doctor: {len(failed)} check(s) failed"
        )
        return "\n".join(lines)


__all__ = [
    "CORE_TOOLS",
    "LEARN_TOOLS",
    "ConformanceAssetError",
    "DoctorReport",
    "ExpectedHookDeployment",
    "ExpectedServedBuild",
    "ExpectedToolSurface",
    "Finding",
    "FindingStatus",
    "extract_hook_stamp",
    "shipped_hook_events",
    "shipped_hook_files",
    "shipped_hook_stamp",
]

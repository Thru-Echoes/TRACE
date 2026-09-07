"""Claude Code adapter.

Installs TRACE enforcement into a consumer project's ``.claude/`` directory:
hook scripts under ``.claude/hooks/``, hook registrations merged into
``.claude/settings.json``, and a minimal block appended to ``CLAUDE.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from trace_mcp.adapters.base import MCP_SERVER_KEY, Adapter, Disposition, InstallResult

_ASSETS = Path(__file__).parent / "assets"
_HOOKS_SRC = _ASSETS / "hooks"
_SETTINGS_SRC = _ASSETS / "settings_template.json"
_CLAUDE_BLOCK_SRC = _ASSETS / "CLAUDE_BLOCK.md"

MARKER_START = "<!-- trace-mcp:claude-code -->"
MARKER_END = "<!-- /trace-mcp:claude-code -->"

# The opening marker is matched by PREFIX, not by the literal above, because an
# installed block may carry a stamp the literal does not have — and blocks
# installed before stamping existed carry none at all. Both must be recognised,
# or the installer would append a second block beside the first.
MARKER_START_RE = re.compile(r"<!-- trace-mcp:claude-code(?:\s+block=([0-9a-f]+))?\s*-->")


def get_block_stamp() -> str:
    """The short digest identifying the instruction block this build ships.

    Derived from the asset's own bytes, so *any* edit to the block changes the
    stamp. A hand-maintained version number would only change when someone
    remembered to change it, which is the failure this replaces: the block was
    installed once and never refreshed again, and nothing could tell.
    """
    return hashlib.sha256(_CLAUDE_BLOCK_SRC.read_bytes()).hexdigest()[:12]


def render_claude_block() -> str:
    """The block as it should appear in a project, with its stamp in the marker."""
    stamp = get_block_stamp()
    return _CLAUDE_BLOCK_SRC.read_text().replace(MARKER_START, f"<!-- trace-mcp:claude-code block={stamp} -->", 1)


def read_installed_block_stamp(text: str) -> str | None:
    """The stamp on an installed block, or None if absent or unstamped.

    Returning None for both cases is deliberate: a block predating stamping and
    a project with no block at all are equally not-this-build, and the caller
    distinguishes them by whether a marker was found.
    """
    match = MARKER_START_RE.search(text)
    return match.group(1) if match else None


HOOK_ASSETS_DIR = _HOOKS_SRC
"""The shipped hook scripts — the single source for which hooks a correct
deployment carries and which version stamp they emit. Read by
``trace_mcp.conformance`` so the deployed-state checker derives its
expectations from the same files the installer copies, instead of restating
them in a second place that can drift."""

SETTINGS_TEMPLATE_PATH = _SETTINGS_SRC
"""The shipped hook registrations — the single source for which host EVENT each
hook must be registered under. A hook registered under the wrong event never
fires for its trigger while still looking installed, so the checker derives the
event map from this file rather than restating it."""


class ClaudeCodeAdapter(Adapter):
    """Claude Code host integration."""

    name = "claude-code"

    def detect(self, directory: Path) -> bool:
        return (directory / ".claude").is_dir() or (directory / "CLAUDE.md").is_file()

    def install(self, directory: Path, *, dry_run: bool = False) -> list[InstallResult]:
        results: list[InstallResult] = []
        results.extend(_install_hooks(directory, dry_run=dry_run))
        results.append(_merge_settings(directory, dry_run=dry_run))
        results.append(_append_claude_block(directory, dry_run=dry_run))
        return results

    def validate(self, directory: Path) -> list[str]:
        errors: list[str] = []

        hooks_dir = directory / ".claude" / "hooks"
        for src in _HOOKS_SRC.glob("*.sh"):
            dst = hooks_dir / src.name
            if not dst.is_file():
                errors.append(f"missing hook script: {dst}")

        settings = directory / ".claude" / "settings.json"
        if not settings.is_file():
            errors.append(f"missing {settings}")
        else:
            try:
                data = json.loads(settings.read_text())
            except json.JSONDecodeError as exc:
                errors.append(f"{settings} is not valid JSON: {exc}")
            else:
                if "hooks" not in data:
                    errors.append(f"{settings} has no 'hooks' key")

        claude_md = directory / "CLAUDE.md"
        if not claude_md.is_file():
            errors.append(f"missing {claude_md}")
        elif MARKER_START_RE.search(claude_md.read_text()) is None:
            # Matched by pattern, not by the bare literal: an installed block
            # carries a stamp in its opening marker, and a block predating
            # stamping carries none. Both are present blocks.
            errors.append(f"{claude_md} missing TRACE marker {MARKER_START}")

        return errors


def _install_hooks(directory: Path, *, dry_run: bool) -> list[InstallResult]:
    hooks_dst = directory / ".claude" / "hooks"
    results: list[InstallResult] = []
    for src in sorted(_HOOKS_SRC.glob("*.sh")):
        dst = hooks_dst / src.name
        disposition: Disposition
        if not dst.exists():
            disposition = "installed"
        elif dst.read_bytes() != src.read_bytes():
            disposition = "updated"
        else:
            disposition = "skipped"
        if disposition != "skipped" and not dry_run:
            hooks_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            dst.chmod(0o755)
        results.append(InstallResult(path=dst, disposition=disposition))
    return results


def _hook_commands(entry: object) -> list[str] | None:
    """The command strings a hook registration runs, or None for malformed shapes.

    Settings files are user-edited JSON: a non-dict entry or a non-list/non-dict
    ``hooks`` value must be skipped (left in place), never dereferenced.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return None
    if not all(isinstance(h, dict) for h in entry["hooks"]):
        return None
    return [h.get("command", "") for h in entry["hooks"]]


def _is_superseded_trace_entry(entry: object, desired: dict) -> bool:
    """True for a registration the INSTALLER itself wrote in a legacy form.

    Same commands as the desired template entry AND exactly the matcher form
    the installer is known to have shipped (the dead bare ``trace_end_session``
    — the only form historical templates ever wrote). Entries running other
    commands, or carrying any other matcher — including a namespaced one a
    user authored for a renamed server key — are the user's: never touched.
    """
    cmds = _hook_commands(entry)
    if cmds is None or cmds != _hook_commands(desired):
        return False
    assert isinstance(entry, dict)  # narrowed by _hook_commands
    matcher = entry.get("matcher", "")
    if matcher == desired.get("matcher"):
        return False
    return matcher == "trace_end_session"


def _same_registration(entry: object, desired: dict) -> bool:
    """Same commands and same matcher — the desired registration is present,
    possibly with user-tuned fields (e.g. a raised timeout), which are respected."""
    cmds = _hook_commands(entry)
    if cmds is None or cmds != _hook_commands(desired):
        return False
    assert isinstance(entry, dict)
    return entry.get("matcher") == desired.get("matcher")


def _derive_tool_matchers(template_hooks: dict[str, list[dict]]) -> None:
    """Rewrite matchers that name a TRACE tool to the full namespaced form.

    Claude Code matchers match the FULL tool name exactly (a simple string is
    not a substring pattern), and hosts namespace MCP tools as
    ``mcp__<server-key>__<tool>`` — so the matcher must be derived from the
    same ``.mcp.json`` server key init writes. A bare tool-name matcher never
    fires: the decision-audit hook shipped dead this way.
    """
    for entries in template_hooks.values():
        for entry in entries:
            matcher = entry.get("matcher", "")
            if matcher == "trace_end_session" or matcher.endswith("__trace_end_session"):
                entry["matcher"] = f"mcp__{MCP_SERVER_KEY}__trace_end_session"


def _merge_settings(directory: Path, *, dry_run: bool) -> InstallResult:
    dst = directory / ".claude" / "settings.json"
    template = json.loads(_SETTINGS_SRC.read_text())
    template_hooks: dict[str, list[dict]] = template.get("hooks", {})
    _derive_tool_matchers(template_hooks)

    existing: dict = {}
    if dst.is_file():
        try:
            existing = json.loads(dst.read_text())
        except json.JSONDecodeError:
            existing = {}

    hooks_section = existing.setdefault("hooks", {})
    changed = False
    for event, entries in template_hooks.items():
        event_hooks = hooks_section.setdefault(event, [])
        if not isinstance(event_hooks, list):
            continue  # malformed user shape — leave it untouched
        for entry in entries:
            # Cleanup BEFORE the presence check, so a stale installer-written
            # registration (the dead short-form decision-audit matcher) is
            # removed even when the desired entry already exists beside it.
            for stale in [e for e in event_hooks if _is_superseded_trace_entry(e, entry)]:
                event_hooks.remove(stale)
                changed = True
            if any(_same_registration(e, entry) for e in event_hooks):
                continue
            event_hooks.append(entry)
            changed = True

    if not changed:
        return InstallResult(path=dst, disposition="skipped")

    disposition: Disposition = "updated" if dst.is_file() else "installed"
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(existing, indent=2) + "\n")
    return InstallResult(path=dst, disposition=disposition)


def _append_claude_block(directory: Path, *, dry_run: bool) -> InstallResult:
    """Install the TRACE block, or refresh it in place when it is out of date.

    An installed block used to be left alone forever, so every edit to the
    template reached only projects that did not yet have one — the block went
    stale everywhere and nothing reported it. The marked region is now replaced
    when its stamp differs from this build's.

    Only the marked region is touched. Everything a project wrote around it is
    preserved, which is the whole reason the markers exist: a consumer's own
    instructions live in the same file and are not ours to rewrite.
    """
    dst = directory / "CLAUDE.md"
    block = render_claude_block()

    if dst.is_file():
        existing = dst.read_text()
        match = MARKER_START_RE.search(existing)
        if match is not None:
            if match.group(1) == get_block_stamp():
                return InstallResult(path=dst, disposition="skipped")
            end = existing.find(MARKER_END, match.end())
            if end == -1:
                # An opening marker with no closing one: the block's extent is
                # unknowable, and guessing it risks eating a project's own text.
                # Left untouched; `trace-mcp doctor` reports it.
                return InstallResult(path=dst, disposition="skipped")
            if not dry_run:
                # `end` points just past the closing marker, so the replacement
                # must not carry the asset's own trailing newline: the text that
                # followed the old block already supplies it. Without this a
                # blank line accumulates on every refresh.
                dst.write_text(existing[: match.start()] + block.rstrip("\n") + existing[end + len(MARKER_END) :])
            return InstallResult(path=dst, disposition="updated")
        if not dry_run:
            sep = "\n" if existing.endswith("\n") else "\n\n"
            dst.write_text(existing + sep + block)
        return InstallResult(path=dst, disposition="updated")

    if not dry_run:
        dst.write_text(f"# Project Instructions\n\n{block}")
    return InstallResult(path=dst, disposition="installed")


__all__ = [
    "HOOK_ASSETS_DIR",
    "MARKER_END",
    "MARKER_START",
    "MARKER_START_RE",
    "SETTINGS_TEMPLATE_PATH",
    "ClaudeCodeAdapter",
    "get_block_stamp",
    "read_installed_block_stamp",
    "render_claude_block",
]

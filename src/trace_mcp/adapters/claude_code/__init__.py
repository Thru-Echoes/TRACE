"""Claude Code adapter.

Installs TRACE enforcement into a consumer project's ``.claude/`` directory:
hook scripts under ``.claude/hooks/``, hook registrations merged into
``.claude/settings.json``, and a minimal block appended to ``CLAUDE.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from trace_mcp.adapters.base import Adapter, Disposition, InstallResult

_ASSETS = Path(__file__).parent / "assets"
_HOOKS_SRC = _ASSETS / "hooks"
_SETTINGS_SRC = _ASSETS / "settings_template.json"
_CLAUDE_BLOCK_SRC = _ASSETS / "CLAUDE_BLOCK.md"

MARKER_START = "<!-- trace-mcp:claude-code -->"
MARKER_END = "<!-- /trace-mcp:claude-code -->"


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
        elif MARKER_START not in claude_md.read_text():
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


def _merge_settings(directory: Path, *, dry_run: bool) -> InstallResult:
    dst = directory / ".claude" / "settings.json"
    template = json.loads(_SETTINGS_SRC.read_text())
    template_hooks: dict[str, list[dict]] = template.get("hooks", {})

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
        for entry in entries:
            if entry not in event_hooks:
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
    dst = directory / "CLAUDE.md"
    block = _CLAUDE_BLOCK_SRC.read_text()

    if dst.is_file():
        existing = dst.read_text()
        if MARKER_START in existing:
            return InstallResult(path=dst, disposition="skipped")
        if not dry_run:
            sep = "\n" if existing.endswith("\n") else "\n\n"
            dst.write_text(existing + sep + block)
        return InstallResult(path=dst, disposition="updated")

    if not dry_run:
        dst.write_text(f"# Project Instructions\n\n{block}")
    return InstallResult(path=dst, disposition="installed")


__all__ = ["ClaudeCodeAdapter", "MARKER_END", "MARKER_START"]

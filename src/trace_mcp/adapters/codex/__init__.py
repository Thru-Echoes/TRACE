"""Codex CLI adapter — scaffold only.

Codex CLI does not (yet, as of early 2026) expose a hook model equivalent to
Claude Code's ``PreToolUse`` / ``UserPromptSubmit`` events. When it does,
mirror the Claude Code adapter. Until then, ``trace-mcp init --client=codex``
reports that the adapter is a placeholder rather than silently no-opping.

See ``README.md`` in this directory for what a full Codex adapter needs.
"""

from __future__ import annotations

from pathlib import Path

from trace_mcp.adapters.base import Adapter, InstallResult


class CodexAdapter(Adapter):
    """Placeholder Codex CLI host integration.

    ``install`` raises ``NotImplementedError`` rather than no-opping so that
    users who explicitly pick ``--client=codex`` get a clear signal that the
    work isn't done yet. ``detect`` returns ``False`` so auto-detect never
    silently selects this adapter.
    """

    name = "codex"

    def detect(self, directory: Path) -> bool:  # noqa: ARG002
        return False

    def install(self, directory: Path, *, dry_run: bool = False) -> list[InstallResult]:  # noqa: ARG002
        raise NotImplementedError(
            "Codex adapter is a placeholder. See "
            "src/trace_mcp/adapters/codex/README.md for the implementation "
            "requirements once Codex exposes tool hooks."
        )

    def validate(self, directory: Path) -> list[str]:  # noqa: ARG002
        return ["codex adapter is a placeholder — nothing to validate"]


__all__ = ["CodexAdapter"]

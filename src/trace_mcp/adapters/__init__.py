"""Host-specific adapters for TRACE protocol enforcement.

Each adapter is a pure installer that writes the files, hook scripts, and
configuration needed to make the TRACE protocol enforceable inside one host
(Claude Code, Codex, ...). Adapters contain **no runtime code** that the
core MCP server depends on — ``server.py`` never imports from here.

``trace-mcp init`` dispatches to the appropriate adapter based on auto
detection or an explicit ``--client`` flag.
"""

from __future__ import annotations

from pathlib import Path

from trace_mcp.adapters.base import Adapter
from trace_mcp.adapters.claude_code import ClaudeCodeAdapter
from trace_mcp.adapters.codex import CodexAdapter

# First match wins in auto-detection.
_REGISTRY: list[Adapter] = [
    ClaudeCodeAdapter(),
    CodexAdapter(),
]


def list_adapters() -> list[str]:
    """Return the names of all registered adapters."""
    return [a.name for a in _REGISTRY]


def get_adapter(name: str) -> Adapter:
    """Look up an adapter by name.

    Raises ``KeyError`` if no adapter matches.
    """
    for a in _REGISTRY:
        if a.name == name:
            return a
    raise KeyError(f"Unknown adapter {name!r}. Available: {list_adapters()}")


def detect_adapter(directory: Path) -> Adapter | None:
    """Auto-detect which host is configured for *directory*.

    Returns ``None`` if no adapter recognises the directory.
    """
    for a in _REGISTRY:
        if a.detect(directory):
            return a
    return None


__all__ = ["Adapter", "detect_adapter", "get_adapter", "list_adapters"]

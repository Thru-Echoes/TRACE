"""trace-mcp init — set up TRACE in an existing project directory.

Writes ``.mcp.json`` and dispatches to a host adapter (Claude Code, Codex, ...)
to install hook scripts, merge settings, and append the minimal CLAUDE.md
block. Adapters live in ``trace_mcp.adapters`` and contain no runtime code
imported by the MCP server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trace_mcp.adapters import detect_adapter, get_adapter, list_adapters
from trace_mcp.adapters.base import Adapter

_TRACE_ROOT = str(Path(__file__).resolve().parent.parent.parent)

MCP_CONFIG = {
    "trace": {
        "command": "uvx",
        "args": ["--from", _TRACE_ROOT, "--refresh-package", "trace-mcp", "trace-mcp"],
    }
}


def _write_mcp_json(project_dir: Path) -> str:
    """Write or merge the TRACE entry into ``.mcp.json``. Returns a one-line status."""
    mcp_path = project_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            config = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            config = {"mcpServers": {}}
    else:
        config = {"mcpServers": {}}

    config.setdefault("mcpServers", {})
    was_present = "trace" in config["mcpServers"]
    config["mcpServers"]["trace"] = MCP_CONFIG["trace"]

    mcp_path.write_text(json.dumps(config, indent=2) + "\n")
    return f"  {'updated' if was_present else 'wrote'}: {mcp_path}"


def _pick_adapter(project_dir: Path, explicit: str | None) -> Adapter | None:
    """Resolve which adapter to run, or None to skip host integration."""
    if explicit == "none":
        return None
    if explicit is not None:
        try:
            return get_adapter(explicit)
        except KeyError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    detected = detect_adapter(project_dir)
    if detected is None:
        print(
            "No host adapter auto-detected. Pass --client="
            f"{{{','.join(list_adapters())},none}} to pick one explicitly."
        )
    return detected


def init_project(
    directory: str | None = None,
    *,
    client: str | None = None,
    dry_run: bool = False,
) -> None:
    """Initialize TRACE in a project directory."""
    project_dir = Path(directory) if directory else Path.cwd()

    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory")
        sys.exit(1)

    print(f"Initializing TRACE in {project_dir}")

    # 1. .mcp.json (host-independent)
    if not dry_run:
        print(_write_mcp_json(project_dir))
    else:
        print(f"  [dry-run] would write: {project_dir / '.mcp.json'}")

    # 2. Host adapter
    adapter = _pick_adapter(project_dir, client)
    if adapter is None:
        print("Skipping host adapter installation.")
        return

    print(f"Installing {adapter.name} adapter...")
    try:
        results = adapter.install(project_dir, dry_run=dry_run)
    except NotImplementedError as exc:
        print(f"  {adapter.name}: {exc}")
        return

    for r in results:
        prefix = "[dry-run] " if dry_run else ""
        print(f"  {prefix}{r.disposition}: {r.path}")

    if not dry_run:
        errors = adapter.validate(project_dir)
        if errors:
            print("Validation errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)

    print()
    print("TRACE is ready. Start Claude Code in this directory and the")
    print("TRACE tools will be available automatically.")


def main() -> None:
    """CLI entry point for trace-mcp init."""
    parser = argparse.ArgumentParser(
        prog="trace-mcp init",
        description="Set up TRACE in an existing project directory.",
    )
    parser.add_argument("directory", nargs="?", default=None, help="project directory (default: cwd)")
    parser.add_argument(
        "--client",
        choices=[*list_adapters(), "none", "auto"],
        default="auto",
        help="host adapter to install (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be written without touching files",
    )
    # Allow legacy `trace-mcp init init .` invocation used by bare `trace-mcp-init`.
    args = sys.argv[1:]
    if args and args[0] == "init":
        sys.argv[1:] = args[1:]

    ns = parser.parse_args()
    client = None if ns.client == "auto" else ns.client
    init_project(ns.directory, client=client, dry_run=ns.dry_run)

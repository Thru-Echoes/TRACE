"""trace-mcp init — set up TRACE in an existing project directory.

Writes ``.mcp.json`` and dispatches to a host adapter (Claude Code, Codex, ...)
to install hook scripts, merge settings, and append the minimal CLAUDE.md
block. Adapters live in ``trace_mcp.adapters`` and contain no runtime code
imported by the MCP server.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from trace_mcp.adapters import detect_adapter, get_adapter, list_adapters
from trace_mcp.adapters.base import Adapter


class TraceSourceUnresolvedError(RuntimeError):
    """No safe ``uvx --from <X>`` source could be determined for `.mcp.json`."""


class TraceInitError(RuntimeError):
    """`.mcp.json` cannot be safely updated (e.g. the existing file is not valid JSON).

    Raised instead of silently discarding and replacing a file we could not
    parse — overwriting it would destroy sibling MCP servers and any hand-added
    configuration the user could not have intended to lose.
    """


def _resolve_trace_source() -> str:
    """Pick the value to write into `.mcp.json`'s `uvx --from <X>` argument.

    Order:
    1. ``TRACE_SOURCE_PATH`` env var (explicit override) — point at a local
       clone, e.g. `TRACE_SOURCE_PATH=/abs/path/to/TRACE`.
    2. Editable / source checkout: the repo root, which is three levels above
       this file (``src/trace_mcp/init_project.py``).
    3. Installed wheel (module under ``site-packages``): **fail closed**
       (raise ``TraceSourceUnresolvedError``). The PyPI distribution name
       ``trace-mcp`` belongs to an unrelated project, so writing
       ``uvx --from trace-mcp`` into `.mcp.json` would make the next MCP
       server start download and execute a third party's code (dependency
       confusion). Until this package is published under its own name, an
       installed copy has no safe source to infer — the user must supply
       ``TRACE_SOURCE_PATH``.
    """
    if env_override := os.environ.get("TRACE_SOURCE_PATH"):
        return env_override
    here = Path(__file__).resolve()
    if "site-packages" not in here.parts:
        return str(here.parent.parent.parent)
    raise TraceSourceUnresolvedError(
        "Cannot determine a safe source for `.mcp.json`'s `uvx --from <X>`: "
        "trace-mcp init is running from an installed wheel, and the name "
        "'trace-mcp' on PyPI belongs to an UNRELATED package — writing it "
        "would make the next MCP server start download and run third-party "
        "code (dependency confusion). Set "
        "TRACE_SOURCE_PATH=/abs/path/to/your/TRACE/clone and re-run."
    )


def _mcp_server_config() -> dict:
    """Build the `.mcp.json` ``mcpServers`` entry for TRACE.

    Resolves the ``--from`` source lazily, at write time — not at import
    time — so importing this module (tests, ``--help``) never raises;
    only actually writing `.mcp.json` can surface
    ``TraceSourceUnresolvedError``.
    """
    source = _resolve_trace_source()
    return {
        "trace": {
            "command": "uvx",
            "args": ["--from", source, "--refresh-package", "trace-mcp", "trace-mcp"],
        }
    }


def _extract_with_packages(args: object) -> list[str]:
    """Return the ordered, de-duplicated ``--with <pkg>`` names present in *args*.

    Tolerant of malformed input: a non-list ``args``, a trailing ``--with`` with
    no value, or a non-string value are all skipped rather than raising.
    """
    if not isinstance(args, list):
        return []
    packages: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args) and isinstance(args[i + 1], str):
            if args[i + 1] not in packages:
                packages.append(args[i + 1])
            i += 2
        else:
            i += 1
    return packages


def _rebuild_args(fresh_args: list[str], with_packages: list[str]) -> list[str]:
    """Canonical fresh ``args`` with preserved ``--with`` pairs inserted before the command.

    ``fresh_args`` ends with the tool name (the uvx command); preserved
    ``--with`` flags are inserted just before it so uvx option order stays valid.
    """
    if not with_packages:
        return list(fresh_args)
    with_flags: list[str] = []
    for pkg in with_packages:
        with_flags += ["--with", pkg]
    return fresh_args[:-1] + with_flags + fresh_args[-1:]


def _merge_trace_entry(existing: object, fresh: dict) -> dict:
    """Merge the freshly-built ``trace`` entry over an existing one.

    Preserves hand-added ``--with`` extras, any ``env`` block, and unknown keys
    from *existing* while adopting the freshly-resolved ``command`` and
    ``--from``/``--refresh-package`` source. If *existing* is not a dict (a
    malformed entry), the fresh entry replaces it wholesale.
    """
    if not isinstance(existing, dict):
        return fresh
    merged = dict(existing)
    merged["command"] = fresh["command"]
    merged["args"] = _rebuild_args(fresh["args"], _extract_with_packages(existing.get("args")))
    env: dict = {}
    if isinstance(existing.get("env"), dict):
        env.update(existing["env"])
    if isinstance(fresh.get("env"), dict):
        env.update(fresh["env"])
    if env:
        merged["env"] = env
    return merged


def _write_mcp_json(project_dir: Path) -> str:
    """Write or merge the TRACE entry into ``.mcp.json``. Returns a one-line status.

    Re-running is non-destructive: an existing ``trace`` entry's ``--with``
    extras, ``env`` block, and unknown keys are preserved, and sibling servers
    are left untouched. A pre-existing ``.mcp.json`` that is not valid JSON (or
    not a JSON object) is left on disk and raises ``TraceInitError`` (fail
    closed) rather than being silently discarded and replaced.
    """
    mcp_path = project_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            config = json.loads(mcp_path.read_text())
        except json.JSONDecodeError as exc:
            raise TraceInitError(
                f"{mcp_path} is not valid JSON ({exc}); refusing to overwrite it. Fix or remove the file and re-run."
            ) from exc
        if not isinstance(config, dict):
            raise TraceInitError(
                f"{mcp_path} does not contain a JSON object; refusing to overwrite it. "
                "Fix or remove the file and re-run."
            )
        servers = config.get("mcpServers")
        if servers is None:
            servers = {}
            config["mcpServers"] = servers
        elif not isinstance(servers, dict):
            raise TraceInitError(
                f"{mcp_path} has a non-object 'mcpServers'; refusing to overwrite it. "
                "Fix or remove the file and re-run."
            )
    else:
        config = {"mcpServers": {}}
        servers = config["mcpServers"]

    was_present = "trace" in servers
    fresh_entry = _mcp_server_config()["trace"]
    servers["trace"] = _merge_trace_entry(servers.get("trace"), fresh_entry) if was_present else fresh_entry

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
            f"No host adapter auto-detected. Pass --client={{{','.join(list_adapters())},none}} to pick one explicitly."
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

    # 1. .mcp.json (host-independent). A dry run resolves the source too, so
    # an unresolvable source is discovered before a real run, not during it.
    try:
        if not dry_run:
            print(_write_mcp_json(project_dir))
        else:
            entry = _mcp_server_config()["trace"]
            print(f"  [dry-run] would write: {project_dir / '.mcp.json'} (uvx --from {entry['args'][1]})")
    except (TraceSourceUnresolvedError, TraceInitError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

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

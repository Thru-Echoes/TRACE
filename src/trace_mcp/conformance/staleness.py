"""Report when the running server is older than the source it was built from.

A merge is not a deploy. An MCP server keeps the build it started with for the
life of the host session, so work merged after a server started is unreachable
from that server no matter how current the checkout is. This was not
hypothetical: every session recorded between 2026-09-03 and 2026-09-06 was
stamped with a wire version three days out of date, because the servers writing
them had been running since before the schema bump landed.

The failure is silent in the worst way. MCP argument models discard unknown
fields, so a client calling a tool with an argument a newer build added gets it
dropped without an error — the event is written without it, and the record is
indistinguishable from one where the caller chose not to supply it. Nothing in
the record says the build was old; the version stamp just quietly reads low.

This module closes that by comparing the running build's own versions against
the versions declared in the source tree the project's ``.mcp.json`` launches
from, and surfacing the difference where a reader will see it: the session-start
banner.

It is advisory and fail-soft by construction. A project with no config, a config
that does not build from a local path, an unreadable or moved source tree, or a
launch that installs from a git ref or a registry all yield no notice rather
than a warning nobody can act on. It never raises, so a session can never fail
to start because this check could not run.

Side effects: reads ``<project_dir>/.mcp.json`` and two files under the source
tree it names. Writes nothing.

Exports:
    get_stale_build_notice   advisory line for the session-start banner, or None
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from trace_mcp import __version__
from trace_mcp.adapters.base import MCP_SERVER_KEY
from trace_mcp.schema import SCHEMA_VERSION

_PACKAGE_VERSION_RE = re.compile(r"^version\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
_SCHEMA_VERSION_RE = re.compile(r"^SCHEMA_VERSION\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def _source_dir_from_config(project_dir: Path) -> Path | None:
    """The local directory this project's TRACE server builds from, if any.

    Returns None when there is no config, no trace server entry, no ``--from``
    argument, or the value is not an existing local directory — a git ref or a
    registry name is a legitimate configuration this check simply cannot compare.
    """
    config_path = project_dir / ".mcp.json"
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(config, dict):
        return None

    entry = (config.get("mcpServers") or {}).get(MCP_SERVER_KEY)
    if not isinstance(entry, dict):
        return None
    args = entry.get("args")
    if not isinstance(args, list) or "--from" not in args:
        return None

    index = args.index("--from")
    if index + 1 >= len(args):
        return None
    candidate = args[index + 1]
    if not isinstance(candidate, str) or candidate.startswith("git+"):
        return None

    try:
        source = Path(candidate).expanduser()
        return source if source.is_dir() else None
    except (OSError, ValueError):
        return None


def _declared_versions(source_dir: Path) -> tuple[str | None, str | None]:
    """The package and wire versions the source tree declares, by reading files.

    Read with regexes rather than by importing: importing a second copy of the
    package into a running server is exactly the kind of side effect a check
    that runs on every session start must not have.
    """
    package = schema = None
    try:
        text = (source_dir / "pyproject.toml").read_text(encoding="utf-8")
        match = _PACKAGE_VERSION_RE.search(text)
        package = match.group(1) if match else None
    except OSError:
        pass
    try:
        text = (source_dir / "src" / "trace_mcp" / "schema" / "session.py").read_text(encoding="utf-8")
        match = _SCHEMA_VERSION_RE.search(text)
        schema = match.group(1) if match else None
    except OSError:
        pass
    return package, schema


def get_stale_build_notice(project_dir: Path | None = None) -> str | None:
    """Advisory line naming a running build older than its source, or None.

    Inputs: *project_dir*, the directory holding the project's ``.mcp.json``
    (default: the process working directory, which is where a host launches the
    server).

    Output: a one-or-two-line notice naming both versions and the remedy, or
    None when the versions agree or the comparison cannot be made.

    Side effects: reads the config and two source files. Never raises — any
    failure yields None, because a session must never fail to start over an
    advisory check.
    """
    try:
        source_dir = _source_dir_from_config(project_dir or Path.cwd())
        if source_dir is None:
            return None

        declared_package, declared_schema = _declared_versions(source_dir)
        drift: list[str] = []
        if declared_package and declared_package != __version__:
            drift.append(f"package {__version__} running vs {declared_package} in source")
        if declared_schema and declared_schema != SCHEMA_VERSION:
            drift.append(f"wire {SCHEMA_VERSION} running vs {declared_schema} in source")
        if not drift:
            return None

        return (
            f"⚠️  This server is running an older build than its source: {'; '.join(drift)}. "
            "A merge is not a deploy — an MCP server keeps the build it started with, and a tool "
            "argument this build does not know is dropped silently rather than reported. "
            "Restart the host session to pick up the current build."
        )
    except Exception:  # noqa: BLE001 — advisory only; never block a session start
        return None

"""trace-mcp init — set up TRACE in an existing project directory.

Writes ``.mcp.json`` and dispatches to a host adapter (Claude Code, Codex, ...)
to install hook scripts, merge settings, and append the minimal CLAUDE.md
block. Adapters live in ``trace_mcp.adapters`` and contain no runtime code
imported by the MCP server.

Init is also the sanctioned place where a project's canonical identity is
minted: it enrolls the project in the alias registry and writes the three
artifacts that make every consumer agree on that identity — the
``TRACE_PROJECT`` env pin in ``.mcp.json`` (the server), a
``.claude/trace.project`` pin file (the hooks), and a machine-parseable
CLAUDE.md pin line (a model reading the repository). A running server never
mints identity; only this command does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from trace_mcp import project_identity as pident
from trace_mcp.adapters import detect_adapter, get_adapter, list_adapters
from trace_mcp.adapters.base import Adapter

# Bold-tolerant, and byte-identical in intent to the hooks' shared block: the
# absence check MUST accept the bolded form, or init appends a second pin line
# to a file that already has one.
_PIN_LINE_RE = re.compile(r'TRACE project name\**\s*:\s*"([^"]+)"')

_PIN_FILE = Path(".claude") / "trace.project"

_INIT_ACTOR = "trace-mcp init"


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


LEARN_EXTRAS: tuple[str, ...] = ("openai", "numpy", "model2vec")
"""Optional dependencies the trace-learn extension needs in order to register.

`uvx --from <src> trace-mcp` installs the base package only, so without these
the extension does not load and the server comes up with the 17 core tools
instead of the documented 22 — silently, because a missing optional dependency
is not an error. They are `--with` flags rather than a hard dependency so the
core install stays `mcp` + `pydantic`.

Installing `openai` does not enable cloud calls: LLM matching and extraction
remain opt-in behind `TRACE_LLM_ENABLED` and an API key, and the local
`model2vec` backend is the default.
"""


def _mcp_server_config(project_key: str | None = None) -> dict:
    """Build the `.mcp.json` ``mcpServers`` entry for TRACE.

    Resolves the ``--from`` source lazily, at write time — not at import
    time — so importing this module (tests, ``--help``) never raises;
    only actually writing `.mcp.json` can surface
    ``TraceSourceUnresolvedError``.

    When *project_key* is given the entry carries ``env.TRACE_PROJECT``, which
    pins the server process to one project: with a pin, cross-project reads and
    writes are refused rather than silently accepted.
    """
    source = _resolve_trace_source()
    with_flags: list[str] = []
    for pkg in LEARN_EXTRAS:
        with_flags += ["--with", pkg]
    entry: dict = {
        "command": "uvx",
        "args": ["--from", source, *with_flags, "--refresh-package", "trace-mcp", "trace-mcp"],
    }
    if project_key:
        entry["env"] = {"TRACE_PROJECT": project_key}
    return {"trace": entry}


# ── Project identity ──────────────────────────────────────────────────────


def _read_pin_line(claude_md: Path) -> str | None:
    """Return the project name declared in *claude_md*, or None."""
    if not claude_md.is_file():
        return None
    match = _PIN_LINE_RE.search(claude_md.read_text(errors="replace"))
    return match.group(1) if match else None


def get_project_label(project_dir: Path) -> str:
    """Return the best-effort display label for *project_dir*.

    Order — the same precedence the hooks use, so init and the hooks cannot
    disagree about which project a directory is: the ``.claude/trace.project``
    pin file (which makes re-running init idempotent), then a CLAUDE.md pin
    line, then the git toplevel basename, then the directory name.

    Side effects: reads files under *project_dir* and may run ``git``.
    """
    pin_file = project_dir / _PIN_FILE
    if pin_file.is_file():
        pinned = pin_file.read_text(errors="replace").strip()
        if pinned:
            return pinned
    declared = _read_pin_line(project_dir / "CLAUDE.md")
    if declared:
        return declared
    try:
        import subprocess

        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        if top:
            return Path(top).name
    except Exception:
        pass
    return project_dir.name


def get_project_key(project_dir: Path, explicit: str | None = None) -> str:
    """Resolve *project_dir* (or *explicit*) to a canonical project key.

    Pure: consults the registry for an existing alias/key match but never
    enrolls and never writes. Raises ``TraceInitError`` when the label cannot
    form a usable key or names a reserved one.
    """
    label = explicit or get_project_label(project_dir)
    try:
        key = pident.canonical_project_key(label)
    except pident.ProjectKeyError as exc:
        raise TraceInitError(f"cannot derive a project key: {exc}") from exc
    if key in pident.RESERVED_KEYS:
        raise TraceInitError(
            f"'{label}' resolves to the reserved key '{key}', which is not a project. "
            "Pass --project <name> with a real project name."
        )
    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError as exc:
        raise TraceInitError(
            f"the project registry at {pident.registry_path()} is unreadable ({exc}); "
            "refusing to guess this project's identity. Repair or move the file and re-run."
        ) from exc
    if registry is not None:
        hit = registry.resolve(label)
        if hit is not None:
            return hit
    return key


def _enroll_project(key: str, label: str) -> str:
    """Enroll *key* in the alias registry if absent. Returns a one-line status.

    Side effects: writes ``~/.trace/projects.json`` under the fail-closed
    registry lock. Enrolling here — rather than in a running server — is what
    keeps identity minting a deliberate, human-initiated act.
    """
    try:
        with pident.locked_registry() as registry:
            hit = registry.resolve(label) or registry.resolve(key)
            if hit is not None:
                return f"  registry: '{hit}' already enrolled"
            registry.projects[key] = pident.ProjectEntry(
                key=key,
                display_label=label,
                enrolled_by=_INIT_ACTOR,
            )
            registry.history.append(
                pident.RegistryChange(
                    actor=_INIT_ACTOR,
                    action="enroll",
                    details={"key": key, "label": label},
                )
            )
            return f"  registry: enrolled '{key}'"
    except pident.RegistryUnavailableError as exc:
        raise TraceInitError(
            f"the project registry at {pident.registry_path()} is unreadable ({exc}); "
            "refusing to overwrite it. Repair or move the file and re-run."
        ) from exc
    except TimeoutError as exc:
        raise TraceInitError(
            f"could not acquire the project registry lock ({exc}). Another process is "
            "writing it; retry once that finishes."
        ) from exc


def _write_pin_file(project_dir: Path, key: str) -> str:
    """Write ``.claude/trace.project`` — the hooks' highest-precedence source."""
    path = project_dir / _PIN_FILE
    existing = path.read_text(errors="replace").strip() if path.is_file() else None
    if existing == key:
        return f"  skipped: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{key}\n")
    return f"  {'updated' if existing is not None else 'wrote'}: {path}"


def _write_claude_pin_line(project_dir: Path, key: str) -> str:
    """Add the machine-parseable pin line to CLAUDE.md when none is present.

    An existing line is left exactly as written — including a bolded one, which
    the absence check accepts. Rewriting someone's declared project name is how
    identity drifts; declaring it once when it is missing is the whole job.
    """
    path = project_dir / "CLAUDE.md"
    declared = _read_pin_line(path)
    if declared is not None:
        return f"  skipped: {path} (already declares '{declared}')"
    line = f'TRACE project name: "{key}"\n'
    if path.is_file():
        existing = path.read_text()
        sep = "\n" if existing.endswith("\n") else "\n\n"
        path.write_text(existing + sep + line)
        return f"  updated: {path}"
    path.write_text(f"# Project Instructions\n\n{line}")
    return f"  wrote: {path}"


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

    Packages the fresh args already carry are dropped from *with_packages*
    rather than appended again: the canonical entry now ships the trace-learn
    extras itself, so re-running init over a config that already had them would
    otherwise duplicate every one on each run.
    """
    already = set(_extract_with_packages(fresh_args))
    extra = [pkg for pkg in with_packages if pkg not in already]
    if not extra:
        return list(fresh_args)
    with_flags: list[str] = []
    for pkg in extra:
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


def _write_mcp_json(project_dir: Path, project_key: str | None = None) -> str:
    """Write or merge the TRACE entry into ``.mcp.json``. Returns a one-line status.

    Re-running is non-destructive: an existing ``trace`` entry's ``--with``
    extras, ``env`` block, and unknown keys are preserved, and sibling servers
    are left untouched. When *project_key* is given, ``env.TRACE_PROJECT`` is
    set to it — the fresh value wins over a stale one, since init is the
    authority on a project's identity. A pre-existing ``.mcp.json`` that is not
    valid JSON (or not a JSON object) is left on disk and raises
    ``TraceInitError`` (fail closed) rather than being silently discarded and
    replaced.
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
    fresh_entry = _mcp_server_config(project_key)["trace"]
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
    project: str | None = None,
) -> None:
    """Initialize TRACE in a project directory.

    *project* overrides the derived display label. Whatever the source, the
    label is reduced to a canonical key, enrolled in the alias registry, and
    written to all three pin locations so the server, the hooks, and a model
    reading the repository resolve one identity.
    """
    project_dir = Path(directory) if directory else Path.cwd()

    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory")
        sys.exit(1)

    print(f"Initializing TRACE in {project_dir}")

    # Preflight the `.mcp.json` source before ANY write. It is the one step
    # that can fail for reasons outside this directory, and discovering that
    # after enrolling the project and writing pin files would leave it half
    # initialized.
    try:
        _resolve_trace_source()
    except TraceSourceUnresolvedError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # 1. Project identity, before anything that embeds it. Resolution is
    # read-only, so an unusable name fails before a single file is touched.
    try:
        label = project or get_project_label(project_dir)
        project_key = get_project_key(project_dir, project)
    except TraceInitError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    print(f"  project: '{project_key}'" + (f" (from label '{label}')" if label != project_key else ""))

    try:
        if not dry_run:
            print(_enroll_project(project_key, label))
            print(_write_pin_file(project_dir, project_key))
            print(_write_claude_pin_line(project_dir, project_key))
        else:
            print(f"  [dry-run] would enroll '{project_key}' in {pident.registry_path()}")
            print(f"  [dry-run] would write: {project_dir / _PIN_FILE}")
    except TraceInitError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # 2. .mcp.json (host-independent). A dry run resolves the source too, so
    # an unresolvable source is discovered before a real run, not during it.
    try:
        if not dry_run:
            print(_write_mcp_json(project_dir, project_key))
        else:
            entry = _mcp_server_config(project_key)["trace"]
            print(f"  [dry-run] would write: {project_dir / '.mcp.json'} (uvx --from {entry['args'][1]})")
    except (TraceSourceUnresolvedError, TraceInitError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # 3. Host adapter
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


def print_project_key(directory: str | None = None) -> int:
    """Print the canonical project key for *directory*. Returns a process exit code.

    The one command that answers "which project does this directory belong to?"
    the same way the server and the hooks answer it.
    """
    project_dir = Path(directory) if directory else Path.cwd()
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=sys.stderr)
        return 1
    try:
        print(get_project_key(project_dir))
    except TraceInitError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


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
    parser.add_argument(
        "--project",
        default=None,
        metavar="NAME",
        help="project name to enrol and pin (default: derived from the pin file, CLAUDE.md, git, or the directory name)",
    )
    # Allow legacy `trace-mcp init init .` invocation used by bare `trace-mcp-init`.
    args = sys.argv[1:]
    if args and args[0] == "init":
        sys.argv[1:] = args[1:]

    ns = parser.parse_args()
    client = None if ns.client == "auto" else ns.client
    init_project(ns.directory, client=client, dry_run=ns.dry_run, project=ns.project)

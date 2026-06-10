"""Validate TRACE session JSON files against the packaged JSON Schema.

This module backs the ``trace-mcp validate`` subcommand. It lives in the
package (not ``scripts/``) and loads ``schemas/trace-v0.4.json`` as package
data via importlib.resources, so validation works identically from wheel
installs, editable installs, and source checkouts. The packaged schema is
written by ``scripts/generate_schema.py`` and guarded byte-identical to the
top-level spec artifact ``schemas/trace-v0.4.json``.

Exports: ``load_schema``, ``validate_file``, ``main``.

Side effects: ``validate_file`` and ``main`` print per-file PASS/FAIL lines to
stdout (this is the CLI's user-facing output); ``main`` prints usage to stderr.
No files are written.

The ``jsonschema`` dependency is optional — install with
``pip install "trace-mcp[validate]"``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib import resources
from pathlib import Path

_SCHEMA_FILENAME = "trace-v0.4.json"


def load_schema() -> dict:
    """Load the packaged TRACE session JSON Schema as a dict.

    Reads ``trace_mcp/schemas/trace-v0.4.json`` from package data — never from
    a repo-relative path, so it works on installed packages.
    """
    text = (resources.files("trace_mcp") / "schemas" / _SCHEMA_FILENAME).read_text(encoding="utf-8")
    return json.loads(text)


def validate_file(path: Path, schema: dict) -> bool:
    """Validate a single session file against the schema. Returns True if valid.

    Side effect: prints one ``  PASS``/``  FAIL`` line to stdout.
    """
    import jsonschema

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)
        print(f"  PASS  {path}")
        return True
    except jsonschema.ValidationError as e:
        print(f"  FAIL  {path}: {e.message}")
        return False
    except json.JSONDecodeError as e:
        print(f"  FAIL  {path}: Invalid JSON: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``trace-mcp validate``. Returns a process exit code.

    Side effects: prints per-file results and a summary line to stdout; usage
    and dependency errors go to stderr.
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: trace-mcp validate <session.json> [session2.json ...]", file=sys.stderr)
        return 1

    if importlib.util.find_spec("jsonschema") is None:
        print(
            'jsonschema is required for validation: pip install "trace-mcp[validate]"',
            file=sys.stderr,
        )
        return 1

    schema = load_schema()
    results = [validate_file(Path(a), schema) for a in args]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} files valid.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

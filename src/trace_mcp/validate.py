"""Validate TRACE session JSON files against the packaged JSON Schema and the
specification's semantic rules.

This module backs the ``trace-mcp validate`` subcommand. It lives in the
package (not ``scripts/``) and loads ``schemas/trace-v0.5.json`` as package
data via importlib.resources, so validation works identically from wheel
installs, editable installs, and source checkouts. The packaged schema is
written by ``scripts/generate_schema.py`` and guarded byte-identical to the
top-level spec artifact ``schemas/trace-v0.5.json``.

Validation is two passes: the schema for structure, then the Pydantic models
for the cross-field rules of specification §4, which JSON Schema cannot state.
A document that passes the first and fails the second is reported as FAIL.

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

from pydantic import ValidationError

from trace_mcp.schema import Session

_SCHEMA_FILENAME = "trace-v0.5.json"


def load_schema() -> dict:
    """Load the packaged TRACE session JSON Schema as a dict.

    Reads ``trace_mcp/schemas/trace-v0.5.json`` from package data — never from
    a repo-relative path, so it works on installed packages.
    """
    text = (resources.files("trace_mcp") / "schemas" / _SCHEMA_FILENAME).read_text(encoding="utf-8")
    return json.loads(text)


def validate_file(path: Path, schema: dict) -> bool:
    """Validate a session file structurally, then semantically. Returns True if valid.

    Two passes, because neither alone is sufficient. The JSON Schema states the
    document's shape; the specification's §4 rules are cross-field and cannot be
    expressed in it (``resolved_by`` is declared optional, and every event-data
    field is optional, so a decision claiming ``accepted`` with no resolver and
    an event whose ``type`` contradicts its populated data both satisfy the
    schema). Loading the document through the models applies those rules, so a
    producer is told its output conforms only when it actually does.

    Side effect: prints one ``  PASS``/``  FAIL`` line to stdout.
    """
    import jsonschema

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)
        Session.model_validate(data)
        print(f"  PASS  {path}")
        return True
    except jsonschema.ValidationError as e:
        print(f"  FAIL  {path}: {e.message}")
        return False
    except ValidationError as e:
        # Semantic failure (specification §4). Report the first error with the
        # field path that carried it, so the producer knows where to look.
        first = e.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(document root)"
        print(f"  FAIL  {path}: {location}: {first['msg']}")
        return False
    except json.JSONDecodeError as e:
        print(f"  FAIL  {path}: Invalid JSON: {e}")
        return False
    except (OSError, UnicodeDecodeError) as e:
        # Unreadable input (missing file, directory, permissions, non-UTF8):
        # a per-file FAIL keeps multi-file runs going instead of a traceback.
        print(f"  FAIL  {path}: {e}")
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

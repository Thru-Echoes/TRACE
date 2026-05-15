#!/usr/bin/env python3
"""Validate TRACE session JSON files against the schema.

Usage:
    python scripts/validate_session.py ~/.trace/sessions/trace_*.json
    uv run trace-mcp validate ~/.trace/sessions/trace_*.json
"""

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("jsonschema is required: uv pip install jsonschema", file=sys.stderr)
    sys.exit(1)


SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "trace-v0.4.json"


def validate_file(path: Path, schema: dict) -> bool:
    """Validate a single session file. Returns True if valid."""
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
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(f"Usage: {sys.argv[0]} <session.json> [session2.json ...]", file=sys.stderr)
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    paths = [Path(a) for a in args]
    results = [validate_file(p, schema) for p in paths]
    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} files valid.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

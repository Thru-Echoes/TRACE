#!/usr/bin/env python3
"""Generate JSON Schema from TRACE Pydantic models.

Run: python scripts/generate_schema.py
Output (two byte-identical copies, guarded by tests/test_validate_cli.py):
    schemas/trace-v0.4.json                 — top-level spec artifact
    src/trace_mcp/schemas/trace-v0.4.json   — package data for `trace-mcp validate`

(Renamed from trace-v0.3.json in v0.4.1.)
"""

import json
from pathlib import Path

from trace_mcp.schema import SCHEMA_VERSION, Session

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIRS = [
    REPO_ROOT / "schemas",
    REPO_ROOT / "src" / "trace_mcp" / "schemas",
]


def build_schema() -> dict:
    """Build the session-document JSON Schema dict from the Pydantic models."""
    schema = Session.model_json_schema()
    schema["$id"] = "https://trace-protocol.org/schemas/trace-v0.4.json"
    schema["title"] = f"Decision Provenance Session Document v{SCHEMA_VERSION}"
    schema["description"] = (
        "JSON Schema for a session document conforming to the Decision Provenance "
        f"for AI-Assisted Workflows specification v{SCHEMA_VERSION}. "
        "See: https://trace-protocol.org/v0.3 (namespace URI kept at v0.3# per ADR 002 D6 — "
        "additive extensions are valid within the same namespace)."
    )
    return schema


def main() -> None:
    """Write the generated schema to both output locations (side effect: disk writes)."""
    payload = json.dumps(build_schema(), indent=2) + "\n"
    for out_dir in OUTPUT_DIRS:
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "trace-v0.4.json"
        out_path.write_text(payload)
        print(f"Generated: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

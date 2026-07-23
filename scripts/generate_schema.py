#!/usr/bin/env python3
"""Generate JSON Schema from TRACE Pydantic models.

Run: python scripts/generate_schema.py
Output:
    schemas/trace-v0.5.json                 — top-level spec artifact
    src/trace_mcp/schemas/trace-v0.5.json   — package data for `trace-mcp validate`
      (the two are byte-identical, guarded by tests/test_validate_cli.py)
    schemas/trace-projects-v1.json          — published registry interchange schema

(Session schema renamed from trace-v0.4.json in v0.5.0.)

The projects registry is published because interpreting a historical export
depends on the alias table: a reader holding an artifact stamped with a drifted
display label needs that mapping to know which project it belongs to. Its
``version`` field is independent of SCHEMA_VERSION — the registry format and the
session format change on different cadences.
"""

import json
from pathlib import Path

from trace_mcp.project_identity import ProjectRegistry
from trace_mcp.schema import SCHEMA_VERSION, Session

REPO_ROOT = Path(__file__).parent.parent
SESSION_SCHEMA_NAME = "trace-v0.5.json"
REGISTRY_SCHEMA_NAME = "trace-projects-v1.json"
OUTPUT_DIRS = [
    REPO_ROOT / "schemas",
    REPO_ROOT / "src" / "trace_mcp" / "schemas",
]


def build_schema() -> dict:
    """Build the session-document JSON Schema dict from the Pydantic models."""
    schema = Session.model_json_schema()
    schema["$id"] = f"https://trace-protocol.org/schemas/{SESSION_SCHEMA_NAME}"
    schema["title"] = f"Decision Provenance Session Document v{SCHEMA_VERSION}"
    schema["description"] = (
        "JSON Schema for a session document conforming to the Decision Provenance "
        f"for AI-Assisted Workflows specification v{SCHEMA_VERSION}. "
        "See: https://trace-protocol.org/v0.3 (namespace URI kept at v0.3# per ADR 002 D6 — "
        "additive extensions are valid within the same namespace)."
    )
    return schema


def build_registry_schema() -> dict:
    """Build the project-registry JSON Schema dict from the Pydantic models."""
    schema = ProjectRegistry.model_json_schema()
    schema["$id"] = f"https://trace-protocol.org/schemas/{REGISTRY_SCHEMA_NAME}"
    schema["title"] = "TRACE Project Registry v1"
    schema["description"] = (
        "JSON Schema for the TRACE project registry (~/.trace/projects.json): canonical "
        "project keys with their display labels and historical aliases. Published because "
        "interpreting a historical export depends on this alias table — an artifact stamped "
        "with a drifted display label resolves to its project only through it. The registry "
        "`version` field is independent of the session SCHEMA_VERSION."
    )
    return schema


def main() -> None:
    """Write the generated schemas to their output locations (side effect: disk writes)."""
    session_payload = json.dumps(build_schema(), indent=2) + "\n"
    for out_dir in OUTPUT_DIRS:
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / SESSION_SCHEMA_NAME
        out_path.write_text(session_payload)
        print(f"Generated: {out_path} ({out_path.stat().st_size} bytes)")

    # Registry schema is a spec artifact only: `trace-mcp validate` checks session
    # documents, so shipping it as package data would add weight for no consumer.
    registry_path = REPO_ROOT / "schemas" / REGISTRY_SCHEMA_NAME
    registry_path.write_text(json.dumps(build_registry_schema(), indent=2) + "\n")
    print(f"Generated: {registry_path} ({registry_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

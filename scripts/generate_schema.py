#!/usr/bin/env python3
"""Generate JSON Schema from TRACE Pydantic models.

Run: python scripts/generate_schema.py
Output: schemas/trace-v0.4.json (renamed from trace-v0.3.json in v0.4.1)
"""

import json
from pathlib import Path

from trace_mcp.schema import SCHEMA_VERSION, Session

SCHEMA_DIR = Path(__file__).parent.parent / "schemas"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    schema = Session.model_json_schema()
    schema["$id"] = "https://trace-protocol.org/schemas/trace-v0.4.json"
    schema["title"] = f"Decision Provenance Session Document v{SCHEMA_VERSION}"
    schema["description"] = (
        "JSON Schema for a session document conforming to the Decision Provenance "
        f"for AI-Assisted Workflows specification v{SCHEMA_VERSION}. "
        "See: https://trace-protocol.org/v0.3 (namespace URI kept at v0.3# per ADR 002 D6 — "
        "additive extensions are valid within the same namespace)."
    )

    out_path = SCHEMA_DIR / "trace-v0.4.json"
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Generated: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

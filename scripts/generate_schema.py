#!/usr/bin/env python3
"""Generate JSON Schema from TRACE Pydantic models.

Run: python scripts/generate_schema.py
Output: schemas/trace-v0.2.json
"""

import json
from pathlib import Path

from trace_mcp.schema import Session

SCHEMA_DIR = Path(__file__).parent.parent / "schemas"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    schema = Session.model_json_schema()
    schema["$id"] = "https://trace-protocol.org/schemas/trace-v0.2.json"
    schema["title"] = "TRACE Session Schema v0.2"
    schema["description"] = (
        "Schema for a TRACE (Transparent Recording of AI-assisted Collaboration Experiments) "
        "session document. One JSON file per session."
    )

    out_path = SCHEMA_DIR / "trace-v0.2.json"
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Generated: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

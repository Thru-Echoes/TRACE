"""Export tools: JSON, Markdown, and PROV JSON-LD."""

from __future__ import annotations

import json

from trace_mcp.exporters.markdown_export import export_markdown
from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.schema import Session


def export_session(session: Session, *, format: str, pretty: bool = True) -> str:
    """Export a session in the specified format.

    This is the human/artifact-facing path (written to a file, read by people or
    other tools), so JSON is indented by default — unlike the query/retrieval
    tools, whose output is emitted compact because it lands in the model's
    context window. Pass ``pretty=False`` for a compact JSON artifact (e.g. for
    size or piping).
    """
    if format == "json":
        data = session.model_dump(mode="json")
        if pretty:
            return json.dumps(data, indent=2)
        return json.dumps(data, separators=(",", ":"))
    elif format == "markdown":
        return export_markdown(session)
    elif format == "prov-jsonld":
        return export_prov_jsonld(session)
    else:
        return f"Error: Unknown format '{format}'. Use 'json', 'markdown', or 'prov-jsonld'."

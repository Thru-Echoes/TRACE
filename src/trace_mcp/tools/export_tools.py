"""Export tools: JSON, Markdown, and PROV JSON-LD."""

from __future__ import annotations

import json

from trace_mcp.exporters.markdown_export import export_markdown
from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.schema import Session


def export_session(session: Session, *, format: str) -> str:
    """Export a session in the specified format."""
    if format == "json":
        return json.dumps(session.model_dump(mode="json"), indent=2)
    elif format == "markdown":
        return export_markdown(session)
    elif format == "prov-jsonld":
        return export_prov_jsonld(session)
    else:
        return f"Error: Unknown format '{format}'. Use 'json', 'markdown', or 'prov-jsonld'."

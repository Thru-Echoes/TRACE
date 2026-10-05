"""Optional Jev advisory extension for existing TRACE provenance events."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from trace_mcp import project_identity as pident
from trace_mcp.extensions.jev.client import assess_event
from trace_mcp.extensions.jev.config import effective_config, load_config

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from trace_mcp.storage.base import TraceStorage


def register(mcp: MCPServer, storage: TraceStorage) -> None:
    """Register the read-only ``trace_jev_assess_candidate`` tool."""
    config = load_config()

    @mcp.tool()
    async def trace_jev_assess_candidate(session_id: str, event_id: str, project: str | None = None) -> str:
        """Ask Jev whether an existing TRACE event may merit durable human review.

        This read-only advisory tool accepts only decision, annotation, and
        contribution events. It never creates or mutates provenance, resolves a
        decision, or makes an admission/governance claim. Jev model confidence
        is returned separately and is never written to decision.confidence,
        which is reserved for producer-recorded measured effects.
        """
        try:
            label = pident.resolve_scoped_project(project)
            project_key = await pident.validate_project_session(storage, label, session_id)
            session = await storage.get_session(session_id)
            event = next((item for item in session.events if item.id == event_id), None)
            if event is None:
                raise ValueError(f"event {event_id!r} not found in session {session_id!r}")
            result = await assess_event(
                event,
                effective_config(config, label),
                project=label,
                project_key=project_key,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool responses are structured failures
            return json.dumps({"error": str(exc), "session_id": session_id, "event_id": event_id})

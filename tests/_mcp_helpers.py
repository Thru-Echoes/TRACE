"""Shared helpers for tests that call registered tools in process through the SDK.

Importable from any test module because pytest puts this directory on sys.path.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer


async def call_tool_json(mcp: MCPServer, tool: str, args: dict[str, Any]) -> dict:
    """Call a registered tool through ``MCPServer.call_tool`` and decode its JSON text payload.

    mcp 2 returns a ``CallToolResult``; TRACE tools put their JSON in its first text block.
    """
    result = await mcp.call_tool(tool, args)
    return json.loads(result.content[0].text)  # type: ignore[union-attr]

"""Read tools report an unreadable session as an error string, never by raising.

mcp 2 forwards only a tool's own text to the client and reports any exception
as the bare ``Error executing tool <name>``, so a read tool that lets a
``JSONDecodeError`` or ``ValidationError`` escape hides the reason from the
agent. The write tools already return ``Error: ...`` strings; these tests hold
the five read tools to the same rule, through ``MCPServer.call_tool`` so the
SDK's wrapping is part of what is tested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trace_mcp import server
from trace_mcp.storage.json_file import JsonFileStorage

SESSION_ID = "trace_20260101_corrupt"

READ_TOOL_ARGS: dict[str, dict[str, Any]] = {
    "trace_get_session": {"session_id": SESSION_ID},
    "trace_get_events": {"session_id": SESSION_ID},
    "trace_get_decisions": {"session_id": SESSION_ID},
    "trace_get_decision_chain": {"session_id": SESSION_ID, "event_id": "evt_001"},
    "trace_search": {"session_id": SESSION_ID, "query": "anything"},
}


@pytest.fixture
def corrupt_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A storage directory whose one session file is truncated JSON."""
    storage = JsonFileStorage(str(tmp_path))
    monkeypatch.setattr(server, "storage", storage)
    monkeypatch.setattr(server, "active_sessions", {})
    path = tmp_path / f"{SESSION_ID}.json"
    path.write_text('{"id": "' + SESSION_ID + '", "project": "x", "events": [', encoding="utf-8")
    return path


@pytest.mark.parametrize("tool", sorted(READ_TOOL_ARGS))
async def test_read_tool_reports_a_corrupt_session_file_as_text(tool: str, corrupt_store: Path) -> None:
    result = await server.mcp.call_tool(tool, READ_TOOL_ARGS[tool])
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Error"), f"{tool} did not return an error string: {text!r}"
    assert SESSION_ID in text or "JSON" in text or "Expecting" in text, (
        f"{tool} returned an error that does not say what was wrong: {text!r}"
    )

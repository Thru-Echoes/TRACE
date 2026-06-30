"""v0.4.1 — the MCP-wrapper-level passthrough for tool_call host + parent_event_id.

Verifier C (release-gate Round 1) caught that the v0.4.1 schema fields `host`
and `parent_event_id` on tool_call were inaccessible through the public MCP
interface — only the internal `logging_tools.log_tool_call` function exposed
them, and the `trace_log_tool_call` MCP tool in `server.py` did not pass them
through. The fields therefore existed in the schema and the spec but were
silently un-callable.

These tests run the real MCP server in a subprocess and confirm:
1. The `host` parameter on the wrapper is recognized and reaches the event.
2. The `parent_event_id` parameter on the wrapper is recognized and reaches the event.
3. Defaults preserve v0.3.0 / v0.4.0 semantics when neither is set.
4. An invalid `host` value is rejected at the schema level (Pydantic enum).
5. A dangling `parent_event_id` is reported by the referential-integrity check.

These tests use no mocks — real subprocess, real storage, real Pydantic validation.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from test_e2e_server import (  # type: ignore[import-not-found]
    _call_tool,
    _initialize_server,
    _shutdown_server,
    _start_server,
)


async def _start_session(proc: asyncio.subprocess.Process, request_id: int = 10) -> tuple[str, int]:
    response = await _call_tool(
        proc,
        "trace_start_session",
        {"project": "wrapper-test", "recall_learnings": False},
        request_id=request_id,
    )
    text = response["result"]["content"][0]["text"]
    for line in text.splitlines():
        if line.startswith("Session: "):
            return line.removeprefix("Session: ").strip(), request_id + 1
    raise AssertionError(f"No session ID in: {text}")


async def _log_tool_call(
    proc: asyncio.subprocess.Process,
    session_id: str,
    request_id: int,
    **kwargs: Any,
) -> tuple[str, int]:
    response = await _call_tool(
        proc,
        "trace_log_tool_call",
        {
            "server": kwargs.pop("server", "test-server"),
            "tool_name": kwargs.pop("tool_name", "test_tool"),
            "input": kwargs.pop("input", {"key": "value"}),
            "session_id": session_id,
            **kwargs,
        },
        request_id=request_id,
    )
    return response["result"]["content"][0]["text"], request_id + 1


def _read_session_file(sessions_dir: str, session_id: str) -> dict[str, Any]:
    path = Path(sessions_dir) / f"{session_id}.json"
    return json.loads(path.read_text())


@pytest.fixture
async def server_and_dir() -> AsyncIterator[tuple[asyncio.subprocess.Process, str]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = await _start_server(tmpdir)
        try:
            await _initialize_server(proc)
            yield proc, tmpdir
        finally:
            await _shutdown_server(proc)


class TestToolCallWrapperPassesV041Fields:
    """The trace_log_tool_call MCP wrapper must expose host and parent_event_id."""

    async def test_default_host_is_mcp(self, server_and_dir: tuple[asyncio.subprocess.Process, str]) -> None:
        """When host is not specified, the recorded event must have host='mcp'."""
        proc, tmpdir = server_and_dir
        sid, rid = await _start_session(proc)
        text, rid = await _log_tool_call(proc, sid, rid)
        assert "Logged tool call" in text
        data = _read_session_file(tmpdir, sid)
        tool_calls = [e for e in data["events"] if e["type"] == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_call"]["host"] == "mcp"

    async def test_host_internal_passes_through(self, server_and_dir: tuple[asyncio.subprocess.Process, str]) -> None:
        """host='internal' must reach the recorded event (the v0.4.1 subagent-dispatch use case)."""
        proc, tmpdir = server_and_dir
        sid, rid = await _start_session(proc)
        text, rid = await _log_tool_call(proc, sid, rid, host="internal")
        assert "Logged tool call" in text
        data = _read_session_file(tmpdir, sid)
        tool_calls = [e for e in data["events"] if e["type"] == "tool_call"]
        assert tool_calls[0]["tool_call"]["host"] == "internal"

    async def test_host_external_passes_through(self, server_and_dir: tuple[asyncio.subprocess.Process, str]) -> None:
        """host='external' must reach the recorded event (non-MCP external tools)."""
        proc, tmpdir = server_and_dir
        sid, rid = await _start_session(proc)
        _, rid = await _log_tool_call(proc, sid, rid, host="external")
        data = _read_session_file(tmpdir, sid)
        tool_calls = [e for e in data["events"] if e["type"] == "tool_call"]
        assert tool_calls[0]["tool_call"]["host"] == "external"

    async def test_parent_event_id_passes_through_for_valid_event(
        self, server_and_dir: tuple[asyncio.subprocess.Process, str]
    ) -> None:
        """parent_event_id pointing at a real in-session event must be accepted."""
        proc, tmpdir = server_and_dir
        sid, rid = await _start_session(proc)

        # First call: the parent / dispatching event.
        text1, rid = await _log_tool_call(
            proc,
            sid,
            rid,
            tool_name="dispatch_subagents",
            host="internal",
        )
        # Extract event ID from the response text ("Logged tool call: evt_001")
        parent_id = text1.split("Logged tool call: ")[-1].strip()
        assert parent_id.startswith("evt_")

        # Second call: child with parent_event_id pointing to the first.
        text2, rid = await _log_tool_call(
            proc,
            sid,
            rid,
            tool_name="security_subagent",
            host="internal",
            parent_event_id=parent_id,
        )
        assert "Logged tool call" in text2

        data = _read_session_file(tmpdir, sid)
        tool_calls = [e for e in data["events"] if e["type"] == "tool_call"]
        assert len(tool_calls) == 2
        # The child carries the parent linkage. Schema field is "name" (the MCP wrapper
        # accepts "tool_name" as a friendlier alias and maps it to "name" internally).
        child = next(e for e in tool_calls if e["tool_call"]["name"] == "security_subagent")
        assert child["tool_call"]["parent_event_id"] == parent_id

    async def test_dangling_parent_event_id_warns(self, server_and_dir: tuple[asyncio.subprocess.Process, str]) -> None:
        """A parent_event_id that does not exist in-session must surface as a referential-integrity warning."""
        proc, _tmpdir = server_and_dir
        sid, rid = await _start_session(proc)
        text, _ = await _log_tool_call(
            proc,
            sid,
            rid,
            host="internal",
            parent_event_id="evt_does_not_exist",
        )
        # The wrapper surfaces append_event's ValueError as an Error string.
        assert "Dangling reference" in text or "evt_does_not_exist" in text
        assert "parent_event_id" in text

    async def test_invalid_host_value_rejected(self, server_and_dir: tuple[asyncio.subprocess.Process, str]) -> None:
        """host must be one of 'mcp' | 'internal' | 'external'; other values are rejected by Pydantic."""
        proc, tmpdir = server_and_dir
        sid, rid = await _start_session(proc)
        text, rid = await _log_tool_call(proc, sid, rid, host="claude-code")
        # The Pydantic validation failure surfaces as an Error or includes the literal options.
        assert "Error" in text or "mcp" in text or "internal" in text
        # Confirm no event was recorded.
        data = _read_session_file(tmpdir, sid)
        tool_calls = [e for e in data["events"] if e["type"] == "tool_call"]
        assert len(tool_calls) == 0

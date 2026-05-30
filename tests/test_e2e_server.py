"""End-to-end tests for the TRACE MCP server.

These tests start the actual MCP server as a subprocess and communicate
with it via the MCP protocol (JSON-RPC over stdio), verifying that the
full server lifecycle works correctly from a consumer's perspective.

This catches issues that unit tests miss:
- Import failures in the server process
- Extension loading failures
- JSON-RPC protocol compliance
- Tool registration and invocation
- Session persistence across tool calls
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import trace_mcp

import pytest  # noqa: F401 (used by pytest-asyncio for test collection)

TRACE_ROOT = Path(__file__).parent.parent


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jsonrpc_request(method: str, params: dict[str, Any] | None = None, id: int = 1) -> str:
    """Create a JSON-RPC 2.0 request string."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "id": id,
    }
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _make_jsonrpc_notification(method: str, params: dict[str, Any] | None = None) -> str:
    """Create a JSON-RPC 2.0 notification (no id, no response expected)."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


async def _send_and_receive(
    proc: asyncio.subprocess.Process,
    message: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Send a JSON-RPC message and read the response.

    The MCP stdio transport (mcp >= 1.x) uses newline-delimited JSON.
    The server may also send notifications (no "id" field) interleaved
    with responses; we skip those and return the first response that
    has an "id" matching our request.
    """
    assert proc.stdin is not None
    assert proc.stdout is not None

    # Parse the request to get the expected response id
    request = json.loads(message)
    expected_id = request.get("id")

    # Send as newline-delimited JSON
    proc.stdin.write((message + "\n").encode("utf-8"))
    await proc.stdin.drain()

    # Read lines until we get a response (skip notifications)
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        if not line:
            raise ConnectionError("Server closed stdout")
        line_str = line.decode("utf-8").strip()
        if not line_str:
            continue
        try:
            parsed = json.loads(line_str)
        except json.JSONDecodeError:
            continue
        # Skip notifications (no "id" field) and non-matching responses
        if "id" in parsed and parsed["id"] == expected_id:
            return parsed


async def _start_server(sessions_dir: str) -> asyncio.subprocess.Process:
    """Start the TRACE MCP server as a subprocess."""
    env = os.environ.copy()
    env["TRACE_SESSIONS_DIR"] = sessions_dir
    # Import trace_mcp from src/ in the spawned server regardless of whether an
    # editable install is present/healthy (uv re-syncs can silently drop it).
    # Mirrors the pytest `pythonpath = ["src"]` config used for in-process imports.
    src = str(TRACE_ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    # Keep the server-lifecycle e2e deterministic and offline: force the
    # rule-based (BM25) matching path so the trace-learn extension never
    # triggers a lazy model2vec model download or OpenAI call on first
    # recall/extract (a multi-second blocking cold-load that intermittently
    # blew the 15s read timeout). strict_llm must be off and the API key
    # dropped, otherwise strict mode refuses the BM25 fallback and the whole
    # extension fails to register. Embedding/LLM behaviour is covered by the
    # dedicated test_learn_* suites.
    env["TRACE_EMBEDDING_BACKEND"] = "none"
    env["TRACE_LLM_ENABLED"] = "false"
    env["TRACE_STRICT_LLM"] = "false"
    env.pop("OPENAI_API_KEY", None)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "trace_mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    return proc


async def _initialize_server(proc: asyncio.subprocess.Process) -> dict[str, Any]:
    """Perform the MCP initialization handshake."""
    # Send initialize request
    init_request = _make_jsonrpc_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "trace-test", "version": "1.0.0"},
    })
    response = await _send_and_receive(proc, init_request)

    # Send initialized notification
    notif = _make_jsonrpc_notification("notifications/initialized")
    assert proc.stdin is not None
    proc.stdin.write((notif + "\n").encode("utf-8"))
    await proc.stdin.drain()

    return response


async def _call_tool(
    proc: asyncio.subprocess.Process,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: int = 1,
) -> dict[str, Any]:
    """Call an MCP tool and return the result."""
    request = _make_jsonrpc_request("tools/call", {
        "name": tool_name,
        "arguments": arguments,
    }, id=request_id)
    return await _send_and_receive(proc, request)


async def _list_tools(proc: asyncio.subprocess.Process, request_id: int = 1) -> dict[str, Any]:
    """List available MCP tools."""
    request = _make_jsonrpc_request("tools/list", {}, id=request_id)
    return await _send_and_receive(proc, request)


async def _shutdown_server(proc: asyncio.subprocess.Process) -> None:
    """Gracefully shut down the server."""
    assert proc.stdin is not None
    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


# ── Server Startup Tests ────────────────────────────────────────────────────


class TestServerStartup:
    """Tests that the server process starts correctly."""

    async def test_server_starts_without_error(self) -> None:
        """The server should start and respond to initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                response = await _initialize_server(proc)
                assert "result" in response, f"Init failed: {response}"
                assert "serverInfo" in response["result"]
            finally:
                await _shutdown_server(proc)

    async def test_server_reports_correct_info(self) -> None:
        """Server info should contain the correct name and version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                response = await _initialize_server(proc)
                server_info = response["result"]["serverInfo"]
                assert server_info["name"] == "trace"
            finally:
                await _shutdown_server(proc)

    async def test_server_lists_core_tools(self) -> None:
        """The server should register all core TRACE tools."""
        expected_tools = {
            "trace_start_session",
            "trace_end_session",
            "trace_log_tool_call",
            "trace_log_annotation",
            "trace_log_contribution",
            "trace_log_state_change",
            "trace_propose_decision",
            "trace_resolve_decision",
            "trace_get_session",
            "trace_get_events",
            "trace_get_decisions",
            "trace_get_decision_chain",
            "trace_search",
            "trace_list_sessions",
            "trace_export",
            "trace_project_summary",
            "trace_health_check",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                response = await _list_tools(proc, request_id=2)
                assert "result" in response, f"tools/list failed: {response}"
                tool_names = {t["name"] for t in response["result"]["tools"]}
                missing = expected_tools - tool_names
                assert not missing, f"Missing core tools: {missing}"
            finally:
                await _shutdown_server(proc)

    async def test_server_loads_extensions(self) -> None:
        """Extensions (learn) should register their tools."""
        extension_tool_prefixes = ["trace_learn_"]

        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                response = await _list_tools(proc, request_id=2)
                tool_names = {t["name"] for t in response["result"]["tools"]}

                for prefix in extension_tool_prefixes:
                    matching = [t for t in tool_names if t.startswith(prefix)]
                    assert len(matching) > 0, (
                        f"No tools found with prefix '{prefix}'. "
                        f"Extension may have failed to load. "
                        f"Available tools: {sorted(tool_names)}"
                    )
            finally:
                await _shutdown_server(proc)


# ── Full Session Lifecycle E2E ───────────────────────────────────────────────


class TestSessionLifecycleE2E:
    """End-to-end test of a complete TRACE session via the MCP protocol."""

    async def test_full_session_lifecycle(self) -> None:
        """Start session -> log events -> query -> end session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                # 1. Start a session
                response = await _call_tool(proc, "trace_start_session", {
                    "project": "e2e-test",
                    "description": "End-to-end test session",
                }, request_id=req_id)
                req_id += 1

                assert "result" in response, f"start_session failed: {response}"
                content = response["result"]["content"]
                assert len(content) > 0
                result_text = content[0]["text"]
                assert "TRACE audit logging is now active" in result_text

                # Extract session ID
                session_id = result_text.split("Session: ")[1].split("\n")[0]
                assert session_id.startswith("trace_")

                # 2. Propose a decision
                response = await _call_tool(proc, "trace_propose_decision", {
                    "session_id": session_id,
                    "description": "Use cosine distance metric",
                    "proposed_by_type": "ai",
                    "proposed_by_id": "test-ai",
                    "rationale": "Standard for text embedding comparison",
                    "suggestion_type": "proactive",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "evt_001" in result_text

                # 3. Resolve the decision
                response = await _call_tool(proc, "trace_resolve_decision", {
                    "event_id": "evt_001",
                    "session_id": session_id,
                    "disposition": "accepted",
                    "resolved_by_type": "human",
                    "resolved_by_id": "test-researcher",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "accepted" in result_text

                # 4. Log a contribution
                response = await _call_tool(proc, "trace_log_contribution", {
                    "session_id": session_id,
                    "description": "Implemented distance calculation",
                    "direction": "human",
                    "execution": "ai",
                    "artifact": "src/distances.py",
                    "related_decision_ids": ["evt_001"],
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "evt_002" in result_text

                # 5. Log an annotation
                response = await _call_tool(proc, "trace_log_annotation", {
                    "session_id": session_id,
                    "category": "learning",
                    "content": "Cosine distance is invariant to vector magnitude",
                    "tags": ["methodology", "distance"],
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "evt_003" in result_text

                # 6. Log a state change
                response = await _call_tool(proc, "trace_log_state_change", {
                    "session_id": session_id,
                    "description": "Switched to GPU compute",
                    "field": "environment.compute",
                    "old_value": "cpu",
                    "new_value": "gpu",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "evt_004" in result_text

                # 7. Query decisions
                response = await _call_tool(proc, "trace_get_decisions", {
                    "session_id": session_id,
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                decisions = json.loads(result_text)
                assert len(decisions) == 1
                assert decisions[0]["decision"]["disposition"] == "accepted"

                # 8. Search events
                response = await _call_tool(proc, "trace_search", {
                    "session_id": session_id,
                    "query": "cosine",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                results = json.loads(result_text)
                assert len(results) >= 1

                # 9. Get session summary
                response = await _call_tool(proc, "trace_get_session", {
                    "session_id": session_id,
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                summary = json.loads(result_text)
                assert summary["id"] == session_id
                assert summary["metadata"]["project"] == "e2e-test"

                # 10. Health check
                response = await _call_tool(proc, "trace_health_check", {
                    "project": "e2e-test",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                health = json.loads(result_text)
                assert health["version"] == trace_mcp.__version__
                assert health["session_count"] == 1

                # 11. End session
                response = await _call_tool(proc, "trace_end_session", {
                    "session_id": session_id,
                    "summary": "E2E test completed successfully",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "Session ended" in result_text
                assert "4 events" in result_text

                # 12. Verify session file was persisted
                session_file = Path(tmpdir) / f"{session_id}.json"
                assert session_file.exists(), "Session file should be persisted to disk"
                session_data = json.loads(session_file.read_text())
                assert session_data["id"] == session_id
                assert session_data["status"] == "completed"
                assert len(session_data["events"]) == 4

            finally:
                await _shutdown_server(proc)

    async def test_export_formats(self) -> None:
        """Test that all export formats work via MCP."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                # Create a session with one event
                response = await _call_tool(proc, "trace_start_session", {
                    "project": "export-test",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                await _call_tool(proc, "trace_log_annotation", {
                    "session_id": session_id,
                    "category": "learning",
                    "content": "Test annotation for export",
                }, request_id=req_id)
                req_id += 1

                await _call_tool(proc, "trace_end_session", {
                    "session_id": session_id,
                }, request_id=req_id)
                req_id += 1

                # Test JSON export
                response = await _call_tool(proc, "trace_export", {
                    "session_id": session_id,
                    "format": "json",
                }, request_id=req_id)
                req_id += 1
                json_export = response["result"]["content"][0]["text"]
                parsed = json.loads(json_export)
                assert parsed["id"] == session_id

                # Test Markdown export
                response = await _call_tool(proc, "trace_export", {
                    "session_id": session_id,
                    "format": "markdown",
                }, request_id=req_id)
                req_id += 1
                md_export = response["result"]["content"][0]["text"]
                assert "# TRACE Session:" in md_export

                # Test PROV-JSONLD export
                response = await _call_tool(proc, "trace_export", {
                    "session_id": session_id,
                    "format": "prov-jsonld",
                }, request_id=req_id)
                req_id += 1
                prov_export = response["result"]["content"][0]["text"]
                prov_data = json.loads(prov_export)
                assert "@context" in prov_data

            finally:
                await _shutdown_server(proc)


# ── Error Handling E2E ───────────────────────────────────────────────────────


class TestErrorHandlingE2E:
    """Test that the server handles errors gracefully."""

    async def test_nonexistent_session(self) -> None:
        """Querying a nonexistent session should return an error message, not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)

                response = await _call_tool(proc, "trace_get_session", {
                    "session_id": "nonexistent_session_id",
                }, request_id=2)

                result_text = response["result"]["content"][0]["text"]
                assert "error" in result_text.lower() or "not found" in result_text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_end_nonexistent_session(self) -> None:
        """Ending a nonexistent session should return an error message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)

                response = await _call_tool(proc, "trace_end_session", {
                    "session_id": "nonexistent",
                }, request_id=2)

                result_text = response["result"]["content"][0]["text"]
                assert "error" in result_text.lower() or "not found" in result_text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_resolve_nonexistent_decision(self) -> None:
        """Resolving a nonexistent decision should return error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                # Create a valid session first
                response = await _call_tool(proc, "trace_start_session", {
                    "project": "error-test",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                # Try to resolve nonexistent decision
                response = await _call_tool(proc, "trace_resolve_decision", {
                    "event_id": "evt_999",
                    "session_id": session_id,
                    "disposition": "accepted",
                    "resolved_by_type": "human",
                    "resolved_by_id": "researcher",
                }, request_id=req_id)
                req_id += 1

                result_text = response["result"]["content"][0]["text"]
                assert "not found" in result_text.lower() or "error" in result_text.lower()
            finally:
                await _shutdown_server(proc)


# ── Session Persistence E2E ──────────────────────────────────────────────────


class TestSessionPersistenceE2E:
    """Test that sessions survive server restarts."""

    async def test_session_survives_restart(self) -> None:
        """A session created in one server process should be loadable in another."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Start server 1, create a session with events
            proc1 = await _start_server(tmpdir)
            try:
                await _initialize_server(proc1)
                req_id = 2

                response = await _call_tool(proc1, "trace_start_session", {
                    "project": "persistence-test",
                    "description": "Testing persistence",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                await _call_tool(proc1, "trace_propose_decision", {
                    "session_id": session_id,
                    "description": "Test decision for persistence",
                    "proposed_by_type": "ai",
                    "proposed_by_id": "test",
                }, request_id=req_id)
                req_id += 1

                await _call_tool(proc1, "trace_end_session", {
                    "session_id": session_id,
                }, request_id=req_id)
                req_id += 1

            finally:
                await _shutdown_server(proc1)

            # Start server 2, load the session
            proc2 = await _start_server(tmpdir)
            try:
                await _initialize_server(proc2)

                response = await _call_tool(proc2, "trace_get_session", {
                    "session_id": session_id,
                }, request_id=2)

                result_text = response["result"]["content"][0]["text"]
                session_data = json.loads(result_text)
                assert session_data["id"] == session_id
                assert session_data["metadata"]["project"] == "persistence-test"
                assert session_data["event_count"] == 1

                # Also verify list_sessions finds it
                response = await _call_tool(proc2, "trace_list_sessions", {
                    "project": "persistence-test",
                }, request_id=3)

                result_text = response["result"]["content"][0]["text"]
                sessions = json.loads(result_text)
                assert len(sessions) == 1
                assert sessions[0]["id"] == session_id

            finally:
                await _shutdown_server(proc2)


# ── uv run Integration ──────────────────────────────────────────────────────


class TestUvxIntegration:
    """Test that `uvx` can execute trace-mcp correctly.

    This tests the exact command that .mcp.json uses.
    """

    def test_uvx_import(self) -> None:
        """uvx should be able to build and import trace_mcp."""
        result = subprocess.run(
            [
                "uvx",
                "--from", str(TRACE_ROOT),
                "--refresh-package", "trace-mcp",
                "--with", "trace-mcp",
                "python", "-c",
                "import trace_mcp; print(trace_mcp.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"uvx import failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "0." in result.stdout  # Version should start with 0.

    def test_uvx_entry_point_resolves(self) -> None:
        """uvx should resolve the trace-mcp entry point."""
        result = subprocess.run(
            [
                "uvx",
                "--from", str(TRACE_ROOT),
                "--refresh-package", "trace-mcp",
                "--with", "trace-mcp",
                "python", "-c",
                "from trace_mcp.server import main; print('entry point OK')",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"uvx entry point check failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "entry point OK" in result.stdout

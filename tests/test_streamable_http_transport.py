"""Tests for the streamable-http transport option (`trace-mcp --transport streamable-http`).

Two layers:

1. Unit tests for `_parse_server_args` — defaults, explicit flags, and the
   fail-loud exits on unknown transports or flags (a typo'd invocation must
   never silently start a stdio server that an HTTP consumer then waits on).
2. One end-to-end test that starts the real server as a subprocess on an
   ephemeral port and drives a full session lifecycle (start → annotate → end)
   through the `mcp` Streamable HTTP client, then asserts the session JSON
   landed in the isolated sessions dir. This is the consumer's-eye check for
   agent runtimes that reach MCP services over HTTP — registry-style consumers
   that connect to a running endpoint instead of spawning a stdio process.

Side effects: the E2E test binds a loopback TCP port and spawns one
subprocess; all storage goes to pytest tmp dirs via the TRACE_* env overrides
(the same isolation contract tests/conftest.py documents).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from trace_mcp.server import _parse_server_args

TRACE_ROOT = Path(__file__).parent.parent

# Same split rationale as tests/test_e2e_server.py: subprocess cold start
# (interpreter boot + imports + extension discovery) dominates, so it gets its
# own generous, overridable budget; individual requests stay on a short one.
_STARTUP_TIMEOUT = float(os.environ.get("TRACE_E2E_HANDSHAKE_TIMEOUT", "90"))
_REQUEST_TIMEOUT = 15.0


# ── Unit: argument parsing ───────────────────────────────────────────────────


class TestParseServerArgs:
    def test_defaults_are_stdio(self) -> None:
        args = _parse_server_args([])
        assert args.transport == "stdio"
        assert args.host == "127.0.0.1"
        assert args.port == 8765

    def test_streamable_http_with_host_and_port(self) -> None:
        args = _parse_server_args(
            ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"]
        )
        assert args.transport == "streamable-http"
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_unknown_transport_exits_loud(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _parse_server_args(["--transport", "sse"])
        assert excinfo.value.code == 2

    def test_unknown_flag_exits_loud(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _parse_server_args(["--prot", "8080"])
        assert excinfo.value.code == 2

    def test_non_integer_port_exits_loud(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _parse_server_args(["--port", "not-a-port"])
        assert excinfo.value.code == 2


# ── E2E: full lifecycle over Streamable HTTP ─────────────────────────────────


def _free_port() -> int:
    """Return an ephemeral loopback port. Side effects: briefly binds it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _stderr_tail(proc: asyncio.subprocess.Process, limit: int = 4000) -> str:
    """Kill *proc* and return the tail of its stderr (failure paths only)."""
    if proc.stderr is None:
        return ""
    if proc.returncode is None:
        proc.kill()
    try:
        data = await asyncio.wait_for(proc.stderr.read(), timeout=5.0)
    except (TimeoutError, ProcessLookupError):
        return ""
    return data.decode("utf-8", errors="replace").strip()[-limit:]


async def _wait_for_port(port: int, proc: asyncio.subprocess.Process) -> None:
    """Poll until *port* accepts a TCP connection or the server dies.

    Raises with the server's stderr tail on early exit or timeout, so a failed
    boot is attributable instead of surfacing as a bare TimeoutError.
    """
    deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT
    while True:
        if proc.returncode is not None:
            tail = await _stderr_tail(proc)
            raise ConnectionError(
                f"Server exited with {proc.returncode} before listening on {port}. "
                + (f"Stderr:\n{tail}" if tail else "No stderr captured.")
            )
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            if asyncio.get_event_loop().time() > deadline:
                tail = await _stderr_tail(proc)
                raise TimeoutError(
                    f"Port {port} not accepting connections within {_STARTUP_TIMEOUT}s. "
                    + (f"Stderr:\n{tail}" if tail else "No stderr captured.")
                ) from None
            await asyncio.sleep(0.2)


def _result_text(result: object) -> str:
    """Concatenate the text parts of an MCP tool-call result."""
    parts: list[str] = []
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


async def test_streamable_http_full_session_lifecycle(tmp_path: Path) -> None:
    """Boot `--transport streamable-http`, run start → annotate → end over HTTP,
    and verify the session JSON (with the annotation) landed on disk."""
    port = _free_port()
    sessions_dir = tmp_path / "sessions"
    env = os.environ.copy()
    env.update(
        {
            "TRACE_SESSIONS_DIR": str(sessions_dir),
            "TRACE_KNOWLEDGE_DIR": str(tmp_path / "knowledge"),
            "TRACE_SCRATCHPAD_DIR": str(tmp_path / "scratchpad"),
            "TRACE_EGRESS_LOG": str(tmp_path / "egress.jsonl"),
            "TRACE_REGISTRY_PATH": str(tmp_path / "projects.json"),
            "TRACE_PROJECT": "http-transport-e2e",
        }
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "trace_mcp.server",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
        cwd=str(TRACE_ROOT),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _wait_for_port(port, proc)

        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=_REQUEST_TIMEOUT)

                tools = await asyncio.wait_for(session.list_tools(), timeout=_REQUEST_TIMEOUT)
                tool_names = {tool.name for tool in tools.tools}
                assert "trace_start_session" in tool_names
                assert "trace_log_annotation" in tool_names
                assert "trace_end_session" in tool_names

                start_result = await asyncio.wait_for(
                    session.call_tool(
                        "trace_start_session",
                        {"description": "streamable-http transport e2e"},
                    ),
                    timeout=_REQUEST_TIMEOUT,
                )
                start_text = _result_text(start_result)
                match = re.search(r"Session: (trace_\S+)", start_text)
                assert match, f"No session id in start result: {start_text!r}"
                session_id = match.group(1)

                annotate_result = await asyncio.wait_for(
                    session.call_tool(
                        "trace_log_annotation",
                        {
                            "category": "observation",
                            "content": "logged over streamable-http",
                            "session_id": session_id,
                        },
                    ),
                    timeout=_REQUEST_TIMEOUT,
                )
                assert "evt_" in _result_text(annotate_result)

                end_result = await asyncio.wait_for(
                    session.call_tool("trace_end_session", {"session_id": session_id}),
                    timeout=_REQUEST_TIMEOUT,
                )
                assert session_id in _result_text(end_result) or "ended" in _result_text(
                    end_result
                )

        session_files = sorted(sessions_dir.glob("trace_*.json"))
        assert session_files, f"No session file written under {sessions_dir}"
        payload = json.loads(session_files[-1].read_text())
        assert payload["id"] == session_id
        assert payload["status"] == "completed"
        assert "logged over streamable-http" in session_files[-1].read_text()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

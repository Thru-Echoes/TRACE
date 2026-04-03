"""End-to-end tests for TRACE decision guard rails via MCP protocol.

These tests start the actual MCP server as a subprocess and verify
that guard rail warnings appear in JSON-RPC responses.
"""

from __future__ import annotations

import tempfile

from test_e2e_server import (
    _call_tool,
    _initialize_server,
    _shutdown_server,
    _start_server,
)


class TestDecisionGuardsE2E:
    """E2E tests verifying guard rail warnings appear in MCP responses."""

    async def test_e2e_self_resolution_warning(self) -> None:
        """AI proposes + AI resolves -> warning in JSON-RPC response."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                # Start session
                response = await _call_tool(proc, "trace_start_session", {
                    "project": "guard-e2e",
                    "description": "Guard rail E2E test",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                # Propose (AI)
                response = await _call_tool(proc, "trace_propose_decision", {
                    "session_id": session_id,
                    "description": "Use method X",
                    "proposed_by_type": "ai",
                    "proposed_by_id": "test-ai",
                }, request_id=req_id)
                req_id += 1

                # Resolve (AI — self-resolution)
                response = await _call_tool(proc, "trace_resolve_decision", {
                    "event_id": "evt_001",
                    "session_id": session_id,
                    "disposition": "accepted",
                    "resolved_by_type": "ai",
                    "resolved_by_id": "test-ai",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                assert "AI resolved its own proposal" in result_text

            finally:
                await _shutdown_server(proc)

    async def test_e2e_session_end_unresolved(self) -> None:
        """2 proposed, 0 resolved -> audit reports 2 unresolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                response = await _call_tool(proc, "trace_start_session", {
                    "project": "guard-e2e",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                # Propose 2 decisions, resolve none
                for i in range(2):
                    await _call_tool(proc, "trace_propose_decision", {
                        "session_id": session_id,
                        "description": f"Unresolved decision {i}",
                        "proposed_by_type": "ai",
                        "proposed_by_id": "test-ai",
                    }, request_id=req_id)
                    req_id += 1

                # End session
                response = await _call_tool(proc, "trace_end_session", {
                    "session_id": session_id,
                    "summary": "test unresolved",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                assert "Unresolved decisions: 2" in result_text

            finally:
                await _shutdown_server(proc)

    async def test_e2e_session_end_audit_self_resolutions(self) -> None:
        """3 self-resolutions -> audit reports them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                response = await _call_tool(proc, "trace_start_session", {
                    "project": "guard-e2e",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                for i in range(3):
                    await _call_tool(proc, "trace_propose_decision", {
                        "session_id": session_id,
                        "description": f"Self-resolved {i}",
                        "proposed_by_type": "ai",
                        "proposed_by_id": "test-ai",
                    }, request_id=req_id)
                    req_id += 1

                    await _call_tool(proc, "trace_resolve_decision", {
                        "event_id": f"evt_{i + 1:03d}",
                        "session_id": session_id,
                        "disposition": "accepted",
                        "resolved_by_type": "ai",
                        "resolved_by_id": "test-ai",
                    }, request_id=req_id)
                    req_id += 1

                response = await _call_tool(proc, "trace_end_session", {
                    "session_id": session_id,
                    "summary": "self-resolution test",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                assert "AI self-resolutions: 3" in result_text

            finally:
                await _shutdown_server(proc)

    async def test_e2e_proper_workflow_clean(self) -> None:
        """AI proposes -> human resolves -> end -> no guard rail warnings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                req_id = 2

                response = await _call_tool(proc, "trace_start_session", {
                    "project": "guard-e2e",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                session_id = result_text.split("Session: ")[1].split("\n")[0]

                await _call_tool(proc, "trace_propose_decision", {
                    "session_id": session_id,
                    "description": "Use cosine distance",
                    "proposed_by_type": "ai",
                    "proposed_by_id": "test-ai",
                    "suggestion_type": "proactive",
                }, request_id=req_id)
                req_id += 1

                await _call_tool(proc, "trace_resolve_decision", {
                    "event_id": "evt_001",
                    "session_id": session_id,
                    "disposition": "accepted",
                    "resolved_by_type": "human",
                    "resolved_by_id": "researcher",
                }, request_id=req_id)
                req_id += 1

                response = await _call_tool(proc, "trace_end_session", {
                    "session_id": session_id,
                    "summary": "clean workflow",
                }, request_id=req_id)
                req_id += 1
                result_text = response["result"]["content"][0]["text"]
                assert "Unresolved decisions" not in result_text
                assert "AI self-resolutions" not in result_text
                assert "Unlinked corrections" not in result_text

            finally:
                await _shutdown_server(proc)

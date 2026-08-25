"""The golden walkthrough, driven end-to-end over MCP stdio.

One scenario definition (`examples/walkthrough/scenario.py`) is the single
source for two artifacts: the committed `examples/walkthrough/WALKTHROUGH.md`
(rendered by `render_walkthrough.py`) and the run below. This module pins two
properties:

1. **The doc is in sync with the scenario.** Re-rendering the scenario must
   reproduce the committed markdown byte-for-byte; otherwise the manual
   walkthrough and the automated one have drifted — the exact failure mode the
   walkthrough exists to prevent.
2. **The scenario actually works against a real server.** Every step is issued
   to a live server over stdio; the response must not be an MCP error and must
   contain the substrings the scenario declares, which pin the observable
   behavior (the resolved decision's id and disposition, the attribution read
   back at session end, the sentinel learning recalled with a score, and a
   cross-project read and write both failing closed with no foreign store
   created).

The server runs from this source tree (`PYTHONPATH=src`) pinned to a hermetic
``walkthrough`` project with all data directories redirected into ``tmp_path``;
nothing touches the developer's real ``~/.trace``. The same scenario can drive
a *deployed* server as a canary — that is a separate invocation, not this test.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from test_e2e_server import (
    _call_tool,
    _initialize_server,
    _shutdown_server,
    _start_server,
)

# The examples/ tree is not on the default import path (only src/ is), so make
# the repo root importable for the scenario package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.walkthrough.render_walkthrough import WALKTHROUGH_PATH, render_markdown  # noqa: E402
from examples.walkthrough.scenario import FOREIGN_PROJECT, PROJECT, SESSION_ID_PLACEHOLDER, STEPS  # noqa: E402


def test_walkthrough_doc_is_in_sync_with_the_scenario() -> None:
    """The committed WALKTHROUGH.md must equal a fresh render of the scenario, byte for byte."""
    rendered = render_markdown(STEPS).encode("utf-8")
    committed = WALKTHROUGH_PATH.read_bytes()
    assert committed == rendered, (
        "examples/walkthrough/WALKTHROUGH.md is out of sync with scenario.py. "
        "Regenerate it: `python examples/walkthrough/render_walkthrough.py`."
    )


def _response_text(response: dict[str, Any]) -> str:
    """The text payload of a successful tools/call response.

    A JSON-RPC-level error, or an MCP tool result flagged ``isError``, is a
    failure here: the walkthrough's expected cross-project denials are
    application-level errors carried in a normal (non-error) result payload, so
    a tool that actually errored must never satisfy an expectation.
    """
    assert "result" in response, f"tools/call failed at the protocol level: {response}"
    result = response["result"]
    assert not result.get("isError"), f"tool returned an MCP error result: {result}"
    return result["content"][0]["text"]


def _resolve(value: Any, session_id: str | None) -> Any:
    """Recursively substitute the session-id placeholder anywhere in an argument value."""
    if value == SESSION_ID_PLACEHOLDER:
        assert session_id is not None, "a step referenced the session id before start_session ran"
        return session_id
    if isinstance(value, dict):
        return {k: _resolve(v, session_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, session_id) for v in value]
    return value


@pytest.mark.asyncio
async def test_walkthrough_runs_end_to_end_over_stdio(tmp_path: Path) -> None:
    """Every scenario step, issued to a live pinned server, meets its declared expectations."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    env_extra = {
        "TRACE_KNOWLEDGE_DIR": str(knowledge),
        "TRACE_REGISTRY_PATH": str(tmp_path / "projects.json"),
        "TRACE_SCRATCHPAD_DIR": str(tmp_path / "scratchpads"),
        "TRACE_PROJECT": PROJECT,
    }
    proc = await _start_server(str(tmp_path / "sessions"), env_extra=env_extra)
    session_id: str | None = None
    try:
        try:
            await _initialize_server(proc)
        except BaseException:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=10)
            raise
        for i, step in enumerate(STEPS):
            # Deep-copy so substitution never mutates the shared, frozen scenario.
            args = _resolve(copy.deepcopy(step.arguments), session_id)
            response = await _call_tool(proc, step.tool, args, request_id=100 + i)
            text = _response_text(response)
            for needle in step.expect_substrings:
                assert needle in text, f"step {i} ({step.tool}): {needle!r} not in response:\n{text}"
            if step.capture_session_id:
                match = re.search(r"(?m)^Session:[ \t]+(\S+)", text)
                assert match, f"step {i} ({step.tool}): could not capture a session id from:\n{text}"
                session_id = match.group(1)
            if step.expect_session_id:
                assert session_id and session_id in text, (
                    f"step {i} ({step.tool}): the captured session id {session_id!r} is not in:\n{text}"
                )
    finally:
        await _shutdown_server(proc)

    # Fail-closed on disk, not just in the response: the denied cross-project
    # calls must not have created a store for the foreign project.
    assert not (knowledge / f"{FOREIGN_PROJECT}.json").exists(), "a denied cross-project call created a foreign store"

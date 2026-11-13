"""The extraction request keeps each learning within the scope of its source (ADR 008 D10).

The extractor rewords what it reads, so a learning's mark can only describe its
source event. The request therefore says who proposed and resolved each decision
and who wrote each note, and asks for a learning to say what happened and where,
worded as a general rule only when a person accepted a decision that states that
rule itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shadow_helpers import learn_env

from trace_mcp.extensions.learn.extraction import (
    PROPOSAL_WORDING_INSTRUCTION,
    SOURCE_SCOPE_INSTRUCTION,
    build_extraction_prompt,
)
from trace_mcp.schema import Session
from trace_mcp.tools import decision_tools, logging_tools, session_tools


async def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    active: dict[str, Session] = {}
    session = await session_tools.create_session(storage, active, project="collab-a")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="Uploads retry on every 503.")
    for description, resolver in (
        ("Cap upload retries at three.", "human"),
        ("Log every retry.", "ai"),
        ("Back off exponentially.", None),
    ):
        await decision_tools.propose_decision(
            storage, session, description=description, proposed_by_type="ai", proposed_by_id="ai-assistant"
        )
        if resolver is not None:
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id=session.events[-1].id,
                disposition="accepted",
                resolved_by_type=resolver,
                resolved_by_id="alice" if resolver == "human" else "ai-assistant",
            )
    await session_tools.end_session(storage, active, session_id=session.id)
    return await storage.get_session(session.id)


def _event_line(prompt: str, event_id: str) -> str:
    return next(line for line in prompt.splitlines() if line.startswith(f"[{event_id}]"))


async def test_the_request_says_who_proposed_and_who_resolved_each_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = build_extraction_prompt(await _session(tmp_path, monkeypatch), "(none)")
    assert "actor=ai" in _event_line(prompt, "evt_001")
    human, ai, open_ = (_event_line(prompt, e) for e in ("evt_002", "evt_003", "evt_004"))
    assert "proposed_by=ai" in human and "resolved_by=human" in human
    assert "resolved_by=ai" in ai
    assert "proposed_by=ai" in open_ and "resolved_by" not in open_


async def test_the_request_asks_for_learnings_within_the_scope_of_their_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = build_extraction_prompt(await _session(tmp_path, monkeypatch), "(none)")
    assert prompt.endswith("\n\n" + SOURCE_SCOPE_INSTRUCTION + "\n\n" + PROPOSAL_WORDING_INSTRUCTION)
    assert "actionable" not in prompt
    for phrase in ("what happened", "resolved_by=human", "states that rule itself", "never as a rule"):
        assert phrase in SOURCE_SCOPE_INSTRUCTION

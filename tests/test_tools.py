"""Integration tests for TRACE tools — full workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import (
    decision_tools,
    export_tools,
    logging_tools,
    query_tools,
    session_tools,
)


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


class TestFullWorkflow:
    """End-to-end workflow test matching the handoff spec verification checklist."""

    async def test_full_workflow(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
        tmp_path: Path,
    ) -> None:
        # 1. Start session
        result = await session_tools.start_session(
            storage,
            active,
            project="Climate discourse analysis",
            experiment_id="exp-017",
            description="Analyzing IPCC AR6 language shifts",
            participants=[
                {"type": "human", "id": "researcher-jane", "role": "lead"},
                {"type": "ai", "id": "claude-sonnet-4", "role": "assistant"},
            ],
            tags=["ipcc", "adaptation"],
        )
        assert "TRACE audit logging is now active" in result
        session_id = result.split("Session: ")[1].split("\n")[0]
        session = active[session_id]

        # 2. Log a tool call
        evt_id = await logging_tools.log_tool_call(
            storage,
            session,
            server="corpus-search-mcp",
            tool_name="search_passages",
            input={"query": "adaptation", "corpus": "ipcc_ar6"},
            output={"passages_found": 47},
            duration_ms=3200,
            status="success",
            reasoning="Searching for adaptation-related passages",
        )
        assert evt_id == "evt_001"

        # 3. Propose a decision
        dec1_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use cosine similarity threshold of 0.85",
            rationale="F1=0.78 on 30-pair validation set",
            proposed_by_type="ai",
            proposed_by_id="claude-sonnet-4",
            tags=["methodology", "threshold"],
        )
        assert dec1_id == "evt_002"

        # 4. Accept the decision
        result = await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec1_id,
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="researcher-jane",
        )
        assert "accepted" in result

        # 5. Propose another decision
        dec2_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use BGE-large-en-v1.5 embeddings",
            rationale="MiniLM conflates senses of 'adaptation'",
            proposed_by_type="ai",
            proposed_by_id="claude-sonnet-4",
        )
        assert dec2_id == "evt_003"

        # 6. Revise it with a note
        result = await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec2_id,
            disposition="revised",
            resolved_by_type="human",
            resolved_by_id="researcher-jane",
            revision_note="Use domain-fine-tuned model instead",
        )
        assert "revised" in result

        # 7. Log a gotcha annotation
        ann_id = await logging_tools.log_annotation(
            storage,
            session,
            category="gotcha",
            content="IPCC PDFs have inconsistent Unicode encoding for em-dashes",
            tags=["preprocessing", "unicode"],
            related_event_ids=["evt_001"],
            actor_type="ai",
            actor_id="claude-sonnet-4",
        )
        assert ann_id == "evt_004"

        # 8. Log a state change
        sc_id = await logging_tools.log_state_change(
            storage,
            session,
            description="Switched embedding model",
            field="environment.embedding_model",
            old_value="all-MiniLM-L6-v2",
            new_value="BGE-large-en-v1.5",
            reason="Domain sense conflation",
        )
        assert sc_id == "evt_005"

        # 9. Get decisions
        decisions = query_tools.get_decisions(session)
        assert len(decisions) == 2
        assert decisions[0]["decision"]["disposition"] == "accepted"
        assert decisions[1]["decision"]["disposition"] == "revised"

        # 10. Get decision chain
        chain = query_tools.get_decision_chain(session, event_id=dec2_id)
        assert len(chain) == 1  # Single decision, no revision chain

        # 11. Search for text in annotation
        results = query_tools.search_events(session, query="Unicode")
        assert len(results) >= 1
        assert any("Unicode" in str(r) for r in results)

        # 12. End session
        end_result = await session_tools.end_session(
            storage,
            active,
            session_id=session_id,
            summary="Analyzed adaptation language shifts in AR6",
        )
        assert "Session ended" in end_result
        assert "5 events" in end_result
        assert session_id not in active

        # 13. Export as JSON
        loaded = await storage.get_session(session_id)
        json_export = export_tools.export_session(loaded, format="json")
        parsed = json.loads(json_export)
        assert parsed["id"] == session_id
        assert len(parsed["events"]) == 5

        # 14. Export as Markdown
        md_export = export_tools.export_session(loaded, format="markdown")
        assert "# TRACE Session:" in md_export
        assert "Decision Log" in md_export
        assert "Tool Calls" in md_export
        assert "Annotations" in md_export
        assert "Statistics" in md_export


class TestErrorCases:
    async def test_log_to_nonexistent_session(self, storage: JsonFileStorage, active: dict[str, Session]) -> None:
        result = await session_tools.end_session(storage, active, session_id="nonexistent")
        assert "not found" in result.lower() or "error" in result.lower()

    async def test_resolve_nonexistent_decision(self, storage: JsonFileStorage, active: dict[str, Session]) -> None:
        # Create a valid session first
        await session_tools.start_session(storage, active, project="test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        result = await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_999",
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="researcher",
        )
        assert "not found" in result.lower() or "error" in result.lower()

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

        # 7. Log a contribution
        contrib_id = await logging_tools.log_contribution(
            storage,
            session,
            description="Implemented cosine similarity function",
            direction="human",
            execution="ai",
            artifact="src/similarity.py",
            related_decision_ids=[dec1_id],
            tags=["implementation"],
        )
        assert contrib_id == "evt_004"

        # 8. Log a gotcha annotation
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
        assert ann_id == "evt_005"

        # 9. Log a state change
        sc_id = await logging_tools.log_state_change(
            storage,
            session,
            description="Switched embedding model",
            field="environment.embedding_model",
            old_value="all-MiniLM-L6-v2",
            new_value="BGE-large-en-v1.5",
            reason="Domain sense conflation",
        )
        assert sc_id == "evt_006"

        # 10. Get decisions
        decisions = query_tools.get_decisions(session)
        assert len(decisions) == 2
        assert decisions[0]["decision"]["disposition"] == "accepted"
        assert decisions[1]["decision"]["disposition"] == "revised"

        # 10b. Filter decisions by proposed_by_type
        ai_decisions = query_tools.get_decisions(session, proposed_by_type="ai")
        assert len(ai_decisions) == 2

        # 11. Get decision chain
        chain = query_tools.get_decision_chain(session, event_id=dec2_id)
        assert len(chain) == 1  # Single decision, no revision chain

        # 12. Search for text in annotation
        results = query_tools.search_events(session, query="Unicode")
        assert len(results) >= 1
        assert any("Unicode" in str(r) for r in results)

        # 12b. Search for contribution
        results = query_tools.search_events(session, query="cosine similarity")
        assert len(results) >= 1

        # 13. End session
        end_result = await session_tools.end_session(
            storage,
            active,
            session_id=session_id,
            summary="Analyzed adaptation language shifts in AR6",
        )
        assert "Session ended" in end_result
        assert "6 events" in end_result
        assert session_id not in active

        # 14. Export as JSON
        loaded = await storage.get_session(session_id)
        json_export = export_tools.export_session(loaded, format="json")
        parsed = json.loads(json_export)
        assert parsed["id"] == session_id
        assert len(parsed["events"]) == 6

        # 15. Export as Markdown
        md_export = export_tools.export_session(loaded, format="markdown")
        assert "# TRACE Session:" in md_export
        assert "Decision Log" in md_export
        assert "Tool Calls" in md_export
        assert "Contributions" in md_export
        assert "Annotations" in md_export
        assert "Statistics" in md_export


class TestProjectSummary:
    """Tests for cross-session project_summary aggregation."""

    async def test_summary_single_session(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        # Create a session with various events
        await session_tools.start_session(
            storage,
            active,
            project="summary-test",
            participants=[
                {"type": "human", "id": "researcher"},
                {"type": "ai", "id": "claude"},
            ],
        )
        session_id = list(active.keys())[0]
        session = active[session_id]

        # AI proposes a proactive decision, human accepts
        await decision_tools.propose_decision(
            storage,
            session,
            description="Use method A",
            proposed_by_type="ai",
            proposed_by_id="claude",
            suggestion_type="proactive",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_001",
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="researcher",
        )

        # Human proposes a decision, AI doesn't resolve (stays pending)
        await decision_tools.propose_decision(
            storage,
            session,
            description="Try alternative approach",
            proposed_by_type="human",
            proposed_by_id="researcher",
            suggestion_type="requested",
        )

        # Log a contribution
        await logging_tools.log_contribution(
            storage,
            session,
            description="Wrote analysis code",
            direction="human",
            execution="ai",
            related_decision_ids=["evt_001"],
        )

        # Log an annotation
        await logging_tools.log_annotation(storage, session, category="learning", content="Method A works well")

        await session_tools.end_session(storage, active, session_id=session_id)

        summary = await query_tools.project_summary(storage, project="summary-test")

        assert summary["session_count"] == 1
        assert summary["total_events"] == 4
        assert summary["events_by_type"]["decision"] == 2
        assert summary["events_by_type"]["contribution"] == 1
        assert summary["events_by_type"]["annotation"] == 1
        assert summary["decisions"]["total"] == 2
        assert summary["decisions"]["proposed_by_ai"] == 1
        assert summary["decisions"]["proposed_by_human"] == 1
        assert summary["decisions"]["accepted"] == 1
        assert summary["decisions"]["pending"] == 1
        assert summary["decisions"]["acceptance_rate"] == 1.0
        assert summary["decisions"]["suggestion_types"]["proactive"] == 1
        assert summary["decisions"]["suggestion_types"]["requested"] == 1
        assert summary["contributions"]["human_directed_ai_executed"] == 1
        assert summary["annotations_by_category"]["learning"] == 1
        assert len(summary["participants"]) == 2

    async def test_summary_multi_session(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        # Create two sessions for the same project
        for i in range(2):
            await session_tools.start_session(storage, active, project="multi-test")
            session_id = list(active.keys())[-1]
            session = active[session_id]

            await decision_tools.propose_decision(
                storage,
                session,
                description=f"Decision {i}",
                proposed_by_type="ai",
                proposed_by_id="claude",
            )
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id="evt_001",
                disposition="accepted" if i == 0 else "rejected",
                resolved_by_type="human",
                resolved_by_id="researcher",
                revision_note="No good" if i == 1 else None,
            )

            await session_tools.end_session(storage, active, session_id=session_id)

        summary = await query_tools.project_summary(storage, project="multi-test")

        assert summary["session_count"] == 2
        assert summary["decisions"]["total"] == 2
        assert summary["decisions"]["accepted"] == 1
        assert summary["decisions"]["rejected"] == 1
        assert summary["decisions"]["acceptance_rate"] == 0.5

    async def test_summary_empty_project(
        self,
        storage: JsonFileStorage,
    ) -> None:
        summary = await query_tools.project_summary(storage, project="nonexistent")
        assert summary["session_count"] == 0
        assert summary["total_events"] == 0


class TestCorrectionWorkflow:
    """End-to-end test for the 'wrong conda env 3x' correction pattern."""

    async def test_conda_env_correction_scenario(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        # Start session
        result = await session_tools.start_session(
            storage,
            active,
            project="correction-test",
        )
        session_id = result.split("Session: ")[1].split("\n")[0]
        session = active[session_id]

        # AI proposes to use conda env "base"
        dec_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use conda env 'base' for running analysis",
            proposed_by_type="ai",
            proposed_by_id="claude",
            suggestion_type="proactive",
        )
        assert dec_id == "evt_001"

        # Attempt 1: AI tries wrong conda env — fails
        tc1_id = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="run_command",
            input={"command": "conda activate base && python analyze.py"},
            status="error",
            error_message="ModuleNotFoundError: No module named 'pandas'",
        )
        assert tc1_id == "evt_002"

        # Attempt 2: AI retries same wrong env — fails again
        tc2_id = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="run_command",
            input={"command": "conda activate base && pip install pandas && python analyze.py"},
            status="error",
            error_message="PermissionError: base env is read-only",
            retries_event_id=tc1_id,
        )
        assert tc2_id == "evt_003"

        # Attempt 3: AI tries yet another wrong approach — fails
        tc3_id = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="run_command",
            input={"command": "pip install --user pandas && python analyze.py"},
            status="error",
            error_message="ImportError: version conflict",
            retries_event_id=tc2_id,
        )
        assert tc3_id == "evt_004"

        # Human intervenes: rejects the AI's env decision
        result = await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec_id,
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="Wrong env — I forgot to activate ml-dev before starting Claude Code",
        )
        assert "rejected" in result

        # Human logs a correction annotation linking to the failed attempts
        corr_id = await logging_tools.log_annotation(
            storage,
            session,
            category="correction",
            content="Human caught that AI was using wrong conda env 3 times. "
            "Correct env is 'ml-dev', not 'base'. Human had forgotten "
            "to activate it before starting the session.",
            corrects_event_ids=[tc1_id, tc2_id, tc3_id],
            tags=["env", "conda", "human-intervention"],
            actor_type="human",
            actor_id="researcher",
        )
        assert corr_id == "evt_005"

        # AI now proposes correct env (revises original decision)
        dec2_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use conda env 'ml-dev' for running analysis",
            proposed_by_type="human",
            proposed_by_id="researcher",
            suggestion_type="requested",
            revises_event_id=dec_id,
        )
        assert dec2_id == "evt_006"

        # Human accepts
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec2_id,
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="researcher",
        )

        # Final successful attempt with correct env
        tc4_id = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="run_command",
            input={"command": "conda activate ml-dev && python analyze.py"},
            status="success",
            retries_event_id=tc3_id,
        )
        assert tc4_id == "evt_007"

        # --- Verify the recorded data ---

        # Decision chain should link dec_id -> dec2_id
        chain = query_tools.get_decision_chain(session, event_id=dec_id)
        assert len(chain) == 2
        assert chain[0]["decision"]["disposition"] == "rejected"
        assert chain[1]["decision"]["disposition"] == "accepted"

        # Correction annotation should link to all 3 failed tool calls
        events = query_tools.get_events(session, type_filter="annotation")
        correction_events = [e for e in events if e["annotation"]["category"] == "correction"]
        assert len(correction_events) == 1
        assert correction_events[0]["annotation"]["corrects_event_ids"] == [tc1_id, tc2_id, tc3_id]

        # Search should find the correction
        results = query_tools.search_events(session, query="conda")
        assert len(results) >= 1

        await session_tools.end_session(storage, active, session_id=session_id)

    async def test_project_summary_correction_metrics(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """Verify project_summary includes human_interventions metrics."""
        result = await session_tools.start_session(
            storage,
            active,
            project="correction-metrics-test",
        )
        session_id = result.split("Session: ")[1].split("\n")[0]
        session = active[session_id]

        # Log a failed tool call chain (3 retries)
        tc1 = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="cmd",
            input={"c": "1"},
            status="error",
            error_message="fail 1",
        )
        tc2 = await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="cmd",
            input={"c": "2"},
            status="error",
            error_message="fail 2",
            retries_event_id=tc1,
        )
        await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="cmd",
            input={"c": "3"},
            status="success",
            retries_event_id=tc2,
        )

        # Log a correction annotation
        await logging_tools.log_annotation(
            storage,
            session,
            category="correction",
            content="Human corrected the command",
            corrects_event_ids=[tc1, tc2],
            actor_type="human",
            actor_id="researcher",
        )

        # Log a decision that gets rejected
        dec_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use approach X",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec_id,
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="Approach X won't work",
        )

        # Log a decision that gets revised
        dec2_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Use approach Y",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id=dec2_id,
            disposition="revised",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="Use approach Y-prime instead",
        )

        await session_tools.end_session(storage, active, session_id=session_id)

        summary = await query_tools.project_summary(storage, project="correction-metrics-test")

        # Verify human_interventions block
        hi = summary["human_interventions"]
        assert hi["corrections"] == 1
        assert hi["corrections_with_links"] == 1
        assert hi["decision_rejections"] == 1
        assert hi["decision_revisions"] == 1
        assert hi["total"] == 3  # 1 correction + 1 rejection + 1 revision
        assert hi["retry_chains"] == 1  # one chain of 3 tool calls
        assert hi["intervention_rate"] is not None
        assert hi["intervention_rate"] > 0

        # Also verify annotations_by_category includes correction
        assert summary["annotations_by_category"]["correction"] == 1


class TestHealthCheck:
    async def test_health_check_empty(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        result = await query_tools.health_check(storage)
        assert result["version"] == "0.2.0"
        assert result["session_count"] == 0
        assert result["events"]["total"] == 0
        assert result["events"]["by_type"] == {}
        assert result["events"]["by_actor_type"] == {}
        assert result["project_filter"] is None
        assert result["session_filter"] is None

    async def test_health_check_with_sessions(
        self, storage: JsonFileStorage, active: dict[str, Session], tmp_path: Path
    ) -> None:
        msg = await session_tools.start_session(storage, active, project="hc-test")
        session_id = msg.split("Session: ")[1].split("\n")[0]
        session = active[session_id]

        await logging_tools.log_tool_call(
            storage, session, server="test", tool_name="foo", input={"a": 1},
            actor_type="ai", actor_id="claude",
        )
        await logging_tools.log_annotation(
            storage, session, category="learning", content="something interesting",
            actor_type="human", actor_id="researcher",
        )
        await session_tools.end_session(storage, active, session_id=session_id)

        result = await query_tools.health_check(storage)
        assert result["session_count"] == 1
        assert result["events"]["total"] == 2
        assert result["events"]["by_type"]["tool_call"] == 1
        assert result["events"]["by_type"]["annotation"] == 1
        assert result["events"]["by_actor_type"]["ai"] == 1
        assert result["events"]["by_actor_type"]["human"] == 1

    async def test_health_check_project_filter(
        self, storage: JsonFileStorage, active: dict[str, Session], tmp_path: Path
    ) -> None:
        msg1 = await session_tools.start_session(storage, active, project="proj-a")
        sid1 = msg1.split("Session: ")[1].split("\n")[0]
        session1 = active[sid1]
        await logging_tools.log_tool_call(
            storage, session1, server="s", tool_name="t", input={},
            actor_type="ai", actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid1)

        msg2 = await session_tools.start_session(storage, active, project="proj-b")
        sid2 = msg2.split("Session: ")[1].split("\n")[0]
        session2 = active[sid2]
        await logging_tools.log_tool_call(
            storage, session2, server="s", tool_name="t", input={},
            actor_type="ai", actor_id="claude",
        )
        await logging_tools.log_tool_call(
            storage, session2, server="s", tool_name="t2", input={},
            actor_type="ai", actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid2)

        result = await query_tools.health_check(storage, project="proj-b")
        assert result["session_count"] == 1
        assert result["events"]["total"] == 2
        assert result["project_filter"] == "proj-b"

    async def test_health_check_session_filter(
        self, storage: JsonFileStorage, active: dict[str, Session], tmp_path: Path
    ) -> None:
        msg1 = await session_tools.start_session(storage, active, project="hc-sf")
        sid1 = msg1.split("Session: ")[1].split("\n")[0]
        session1 = active[sid1]
        await logging_tools.log_tool_call(
            storage, session1, server="s", tool_name="t", input={},
            actor_type="ai", actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid1)

        msg2 = await session_tools.start_session(storage, active, project="hc-sf")
        sid2 = msg2.split("Session: ")[1].split("\n")[0]
        session2 = active[sid2]
        await logging_tools.log_tool_call(
            storage, session2, server="s", tool_name="t", input={},
            actor_type="ai", actor_id="claude",
        )
        await logging_tools.log_tool_call(
            storage, session2, server="s", tool_name="t2", input={},
            actor_type="ai", actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid2)

        result = await query_tools.health_check(storage, session_id=sid2)
        assert result["session_count"] == 1
        assert result["events"]["total"] == 2
        assert result["session_filter"] == sid2

    async def test_health_check_nonexistent_session(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        result = await query_tools.health_check(storage, session_id="nonexistent_id")
        assert result["session_count"] == 0
        assert result["events"]["total"] == 0

    async def test_health_check_storage_paths(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        result = await query_tools.health_check(storage)
        assert result["storage"]["sessions_dir"] == str(tmp_path)
        assert result["storage"]["sessions_dir_exists"] is True
        assert "knowledge_dir" in result["storage"]


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

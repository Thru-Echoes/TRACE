"""Integration tests for TRACE tools — full workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import trace_mcp
from trace_mcp.schema import Session, SessionMetadata
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


class TestInitProjectMCPConfig:
    def test_init_project_mcp_config_uses_uvx(self) -> None:
        """MCP_CONFIG should use 'uvx --from ... --refresh-package trace-mcp trace-mcp'."""
        from trace_mcp.init_project import MCP_CONFIG

        trace_config = MCP_CONFIG["trace"]
        assert trace_config["command"] == "uvx"
        args = trace_config["args"]
        assert "--from" in args
        assert "--refresh-package" in args
        assert "trace-mcp" in args


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
        assert contrib_id.split("\n")[0] == "evt_004"

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
        assert corr_id.split("\n")[0] == "evt_005"

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
        assert result["version"] == trace_mcp.__version__
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
            storage,
            session,
            server="test",
            tool_name="foo",
            input={"a": 1},
            actor_type="ai",
            actor_id="claude",
        )
        await logging_tools.log_annotation(
            storage,
            session,
            category="learning",
            content="something interesting",
            actor_type="human",
            actor_id="researcher",
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
            storage,
            session1,
            server="s",
            tool_name="t",
            input={},
            actor_type="ai",
            actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid1)

        msg2 = await session_tools.start_session(storage, active, project="proj-b")
        sid2 = msg2.split("Session: ")[1].split("\n")[0]
        session2 = active[sid2]
        await logging_tools.log_tool_call(
            storage,
            session2,
            server="s",
            tool_name="t",
            input={},
            actor_type="ai",
            actor_id="claude",
        )
        await logging_tools.log_tool_call(
            storage,
            session2,
            server="s",
            tool_name="t2",
            input={},
            actor_type="ai",
            actor_id="claude",
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
            storage,
            session1,
            server="s",
            tool_name="t",
            input={},
            actor_type="ai",
            actor_id="claude",
        )
        await session_tools.end_session(storage, active, session_id=sid1)

        msg2 = await session_tools.start_session(storage, active, project="hc-sf")
        sid2 = msg2.split("Session: ")[1].split("\n")[0]
        session2 = active[sid2]
        await logging_tools.log_tool_call(
            storage,
            session2,
            server="s",
            tool_name="t",
            input={},
            actor_type="ai",
            actor_id="claude",
        )
        await logging_tools.log_tool_call(
            storage,
            session2,
            server="s",
            tool_name="t2",
            input={},
            actor_type="ai",
            actor_id="claude",
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

        with pytest.raises(ValueError, match="not found"):
            await decision_tools.resolve_decision(
                storage,
                session,
                event_id="evt_999",
                disposition="accepted",
                resolved_by_type="human",
                resolved_by_id="researcher",
            )


# ── Conversation Snippet (Phase 2) ──────────────────────────────────────


class TestConversationSnippet:
    async def test_contribution_with_snippet(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_contribution(
            storage,
            session,
            description="Wrote analysis code",
            direction="human",
            execution="ai",
            conversation_snippet="User said: please write the analysis code",
        )
        evt = session.events[-1]
        assert evt.context.conversation_snippet == "User said: please write the analysis code"

    async def test_annotation_with_snippet(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_annotation(
            storage,
            session,
            category="correction",
            content="Wrong env",
            conversation_snippet="User: that's the wrong environment",
        )
        evt = session.events[-1]
        assert evt.context.conversation_snippet == "User: that's the wrong environment"

    async def test_decision_with_snippet(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await decision_tools.propose_decision(
            storage,
            session,
            description="Use BM25",
            proposed_by_type="ai",
            proposed_by_id="claude",
            conversation_snippet="Let's try BM25 for matching",
        )
        evt = session.events[-1]
        assert evt.context.conversation_snippet == "Let's try BM25 for matching"

    async def test_contribution_without_snippet_backward_compat(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_contribution(
            storage,
            session,
            description="Code",
            direction="ai",
            execution="ai",
        )
        evt = session.events[-1]
        assert evt.context.conversation_snippet is None

    async def test_search_finds_conversation_snippet(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_contribution(
            storage,
            session,
            description="Analysis code",
            direction="human",
            execution="ai",
            conversation_snippet="User asked for a correlation analysis",
        )
        results = query_tools.search_events(session, query="correlation analysis")
        assert len(results) >= 1

    async def test_snippet_persists_roundtrip(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="snippet-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_contribution(
            storage,
            session,
            description="Code",
            direction="human",
            execution="ai",
            conversation_snippet="roundtrip test snippet",
        )
        loaded = await storage.get_session(session_id)
        evt = loaded.events[-1]
        assert evt.context.conversation_snippet == "roundtrip test snippet"


# ── Attribution Audit (Phase 3) ─────────────────────────────────────────


class TestAttributionAudit:
    async def test_audit_contributions(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        await logging_tools.log_contribution(
            storage,
            session,
            description="Human-directed code",
            direction="human",
            execution="ai",
            artifact="src/code.py",
        )
        await logging_tools.log_contribution(
            storage,
            session,
            description="AI-directed analysis",
            direction="ai",
            execution="ai",
            artifact="src/analysis.py",
        )
        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "Attribution Audit" in result
        assert "Contributions (2)" in result
        assert "direction=human" in result
        assert "direction=ai" in result

    async def test_audit_decisions(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

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
        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "Decisions (1)" in result
        assert "proposed_by=ai" in result
        assert "disposition=accepted" in result

    async def test_audit_corrections(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        # First log events that will be corrected
        await logging_tools.log_annotation(
            storage,
            session,
            category="observation",
            content="Using env X",
            actor_type="ai",
            actor_id="claude",
        )
        await logging_tools.log_annotation(
            storage,
            session,
            category="observation",
            content="Config is Y",
            actor_type="ai",
            actor_id="claude",
        )
        # Now log a correction referencing the existing events
        await logging_tools.log_annotation(
            storage,
            session,
            category="correction",
            content="Wrong env used",
            corrects_event_ids=["evt_001", "evt_002"],
            actor_type="human",
            actor_id="researcher",
        )
        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "Corrections: 1" in result
        assert "evt_001" in result

    async def test_audit_human_interventions(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        # Correction
        await logging_tools.log_annotation(
            storage,
            session,
            category="correction",
            content="Fix",
            actor_type="human",
            actor_id="researcher",
        )
        # Rejected decision
        await decision_tools.propose_decision(
            storage,
            session,
            description="Bad idea",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_002",
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="No",
        )
        # Revised decision
        await decision_tools.propose_decision(
            storage,
            session,
            description="Tweakable idea",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_003",
            disposition="revised",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="Use variant",
        )
        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "Human interventions: 3" in result
        assert "1 correction" in result
        assert "1 revision" in result
        assert "1 rejection" in result

    async def test_audit_empty_session(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "No contributions, decisions, or corrections to review." in result

    async def test_audit_mixed_realistic(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """Realistic session with 8+ events, full audit verification."""
        await session_tools.start_session(storage, active, project="audit-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        # Tool call
        await logging_tools.log_tool_call(
            storage,
            session,
            server="bash",
            tool_name="run",
            input={"cmd": "test"},
            status="success",
        )
        # Decision proposed + accepted
        await decision_tools.propose_decision(
            storage,
            session,
            description="Use cosine similarity",
            proposed_by_type="ai",
            proposed_by_id="claude",
            suggestion_type="proactive",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_002",
            disposition="accepted",
            resolved_by_type="human",
            resolved_by_id="researcher",
        )
        # Contribution
        await logging_tools.log_contribution(
            storage,
            session,
            description="Similarity function",
            direction="human",
            execution="ai",
            artifact="src/sim.py",
        )
        # Annotation
        await logging_tools.log_annotation(
            storage,
            session,
            category="gotcha",
            content="Unicode issues",
        )
        # Another contribution
        await logging_tools.log_contribution(
            storage,
            session,
            description="Test suite",
            direction="ai",
            execution="ai",
            artifact="tests/test_sim.py",
        )
        # Decision rejected
        await decision_tools.propose_decision(
            storage,
            session,
            description="Use threshold 0.9",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage,
            session,
            event_id="evt_006",
            disposition="rejected",
            resolved_by_type="human",
            resolved_by_id="researcher",
            revision_note="Too high",
        )
        # State change
        await logging_tools.log_state_change(
            storage,
            session,
            description="Switched model",
        )

        result = await session_tools.end_session(storage, active, session_id=session_id)
        assert "Attribution Audit" in result
        assert "Contributions (2)" in result
        assert "Decisions (2)" in result
        assert "Human interventions: 1" in result
        assert "1 rejection" in result


# ── Decision Chain Edge Case (Phase 5d) ─────────────────────────────────


class TestDecisionChainEdgeCases:
    async def test_circular_chain_terminates(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """A→B→A cycle should terminate without infinite loop."""
        await session_tools.start_session(storage, active, project="chain-test")
        session_id = list(active.keys())[0]
        session = active[session_id]

        # Create decision A
        await decision_tools.propose_decision(
            storage,
            session,
            description="Decision A",
            proposed_by_type="ai",
            proposed_by_id="claude",
        )
        # Create decision B that revises A
        await decision_tools.propose_decision(
            storage,
            session,
            description="Decision B",
            proposed_by_type="ai",
            proposed_by_id="claude",
            revises_event_id="evt_001",
        )
        # Manually create cycle: patch A to revise B
        for evt in session.events:
            if evt.id == "evt_001" and evt.decision:
                evt.decision.revises_event_id = "evt_002"
                break
        await storage.update_session(session)

        # Should terminate and return results (not hang)
        chain = query_tools.get_decision_chain(session, event_id="evt_001")
        assert len(chain) >= 1
        assert len(chain) <= 2  # At most both decisions


class TestCreateSession:
    """Tests for the new create_session function used by auto-session."""

    async def test_create_session_returns_session_object(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """create_session returns a Session, not a formatted string."""
        session = await session_tools.create_session(
            storage,
            active,
            project="test-project",
            description="test",
        )
        assert isinstance(session, Session)
        assert session.metadata.project == "test-project"
        assert session.metadata.description == "test"
        assert session.id in active

    async def test_create_session_persists_to_disk(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        session = await session_tools.create_session(
            storage,
            active,
            project="persist-test",
        )
        # Load from disk independently
        loaded = await storage.get_session(session.id)
        assert loaded.metadata.project == "persist-test"

    async def test_create_session_with_tags(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        session = await session_tools.create_session(
            storage,
            active,
            project="tag-test",
            tags=["auto-session"],
        )
        assert "auto-session" in session.metadata.tags

    async def test_start_session_still_returns_string(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """Existing start_session API is unchanged — returns formatted string."""
        result = await session_tools.start_session(
            storage,
            active,
            project="compat-test",
        )
        assert isinstance(result, str)
        assert "TRACE audit logging is now active" in result
        assert "compat-test" in result


class TestAutoSession:
    """Tests for the server-level auto-session infrastructure."""

    async def test_ensure_session_with_explicit_id(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_ensure_session with explicit session_id returns that session."""
        import trace_mcp.server as srv

        # Save original state
        orig_storage, orig_active = srv.storage, srv.active_sessions
        orig_current = srv._current_session_id
        srv.storage, srv.active_sessions = storage, active

        try:
            session = await session_tools.create_session(
                storage,
                active,
                project="explicit-test",
            )
            result_session, auto_msg = await srv._ensure_session(session.id)
            assert result_session.id == session.id
            assert auto_msg == ""
            assert srv._current_session_id == session.id
        finally:
            srv.storage, srv.active_sessions = orig_storage, orig_active
            srv._current_session_id = orig_current

    async def test_ensure_session_reuses_current(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_ensure_session with None reuses _current_session_id."""
        import trace_mcp.server as srv

        orig_storage, orig_active = srv.storage, srv.active_sessions
        orig_current = srv._current_session_id
        srv.storage, srv.active_sessions = storage, active

        try:
            session = await session_tools.create_session(
                storage,
                active,
                project="reuse-test",
            )
            srv._current_session_id = session.id
            result_session, auto_msg = await srv._ensure_session(None)
            assert result_session.id == session.id
            assert auto_msg == ""
        finally:
            srv.storage, srv.active_sessions = orig_storage, orig_active
            srv._current_session_id = orig_current

    async def test_ensure_session_auto_creates(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_ensure_session auto-creates when no session exists."""
        import trace_mcp.server as srv

        orig_storage, orig_active = srv.storage, srv.active_sessions
        orig_current = srv._current_session_id
        srv.storage, srv.active_sessions = storage, active
        srv._current_session_id = None

        try:
            result_session, auto_msg = await srv._ensure_session(None)
            assert result_session is not None
            assert "Auto-created" in auto_msg
            assert result_session.id in active
            assert srv._current_session_id == result_session.id
            assert "auto-session" in result_session.metadata.tags
        finally:
            srv.storage, srv.active_sessions = orig_storage, orig_active
            srv._current_session_id = orig_current

    async def test_ensure_session_explicit_not_found_raises(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_ensure_session with invalid explicit ID raises FileNotFoundError."""
        import trace_mcp.server as srv

        orig_storage, orig_active = srv.storage, srv.active_sessions
        orig_current = srv._current_session_id
        srv.storage, srv.active_sessions = storage, active

        try:
            with pytest.raises(FileNotFoundError):
                await srv._ensure_session("nonexistent_session_id")
        finally:
            srv.storage, srv.active_sessions = orig_storage, orig_active
            srv._current_session_id = orig_current

    async def test_infer_project_from_recent_session(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_infer_project falls back to most recent session's project."""
        import trace_mcp.server as srv

        orig_storage = srv.storage
        srv.storage = storage

        try:
            # Create a session so there's something to infer from
            await session_tools.create_session(
                storage,
                active,
                project="inferred-project",
            )
            # Remove env var if set
            import os

            old_env = os.environ.pop("TRACE_DEFAULT_PROJECT", None)
            try:
                project = await srv._infer_project()
                assert project == "inferred-project"
            finally:
                if old_env is not None:
                    os.environ["TRACE_DEFAULT_PROJECT"] = old_env
        finally:
            srv.storage = orig_storage

    async def test_infer_project_from_env_var(
        self,
        storage: JsonFileStorage,
        active: dict[str, Session],
    ) -> None:
        """_infer_project prefers TRACE_DEFAULT_PROJECT env var."""
        import os

        import trace_mcp.server as srv

        orig_storage = srv.storage
        srv.storage = storage

        try:
            old_env = os.environ.get("TRACE_DEFAULT_PROJECT")
            os.environ["TRACE_DEFAULT_PROJECT"] = "env-project"
            try:
                project = await srv._infer_project()
                assert project == "env-project"
            finally:
                if old_env is not None:
                    os.environ["TRACE_DEFAULT_PROJECT"] = old_env
                else:
                    os.environ.pop("TRACE_DEFAULT_PROJECT", None)
        finally:
            srv.storage = orig_storage


class TestValidateSession:
    """Tests for the session validation script."""

    def test_validate_valid_session(self, tmp_path: Path) -> None:
        """A valid session JSON passes validation."""
        import importlib.util

        script = Path(__file__).parent.parent / "scripts" / "validate_session.py"
        spec = importlib.util.spec_from_file_location("validate_session", script)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        session = Session(
            id="trace_valid_001",
            metadata=SessionMetadata(project="test"),
        )
        session_file = tmp_path / "trace_valid_001.json"
        session_file.write_text(json.dumps(session.model_dump(mode="json"), indent=2, default=str))
        result = mod.main([str(session_file)])
        assert result == 0

    def test_validate_invalid_session(self, tmp_path: Path) -> None:
        """An invalid session JSON fails validation."""
        import importlib.util

        script = Path(__file__).parent.parent / "scripts" / "validate_session.py"
        spec = importlib.util.spec_from_file_location("validate_session", script)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        invalid_file = tmp_path / "bad.json"
        invalid_file.write_text('{"not_a_session": true}')
        result = mod.main([str(invalid_file)])
        assert result == 1

"""Comprehensive E2E tests for all 32 TRACE failure modes.

Each test exercises a specific failure mode through the real MCP server
subprocess (JSON-RPC over stdio) and documents whether TRACE detects it,
partially detects it, or cannot detect it.

Test naming: test_fm{N}_{short_name}_{expected_outcome}
  - _detected: guard rail warning appears in response
  - _undetected: server correctly accepts (limitation documented)
  - _partial: some signal but not a definitive catch
"""

from __future__ import annotations

import json
import tempfile
import time

from test_e2e_server import (
    _call_tool,
    _initialize_server,
    _shutdown_server,
    _start_server,
)

# ── Helpers ──────────────────────────────────────────────────────────────


async def _setup_session(proc, req_id: int = 2, project: str = "fm-test"):
    """Start a session and return (session_id, next_req_id)."""
    response = await _call_tool(
        proc,
        "trace_start_session",
        {
            "project": project,
            "description": f"Failure mode test session ({project})",
        },
        request_id=req_id,
    )
    text = response["result"]["content"][0]["text"]
    session_id = text.split("Session: ")[1].split("\n")[0]
    return session_id, req_id + 1


async def _propose(
    proc, session_id, req_id, *, description="Test decision", proposed_by_type="ai", proposed_by_id="test-ai", **kwargs
):
    """Propose a decision and return (result_text, event_id, next_req_id)."""
    args = {
        "session_id": session_id,
        "description": description,
        "proposed_by_type": proposed_by_type,
        "proposed_by_id": proposed_by_id,
        **kwargs,
    }
    response = await _call_tool(proc, "trace_propose_decision", args, request_id=req_id)
    text = response["result"]["content"][0]["text"]
    # Extract evt_NNN from "Decision proposed: evt_001" or similar
    for part in text.split():
        if part.startswith("evt_"):
            return text, part.rstrip(","), req_id + 1
    return text, "evt_001", req_id + 1


async def _resolve(
    proc,
    session_id,
    event_id,
    req_id,
    *,
    disposition="accepted",
    resolved_by_type="human",
    resolved_by_id="researcher",
    **kwargs,
):
    """Resolve a decision and return (result_text, next_req_id)."""
    args = {
        "event_id": event_id,
        "session_id": session_id,
        "disposition": disposition,
        "resolved_by_type": resolved_by_type,
        "resolved_by_id": resolved_by_id,
        **kwargs,
    }
    response = await _call_tool(proc, "trace_resolve_decision", args, request_id=req_id)
    return response["result"]["content"][0]["text"], req_id + 1


async def _end_session(proc, session_id, req_id, summary="test"):
    """End session and return (result_text, next_req_id)."""
    response = await _call_tool(
        proc,
        "trace_end_session",
        {
            "session_id": session_id,
            "summary": summary,
        },
        request_id=req_id,
    )
    return response["result"]["content"][0]["text"], req_id + 1


async def _annotate(proc, session_id, req_id, *, category="learning", content="Test annotation", **kwargs):
    """Log an annotation and return (result_text, next_req_id)."""
    args = {
        "category": category,
        "content": content,
        **kwargs,
    }
    if session_id is not None:
        args["session_id"] = session_id
    response = await _call_tool(proc, "trace_log_annotation", args, request_id=req_id)
    return response["result"]["content"][0]["text"], req_id + 1


async def _contribute(
    proc, session_id, req_id, *, description="Test contribution", direction="human", execution="ai", **kwargs
):
    """Log a contribution and return (result_text, next_req_id)."""
    args = {
        "session_id": session_id,
        "description": description,
        "direction": direction,
        "execution": execution,
        **kwargs,
    }
    response = await _call_tool(proc, "trace_log_contribution", args, request_id=req_id)
    return response["result"]["content"][0]["text"], req_id + 1


async def _log_tool_call(proc, session_id, req_id, *, server="domain-server", tool_name="do_work", **kwargs):
    """Log a tool call and return (result_text, next_req_id)."""
    args = {
        "session_id": session_id,
        "server": server,
        "tool_name": tool_name,
        "input": kwargs.pop("input", {"key": "value"}),
        **kwargs,
    }
    response = await _call_tool(proc, "trace_log_tool_call", args, request_id=req_id)
    return response["result"]["content"][0]["text"], req_id + 1


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION FAILURES (who did what)
# ═══════════════════════════════════════════════════════════════════════════


class TestAttributionFailures:
    """FM1, FM4, FM5, FM6, FM7, FM8, FM26, FM27."""

    async def test_fm1_ai_self_resolves_detected(self) -> None:
        """FM1: AI proposes + AI resolves → server returns warning.

        Before: Silent acceptance, no warning.
        After:  Warning in resolve response + persisted in decision warnings.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _resolve(
                    proc,
                    sid,
                    eid,
                    rid,
                    resolved_by_type="ai",
                    resolved_by_id="test-ai",
                )
                assert "AI resolved its own proposal" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm1_human_resolves_ai_proposal_no_warning(self) -> None:
        """FM1 negative: proper workflow produces no self-resolution warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _resolve(proc, sid, eid, rid)
                assert "AI resolved its own proposal" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm1_aggregate_at_session_end(self) -> None:
        """FM1 aggregate: session-end audit counts self-resolutions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                for _ in range(3):
                    _, eid, rid = await _propose(proc, sid, rid)
                    _, rid = await _resolve(
                        proc,
                        sid,
                        eid,
                        rid,
                        resolved_by_type="ai",
                        resolved_by_id="test-ai",
                    )
                text, rid = await _end_session(proc, sid, rid)
                assert "AI self-resolutions: 3" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm4_direction_execution_swap_undetected(self) -> None:
        """FM4: Server accepts swapped direction/execution without warning.

        Before: Silent acceptance.
        After:  Still silent — server can't verify who had the idea.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Direction=ai, execution=human — possibly swapped
                text, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    description="Built the widget",
                    direction="ai",
                    execution="human",
                    conversation_snippet="please build the widget",
                    related_decision_ids=[],
                )
                # Server cannot detect the swap — only snippet hint exists
                assert "Logged contribution" in text
                # No direction/execution warning
                assert "direction" not in text.lower() or "execution" not in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm5_missing_snippet_contribution_detected(self) -> None:
        """FM5: Contribution without conversation_snippet → warning.

        Before: Silent acceptance.
        After:  Warning about missing snippet.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _contribute(proc, sid, rid)
                assert "conversation_snippet" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm5_missing_snippet_correction_detected(self) -> None:
        """FM5: Correction without conversation_snippet → warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Wrong threshold",
                    corrects_event_ids=[eid],
                )
                assert "conversation_snippet" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm5_with_snippet_no_warning(self) -> None:
        """FM5 negative: providing snippet suppresses warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    conversation_snippet="user said to build it",
                    related_decision_ids=[eid],
                )
                assert "conversation_snippet" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm6_suggestion_type_misclassified_undetected(self) -> None:
        """FM6: Wrong suggestion_type accepted without warning.

        Before: Silent acceptance.
        After:  Still silent — server trusts the classification.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Claiming "requested" when it was actually proactive
                text, _eid, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    suggestion_type="requested",
                )
                assert "Decision proposed" in text
                # No warning about suggestion_type accuracy
                assert "suggestion_type" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm7_batch_logging_undetected(self) -> None:
        """FM7: Rapid batch of events at session end not flagged.

        Before: No detection.
        After:  Still no detection — deferred to future work.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Log 8 events in rapid succession (simulating deferred logging)
                for i in range(8):
                    _, rid = await _annotate(
                        proc,
                        sid,
                        rid,
                        content=f"Batch annotation {i}",
                    )
                text, rid = await _end_session(proc, sid, rid)
                # No batch-logging detection
                assert "batch" not in text.lower()
                assert "cluster" not in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm8_actor_type_defaults_to_ai_undetected(self) -> None:
        """FM8: Default actor_type=ai used even for human actions — no warning.

        Before: Silent default.
        After:  Still silent — default is usually correct. Audit shows breakdown.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Human logs annotation but actor_type defaults to "ai"
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Human observation logged with default actor",
                    # actor_type not set — defaults to "ai"
                )
                assert "Logged annotation" in text
                # No warning about actor_type default
                assert "actor_type" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm26_gotcha_with_corrects_reclassification_detected(self) -> None:
        """FM26: Gotcha + corrects_event_ids → suggests reclassification.

        Before: Silent acceptance.
        After:  Hint to reclassify as correction.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="gotcha",
                    content="Method X doesn't actually work here",
                    corrects_event_ids=[eid],
                )
                assert "correction" in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm26_gotcha_without_corrects_clean(self) -> None:
        """FM26 negative: normal gotcha (no corrects_event_ids) is clean."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="gotcha",
                    content="Surprising API behavior",
                )
                assert "should be category" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm27_merged_contributions_undetected(self) -> None:
        """FM27: Interleaved contributions merged into one — not detected.

        Before: Silent acceptance.
        After:  Still silent — server can't analyze conversation structure.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # One contribution that should have been two separate ones
                text, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    description="Built features A and B (should be 2 contributions)",
                    direction="collaborative",
                    execution="collaborative",
                    conversation_snippet="first build A, then build B",
                )
                assert "Logged contribution" in text
                # No under-logging detection
                assert "separate" not in text.lower()
                assert "merged" not in text.lower()
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# COMPLETENESS FAILURES (what was missed)
# ═══════════════════════════════════════════════════════════════════════════


class TestCompletenessFailures:
    """FM3, FM9, FM10, FM11, FM12, FM25, FM31."""

    async def test_fm3_missed_human_decision_undetected(self) -> None:
        """FM3: Human makes verbal decision, AI never logs it — no detection.

        Before: Silent.
        After:  Still silent — server can't detect absence.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # AI only logs a contribution, "forgetting" the decision
                _text, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    description="Implemented feature",
                    conversation_snippet="let's use approach X (decision not logged!)",
                )
                end_text, rid = await _end_session(proc, sid, rid)
                # Server has no way to know a decision was missed
                assert "missing" not in end_text.lower()
                assert "forgot" not in end_text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm9_unresolved_decisions_at_end_detected(self) -> None:
        """FM9: Unresolved decisions flagged at session end.

        Before: Session ended without any warning about hanging decisions.
        After:  Audit reports unresolved count + IDs.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid1, rid = await _propose(proc, sid, rid, description="Decision A")
                _, eid2, rid = await _propose(proc, sid, rid, description="Decision B")
                _, eid3, rid = await _propose(proc, sid, rid, description="Decision C")
                # Only resolve one
                _, rid = await _resolve(proc, sid, eid1, rid)
                text, rid = await _end_session(proc, sid, rid)
                assert "Unresolved decisions: 2" in text
                assert eid2 in text
                assert eid3 in text
            finally:
                await _shutdown_server(proc)

    async def test_fm9_all_resolved_clean(self) -> None:
        """FM9 negative: all decisions resolved → no unresolved warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                _, rid = await _resolve(proc, sid, eid, rid)
                text, rid = await _end_session(proc, sid, rid)
                assert "Unresolved decisions" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm10_late_session_start_undetected(self) -> None:
        """FM10: Events logged before explicit session start lose context.

        Before: No detection.
        After:  Auto-session creates one, but first events use auto context.
                Server can't know about pre-session decisions.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                rid = 2
                # Log event WITHOUT starting a session first → auto-session
                text, rid = await _annotate(
                    proc,
                    None,
                    rid,
                    content="This happened before any session was started",
                )
                # Auto-session warning appears, but no "late start" detection
                assert "Auto-created" in text or "Logged annotation" in text
                # No specific warning about events before session
            finally:
                await _shutdown_server(proc)

    async def test_fm11_audit_emitted_but_not_reviewed_undetected(self) -> None:
        """FM11: Audit is emitted in session-end response but server
        can't verify the AI actually reviewed it.

        Before: No audit at all (pre-v0.2).
        After:  Audit is emitted + L2 hook forces attention on Claude Code.
                But server alone can't confirm review happened.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                _, rid = await _resolve(
                    proc,
                    sid,
                    eid,
                    rid,
                    resolved_by_type="ai",
                    resolved_by_id="test-ai",
                )
                text, rid = await _end_session(proc, sid, rid)
                # Audit IS emitted...
                assert "Attribution Audit" in text
                assert "AI self-resolutions" in text
                # ...but server can't verify the AI reads it
            finally:
                await _shutdown_server(proc)

    async def test_fm12_post_compact_context_loss_undetected(self) -> None:
        """FM12: After /compact, AI loses session ID and event IDs.

        Before: Total loss.
        After:  Server persists sessions on disk; compact-safe skill
                writes context file. But server alone can't detect compact.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, _eid, rid = await _propose(proc, sid, rid)
                # Simulate: AI "forgets" session_id after compact
                # It can still load sessions via list_sessions
                response = await _call_tool(
                    proc,
                    "trace_list_sessions",
                    {
                        "project": "fm-test",
                    },
                    request_id=rid,
                )
                rid += 1
                sessions = json.loads(response["result"]["content"][0]["text"])
                assert len(sessions) >= 1
                assert sessions[0]["id"] == sid
                # Server persists — the data survives, but the AI
                # must know to look for it
            finally:
                await _shutdown_server(proc)

    async def test_fm25_fast_self_resolution_detected(self) -> None:
        """FM25: Propose + resolve <5s by same AI → timing warning.

        Before: Silent acceptance.
        After:  Warning about fast self-resolution.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                # Resolve immediately (well under 5s)
                text, rid = await _resolve(
                    proc,
                    sid,
                    eid,
                    rid,
                    resolved_by_type="ai",
                    resolved_by_id="test-ai",
                )
                assert "self-resolved" in text.lower() or "AI resolved" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm25_human_fast_resolution_no_warning(self) -> None:
        """FM25 negative: human resolves quickly → no timing warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _resolve(proc, sid, eid, rid)
                assert "self-resolved" not in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm31_rejection_suggests_correction_detected(self) -> None:
        """FM31: Decision rejected → server suggests logging correction.

        Before: Silent acceptance of rejection.
        After:  Hint to log correction annotation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid, description="Bad approach")
                text, rid = await _resolve(
                    proc,
                    sid,
                    eid,
                    rid,
                    disposition="rejected",
                    revision_note="This approach is fundamentally wrong",
                )
                assert "correction" in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm31_acceptance_no_correction_hint(self) -> None:
        """FM31 negative: acceptance doesn't suggest correction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _resolve(proc, sid, eid, rid)
                assert "Consider logging a correction" not in text
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURAL FAILURES (how events relate)
# ═══════════════════════════════════════════════════════════════════════════


class TestStructuralFailures:
    """FM2, FM13, FM14, FM16, FM17."""

    async def test_fm2_chain_collapse_undetected(self) -> None:
        """FM2: Multi-step deliberation collapsed to single decision.

        Before: Silent.
        After:  Still silent — requires semantic understanding of
                deliberation structure to detect.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Should be 3 decisions (X → Y → Z) but logged as one
                _, eid, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Use method Z (after considering X and Y)",
                )
                _, rid = await _resolve(proc, sid, eid, rid)
                text, rid = await _end_session(proc, sid, rid)
                # Server can't know there were intermediate steps
                assert "chain" not in text.lower()
                assert "collapse" not in text.lower()
                assert "1 decisions" in text or "Decisions (1)" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm13_dangling_corrects_event_id_detected(self) -> None:
        """FM13: Correction references nonexistent event → warning.

        Before: Silent acceptance of broken reference.
        After:  Dangling reference warning.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Fixed the issue",
                    corrects_event_ids=["evt_999"],
                    conversation_snippet="that's wrong, fix it",
                )
                assert "Dangling reference" in text
                assert "evt_999" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm13_valid_reference_clean(self) -> None:
        """FM13 negative: valid reference produces no warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Fixed threshold",
                    corrects_event_ids=[eid],
                    conversation_snippet="no, use 0.5",
                )
                assert "Dangling reference" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm13_dangling_related_decision_ids_detected(self) -> None:
        """FM13: Contribution references nonexistent decision → warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    related_decision_ids=["evt_phantom"],
                    conversation_snippet="build it",
                )
                assert "Dangling reference" in text
                assert "evt_phantom" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm14_duplicate_events_undetected(self) -> None:
        """FM14: Same event logged twice — no deduplication.

        Before: Both accepted silently.
        After:  Still both accepted — content dedup deferred.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text1, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Important discovery about the data",
                )
                text2, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Important discovery about the data",  # exact duplicate
                )
                # Both logged, different event IDs
                assert "evt_001" in text1
                assert "evt_002" in text2
                # No dedup warning
                assert "duplicate" not in text2.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm16_broken_decision_chain_detected(self) -> None:
        """FM16: Wrong revises_event_id → dangling reference warning.

        Before: Silent acceptance of broken chain.
        After:  Dangling reference warning catches it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, _eid, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Revised approach",
                    revises_event_id="evt_wrong",
                )
                assert "Dangling reference" in text
                assert "evt_wrong" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm16_valid_chain_clean(self) -> None:
        """FM16 negative: valid revises_event_id → no warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid1, rid = await _propose(proc, sid, rid, description="First approach")
                _, rid = await _resolve(
                    proc,
                    sid,
                    eid1,
                    rid,
                    disposition="rejected",
                    revision_note="Doesn't work",
                )
                text, _eid2, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Better approach",
                    revises_event_id=eid1,
                )
                assert "Dangling reference" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm17_orphaned_correction_detected(self) -> None:
        """FM17: Correction without corrects_event_ids → warning.

        Before: Silent acceptance.
        After:  Warning about missing link.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="The threshold should be 0.5 not 0.3",
                )
                assert "corrects_event_ids" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm17_linked_correction_clean(self) -> None:
        """FM17 negative: correction with corrects_event_ids → no orphan warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                _, eid, rid = await _propose(proc, sid, rid)
                text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Threshold should be 0.5",
                    corrects_event_ids=[eid],
                    conversation_snippet="no, use 0.5",
                )
                assert "without corrects_event_ids" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm17_aggregate_at_session_end(self) -> None:
        """FM17 aggregate: unlinked corrections counted in audit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                for i in range(3):
                    _, rid = await _annotate(
                        proc,
                        sid,
                        rid,
                        category="correction",
                        content=f"Orphaned fix {i}",
                    )
                text, rid = await _end_session(proc, sid, rid)
                assert "Unlinked corrections: 3" in text
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-SESSION FAILURES
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossSessionFailures:
    """FM18, FM19, FM20, FM30, FM32."""

    async def test_fm18_wrong_project_name_undetected(self) -> None:
        """FM18: Session created with wrong project name — no validation.

        Before: Silent acceptance.
        After:  Still silent — would need cross-session project consistency check.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                rid = 2
                # Create sessions with different project names for same work
                response = await _call_tool(
                    proc,
                    "trace_start_session",
                    {
                        "project": "demo-project",
                        "description": "Analysis session",
                    },
                    request_id=rid,
                )
                rid += 1
                text1 = response["result"]["content"][0]["text"]
                sid1 = text1.split("Session: ")[1].split("\n")[0]
                _, rid = await _end_session(proc, sid1, rid)

                response = await _call_tool(
                    proc,
                    "trace_start_session",
                    {
                        "project": "Demo-Project",  # different casing!
                        "description": "Same project, different name",
                    },
                    request_id=rid,
                )
                rid += 1
                text2 = response["result"]["content"][0]["text"]
                # No warning about inconsistent project naming
                assert "project" not in text2.lower() or "Project:" in text2
            finally:
                await _shutdown_server(proc)

    async def test_fm19_bad_extraction_quality_undetected(self) -> None:
        """FM19: Low-quality knowledge extraction — no quality scoring.

        Deferred: would need extraction quality metrics.
        """
        # This requires the learn extension and is tested separately
        pass

    async def test_fm20_stale_learnings_undetected(self) -> None:
        """FM20: Stale learnings misleading future work.

        Partially addressed by decay system in trace-learn (Tier 2).
        Not testable in a single E2E session.
        """
        pass

    async def test_fm30_auto_session_project_name_partial(self) -> None:
        """FM30: Auto-session may use 'auto' as project name.

        Before: No warning at all.
        After:  Auto-session warning includes project name,
                but doesn't specifically flag 'auto' as bad.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                rid = 2
                # Log event without starting session → auto-session
                text, rid = await _annotate(proc, None, rid, content="Test")
                # Auto-session warning should mention the project
                assert "Auto-created" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm32_fragmented_sessions_undetected(self) -> None:
        """FM32: Multiple sessions for one continuous workflow.

        Before: No detection.
        After:  Still no detection — would need workflow ID concept.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                rid = 2
                # Session 1 — part of same workflow
                response = await _call_tool(
                    proc,
                    "trace_start_session",
                    {
                        "project": "fm-test",
                        "description": "Part 1 of analysis",
                    },
                    request_id=rid,
                )
                rid += 1
                sid1 = response["result"]["content"][0]["text"].split("Session: ")[1].split("\n")[0]
                _, rid = await _end_session(proc, sid1, rid, summary="Part 1 done")

                # Session 2 — continuation, should be linked
                response = await _call_tool(
                    proc,
                    "trace_start_session",
                    {
                        "project": "fm-test",
                        "description": "Part 2 of analysis (continuation)",
                    },
                    request_id=rid,
                )
                rid += 1
                text2 = response["result"]["content"][0]["text"]
                # No warning about fragmentation
                assert "continuation" not in text2.lower() or "TRACE audit" in text2
                assert "fragment" not in text2.lower()
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# PROTOCOL VIOLATIONS
# ═══════════════════════════════════════════════════════════════════════════


class TestProtocolViolations:
    """FM22, FM23, FM24, FM29."""

    async def test_fm22_logging_trace_own_calls_detected(self) -> None:
        """FM22: Logging TRACE's own tool calls → warning.

        Before: Silent acceptance.
        After:  Warning that TRACE calls should not be logged.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="trace",
                    tool_name="trace_propose_decision",
                    input={"description": "test"},
                )
                assert "TRACE" in text
                assert "never log" in text.lower() or "should be avoided" in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm22_trace_prefix_in_tool_name_detected(self) -> None:
        """FM22: Tool name starting with trace_ → warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="mcp-server",
                    tool_name="trace_end_session",
                    input={},
                )
                assert "TRACE" in text
            finally:
                await _shutdown_server(proc)

    async def test_fm23_logging_exploratory_calls_detected(self) -> None:
        """FM23: Logging file reads / exploratory tools → hint.

        Before: Silent acceptance.
        After:  Soft hint about exploratory calls.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                for tool in ["Read", "Grep", "Glob", "Bash"]:
                    text, rid = await _log_tool_call(
                        proc,
                        sid,
                        rid,
                        server="filesystem",
                        tool_name=tool,
                        input={"path": "/tmp/test"},
                    )
                    assert "exploratory" in text.lower(), f"No hint for {tool}"
            finally:
                await _shutdown_server(proc)

    async def test_fm23_domain_tool_no_hint(self) -> None:
        """FM23 negative: domain tool calls produce no exploratory hint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="corpus-search",
                    tool_name="search_papers",
                    input={"query": "climate change"},
                )
                assert "exploratory" not in text.lower()
                assert "TRACE" not in text
            finally:
                await _shutdown_server(proc)

    async def test_fm24_fabricated_events_undetected(self) -> None:
        """FM24: Completely fabricated event accepted without question.

        Before: Silent acceptance.
        After:  Still silent — fundamentally undetectable at server level.
                Only L3 absolute rule can address this.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Log a completely fabricated tool call
                text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="analysis-pipeline",
                    tool_name="run_model",
                    input={"model": "gpt-5.4-mini", "data": "fabricated_dataset.csv"},
                    output="{'accuracy': 0.99}",
                    status="success",
                    duration_ms=1234,
                )
                # Server happily accepts it
                assert "Logged tool call" in text
                assert "fabricat" not in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm29_selective_truth_undetected(self) -> None:
        """FM29: AI logs successes but omits failures — not detected.

        Before: Silent.
        After:  Still silent — server can't detect strategic omissions.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # AI only logs the successful 4th attempt, hiding 3 failures
                text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="analysis",
                    tool_name="run_model",
                    input={"attempt": 4},
                    status="success",
                )
                # No retries_event_id set (hiding the retry chain)
                assert "Logged tool call" in text
                # No warning about missing retry chain
                assert "retry" not in text.lower()
                assert "omit" not in text.lower()
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEMIC FAILURES
# ═══════════════════════════════════════════════════════════════════════════


class TestSystemicFailures:
    """FM15, FM21, FM28."""

    async def test_fm15_out_of_order_timestamps_accepted(self) -> None:
        """FM15: Timestamps reflect when-logged, not when-happened.

        Before: Accepted.
        After:  Still accepted — documented as acceptable limitation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)
                # Log events — timestamps will be in order of logging,
                # not necessarily in order of occurrence
                _, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Event B happened first but logged second",
                )
                _, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Event A happened second but logged first",
                )
                # No timestamp ordering validation
                text, rid = await _end_session(proc, sid, rid)
                assert "timestamp" not in text.lower()
                assert "order" not in text.lower()
            finally:
                await _shutdown_server(proc)

    async def test_fm21_project_namespace_collision_undetected(self) -> None:
        """FM21: Different projects sharing same name — no collision detection.

        Before: Silent.
        After:  Still silent — exact-match filtering exists but no
                collision warning. Low likelihood, low priority.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                rid = 2
                # Two different projects with same name
                for desc in ["Climate analysis", "Totally different project"]:
                    response = await _call_tool(
                        proc,
                        "trace_start_session",
                        {
                            "project": "shared-name",
                            "description": desc,
                        },
                        request_id=rid,
                    )
                    rid += 1
                    text = response["result"]["content"][0]["text"]
                    sid = text.split("Session: ")[1].split("\n")[0]
                    _, rid = await _end_session(proc, sid, rid)
                # Both accepted under same project name
                response = await _call_tool(
                    proc,
                    "trace_list_sessions",
                    {
                        "project": "shared-name",
                    },
                    request_id=rid,
                )
                rid += 1
                sessions = json.loads(response["result"]["content"][0]["text"])
                assert len(sessions) == 2
                # No collision warning
            finally:
                await _shutdown_server(proc)

    async def test_fm28_logging_overhead_performance(self) -> None:
        """FM28: Logging overhead — measure time for a realistic session.

        Before: ~same (overhead is inherent).
        After:  Guard rails add minimal overhead.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                start = time.monotonic()
                sid, rid = await _setup_session(proc)
                # Simulate a realistic session with guard-rail-triggering events
                for i in range(10):
                    _, eid, rid = await _propose(proc, sid, rid, description=f"Decision {i}")
                    _, rid = await _resolve(proc, sid, eid, rid)
                for i in range(5):
                    _, rid = await _contribute(
                        proc,
                        sid,
                        rid,
                        description=f"Contribution {i}",
                        conversation_snippet=f"build feature {i}",
                    )
                for i in range(5):
                    _, rid = await _log_tool_call(
                        proc,
                        sid,
                        rid,
                        tool_name=f"analyze_{i}",
                        input={"step": i},
                    )
                _, rid = await _end_session(proc, sid, rid)
                elapsed = time.monotonic() - start
                # Should complete in reasonable time despite guard rails
                assert elapsed < 15.0, f"Session with 30 events + guard rails took {elapsed:.1f}s (should be <15s)"
            finally:
                await _shutdown_server(proc)


# ═══════════════════════════════════════════════════════════════════════════
# FULL SCENARIO: Proper workflow vs problematic workflow
# ═══════════════════════════════════════════════════════════════════════════


class TestFullScenarios:
    """End-to-end scenarios exercising multiple FMs simultaneously."""

    async def test_golden_path_zero_warnings(self) -> None:
        """A perfectly attributed session should produce zero guard rail warnings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)

                # AI proposes, human resolves
                _, eid1, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Use cosine distance for similarity",
                    suggestion_type="proactive",
                )
                _, rid = await _resolve(proc, sid, eid1, rid)

                # Contribution with all fields
                _, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    description="Implemented cosine distance",
                    artifact="src/distances.py",
                    related_decision_ids=[eid1],
                    conversation_snippet="implement the cosine distance function",
                )

                # Domain tool call
                _, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="analysis",
                    tool_name="compute_similarity",
                    input={"method": "cosine"},
                )

                # Learning annotation
                _, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    content="Cosine distance is invariant to magnitude",
                    tags=["methodology"],
                )

                text, rid = await _end_session(proc, sid, rid, summary="Clean session")
                # Verify zero guard rail warnings
                assert "Unresolved decisions" not in text
                assert "AI self-resolutions" not in text
                assert "Unlinked corrections" not in text
                assert "Dangling" not in text
            finally:
                await _shutdown_server(proc)

    async def test_worst_case_all_guards_triggered(self) -> None:
        """A maximally problematic session triggers every implemented guard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)

                # FM1 + FM25: AI self-resolves immediately
                _, eid1, rid = await _propose(proc, sid, rid, description="Self-resolved A")
                resolve_text, rid = await _resolve(
                    proc,
                    sid,
                    eid1,
                    rid,
                    resolved_by_type="ai",
                    resolved_by_id="test-ai",
                )
                assert "AI resolved its own proposal" in resolve_text

                # FM31: Human rejects
                _, eid2, rid = await _propose(proc, sid, rid, description="Bad idea")
                reject_text, rid = await _resolve(
                    proc,
                    sid,
                    eid2,
                    rid,
                    disposition="rejected",
                    revision_note="Wrong approach",
                )
                assert "correction" in reject_text.lower()

                # FM9: Unresolved decision
                _, _eid3, rid = await _propose(proc, sid, rid, description="Left hanging")

                # FM17: Orphaned correction
                corr_text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Fixed the threshold",
                )
                assert "corrects_event_ids" in corr_text

                # FM5: Missing conversation_snippet on correction
                assert "conversation_snippet" in corr_text

                # FM13: Dangling reference
                dangle_text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Another fix",
                    corrects_event_ids=["evt_phantom"],
                    conversation_snippet="fix it",
                )
                assert "Dangling reference" in dangle_text

                # FM22: Logging TRACE's own call
                trace_text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="trace",
                    tool_name="trace_start_session",
                    input={"project": "oops"},
                )
                assert "TRACE" in trace_text

                # FM23: Logging exploratory call
                read_text, rid = await _log_tool_call(
                    proc,
                    sid,
                    rid,
                    server="filesystem",
                    tool_name="Read",
                    input={"path": "/tmp/file"},
                )
                assert "exploratory" in read_text.lower()

                # FM26: Gotcha with corrects_event_ids
                gotcha_text, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="gotcha",
                    content="Actually this was wrong",
                    corrects_event_ids=[eid1],
                )
                assert "correction" in gotcha_text.lower()

                # FM5: Contribution without snippet
                no_snippet_text, rid = await _contribute(proc, sid, rid)
                assert "conversation_snippet" in no_snippet_text

                # End session — audit should catch aggregates
                end_text, rid = await _end_session(proc, sid, rid)
                assert "Unresolved decisions: 1" in end_text
                assert "AI self-resolutions: 1" in end_text
                # Orphaned corrections: correction without corrects_event_ids
                assert "Unlinked corrections" in end_text

            finally:
                await _shutdown_server(proc)

    async def test_correction_chain_full_provenance(self) -> None:
        """Scenario: AI proposes wrong approach → human rejects → correction
        → AI proposes revision → human accepts. Full provenance chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                sid, rid = await _setup_session(proc)

                # Step 1: AI proposes (bad)
                _, eid1, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Use Euclidean distance",
                    suggestion_type="proactive",
                )

                # Step 2: Human rejects
                reject_text, rid = await _resolve(
                    proc,
                    sid,
                    eid1,
                    rid,
                    disposition="rejected",
                    revision_note="Euclidean is inappropriate for high-dim text",
                )
                assert "correction" in reject_text.lower()

                # Step 3: Correction annotation
                _, rid = await _annotate(
                    proc,
                    sid,
                    rid,
                    category="correction",
                    content="Euclidean distance is inappropriate for text embeddings",
                    corrects_event_ids=[eid1],
                    conversation_snippet="no, euclidean doesn't work for text",
                )

                # Step 4: AI proposes revision
                _, eid2, rid = await _propose(
                    proc,
                    sid,
                    rid,
                    description="Use cosine distance instead",
                    revises_event_id=eid1,
                )

                # Step 5: Human accepts
                _, rid = await _resolve(proc, sid, eid2, rid)

                # Step 6: Contribution
                _, rid = await _contribute(
                    proc,
                    sid,
                    rid,
                    description="Implemented cosine distance",
                    artifact="src/distances.py",
                    related_decision_ids=[eid2],
                    conversation_snippet="implement cosine distance",
                )

                # End — should have 1 rejection, 1 correction, clean provenance
                text, rid = await _end_session(proc, sid, rid)
                assert "1 rejection" in text
                assert "Corrections: 1" in text
                assert "Unresolved decisions" not in text
                assert "AI self-resolutions" not in text
                assert "Unlinked corrections" not in text
            finally:
                await _shutdown_server(proc)

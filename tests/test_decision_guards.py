"""Tests for TRACE decision guard rails and failure mode coverage.

Tests the 7 essential guard rails (FM1, FM5, FM13, FM17, FM22, FM31, audit)
and probes all 32 failure modes to document detection coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, session_tools


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


async def _make_session(
    storage: JsonFileStorage,
    active: dict[str, Session],
    project: str = "guard-test",
) -> Session:
    """Helper to create a session and return the Session object."""
    result = await session_tools.start_session(
        storage, active, project=project, description="Guard rail test session"
    )
    session_id = result.split("Session: ")[1].split("\n")[0]
    return active[session_id]


# ── Class 1: FM1 — Same-Actor Self-Resolution ────────────────────────────


class TestSameActorWarning:
    """FM1: AI should not resolve its own proposals."""

    async def test_ai_self_resolves_gets_warning(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """AI proposes + AI resolves -> warning in result."""
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        # Extract clean event ID (strip any referential integrity warnings)
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        assert "AI resolved its own proposal" in result

    async def test_human_resolves_ai_proposal_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """AI proposes + human resolves -> no self-resolution warning."""
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        assert "AI resolved its own proposal" not in result

    async def test_human_self_resolves_warns(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """v0.4.1: human\u2192human same-instance self-resolution NOW warns.

        Inverted from v0.3.x behavior. Per spec \u00a73.6 Proposer Identity Rule
        and the Attribution rule, the proposer should differ from the
        resolver in multi-actor workflows. The waggle audit's evt_025
        was exactly this pattern (human-proposes plan\u2192human-accepts) and
        was silently allowed by the v0.3.x ai-only FM1.

        The warning uses the v0.4.1 general-case message rather than the
        ai-specific one (which is reserved for ai\u2192ai backward compat).
        """
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="human", proposed_by_id="researcher",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        # v0.4.1: same-instance human\u2192human DOES warn (general-case message)
        assert "Same actor instance proposed and resolved this decision" in result
        assert "spec \u00a73.6" in result
        # The ai-specific message should NOT appear for human\u2192human
        assert "AI resolved its own proposal" not in result

    async def test_human_different_instance_self_resolves_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Two different humans proposing/resolving: NO warning (different instances).

        v0.4.1: the same-instance rule is on (type, id) tuple equality.
        Same type but different id is not self-resolution.
        """
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="human", proposed_by_id="researcher-alice",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher-bob",
        )
        # Different ids \u2192 not same-instance \u2192 no self-resolution warning
        assert "Same actor instance" not in result
        assert "AI resolved its own proposal" not in result

    async def test_ai_resolves_human_proposal_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Human proposes + AI resolves -> no self-resolution warning (different actors)."""
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="human", proposed_by_id="researcher",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        assert "AI resolved its own proposal" not in result

    async def test_suppress_env_var(
        self, storage: JsonFileStorage, active: dict[str, Session], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TRACE_SUPPRESS_SELF_RESOLVE_WARNING=true -> no per-event warning."""
        monkeypatch.setenv("TRACE_SUPPRESS_SELF_RESOLVE_WARNING", "true")
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        assert "AI resolved its own proposal" not in result

    async def test_warning_persisted_on_decision(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Guard rail warnings are persisted in the DecisionData.warnings field."""
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        # Check the event in the session
        target = next(e for e in session.events if e.id == evt_id)
        assert target.decision is not None
        assert len(target.decision.warnings) > 0
        assert any("AI resolved" in w for w in target.decision.warnings)


# ── Class 2: FM31 — Rejection Suggests Correction ────────────────────────


class TestRejectionHint:
    """FM31: When a decision is rejected, suggest logging a correction."""

    async def test_rejection_suggests_correction(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="rejected",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Method X doesn't apply here",
        )
        assert "correction" in result.lower()

    async def test_acceptance_no_correction_hint(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session,
            event_id=evt_id, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        assert "correction" not in result.lower() or "correction" in result.lower() and "rejected" in result.lower()
        # More precise: no correction suggestion for accepted decisions
        assert "Consider logging a correction" not in result


# ── Class 3: FM17 — Orphaned Corrections ─────────────────────────────────


class TestOrphanedCorrections:
    """FM17: Corrections should link to corrected events."""

    async def test_correction_without_links_warns(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="The threshold should be 0.5 not 0.3",
        )
        assert "corrects_event_ids" in result

    async def test_correction_with_links_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        # First create an event to correct
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use threshold 0.3",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="The threshold should be 0.5 not 0.3",
            corrects_event_ids=[evt_id],
            conversation_snippet="no, use 0.5 instead",
        )
        # Should not have the orphaned correction warning
        assert "without corrects_event_ids" not in result

    async def test_correction_missing_snippet_warns(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM5: Correction without conversation_snippet."""
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use threshold 0.3",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="Threshold should be 0.5",
            corrects_event_ids=[evt_id],
            # No conversation_snippet
        )
        assert "conversation_snippet" in result


# ── Class 4: FM5 — Missing Conversation Snippet ──────────────────────────


class TestMissingSnippet:
    """FM5: Contributions and corrections should have conversation_snippet."""

    async def test_contribution_missing_snippet_warns(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_contribution(
            storage, session,
            description="Implemented distance calc",
            direction="human", execution="ai",
            artifact="src/distances.py",
        )
        assert "conversation_snippet" in result

    async def test_contribution_with_snippet_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        # Create a real decision so related_decision_ids doesn't dangle
        eid = await decision_tools.propose_decision(
            storage, session,
            description="Use cosine distance",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        result = await logging_tools.log_contribution(
            storage, session,
            description="Implemented distance calc",
            direction="human", execution="ai",
            artifact="src/distances.py",
            conversation_snippet="implement the distance calculation using cosine",
            related_decision_ids=[eid],
        )
        assert "conversation_snippet" not in result


# ── Class 5: FM13 — Dangling Event ID References ─────────────────────────


class TestReferentialIntegrity:
    """FM13/FM16: Event ID references must point to existing events."""

    async def test_dangling_corrects_event_id_raises(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*evt_nonexistent"):
            await logging_tools.log_annotation(
                storage, session,
                category="correction",
                content="Fix the threshold",
                corrects_event_ids=["evt_nonexistent"],
                conversation_snippet="that's wrong",
            )

    async def test_valid_corrects_event_id_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use threshold 0.3",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="Use 0.5 instead",
            corrects_event_ids=[evt_id],
            conversation_snippet="no, use 0.5",
        )
        assert "Dangling reference" not in result

    async def test_dangling_revises_event_id_raises(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*evt_ghost"):
            await decision_tools.propose_decision(
                storage, session,
                description="Revised approach",
                proposed_by_type="ai", proposed_by_id="claude",
                revises_event_id="evt_ghost",
            )

    async def test_dangling_related_decision_ids_raises(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*evt_nope"):
            await logging_tools.log_contribution(
                storage, session,
                description="Built the thing",
                direction="human", execution="ai",
                related_decision_ids=["evt_nope"],
                conversation_snippet="build it",
            )


# ── Class 6: FM22/FM23 — Tool Call Blocklist ──────────────────────────────


class TestToolCallBlocklist:
    """FM22/FM23: Don't log TRACE's own calls or exploratory tools."""

    async def test_trace_tool_call_warns(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_tool_call(
            storage, session,
            server="trace",
            tool_name="trace_start_session",
            input={"project": "test"},
        )
        assert "never log TRACE" in result.lower() or "TRACE" in result

    async def test_exploratory_tool_hint(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_tool_call(
            storage, session,
            server="filesystem",
            tool_name="Read",
            input={"path": "/tmp/file.txt"},
        )
        assert "exploratory" in result.lower()

    async def test_domain_tool_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_tool_call(
            storage, session,
            server="corpus-search",
            tool_name="search_papers",
            input={"query": "climate change"},
        )
        assert "\u26a0\ufe0f" not in result


# ── Class 7: FM26 — Gotcha Reclassification Hint ─────────────────────────


class TestGotchaReclassification:
    """FM26: Gotcha + corrects_event_ids suggests it should be a correction."""

    async def test_gotcha_with_corrects_hints_reclassify(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        evt_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        evt_id = evt_id.split("\n")[0]
        result = await logging_tools.log_annotation(
            storage, session,
            category="gotcha",
            content="Method X doesn't work here",
            corrects_event_ids=[evt_id],
        )
        assert "correction" in result.lower()

    async def test_gotcha_without_corrects_clean(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _make_session(storage, active)
        result = await logging_tools.log_annotation(
            storage, session,
            category="gotcha",
            content="Surprising API behavior",
        )
        assert "should be category" not in result


# ── Class 8: Enhanced Attribution Audit ───────────────────────────────────


class TestAttributionAuditEnhanced:
    """Attribution audit at session end catches aggregate issues."""

    async def test_audit_flags_unresolved(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """3 proposed, 1 resolved -> 2 unresolved."""
        session = await _make_session(storage, active)
        ids = []
        for i in range(3):
            eid = await decision_tools.propose_decision(
                storage, session,
                description=f"Decision {i}",
                proposed_by_type="ai", proposed_by_id="claude",
            )
            ids.append(eid.split("\n")[0])
        # Resolve only the first
        await decision_tools.resolve_decision(
            storage, session,
            event_id=ids[0], disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Unresolved decisions: 2" in result

    async def test_audit_flags_self_resolutions(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """3 AI self-resolved -> count == 3."""
        session = await _make_session(storage, active)
        for i in range(3):
            eid = await decision_tools.propose_decision(
                storage, session,
                description=f"Decision {i}",
                proposed_by_type="ai", proposed_by_id="claude",
            )
            eid = eid.split("\n")[0]
            await decision_tools.resolve_decision(
                storage, session,
                event_id=eid, disposition="accepted",
                resolved_by_type="ai", resolved_by_id="claude",
            )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "AI self-resolutions: 3" in result

    async def test_audit_clean_session(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Properly attributed session -> no guard rail warnings."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session,
            description="Use cosine distance",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=eid, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Unresolved decisions" not in result
        assert "AI self-resolutions" not in result
        assert "Unlinked corrections" not in result

    async def test_audit_mixed_session(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """2 proper + 1 self-resolve + 1 unresolved -> both counts."""
        session = await _make_session(storage, active)
        # 2 proper
        for i in range(2):
            eid = await decision_tools.propose_decision(
                storage, session,
                description=f"Proper decision {i}",
                proposed_by_type="ai", proposed_by_id="claude",
            )
            eid = eid.split("\n")[0]
            await decision_tools.resolve_decision(
                storage, session,
                event_id=eid, disposition="accepted",
                resolved_by_type="human", resolved_by_id="researcher",
            )
        # 1 self-resolve
        eid = await decision_tools.propose_decision(
            storage, session,
            description="Self-resolved",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=eid, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        # 1 unresolved
        await decision_tools.propose_decision(
            storage, session,
            description="Left hanging",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Unresolved decisions: 1" in result
        assert "AI self-resolutions: 1" in result

    async def test_audit_orphaned_corrections(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """2 corrections without corrects_event_ids -> unlinked count."""
        session = await _make_session(storage, active)
        await logging_tools.log_annotation(
            storage, session,
            category="correction", content="Fix 1",
        )
        await logging_tools.log_annotation(
            storage, session,
            category="correction", content="Fix 2",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Unlinked corrections: 2" in result

    async def test_audit_render_includes_warnings(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Audit warnings are included in the rendered text."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session,
            description="Self-resolved",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=eid, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        audit = session_tools._build_attribution_audit(session)
        rendered = audit.render()
        assert "AI self-resolutions" in rendered
        assert "verify human" in rendered.lower()


# ── Class 9: Full Failure Mode Coverage Probes ────────────────────────────


class TestFailureModeCoverage:
    """Probe tests documenting detection coverage for all 32 failure modes.

    Tests marked with 'DETECTED' verify the guard catches it.
    Tests marked with 'UNDETECTABLE' verify the system correctly does NOT
    false-positive on legitimate uses (since server can't detect these).
    Tests marked with 'NOTED' document that we've considered the FM but
    defer implementation.
    """

    # --- Attribution failures ---

    async def test_fm1_ai_self_resolves_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM1: DETECTED — AI self-resolution triggers warning."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session, description="X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session, event_id=eid, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        assert "AI resolved its own proposal" in result

    async def test_fm4_direction_execution_swap_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM4: UNDETECTABLE — server can't verify who had the idea.
        We only check snippet is present as a weak proxy."""
        session = await _make_session(storage, active)
        # Even a wrong swap looks valid to the server
        result = await logging_tools.log_contribution(
            storage, session,
            description="Built widget",
            direction="ai", execution="human",  # possibly swapped!
            conversation_snippet="build the widget please",
        )
        # No warning about direction/execution — server trusts it
        assert "evt_" in result

    async def test_fm5_missing_snippet_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM5: DETECTED — missing conversation_snippet triggers warning."""
        session = await _make_session(storage, active)
        result = await logging_tools.log_contribution(
            storage, session,
            description="Built it",
            direction="human", execution="ai",
        )
        assert "conversation_snippet" in result

    async def test_fm6_suggestion_type_misclassified_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM6: UNDETECTABLE — server can't verify suggestion_type accuracy."""
        session = await _make_session(storage, active)
        # Misclassified as "requested" when it was actually "proactive"
        eid = await decision_tools.propose_decision(
            storage, session, description="X",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="requested",
        )
        # No warning — server trusts the classification
        assert "evt_" in eid

    async def test_fm7_batch_logging_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM7: NOTED — timestamp clustering detection deferred.
        Events logged rapidly are indistinguishable from legitimate rapid work."""
        session = await _make_session(storage, active)
        for i in range(5):
            await logging_tools.log_annotation(
                storage, session,
                category="learning", content=f"Learning {i}",
            )
        # Currently no batch-logging detection — noted for future
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Session ended" in result

    async def test_fm8_actor_type_default_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM8: NOTED — actor_type defaults to 'ai', which is usually correct
        but could mask human actions. Audit breakdown helps catch this."""
        session = await _make_session(storage, active)
        # Default actor_type="ai" is used
        await logging_tools.log_annotation(
            storage, session,
            category="learning", content="Something",
        )
        evt = session.events[-1]
        assert evt.actor.type == "ai"  # Default — might be wrong but acceptable

    async def test_fm26_gotcha_reclassification_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM26: DETECTED — gotcha + corrects_event_ids suggests correction."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session, description="X",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        result = await logging_tools.log_annotation(
            storage, session,
            category="gotcha", content="Oops",
            corrects_event_ids=[eid],
        )
        assert "correction" in result.lower()

    async def test_fm27_merged_contributions_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM27: UNDETECTABLE — server can't tell if interleaved contributions
        were improperly merged. Would need conversation structure analysis."""
        session = await _make_session(storage, active)
        # One combined contribution where there should have been two
        await logging_tools.log_contribution(
            storage, session,
            description="Built widgets A and B",  # should be 2 separate
            direction="collaborative", execution="collaborative",
            conversation_snippet="build A, then build B",
        )
        # Server can't detect this — it's a single valid contribution
        assert len([e for e in session.events if e.type == "contribution"]) == 1

    # --- Completeness failures ---

    async def test_fm3_missed_logging_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM3: UNDETECTABLE — server can't detect absence of events."""
        session = await _make_session(storage, active)
        # Human makes a decision verbally, AI doesn't log it
        # Server has no way to know
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Session ended" in result  # No warning about missing events

    async def test_fm9_abandoned_session_partially_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM9: PARTIALLY DETECTED — unresolved decisions flagged at session end.
        Truly abandoned sessions (never ended) need external reaper."""
        session = await _make_session(storage, active)
        await decision_tools.propose_decision(
            storage, session, description="Never resolved",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="test"
        )
        assert "Unresolved decisions: 1" in result

    async def test_fm10_late_start_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM10: NOTED — late session start loses early decisions.
        Auto-session mitigates but can't retroactively capture."""
        # This is about the conversation starting before trace_start_session
        # Server can't detect it — it's a client-side timing issue
        pass  # Documented as known limitation

    async def test_fm11_audit_not_reviewed_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM11: NOTED — audit is emitted but we can't force the AI to review it.
        L2 hook (PostToolUse) helps on Claude Code."""
        pass  # Mitigated by L2 hook

    async def test_fm12_post_compact_loss_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM12: NOTED — post-compact context loss of session ID.
        Mitigated by compact-safe skill and server-side session persistence."""
        pass  # Mitigated by existing infrastructure

    async def test_fm25_fast_resolution_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM25: DETECTED — propose + resolve <5s by AI -> timing warning."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session, description="Quick decision",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        # Resolve immediately (< 5s)
        result = await decision_tools.resolve_decision(
            storage, session, event_id=eid, disposition="accepted",
            resolved_by_type="ai", resolved_by_id="claude",
        )
        assert "self-resolved" in result.lower() or "AI resolved" in result

    async def test_fm31_rejection_correction_hint_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM31: DETECTED — rejection suggests correction annotation."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session, description="Bad idea",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid = eid.split("\n")[0]
        result = await decision_tools.resolve_decision(
            storage, session, event_id=eid, disposition="rejected",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Doesn't apply",
        )
        assert "correction" in result.lower()

    # --- Structural failures ---

    async def test_fm2_chain_collapse_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM2: UNDETECTABLE — server can't tell if a multi-step deliberation
        was collapsed into a single decision. Requires semantic understanding."""
        session = await _make_session(storage, active)
        # This looks like a single decision but was actually 3 iterations
        await decision_tools.propose_decision(
            storage, session,
            description="Final approach: use method Z after considering X, Y",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        # Server has no way to know there were intermediate steps
        assert len([e for e in session.events if e.type == "decision"]) == 1

    async def test_fm13_dangling_refs_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM13: DETECTED — dangling event ID references raise ValueError."""
        session = await _make_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*evt_phantom"):
            await logging_tools.log_annotation(
                storage, session,
                category="correction", content="Fix",
                corrects_event_ids=["evt_phantom"],
                conversation_snippet="fix it",
            )

    async def test_fm14_duplicate_events_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM14: NOTED — duplicate event detection deferred.
        Content dedup would need fuzzy matching."""
        session = await _make_session(storage, active)
        await logging_tools.log_annotation(
            storage, session,
            category="learning", content="Important finding",
        )
        await logging_tools.log_annotation(
            storage, session,
            category="learning", content="Important finding",  # duplicate!
        )
        # Currently no dedup — both are logged
        assert len([e for e in session.events if e.type == "annotation"]) == 2

    async def test_fm16_broken_decision_chain_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM16: DETECTED via FM13 — wrong revises_event_id raises ValueError."""
        session = await _make_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*evt_wrong"):
            await decision_tools.propose_decision(
                storage, session,
                description="Revision of phantom",
                proposed_by_type="ai", proposed_by_id="claude",
                revises_event_id="evt_wrong",
            )

    async def test_fm17_orphaned_correction_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM17: DETECTED — correction without corrects_event_ids warns."""
        session = await _make_session(storage, active)
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction", content="Fixed the issue",
        )
        assert "corrects_event_ids" in result

    # --- Cross-session failures ---

    async def test_fm18_wrong_project_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM18: NOTED — project name consistency check deferred."""
        pass  # Would need cross-session analysis

    async def test_fm19_bad_extraction_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM19: NOTED — knowledge extraction quality scoring deferred."""
        pass

    async def test_fm20_stale_learnings_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM20: NOTED — stale learning detection handled by decay system."""
        pass

    async def test_fm30_auto_project_name_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM30: NOTED — auto-session with 'auto' project name.
        Auto-session already warns about this."""
        pass

    async def test_fm32_fragmented_sessions_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM32: NOTED — multiple sessions for one workflow. Would need
        session linking / workflow ID concept."""
        pass

    # --- Protocol violations ---

    async def test_fm22_trace_self_logging_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM22: DETECTED — logging TRACE tool calls triggers warning."""
        session = await _make_session(storage, active)
        result = await logging_tools.log_tool_call(
            storage, session,
            server="trace-mcp",
            tool_name="trace_propose_decision",
            input={"description": "test"},
        )
        assert "TRACE" in result

    async def test_fm23_exploratory_logging_detected(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM23: DETECTED — logging exploratory tools triggers hint."""
        session = await _make_session(storage, active)
        result = await logging_tools.log_tool_call(
            storage, session,
            server="filesystem",
            tool_name="Grep",
            input={"pattern": "test"},
        )
        assert "exploratory" in result.lower()

    async def test_fm24_fabrication_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM24: UNDETECTABLE — server can't distinguish real from fabricated.
        Only L3 absolute rule can address this."""
        session = await _make_session(storage, active)
        # A completely fabricated event looks valid
        await logging_tools.log_annotation(
            storage, session,
            category="learning",
            content="Discovered that X causes Y",  # might be fabricated
        )
        # Server cannot tell — this is fundamentally undetectable
        assert len(session.events) == 1

    async def test_fm29_selective_truth_undetectable(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM29: UNDETECTABLE — server can't detect strategic omissions."""
        session = await _make_session(storage, active)
        # AI logs the success but not the 3 failures that preceded it
        await logging_tools.log_tool_call(
            storage, session,
            server="analysis",
            tool_name="run_model",
            input={"params": "final"},
            status="success",
        )
        # Server has no way to know about the missing failures
        assert len(session.events) == 1

    # --- Systemic ---

    async def test_fm28_logging_overhead_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM28: NOTED — logging overhead is inherent.
        Mitigated by auto-tools and efficient implementation."""
        pass

    async def test_fm15_out_of_order_timestamps_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM15: NOTED — acceptable limitation. Timestamps are
        when-logged not when-happened."""
        pass

    async def test_fm21_project_namespace_collision_noted(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """FM21: NOTED — exact-match project filtering already exists."""
        pass


# ── Class 10: Scenario Simulations ────────────────────────────────────────


class TestScenarioSimulations:
    """End-to-end scenarios testing guard rail interactions."""

    async def test_scenario_proper_workflow(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Fully proper workflow: propose(ai) -> resolve(human) -> contribute -> end.
        Should produce zero warnings."""
        session = await _make_session(storage, active)
        eid = await decision_tools.propose_decision(
            storage, session,
            description="Use cosine distance for text similarity",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="proactive",
        )
        eid = eid.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=eid, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        await logging_tools.log_contribution(
            storage, session,
            description="Implemented cosine distance calculation",
            direction="human", execution="ai",
            artifact="src/distances.py",
            related_decision_ids=[eid],
            conversation_snippet="implement the cosine distance function",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id,
            summary="Implemented distance calculation",
        )
        assert "Unresolved decisions" not in result
        assert "AI self-resolutions" not in result
        assert "Unlinked corrections" not in result

    async def test_scenario_correction_chain(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """Rejection + correction + retry: proper provenance chain."""
        session = await _make_session(storage, active)
        # AI proposes bad approach
        eid1 = await decision_tools.propose_decision(
            storage, session,
            description="Use Euclidean distance",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        eid1 = eid1.split("\n")[0]
        # Human rejects
        reject_result = await decision_tools.resolve_decision(
            storage, session,
            event_id=eid1, disposition="rejected",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Euclidean is wrong for text embeddings",
        )
        assert "correction" in reject_result.lower()  # FM31 hint
        # Human logs correction
        await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="Euclidean distance is inappropriate for high-dimensional text embeddings",
            corrects_event_ids=[eid1],
            conversation_snippet="no, euclidean doesn't work for text embeddings",
        )
        # AI proposes revised approach
        eid2 = await decision_tools.propose_decision(
            storage, session,
            description="Use cosine distance instead",
            proposed_by_type="ai", proposed_by_id="claude",
            revises_event_id=eid1,
        )
        eid2 = eid2.split("\n")[0]
        await decision_tools.resolve_decision(
            storage, session,
            event_id=eid2, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="Fixed distance metric",
        )
        assert "1 rejection" in result
        assert "Corrections: 1" in result

    async def test_scenario_rapid_fire_performance(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """25 proper + 25 self-resolved decisions. Should complete quickly."""
        import time
        start = time.monotonic()
        session = await _make_session(storage, active)
        for i in range(25):
            eid = await decision_tools.propose_decision(
                storage, session,
                description=f"Proper decision {i}",
                proposed_by_type="ai", proposed_by_id="claude",
            )
            eid = eid.split("\n")[0]
            await decision_tools.resolve_decision(
                storage, session,
                event_id=eid, disposition="accepted",
                resolved_by_type="human", resolved_by_id="researcher",
            )
        for i in range(25):
            eid = await decision_tools.propose_decision(
                storage, session,
                description=f"Self-resolved {i}",
                proposed_by_type="ai", proposed_by_id="claude",
            )
            eid = eid.split("\n")[0]
            await decision_tools.resolve_decision(
                storage, session,
                event_id=eid, disposition="accepted",
                resolved_by_type="ai", resolved_by_id="claude",
            )
        result = await session_tools.end_session(
            storage, active, session_id=session.id, summary="perf test",
        )
        elapsed = time.monotonic() - start
        assert "AI self-resolutions: 25" in result
        assert elapsed < 10.0, f"Took {elapsed:.1f}s — should be under 10s"

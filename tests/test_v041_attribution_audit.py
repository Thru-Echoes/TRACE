"""E2E tests for v0.4.1 AttributionAudit extensions (L5.1-L5.8).

Exercises the new audit metrics via real storage and real session
lifecycle. Each test builds a session that should trigger one of the
new audit signals, runs the full append → build-audit → render path,
and asserts on the structured fields AND on the rendered output.

If these tests fail, the v0.4.1 audit visibility is broken and the
silent-warning failure modes the waggle audit identified are NOT
being surfaced.

Fail-loudly contract: every test raises on incorrect behavior. Real
JSON-file storage, real session round-trip, no mocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    EventContext,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor, Environment
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools.session_tools import (
    _build_attribution_audit,
    _is_explicit_absence,
    append_event,
)


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


def _make_session(session_id: str, *, with_claude_code_env: bool = True) -> Session:
    """Build a real multi-actor session for these audit tests."""
    env = Environment(client="Claude Code") if with_claude_code_env else None
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="trace-mcp-v041-audit-test",
            participants=[
                Actor(type="human", id="researcher"),
                Actor(type="ai", id="claude-opus-4.7"),
            ],
            environment=env,
        ),
    )


# ── L5.2: _is_explicit_absence helper boundary cases ─────────────────────


class TestExplicitAbsenceHelper:
    """Allow-list semantics + whitespace tolerance per v0.4.1 §3.4.1."""

    def test_autonomous_stretch_marker(self) -> None:
        assert _is_explicit_absence("<autonomous-stretch>")

    def test_no_recent_user_message_marker(self) -> None:
        assert _is_explicit_absence("<no recent user message>")

    def test_marker_with_leading_whitespace(self) -> None:
        # v0.4.1 amendment A6: .strip() so producers that introduce whitespace
        # don't accidentally produce a "missing snippet" instead of "explicit absence"
        assert _is_explicit_absence("  <autonomous-stretch>  ")

    def test_none_is_not_explicit_absence(self) -> None:
        """None means controller forgot — NOT honest absence."""
        assert not _is_explicit_absence(None)

    def test_empty_string_is_not_explicit_absence(self) -> None:
        assert not _is_explicit_absence("")

    def test_arbitrary_angle_bracketed_text_does_not_match(self) -> None:
        """Round 3 A6: prevent over-match on real user text like <script>."""
        assert not _is_explicit_absence("<script>alert(1)</script>")
        assert not _is_explicit_absence("<some other angle thing>")
        assert not _is_explicit_absence("<my draft>")

    def test_real_user_message_does_not_match(self) -> None:
        assert not _is_explicit_absence("proceed with those steps")
        assert not _is_explicit_absence("I want you to continue with Phase I")

    def test_marker_with_extra_internal_content_does_not_match(self) -> None:
        # Allow-list is exact-string after strip; substrings/prefixes don't match
        assert not _is_explicit_absence("<autonomous-stretch with extra>")


# ── L5.3: Missing-snippet counters ───────────────────────────────────────


class TestMissingSnippetCounters:
    """v0.4.1 §3.4.1: count contributions and corrections missing snippets."""

    async def test_contribution_without_snippet_is_counted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_contrib_missing")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="produced an artifact",
                direction="human",
                execution="ai",
            ),
            # NO conversation_snippet set
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.missing_snippet_contribution_count == 1
        assert audit.missing_snippet_correction_count == 0

    async def test_correction_without_snippet_is_counted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_correction_missing")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(
                category="correction",
                content="something was wrong",
                corrects_event_ids=["external:https://example.com/x"],
            ),
            # NO conversation_snippet set
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.missing_snippet_correction_count == 1
        assert audit.missing_snippet_contribution_count == 0

    async def test_contribution_with_real_snippet_is_not_counted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_contrib_with_snippet")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="produced an artifact",
                direction="human",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="please write the function"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.missing_snippet_contribution_count == 0
        assert audit.explicit_absence_snippet_count == 0


class TestExplicitAbsenceCounters:
    """v0.4.1: snippets set to explicit absence markers count separately."""

    async def test_autonomous_stretch_marker_is_explicit_absence(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_autonomous_marker")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="autonomous work artifact",
                direction="human",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="<autonomous-stretch>"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        # Honest absence — not counted as "missing"
        assert audit.missing_snippet_contribution_count == 0
        assert audit.explicit_absence_snippet_count == 1


# ── L5.4: Structural attribution-warning detector ────────────────────────


class TestAttributionWarningDetector:
    """v0.4.1 §3.6 Proposer Identity Rule: same-instance self-resolution."""

    async def test_human_same_instance_self_resolution_counts(self, storage: JsonFileStorage) -> None:
        """The evt_025 pattern: human proposes and human accepts.

        Pre-v0.4.1 this was invisible. v0.4.1 surfaces it via
        attribution_warning_count.
        """
        session = _make_session("trace_test_human_self_resolve")
        await storage.create_session(session)

        same_human = Actor(type="human", id="researcher")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=same_human,
            decision=DecisionData(
                description="Begin matcher iteration",
                proposed_by=same_human,
                disposition="accepted",
                resolved_by=same_human,
                suggestion_type="requested",
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.attribution_warning_count == 1
        assert event.id in audit.attribution_warning_ids

    async def test_single_actor_human_self_resolution_no_warning(self, storage: JsonFileStorage) -> None:
        """Round-3 A1 / decision evt_016: in a SINGLE-actor session
        ({human} only), human→human same-instance self-resolution must
        NOT increment attribution_warning_count. This is the false
        positive A1 named with production data."""
        session = Session(
            id="trace_single_actor_human",
            metadata=SessionMetadata(project="single-actor"),
        )
        await storage.create_session(session)
        same = Actor(type="human", id="researcher")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=same,
            decision=DecisionData(
                description="solo decision",
                proposed_by=same,
                disposition="accepted",
                resolved_by=same,
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.attribution_warning_count == 0
        assert audit.attribution_warning_ids == []

    async def test_single_actor_ai_self_resolution_asymmetry(self, storage: JsonFileStorage) -> None:
        """Round-3 GAP-1: single-actor ai→ai self-resolution →
        self_resolution_count==1 (ai-only, ungated, backward-compat) but
        attribution_warning_count==0 (multi-actor-gated)."""
        session = Session(
            id="trace_single_actor_ai",
            metadata=SessionMetadata(project="single-actor"),
        )
        await storage.create_session(session)
        same = Actor(type="ai", id="claude")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=same,
            decision=DecisionData(
                description="solo ai decision",
                proposed_by=same,
                disposition="accepted",
                resolved_by=same,
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.self_resolution_count == 1
        assert audit.attribution_warning_count == 0

    async def test_different_human_instances_no_warning(self, storage: JsonFileStorage) -> None:
        """Two different humans (e.g., reviewer + lead) does NOT trigger."""
        session = _make_session("trace_test_different_humans")
        await storage.create_session(session)

        proposer = Actor(type="human", id="researcher-alice")
        resolver = Actor(type="human", id="researcher-bob")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=proposer,
            decision=DecisionData(
                description="Use threshold 0.80",
                proposed_by=proposer,
                disposition="accepted",
                resolved_by=resolver,
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.attribution_warning_count == 0

    async def test_ai_self_resolution_counts_in_both_metrics(self, storage: JsonFileStorage) -> None:
        """ai→ai same-instance increments BOTH self_resolution_count (v0.3 ai-only)
        AND attribution_warning_count (v0.4.1 generalized). Backward compat."""
        session = _make_session("trace_test_ai_self_resolve")
        await storage.create_session(session)

        same_ai = Actor(type="ai", id="claude-opus-4.7")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=same_ai,
            decision=DecisionData(
                description="proactive choice",
                proposed_by=same_ai,
                disposition="accepted",
                resolved_by=same_ai,
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.self_resolution_count == 1  # v0.3 ai-only
        assert audit.attribution_warning_count == 1  # v0.4.1 generalized

    async def test_proposed_state_does_not_count(self, storage: JsonFileStorage) -> None:
        """Decisions still in 'proposed' state aren't self-resolutions."""
        session = _make_session("trace_test_proposed_only")
        await storage.create_session(session)

        proposer = Actor(type="human", id="researcher")
        event = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=proposer,
            decision=DecisionData(
                description="awaiting resolution",
                proposed_by=proposer,
                disposition="proposed",
                # No resolved_by since disposition is "proposed"
            ),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.attribution_warning_count == 0
        assert audit.unresolved_decision_count == 1


# ── L5.5: Orphan-discovery hint detector ─────────────────────────────────


class TestOrphanDiscoveryHint:
    """v0.4.1 §3.7 + §8.1: contributions describing discoveries without
    a near-in-time discovery/correction/gotcha annotation get flagged."""

    async def test_contribution_with_discovery_phrase_no_anchor_fires(self, storage: JsonFileStorage) -> None:
        """Plain contribution with 'discovered' in description, no prior annotation."""
        session = _make_session("trace_test_orphan_discovery")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Implemented the matcher and discovered a Pydantic crash in the disambiguator",
                direction="ai",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="<autonomous-stretch>"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.orphan_discovery_hint_count == 1
        assert event.id in audit.orphan_discovery_event_ids

    async def test_orphan_discovery_is_hint_only_not_a_warning(self, storage: JsonFileStorage) -> None:
        """P4 / A8: orphan-discovery is a low-severity HINT
        (orphan_discovery_hint_count). It must NOT also be pushed into the
        higher-severity `warnings` list — that duplicate over-weighted a
        heuristic signal."""
        session = _make_session("trace_test_orphan_not_warning")
        await storage.create_session(session)
        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Implemented the matcher and discovered a Pydantic crash",
                direction="ai",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="<autonomous-stretch>"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.orphan_discovery_hint_count == 1  # still surfaced as a hint
        assert not any("discovery-language" in w or "moment of discovery" in w for w in audit.warnings), (
            f"orphan-discovery must not be a high-severity warning: {audit.warnings!r}"
        )

    async def test_innocuous_turned_out_phrase_does_not_fire(self, storage: JsonFileStorage) -> None:
        """P4 / A8: 'turned out' is too broad ('turned out cleaner' etc.)
        and is dropped from the phrase list to cut false positives."""
        session = _make_session("trace_test_innocuous_phrase")
        await storage.create_session(session)
        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Refactored the loader; the code turned out cleaner this way",
                direction="ai",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="<autonomous-stretch>"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.orphan_discovery_hint_count == 0

    async def test_contribution_with_nearby_discovery_annotation_no_fire(self, storage: JsonFileStorage) -> None:
        """Discovery annotation logged shortly before contribution → no orphan."""
        session = _make_session("trace_test_anchored_discovery")
        await storage.create_session(session)

        # First: log the discovery
        discovery = TraceEvent(
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(
                category="discovery",
                content="Pydantic crash: plausibility_score field name mismatch",
            ),
            timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )
        await append_event(storage, session, discovery)

        # Then: contribution that summarizes the discovery
        contrib = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Fixed the Pydantic crash that turned out to be a field mismatch",
                direction="ai",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="<autonomous-stretch>"),
        )
        await append_event(storage, session, contrib)

        audit = _build_attribution_audit(session)
        # No orphan — there's a discovery anchor within 30min before
        assert audit.orphan_discovery_hint_count == 0

    async def test_contribution_with_no_discovery_phrase_no_fire(self, storage: JsonFileStorage) -> None:
        """Routine contribution description doesn't trigger the hint."""
        session = _make_session("trace_test_no_phrase")
        await storage.create_session(session)

        event = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="Implemented the requested function with tests",
                direction="human",
                execution="ai",
            ),
            context=EventContext(conversation_snippet="please write it"),
        )
        await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert audit.orphan_discovery_hint_count == 0


# ── L5.6: Dispatch-visibility hint (advisory, no counter) ────────────────


class TestDispatchVisibilityHint:
    """v0.4.1 amendment A3: raised threshold + advisory hint (no counter).

    Production sessions routinely have 0 tool_calls; threshold must be
    high enough to avoid permanent noise.
    """

    async def test_high_contributions_no_tool_calls_claude_code_fires(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_dispatch_hint", with_claude_code_env=True)
        await storage.create_session(session)

        # 10 contributions, 0 tool_calls
        for i in range(10):
            event = TraceEvent(
                session_id=session.id,
                type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description=f"task {i}",
                    direction="human",
                    execution="ai",
                ),
                context=EventContext(conversation_snippet="please do task {i}"),
            )
            await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        # Hint shows up in warnings (advisory), no dedicated counter
        assert any("[hint]" in w and "tool_call" in w for w in audit.warnings)

    async def test_few_contributions_no_hint(self, storage: JsonFileStorage) -> None:
        """Below threshold (9 contributions) — no hint."""
        session = _make_session("trace_test_below_threshold")
        await storage.create_session(session)

        for i in range(9):
            event = TraceEvent(
                session_id=session.id,
                type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description=f"task {i}",
                    direction="human",
                    execution="ai",
                ),
                context=EventContext(conversation_snippet="x"),
            )
            await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        assert not any("[hint]" in w for w in audit.warnings)

    async def test_with_tool_calls_no_hint(self, storage: JsonFileStorage) -> None:
        """Already has tool_call events — assumption is dispatches are captured."""
        session = _make_session("trace_test_has_tool_calls")
        await storage.create_session(session)

        for i in range(10):
            event = TraceEvent(
                session_id=session.id,
                type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description=f"task {i}",
                    direction="human",
                    execution="ai",
                ),
                context=EventContext(conversation_snippet="x"),
            )
            await append_event(storage, session, event)

        # One tool_call disables the hint
        tc = TraceEvent(
            session_id=session.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(
                server="claude-code",
                name="Agent",
                input={"task": "implementer"},
                host="internal",
            ),
        )
        await append_event(storage, session, tc)

        audit = _build_attribution_audit(session)
        assert not any("[hint]" in w for w in audit.warnings)

    async def test_non_claude_code_client_no_hint(self, storage: JsonFileStorage) -> None:
        """Hint scoped to Claude Code only (the canonical internal-dispatch host)."""
        session = _make_session("trace_test_other_client", with_claude_code_env=False)
        session.metadata.environment = Environment(client="some-other-tool")
        await storage.create_session(session)

        for i in range(10):
            event = TraceEvent(
                session_id=session.id,
                type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description=f"task {i}",
                    direction="human",
                    execution="ai",
                ),
                context=EventContext(conversation_snippet="x"),
            )
            await append_event(storage, session, event)

        audit = _build_attribution_audit(session)
        # Wrong client → no hint
        assert not any("[hint]" in w for w in audit.warnings)

    async def test_no_environment_no_hint_no_crash(self, storage: JsonFileStorage) -> None:
        """Round 3 amendment: guard against environment is None for legacy sessions."""
        session = _make_session("trace_test_no_env", with_claude_code_env=False)
        # environment remains None
        await storage.create_session(session)

        for i in range(10):
            event = TraceEvent(
                session_id=session.id,
                type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description=f"task {i}",
                    direction="human",
                    execution="ai",
                ),
                context=EventContext(conversation_snippet="x"),
            )
            await append_event(storage, session, event)

        # MUST NOT raise (the guard against env is None)
        audit = _build_attribution_audit(session)
        assert not any("[hint]" in w for w in audit.warnings)


# ── L5.8: Severity-ordered rendering ─────────────────────────────────────


class TestRenderOrdering:
    """v0.4.1: render order is severity-ordered.

    Critical issues (unresolved decisions, attribution warnings) appear
    BEFORE less-critical ones (orphan-discovery hints, missing snippets).
    """

    async def test_attribution_warning_before_missing_snippet_in_render(self, storage: JsonFileStorage) -> None:
        """Build a session triggering both signals, verify ordering."""
        session = _make_session("trace_test_render_order")
        await storage.create_session(session)

        # Add a same-instance self-resolution (critical)
        same = Actor(type="human", id="researcher")
        d = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=same,
            decision=DecisionData(
                description="some decision",
                proposed_by=same,
                disposition="accepted",
                resolved_by=same,
            ),
        )
        await append_event(storage, session, d)

        # Add a contribution missing snippet (lower severity)
        c = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude"),
            contribution=ContributionData(
                description="task",
                direction="human",
                execution="ai",
            ),
        )
        await append_event(storage, session, c)

        audit = _build_attribution_audit(session)
        rendered = audit.render()

        # Attribution warning appears BEFORE missing-snippet in render
        attr_idx = rendered.find("Attribution warnings")
        miss_idx = rendered.find("Missing conversation_snippet")
        assert attr_idx >= 0, "attribution warning section missing"
        assert miss_idx >= 0, "missing-snippet section missing"
        assert attr_idx < miss_idx, "expected attribution warning BEFORE missing snippet"

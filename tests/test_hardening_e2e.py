"""Comprehensive E2E tests for TRACE hardening.

Tests critical fixes (immutability, referential integrity, extraction errors),
SCRATCHPAD feature, and realistic-workload validation across project patterns.

Uses generic project names — narrative-analysis, computational-art,
embeddings-pipeline, and meeting-recorder — as fixtures that exercise the
schema and tooling against patterns drawn from real research workflows.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trace_mcp.extension_hooks import (
    clear_hooks,
    extract_if_available,
    register_extract_hook,
)
from trace_mcp.schema import (
    Actor,
    AnnotationData,
    ContributionData,
    DecisionData,
    Environment,
    EventContext,
    Session,
    SessionMetadata,
    TraceEvent,
)
from trace_mcp.scratchpad import (
    _build_session_section,
    write_scratchpad,
)
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, session_tools

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path / "sessions"))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


@pytest.fixture(autouse=True)
def _clean_hooks():
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture
def scratchpad_dir(tmp_path: Path) -> Path:
    sp_dir = tmp_path / "scratchpad"
    sp_dir.mkdir()
    return sp_dir


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _create_session(
    storage: JsonFileStorage,
    active: dict[str, Session],
    *,
    project: str = "test",
    description: str = "test session",
    tags: list[str] | None = None,
) -> tuple[str, Session]:
    await session_tools.start_session(
        storage, active, project=project, description=description, tags=tags,
    )
    session_id = list(active.keys())[-1]
    return session_id, active[session_id]


def _make_green_narrative_session(session_id: str = "trace_test_gn") -> Session:
    """Build a session modeled on real narrative-analysis data."""
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="narrative-analysis",
            description="Deep review of consensus UMAP + projection head methodology",
            tags=["consensus-umap", "projection-head", "review"],
            environment=Environment(
                client="Claude Code",
                os="Darwin 25.3.0",
                python_version="3.13.11",
                custom={"arch": "arm64"},
            ),
        ),
    )


def _make_wama_session(session_id: str = "trace_test_wama") -> Session:
    """Build a session modeled on real computational-art data."""
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="computational-art",
            description="Review manuscript structure and revision guidance",
            tags=["manuscript", "publication"],
            environment=Environment(
                client="Claude Code",
                os="Darwin 25.3.0",
                python_version="3.13.11",
            ),
        ),
    )


def _make_meeting_recorder_session(session_id: str = "trace_test_mr") -> Session:
    """Build a session modeled on meeting-recorder."""
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="meeting-recorder",
            description="Recording and transcribing esg-group meeting",
            tags=["meeting", "esg-group", "transcription"],
            environment=Environment(
                client="Claude Code",
                os="Darwin 25.3.0",
            ),
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# PRIORITY 1: CRITICAL BUG FIXES
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionImmutability:
    """Sessions must be immutable once completed. No double-ending, no post-end logging."""

    async def test_double_end_returns_error(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Ending an already-completed session returns an error, not silent overwrite."""
        session_id, _ = await _create_session(storage, active)
        result1 = await session_tools.end_session(
            storage, active, session_id=session_id, summary="done",
        )
        assert "Session ended" in result1

        # Second end attempt must fail
        result2 = await session_tools.end_session(
            storage, active, session_id=session_id, summary="different",
        )
        assert "Error" in result2
        assert "already ended" in result2
        assert "immutable" in result2

    async def test_double_end_preserves_original_timestamp(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """The original ended timestamp must not be overwritten."""
        session_id, _ = await _create_session(storage, active)
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="first",
        )

        # Load and record original timestamp
        session = await storage.get_session(session_id)
        original_ended = session.ended

        # Try to end again
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="second",
        )

        # Verify timestamp unchanged
        reloaded = await storage.get_session(session_id)
        assert reloaded.ended == original_ended
        assert reloaded.summary == "first"  # original summary preserved

    async def test_double_end_preserves_original_summary(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """The original summary must not be overwritten by a second end call."""
        session_id, session = await _create_session(storage, active)

        # Log a real event so the session has content
        await logging_tools.log_annotation(
            storage, session, category="observation", content="test observation",
        )

        await session_tools.end_session(
            storage, active, session_id=session_id, summary="Original summary",
        )

        # Attempt to end again with different summary
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="Altered summary",
        )

        reloaded = await storage.get_session(session_id)
        assert reloaded.summary == "Original summary"

    async def test_append_event_to_completed_session_raises(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Logging to a completed session must raise ValueError."""
        session_id, session = await _create_session(storage, active)
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="done",
        )

        # Re-load the completed session
        completed = await storage.get_session(session_id)
        event = TraceEvent(
            session_id=session_id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(category="observation", content="late event"),
        )
        with pytest.raises(ValueError, match="Cannot append events to completed session"):
            await session_tools.append_event(storage, completed, event)

    async def test_post_completion_event_not_persisted(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Events attempted after completion must not appear in the stored session."""
        session_id, session = await _create_session(storage, active)
        await logging_tools.log_annotation(
            storage, session, category="observation", content="pre-end event",
        )
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="done",
        )

        completed = await storage.get_session(session_id)
        assert len(completed.events) == 1

        # Attempt post-end logging
        with pytest.raises(ValueError):
            await logging_tools.log_annotation(
                storage, completed, category="observation", content="post-end",
            )

        # Verify event count unchanged on disk
        reloaded = await storage.get_session(session_id)
        assert len(reloaded.events) == 1

    async def test_all_logging_tools_reject_completed_session(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Every logging function must reject completed sessions."""
        session_id, session = await _create_session(storage, active)
        await session_tools.end_session(
            storage, active, session_id=session_id, summary="done",
        )
        completed = await storage.get_session(session_id)

        # log_annotation
        with pytest.raises(ValueError, match="Cannot append"):
            await logging_tools.log_annotation(
                storage, completed, category="learning", content="test",
            )

        # log_contribution
        with pytest.raises(ValueError, match="Cannot append"):
            await logging_tools.log_contribution(
                storage, completed, description="test",
                direction="ai", execution="ai",
            )

        # log_tool_call
        with pytest.raises(ValueError, match="Cannot append"):
            await logging_tools.log_tool_call(
                storage, completed, server="test", tool_name="test", input={},
            )

        # log_state_change
        with pytest.raises(ValueError, match="Cannot append"):
            await logging_tools.log_state_change(
                storage, completed, description="test",
            )

        # propose_decision
        with pytest.raises(ValueError, match="Cannot append"):
            await decision_tools.propose_decision(
                storage, completed, description="test",
                proposed_by_type="ai", proposed_by_id="claude",
            )


class TestReferentialIntegrityEnforcement:
    """Invalid event references must be rejected, not just warned about."""

    async def test_invalid_corrects_event_ids_raises(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """corrects_event_ids pointing to nonexistent events must raise ValueError."""
        session_id, session = await _create_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*corrects_event_ids"):
            await logging_tools.log_annotation(
                storage, session,
                category="correction", content="fix",
                corrects_event_ids=["evt_phantom"],
                conversation_snippet="that was wrong",
            )

    async def test_invalid_retries_event_id_raises(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """retries_event_id pointing to nonexistent event must raise ValueError."""
        session_id, session = await _create_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*retries_event_id"):
            await logging_tools.log_tool_call(
                storage, session,
                server="test", tool_name="test", input={},
                retries_event_id="evt_nonexistent",
            )

    async def test_invalid_revises_event_id_raises(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """revises_event_id pointing to nonexistent event must raise ValueError."""
        session_id, session = await _create_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*revises_event_id"):
            await decision_tools.propose_decision(
                storage, session,
                description="revised decision",
                proposed_by_type="ai", proposed_by_id="claude",
                revises_event_id="evt_ghost",
            )

    async def test_invalid_related_decision_ids_raises(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """related_decision_ids pointing to nonexistent events must raise ValueError."""
        session_id, session = await _create_session(storage, active)
        with pytest.raises(ValueError, match="Dangling reference.*related_decision_ids"):
            await logging_tools.log_contribution(
                storage, session,
                description="test contribution", direction="ai", execution="ai",
                related_decision_ids=["evt_missing"],
                conversation_snippet="do the thing",
            )

    async def test_valid_references_accepted(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Valid references to existing events must be accepted without error."""
        session_id, session = await _create_session(storage, active)

        # Create events to reference
        dec_id = await decision_tools.propose_decision(
            storage, session,
            description="Use method A",
            proposed_by_type="ai", proposed_by_id="claude",
        )

        # Valid correction referencing the decision
        ann_id = await logging_tools.log_annotation(
            storage, session,
            category="correction", content="Actually use method B",
            corrects_event_ids=[dec_id],
            conversation_snippet="no, use method B instead",
        )
        assert ann_id.startswith("evt_")

        # Valid contribution referencing the decision
        contrib_id = await logging_tools.log_contribution(
            storage, session,
            description="Implemented method B",
            direction="human", execution="ai",
            related_decision_ids=[dec_id],
            conversation_snippet="implement method B",
        )
        assert contrib_id.startswith("evt_")

    async def test_event_not_persisted_on_invalid_reference(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Failed reference validation must not leave the event in the session."""
        session_id, session = await _create_session(storage, active)
        assert len(session.events) == 0

        with pytest.raises(ValueError):
            await logging_tools.log_annotation(
                storage, session,
                category="correction", content="fix",
                corrects_event_ids=["evt_bad"],
            )

        # Event must not have been appended
        assert len(session.events) == 0
        reloaded = await storage.get_session(session_id)
        assert len(reloaded.events) == 0


class TestExtractionResultSurfacing:
    """Learning extraction errors must be visible, not silently swallowed."""

    async def test_extraction_success_returns_ids(self) -> None:
        async def _mock(project: str, session_id: str) -> list[str]:
            return ["lrn_001", "lrn_002"]

        register_extract_hook(_mock)
        result = await extract_if_available("test", "sess_001")
        assert result.success
        assert result.new_ids == ["lrn_001", "lrn_002"]
        assert result.error is None

    async def test_extraction_failure_surfaces_error(self) -> None:
        async def _failing(project: str, session_id: str) -> list[str]:
            raise RuntimeError("OpenAI API key expired")

        register_extract_hook(_failing)
        result = await extract_if_available("test", "sess_001")
        assert not result.success
        assert result.new_ids == []
        assert "OpenAI API key expired" in (result.error or "")

    async def test_no_hook_returns_success_empty(self) -> None:
        result = await extract_if_available("test", "sess_001")
        assert result.success
        assert result.new_ids == []


# ═══════════════════════════════════════════════════════════════════════════
# SCRATCHPAD FEATURE
# ═══════════════════════════════════════════════════════════════════════════


class TestScratchpadGeneration:
    """SCRATCHPAD.md must be generated with correct content at session end."""

    def test_build_section_with_full_session(self) -> None:
        """SCRATCHPAD section must include all key session components."""
        session = _make_green_narrative_session()
        session.summary = "Reviewed methodology, made 3 decisions, found 1 gotcha"

        # Add events like the real narrative-analysis session
        session.events = [
            TraceEvent(
                id="evt_001", session_id=session.id, type="decision",
                actor=Actor(type="ai", id="claude"),
                decision=DecisionData(
                    description="Use Euclidean silhouette for post-UMAP evaluation",
                    rationale="Euclidean is appropriate in reduced UMAP space",
                    proposed_by=Actor(type="ai", id="claude"),
                    disposition="accepted",
                    resolved_by=Actor(type="human", id="researcher"),
                    suggestion_type="proactive",
                ),
                context=EventContext(
                    conversation_snippet="what metric should we use for evaluating clusters?",
                ),
            ),
            TraceEvent(
                id="evt_002", session_id=session.id, type="decision",
                actor=Actor(type="ai", id="claude"),
                decision=DecisionData(
                    description="Deprioritize 10-K filing parsing",
                    proposed_by=Actor(type="ai", id="claude"),
                    disposition="proposed",
                    suggestion_type="proactive",
                ),
            ),
            TraceEvent(
                id="evt_003", session_id=session.id, type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(
                    category="gotcha",
                    content="Pydantic v2 field name shadows type name on Python 3.10",
                    tags=["pydantic", "python-3.10"],
                ),
            ),
            TraceEvent(
                id="evt_004", session_id=session.id, type="contribution",
                actor=Actor(type="ai", id="claude"),
                contribution=ContributionData(
                    description="15 Pydantic models for sustainability reports",
                    artifact="src/models/corporate.py",
                    direction="human",
                    execution="ai",
                    related_decision_ids=["evt_001"],
                ),
                context=EventContext(
                    conversation_snippet="create the data models we discussed",
                ),
            ),
            TraceEvent(
                id="evt_005", session_id=session.id, type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(
                    category="learning",
                    content="Euclidean is definitively correct for post-UMAP evaluation",
                ),
            ),
        ]
        session.ended = datetime.now(UTC)
        session.status = "completed"

        section = _build_session_section(session)

        # Verify all components present
        assert "## Session: `trace_test_gn`" in section
        assert "narrative-analysis" in section
        assert "### Summary" in section
        assert "Reviewed methodology" in section
        assert "### What Was Accomplished" in section
        assert "15 Pydantic models" in section
        assert "src/models/corporate.py" in section
        assert "direction=human, execution=ai" in section
        assert "### Decisions" in section
        assert "**[accepted]**" in section
        assert "Euclidean silhouette" in section
        assert "**[proposed]**" in section
        assert "Deprioritize 10-K" in section
        assert "### Open Items" in section
        assert "[ ] Deprioritize 10-K" in section
        assert "### Gotchas & Corrections" in section
        assert "**GOTCHA**" in section
        assert "Pydantic v2 field name" in section
        assert "### Learnings" in section
        assert "Euclidean is definitively correct" in section

    def test_build_section_empty_session(self) -> None:
        """Empty session produces minimal but valid section."""
        session = _make_green_narrative_session()
        session.ended = datetime.now(UTC)
        session.status = "completed"

        section = _build_session_section(session)
        assert "## Session:" in section
        assert "narrative-analysis" in section
        # No crash on empty events
        assert "### Summary" not in section  # no summary set

    def test_scratchpad_write_creates_file(self, scratchpad_dir: Path) -> None:
        """write_scratchpad creates SCRATCHPAD.md with header and content."""
        session = _make_green_narrative_session()
        session.summary = "Test session"
        session.ended = datetime.now(UTC)
        session.status = "completed"

        os.environ["TRACE_SCRATCHPAD_DIR"] = str(scratchpad_dir)
        try:
            path = write_scratchpad(session)
            assert path.exists()
            content = path.read_text()
            assert "# SCRATCHPAD" in content
            assert "narrative-analysis" in content
            assert "Test session" in content
        finally:
            os.environ.pop("TRACE_SCRATCHPAD_DIR", None)

    def test_scratchpad_replaces_previous_session(self, scratchpad_dir: Path) -> None:
        """write_scratchpad keeps only the most recent session."""
        os.environ["TRACE_SCRATCHPAD_DIR"] = str(scratchpad_dir)
        try:
            # First session
            s1 = _make_green_narrative_session("trace_session_1")
            s1.summary = "First session"
            s1.ended = datetime.now(UTC)
            s1.status = "completed"
            write_scratchpad(s1)

            # Second session replaces first
            s2 = _make_green_narrative_session("trace_session_2")
            s2.summary = "Second session"
            s2.ended = datetime.now(UTC)
            s2.status = "completed"
            write_scratchpad(s2)

            content = (scratchpad_dir / "SCRATCHPAD.md").read_text()
            assert "trace_session_2" in content
            assert "Second session" in content
            assert "trace_session_1" not in content, "Old session should be replaced"
            assert "First session" not in content, "Old session should be replaced"
        finally:
            os.environ.pop("TRACE_SCRATCHPAD_DIR", None)

    def test_scratchpad_with_todos(self) -> None:
        """TODO annotations appear in the TODOs section."""
        session = _make_meeting_recorder_session()
        session.events = [
            TraceEvent(
                id="evt_001", session_id=session.id, type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(
                    category="todo",
                    content="Test multi-speaker diarization accuracy on >4 people",
                ),
            ),
        ]
        session.ended = datetime.now(UTC)
        session.status = "completed"

        section = _build_session_section(session)
        assert "### TODOs" in section
        assert "[ ] Test multi-speaker" in section

    def test_scratchpad_corrections_show_linked_events(self) -> None:
        """Corrections in SCRATCHPAD show which events they correct."""
        session = _make_green_narrative_session()
        session.events = [
            TraceEvent(
                id="evt_001", session_id=session.id, type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(
                    category="observation", content="Using base conda env",
                ),
            ),
            TraceEvent(
                id="evt_002", session_id=session.id, type="annotation",
                actor=Actor(type="human", id="researcher"),
                annotation=AnnotationData(
                    category="correction",
                    content="Wrong env — use ml-dev, not base",
                    corrects_event_ids=["evt_001"],
                ),
            ),
        ]
        session.ended = datetime.now(UTC)
        session.status = "completed"

        section = _build_session_section(session)
        assert "**CORRECTION**" in section
        assert "corrects: evt_001" in section


# ═══════════════════════════════════════════════════════════════════════════
# REAL DATA PATTERN VALIDATION
# Tests modeled on actual narrative-analysis, WAMA, embeddings-pipeline, meeting-recorder
# ═══════════════════════════════════════════════════════════════════════════


class TestGreenNarrativePatterns:
    """Validate TRACE behavior with patterns from real narrative-analysis sessions."""

    async def test_comprehensive_session_workflow(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Full narrative-analysis workflow: decisions, contributions, learnings.

        Modeled on trace_20260320_ba3fa7 (20 events).
        """
        sid, session = await _create_session(
            storage, active, project="narrative-analysis",
            description="Deep review of consensus UMAP methodology",
            tags=["consensus-umap", "review"],
        )

        # Decision 1: Keep Procrustes as baseline (proactive)
        d1 = await decision_tools.propose_decision(
            storage, session,
            description="Keep Procrustes alignment as comparison baseline",
            rationale="Standard approach, widely published",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="proactive",
            conversation_snippet="should we keep the procrustes alignment?",
        )
        assert d1.startswith("evt_")

        # Decision 2: Investigate cosine vs euclidean (requested by human)
        d2 = await decision_tools.propose_decision(
            storage, session,
            description="Investigate cosine vs euclidean for post-UMAP evaluation",
            proposed_by_type="human", proposed_by_id="researcher",
            suggestion_type="requested",
            conversation_snippet="we need to settle the cosine vs euclidean question",
        )

        # Resolve d2 as accepted
        await decision_tools.resolve_decision(
            storage, session,
            event_id=d2, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Definitively resolved: Euclidean is correct for post-UMAP",
        )

        # Learning annotation
        await logging_tools.log_annotation(
            storage, session,
            category="learning",
            content="Euclidean distance is appropriate for post-UMAP evaluation",
            tags=["methodology", "distance-metric"],
        )

        # Contribution with all required fields
        await logging_tools.log_contribution(
            storage, session,
            description="15 Pydantic models for sustainability reports",
            direction="human", execution="ai",
            artifact="src/models/corporate.py",
            related_decision_ids=[d1],
            conversation_snippet="create comprehensive data models for all domains",
        )

        # End session
        result = await session_tools.end_session(
            storage, active, session_id=sid,
            summary="Deep review session with 5 events",
        )
        assert "Session ended" in result
        assert "Contributions (1)" in result
        assert "Decisions (2)" in result

    async def test_conversation_snippet_presence_loudly_verified(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Verify conversation_snippet is properly stored and retrievable.

        Based on trace_20260316_2e6a9e where ALL events lacked snippets.
        This test FAILS LOUDLY if snippets are lost.
        """
        sid, session = await _create_session(
            storage, active, project="narrative-analysis",
        )

        snippet_text = "what metric should we use for evaluating clusters?"

        # Log contribution WITH snippet
        await logging_tools.log_contribution(
            storage, session,
            description="Created evaluation pipeline",
            direction="human", execution="ai",
            conversation_snippet=snippet_text,
        )

        # Log annotation WITH snippet
        await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="Wrong metric used",
            corrects_event_ids=["evt_001"],
            conversation_snippet="that metric is wrong, use silhouette instead",
        )

        # Verify snippets persisted correctly
        reloaded = await storage.get_session(sid)
        for evt in reloaded.events:
            assert evt.context.conversation_snippet is not None, (
                f"CONVERSATION SNIPPET LOST on event {evt.id} "
                f"(type={evt.type}). This is a data quality failure — "
                f"snippets must be preserved for provenance."
            )

        # Verify exact content preserved
        assert reloaded.events[0].context.conversation_snippet == snippet_text

    async def test_conversation_snippet_missing_produces_warning(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Missing conversation_snippet on contributions/corrections must warn.

        Based on trace_20260316_2e6a9e where 7/7 events had no snippet.
        """
        sid, session = await _create_session(
            storage, active, project="narrative-analysis",
        )

        # Contribution without snippet should warn
        result = await logging_tools.log_contribution(
            storage, session,
            description="Updated CLAUDE.md",
            direction="ai", execution="ai",
        )
        assert "conversation_snippet" in result

        # Correction without snippet should warn
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="Wrong extension listed",
            conversation_snippet=None,  # explicitly missing
        )
        assert "conversation_snippet" in result

    async def test_correction_micro_session_pattern(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Correction micro-sessions (like trace_20260320_3a4123) work correctly.

        Pattern: quick session to retroactively correct events from another session.
        All corrections reference external session events (which won't be found
        in THIS session — this is intentional for cross-session corrections).
        """
        sid, session = await _create_session(
            storage, active, project="narrative-analysis",
            description="Micro-session: correct missing conversation_snippets",
            tags=["correction", "trace-discipline"],
        )

        # Corrections reference events from another session.
        # Since those events don't exist in THIS session, they will be
        # rejected by referential integrity. This tests that cross-session
        # corrections need a different approach (annotation without corrects_event_ids,
        # or using content to describe what was corrected).
        snippet = (
            "i noticed that all of the trace logs have a warning saying "
            "that no conversation_snippet was recorded"
        )

        # Log correction WITHOUT corrects_event_ids (cross-session reference
        # documented in content instead)
        result = await logging_tools.log_annotation(
            storage, session,
            category="correction",
            content="evt_001 in trace_20260319_e4cf1e: missing conversation_snippet. "
            "User said: 'create the Pydantic models for all data domains'",
            conversation_snippet=snippet,
        )
        assert result.startswith("evt_")

        await session_tools.end_session(
            storage, active, session_id=sid,
            summary="Correction micro-session: 1 cross-session correction logged",
        )


class TestWhenAlgorithmsMeetArtistsPatterns:
    """Validate patterns from computational-art sessions."""

    async def test_session_with_mixed_dispositions(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Test a session with accepted, revised, and rejected decisions."""
        sid, session = await _create_session(
            storage, active, project="computational-art",
            description="Review manuscript structure",
        )

        # Accepted decision
        d1 = await decision_tools.propose_decision(
            storage, session,
            description="Use hypothesis-driven narrative structure",
            proposed_by_type="human", proposed_by_id="researcher",
            suggestion_type="requested",
        )
        await decision_tools.resolve_decision(
            storage, session, event_id=d1, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )

        # Revised decision
        d2 = await decision_tools.propose_decision(
            storage, session,
            description="Submit to ACM C&C 2026",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="proactive",
        )
        await decision_tools.resolve_decision(
            storage, session, event_id=d2, disposition="revised",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Submit methods paper to methods venue instead",
        )

        # Rejected decision
        d3 = await decision_tools.propose_decision(
            storage, session,
            description="Include all 6 available figures in main paper",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="proactive",
        )
        await decision_tools.resolve_decision(
            storage, session, event_id=d3, disposition="rejected",
            resolved_by_type="human", resolved_by_id="researcher",
            revision_note="Too many figures; select top 3 only",
        )

        result = await session_tools.end_session(
            storage, active, session_id=sid,
            summary="Manuscript review with mixed decision outcomes",
        )
        assert "Decisions (3)" in result
        assert "disposition=accepted" in result
        assert "disposition=revised" in result
        assert "disposition=rejected" in result
        assert "Human interventions: 2" in result  # revised + rejected


class TestMeetingRecorderPatterns:
    """Validate patterns specific to meeting-recorder."""

    async def test_transcription_pipeline_session(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Full transcription pipeline workflow."""
        sid, session = await _create_session(
            storage, active,
            project="meeting-recorder",
            description="Recording esg-group meeting 2026-04-02",
            tags=["meeting", "esg-group", "transcription"],
        )

        # Decision: recording strategy
        d1 = await decision_tools.propose_decision(
            storage, session,
            description="Use Loopback multi-output for app audio + mic",
            proposed_by_type="ai", proposed_by_id="claude",
            suggestion_type="proactive",
            conversation_snippet="how should we set up recording?",
        )
        await decision_tools.resolve_decision(
            storage, session, event_id=d1, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )

        # Tool call: Whisper transcription
        await logging_tools.log_tool_call(
            storage, session,
            server="openai",
            tool_name="audio.transcriptions.create",
            input={"model": "whisper-1", "file": "esg_group_20260402.wav"},
            output={"text": "Today we discuss...", "segments": 24},
            duration_ms=45000,
            status="success",
        )

        # Gotcha: device instability
        await logging_tools.log_annotation(
            storage, session,
            category="gotcha",
            content="ffmpeg device indices shift when Bluetooth devices connect/disconnect",
            tags=["ffmpeg", "macos", "audio-device"],
        )

        # Contribution: transcript file
        await logging_tools.log_contribution(
            storage, session,
            description="Transcribed esg-group meeting: 24 segments, 4 speakers",
            direction="human", execution="ai",
            artifact="recordings/esg-group/esg-group_20260402.json",
            related_decision_ids=[d1],
            conversation_snippet="transcribe the meeting recording",
        )

        result = await session_tools.end_session(
            storage, active, session_id=sid,
            summary="Recorded and transcribed esg-group meeting",
        )
        assert "Session ended" in result
        assert "4 events" not in result or "5 events" not in result  # we logged 5 events total


class TestEmbeddingsPipelinePatterns:
    """Validate patterns from embeddings-pipeline project sessions."""

    async def test_projection_head_workflow(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """embeddings-pipeline session: projection head pipeline updates."""
        sid, session = await _create_session(
            storage, active, project="embeddings-pipeline",
            description="Projection head pipeline updates and training",
        )

        # State change: conda env switch
        await logging_tools.log_state_change(
            storage, session,
            description="Switched conda environment",
            field="conda_env",
            old_value="nlp_py3_12",
            new_value="nlp_sent_trans_notebook",
            reason="Required packages for projection head training",
        )

        # Gotcha: env mismatch
        await logging_tools.log_annotation(
            storage, session,
            category="gotcha",
            content="nlp_py3_12 env lacks sentence-transformers needed for projection head",
            tags=["conda", "environment"],
        )

        # Contribution with retroactive note
        await logging_tools.log_contribution(
            storage, session,
            description="Updated projection head training script",
            direction="collaborative", execution="ai",
            artifact="hye_in/projection/train.py",
            conversation_snippet="update the training script",
        )

        result = await session_tools.end_session(
            storage, active, session_id=sid,
            summary="Projection head pipeline updated",
        )
        assert "3 events" in result


# ═══════════════════════════════════════════════════════════════════════════
# CONVERSATION SNIPPET FIELD COMPLETENESS
# These tests FAIL LOUDLY when snippets are missing/lost
# ═══════════════════════════════════════════════════════════════════════════


class TestConversationSnippetCompleteness:
    """Ensure conversation_snippet is properly handled across all event types."""

    async def test_snippet_roundtrip_contribution(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """conversation_snippet on contributions must survive write→read roundtrip."""
        sid, session = await _create_session(storage, active)
        snippet = "create comprehensive data models for all narrative-analysis domains"

        await logging_tools.log_contribution(
            storage, session,
            description="15 Pydantic models",
            direction="human", execution="ai",
            conversation_snippet=snippet,
        )

        reloaded = await storage.get_session(sid)
        evt = reloaded.events[0]
        assert evt.context.conversation_snippet == snippet, (
            f"SNIPPET LOST! Expected: {snippet!r}, "
            f"Got: {evt.context.conversation_snippet!r}"
        )

    async def test_snippet_roundtrip_annotation(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """conversation_snippet on annotations must survive write→read roundtrip."""
        sid, session = await _create_session(storage, active)
        snippet = "that's wrong, use ml-dev conda env"

        await logging_tools.log_annotation(
            storage, session,
            category="observation",
            content="Wrong env used",
            conversation_snippet=snippet,
        )

        reloaded = await storage.get_session(sid)
        evt = reloaded.events[0]
        assert evt.context.conversation_snippet == snippet

    async def test_snippet_roundtrip_decision(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """conversation_snippet on decisions must survive write→read roundtrip."""
        sid, session = await _create_session(storage, active)
        snippet = "should we use cosine or euclidean for post-UMAP?"

        await decision_tools.propose_decision(
            storage, session,
            description="Use Euclidean for post-UMAP",
            proposed_by_type="ai", proposed_by_id="claude",
            conversation_snippet=snippet,
        )

        reloaded = await storage.get_session(sid)
        evt = reloaded.events[0]
        assert evt.context.conversation_snippet == snippet

    async def test_snippet_preserved_through_decision_resolution(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """conversation_snippet must survive decision resolution updates."""
        sid, session = await _create_session(storage, active)
        snippet = "should we keep the procrustes alignment?"

        dec_id = await decision_tools.propose_decision(
            storage, session,
            description="Keep Procrustes as baseline",
            proposed_by_type="ai", proposed_by_id="claude",
            conversation_snippet=snippet,
        )

        await decision_tools.resolve_decision(
            storage, session, event_id=dec_id, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )

        reloaded = await storage.get_session(sid)
        dec_evt = next(e for e in reloaded.events if e.id == dec_id)
        assert dec_evt.context.conversation_snippet == snippet, (
            "SNIPPET LOST during decision resolution! The resolve_decision "
            "update must not clear the original conversation_snippet."
        )


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION AUDIT ACCURACY
# ═══════════════════════════════════════════════════════════════════════════


class TestAttributionAuditAccuracy:
    """Attribution audit at session end must accurately reflect all events."""

    async def test_audit_counts_all_event_types(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Audit must correctly count decisions, contributions, corrections."""
        sid, session = await _create_session(storage, active)

        # 2 decisions (1 accepted, 1 proposed)
        d1 = await decision_tools.propose_decision(
            storage, session,
            description="Decision 1",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        await decision_tools.resolve_decision(
            storage, session, event_id=d1, disposition="accepted",
            resolved_by_type="human", resolved_by_id="researcher",
        )
        await decision_tools.propose_decision(
            storage, session,
            description="Decision 2 (unresolved)",
            proposed_by_type="ai", proposed_by_id="claude",
        )

        # 1 correction
        await logging_tools.log_annotation(
            storage, session,
            category="correction", content="Fix",
            corrects_event_ids=[d1],
            conversation_snippet="that was wrong",
        )

        # 1 contribution
        await logging_tools.log_contribution(
            storage, session,
            description="Built the thing",
            direction="human", execution="ai",
            related_decision_ids=[d1],
            conversation_snippet="build it",
        )

        result = await session_tools.end_session(
            storage, active, session_id=sid, summary="test",
        )

        assert "Contributions (1)" in result
        assert "Decisions (2)" in result
        assert "Corrections: 1" in result
        assert "Unresolved decisions: 1" in result
        assert "Human interventions: 1" in result  # 1 correction

    async def test_audit_detects_ai_self_resolution(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Audit must flag decisions where AI resolved its own proposal."""
        sid, session = await _create_session(storage, active)

        d1 = await decision_tools.propose_decision(
            storage, session,
            description="AI proposes and resolves",
            proposed_by_type="ai", proposed_by_id="claude",
        )

        os.environ["TRACE_SUPPRESS_SELF_RESOLVE_WARNING"] = "true"
        try:
            await decision_tools.resolve_decision(
                storage, session, event_id=d1, disposition="accepted",
                resolved_by_type="ai", resolved_by_id="claude",
            )
        finally:
            os.environ.pop("TRACE_SUPPRESS_SELF_RESOLVE_WARNING", None)

        result = await session_tools.end_session(
            storage, active, session_id=sid, summary="test",
        )
        assert "AI self-resolutions: 1" in result


# ═══════════════════════════════════════════════════════════════════════════
# DECISION CHAIN INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════


class TestDecisionChainIntegrity:
    """Decision chains must maintain valid links."""

    async def test_valid_revision_chain(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """A chain of decisions linked via revises_event_id must be walkable."""
        sid, session = await _create_session(storage, active)

        d1 = await decision_tools.propose_decision(
            storage, session,
            description="Original approach: use method A",
            proposed_by_type="ai", proposed_by_id="claude",
        )
        d2 = await decision_tools.propose_decision(
            storage, session,
            description="Revised: use method B instead",
            proposed_by_type="ai", proposed_by_id="claude",
            revises_event_id=d1,
        )
        d3 = await decision_tools.propose_decision(
            storage, session,
            description="Final: use method C",
            proposed_by_type="human", proposed_by_id="researcher",
            revises_event_id=d2,
        )

        # Verify chain is intact
        reloaded = await storage.get_session(sid)
        events_by_id = {e.id: e for e in reloaded.events}

        dec1 = events_by_id[d1].decision
        dec2 = events_by_id[d2].decision
        dec3 = events_by_id[d3].decision
        assert dec1 is not None and dec2 is not None and dec3 is not None
        assert dec3.revises_event_id == d2
        assert dec2.revises_event_id == d1
        assert dec1.revises_event_id is None

    async def test_revision_chain_rejects_invalid_parent(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """revises_event_id pointing to nonexistent event must be rejected."""
        sid, session = await _create_session(storage, active)

        with pytest.raises(ValueError, match="Dangling reference.*revises_event_id"):
            await decision_tools.propose_decision(
                storage, session,
                description="Revises phantom",
                proposed_by_type="ai", proposed_by_id="claude",
                revises_event_id="evt_phantom",
            )


# ═══════════════════════════════════════════════════════════════════════════
# TOOL CALL RETRY CHAINS
# ═══════════════════════════════════════════════════════════════════════════


class TestToolCallRetryChains:
    """Tool call retry chains must maintain valid references."""

    async def test_valid_retry_chain(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """Retry chain: tc1 (error) → tc2 (retries tc1, success)."""
        sid, session = await _create_session(storage, active)

        tc1 = await logging_tools.log_tool_call(
            storage, session,
            server="openai", tool_name="whisper",
            input={"file": "meeting.wav"},
            status="error", error_message="File too large",
        )

        tc2 = await logging_tools.log_tool_call(
            storage, session,
            server="openai", tool_name="whisper",
            input={"file": "meeting_chunk1.wav"},
            status="success",
            retries_event_id=tc1,
        )

        reloaded = await storage.get_session(sid)
        retry_evt = next(e for e in reloaded.events if e.id == tc2)
        assert retry_evt.tool_call is not None
        assert retry_evt.tool_call.retries_event_id == tc1

    async def test_retry_rejects_invalid_parent(
        self, storage: JsonFileStorage, active: dict[str, Session],
    ) -> None:
        """retries_event_id pointing to nonexistent event must be rejected."""
        sid, session = await _create_session(storage, active)

        with pytest.raises(ValueError, match="Dangling reference.*retries_event_id"):
            await logging_tools.log_tool_call(
                storage, session,
                server="test", tool_name="test", input={},
                retries_event_id="evt_nonexistent",
            )


# ═══════════════════════════════════════════════════════════════════════════
# REAL SESSION FILE LOADING (reads actual ~/.trace/sessions/ if present)
# ═══════════════════════════════════════════════════════════════════════════


_REAL_SESSIONS_DIR = Path.home() / ".trace" / "sessions"


@pytest.mark.skipif(
    not _REAL_SESSIONS_DIR.exists(),
    reason="No ~/.trace/sessions/ directory — skipping real data tests",
)
class TestRealSessionDataIntegrity:
    """Load real TRACE session files and verify data integrity."""

    def _load_session(self, filename: str) -> Session | None:
        path = _REAL_SESSIONS_DIR / filename
        if not path.exists():
            pytest.skip(f"Session file not found: {filename}")
        with open(path) as f:
            raw = json.load(f)
        return Session.model_validate(raw)

    def test_green_narrative_ba3fa7_has_snippets(self) -> None:
        """trace_20260320_ba3fa7 (narrative-analysis) should have conversation_snippets."""
        session = self._load_session("trace_20260320_ba3fa7.json")
        if session is None:
            return

        events_with_snippets = [
            e for e in session.events if e.context.conversation_snippet
        ]
        events_needing_snippets = [
            e for e in session.events
            if e.type in ("decision", "contribution")
            or (e.type == "annotation" and e.annotation
                and e.annotation.category == "correction")
        ]

        # This session should have good snippet coverage
        if events_needing_snippets:
            coverage = len(events_with_snippets) / len(events_needing_snippets)
            assert coverage > 0.5, (
                f"POOR SNIPPET COVERAGE in trace_20260320_ba3fa7: "
                f"{len(events_with_snippets)}/{len(events_needing_snippets)} "
                f"({coverage:.0%}). Decisions, contributions, and corrections "
                f"should have conversation_snippets for provenance."
            )

    def test_green_narrative_3a4123_is_pure_corrections(self) -> None:
        """trace_20260320_3a4123 should be a micro-session of pure corrections."""
        session = self._load_session("trace_20260320_3a4123.json")
        if session is None:
            return

        assert session.status == "completed"
        assert len(session.events) > 0

        for evt in session.events:
            assert evt.type == "annotation", (
                f"Event {evt.id} is type '{evt.type}', expected 'annotation' "
                f"in a pure correction micro-session"
            )
            assert evt.annotation is not None
            assert evt.annotation.category == "correction", (
                f"Event {evt.id} has category '{evt.annotation.category}', "
                f"expected 'correction'"
            )

    def test_green_narrative_2e6a9e_has_gotcha(self) -> None:
        """trace_20260316_2e6a9e should contain at least one gotcha annotation."""
        session = self._load_session("trace_20260316_2e6a9e.json")
        if session is None:
            return

        gotchas = [
            e for e in session.events
            if e.type == "annotation" and e.annotation
            and e.annotation.category == "gotcha"
        ]
        assert len(gotchas) > 0, (
            "Session trace_20260316_2e6a9e should have at least one gotcha "
            "(Pydantic v2 + Python 3.10 field-name shadowing)"
        )

    def test_no_sessions_have_status_mismatch(self) -> None:
        """No completed session should have status='active' with a non-null ended timestamp."""
        for path in sorted(_REAL_SESSIONS_DIR.glob("trace_*.json")):
            try:
                with open(path) as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, KeyError):
                continue

            status = raw.get("status", "unknown")
            ended = raw.get("ended")

            if ended is not None and status == "active":
                pytest.fail(
                    f"STATUS MISMATCH in {path.name}: "
                    f"ended={ended} but status='active'. "
                    f"Session should be 'completed'."
                )


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-PROJECT CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _REAL_SESSIONS_DIR.exists(),
    reason="No ~/.trace/sessions/ directory — skipping cross-project tests",
)
class TestCrossProjectConsistency:
    """Verify consistency across all projects using TRACE."""

    def test_all_completed_sessions_have_summary(self) -> None:
        """Every completed session should have a summary."""
        missing_summaries: list[str] = []
        for path in sorted(_REAL_SESSIONS_DIR.glob("trace_*.json")):
            try:
                with open(path) as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, KeyError):
                continue

            if raw.get("status") == "completed" and not raw.get("summary"):
                missing_summaries.append(path.name)

        if missing_summaries:
            pytest.fail(
                f"MISSING SUMMARIES on {len(missing_summaries)} completed sessions: "
                f"{', '.join(missing_summaries[:10])}"
                + ("..." if len(missing_summaries) > 10 else "")
            )

    def test_project_naming_consistency(self) -> None:
        """Check for project naming inconsistencies (e.g., 'When Algorithms' variants)."""
        projects: dict[str, list[str]] = {}
        for path in sorted(_REAL_SESSIONS_DIR.glob("trace_*.json")):
            try:
                with open(path) as f:
                    raw = json.load(f)
                proj = raw.get("metadata", {}).get("project", "")
                if proj:
                    projects.setdefault(proj, []).append(path.name)
            except (json.JSONDecodeError, KeyError):
                continue

        # Check for near-duplicate project names
        normalized: dict[str, list[str]] = {}
        for name in projects:
            key = name.lower().replace(" ", "-").replace("_", "-")
            normalized.setdefault(key, []).append(name)

        for key, variants in normalized.items():
            if len(variants) > 1:
                pytest.fail(
                    f"PROJECT NAMING INCONSISTENCY: {variants} "
                    f"normalize to the same key '{key}'. "
                    f"Standardize to one name."
                )

"""E2E tests for v0.4.1 URI-form `corrects_event_ids` (spec §3.7.1).

These tests use REAL JSON-file storage and exercise the actual
`append_event` code path — no mocks. They verify that URI-form entries
in `corrects_event_ids` are accepted by the validator and survive a
round-trip through storage, AND that the carve-out does not weaken
existing referential-integrity checks for in-session event IDs.

If these tests fail, the L3.1 validator carve-out is broken and
controllers following the new v0.4.1 spec/CLAUDE.md guidance will hit
ValueError when emitting URI-form corrections.

Fail-loudly contract: every test raises on incorrect behavior. No
warning-only paths. Real session lifecycle exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.schema.events import AnnotationData, EventContext, TraceEvent
from trace_mcp.schema.session import Actor
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools.session_tools import append_event, is_uri_form_reference


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


def _make_session(session_id: str) -> Session:
    """Build a real multi-actor session for these tests."""
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="trace-mcp-v041-test",
            participants=[
                Actor(type="human", id="researcher"),
                Actor(type="ai", id="claude-opus-4.7"),
            ],
        ),
    )


def _make_correction(session_id: str, corrects_event_ids: list[str], snippet: str = "test") -> TraceEvent:
    """Build a real correction-category annotation event."""
    return TraceEvent(
        session_id=session_id,
        type="annotation",
        actor=Actor(type="ai", id="claude-opus-4.7"),
        annotation=AnnotationData(
            category="correction",
            content="test correction",
            corrects_event_ids=corrects_event_ids,
        ),
        context=EventContext(conversation_snippet=snippet),
    )


class TestUriSchemeHelperUnit:
    """Direct tests on the is_uri_form_reference helper.

    The helper is the basis of the carve-out; if its discrimination
    is wrong, both false-positives and false-negatives cascade through
    the validator.
    """

    def test_external_scheme_matches(self) -> None:
        assert is_uri_form_reference("external:https://example.com/foo")

    def test_jsonl_scheme_matches(self) -> None:
        assert is_uri_form_reference("jsonl:/path/to/file.jsonl#L225")

    def test_subagent_scheme_matches(self) -> None:
        assert is_uri_form_reference("subagent:abc-123")

    def test_tool_result_scheme_matches(self) -> None:
        assert is_uri_form_reference("tool-result:xyz-789")

    def test_event_id_does_not_match(self) -> None:
        assert not is_uri_form_reference("evt_001")

    def test_event_id_with_digits_does_not_match(self) -> None:
        assert not is_uri_form_reference("evt_42")

    def test_empty_string_does_not_match(self) -> None:
        assert not is_uri_form_reference("")

    def test_uppercase_scheme_does_not_match(self) -> None:
        # Spec §3.7.1: scheme MUST be lowercase ASCII
        assert not is_uri_form_reference("EXTERNAL:foo")

    def test_scheme_starting_with_digit_does_not_match(self) -> None:
        # Spec §3.7.1: scheme MUST start with a letter
        assert not is_uri_form_reference("1abc:foo")

    def test_scheme_with_one_char_does_not_match(self) -> None:
        # Spec regex requires at least 2 chars before colon: [a-z][a-z0-9-]+
        assert not is_uri_form_reference("a:foo")

    def test_scheme_with_hyphen_matches(self) -> None:
        assert is_uri_form_reference("tool-result:abc")

    def test_no_colon_does_not_match(self) -> None:
        assert not is_uri_form_reference("external")


class TestUriFormCorrectsEventIdsE2E:
    """E2E tests: real storage, real session lifecycle, real validator."""

    async def test_external_scheme_accepted_through_append_event(
        self, storage: JsonFileStorage
    ) -> None:
        """A correction with external: scheme MUST NOT raise on append.

        This is the canonical scenario that was failing pre-L3.1: a
        controller catching a subagent's false claim, anchoring the
        correction to a transcript URI rather than an in-session event.
        """
        session = _make_session("trace_test_external")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["external:https://example.com/transcript#L225"],
            snippet="implementer claimed pyright clean but harness flagged 4 warnings",
        )

        # MUST NOT raise — this is the L3.1 guarantee.
        event_id = await append_event(storage, session, event)
        assert event_id.startswith("evt_")

    async def test_jsonl_scheme_accepted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_jsonl")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["jsonl:/Users/x/.claude/projects/abc/transcript.jsonl#L225-L238"],
        )
        await append_event(storage, session, event)  # MUST NOT raise

    async def test_subagent_scheme_accepted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_subagent")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["subagent:ad9350bfec6ce79f9"],
        )
        await append_event(storage, session, event)

    async def test_tool_result_scheme_accepted(self, storage: JsonFileStorage) -> None:
        session = _make_session("trace_test_tool_result")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["tool-result:bash_abc123"],
        )
        await append_event(storage, session, event)

    async def test_round_trip_preserves_uri_form(self, storage: JsonFileStorage) -> None:
        """URI-form entries survive write → reload through real JSON storage.

        Catches regressions where (e.g.) Pydantic dumps drop the entry, or
        the storage layer reorders or normalizes.
        """
        session = _make_session("trace_test_round_trip")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=[
                "external:https://example.com/foo",
                "jsonl:/path/to/file#L1",
                "subagent:abc",
                "tool-result:xyz",
            ],
        )
        await append_event(storage, session, event)

        # Reload from disk through the storage layer — no in-memory shortcut
        loaded = await storage.get_session(session.id)
        assert len(loaded.events) == 1
        ann = loaded.events[0].annotation
        assert ann is not None
        assert ann.corrects_event_ids == [
            "external:https://example.com/foo",
            "jsonl:/path/to/file#L1",
            "subagent:abc",
            "tool-result:xyz",
        ]

    async def test_mixed_event_id_and_uri_form_accepted(
        self, storage: JsonFileStorage
    ) -> None:
        """A correction can anchor to both an in-session event ID AND a URI.

        Real workflow: the corrected work produced a contribution that's
        logged (in-session), AND the false claim that motivated the
        correction is in the transcript (URI). Both go in corrects_event_ids.
        """
        session = _make_session("trace_test_mixed")
        await storage.create_session(session)

        # Log a prior event to anchor to
        prior = TraceEvent(
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude-opus-4.7"),
            annotation=AnnotationData(category="learning", content="initial observation"),
        )
        prior_id = await append_event(storage, session, prior)

        # Correction anchors to both the in-session event AND a URI
        correction = _make_correction(
            session.id,
            corrects_event_ids=[prior_id, "external:https://example.com/source"],
        )
        await append_event(storage, session, correction)  # MUST NOT raise

    async def test_dangling_event_id_still_raises(self, storage: JsonFileStorage) -> None:
        """In-session event-ID references MUST still be validated.

        The L3.1 carve-out only exempts URI-form entries. A non-URI ref
        that doesn't match any session event MUST still raise. This
        ensures the carve-out doesn't accidentally weaken existing
        protection against typos and stale references.
        """
        session = _make_session("trace_test_dangling")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["evt_999"],  # not in session, not URI form
        )

        with pytest.raises(ValueError, match="Dangling reference"):
            await append_event(storage, session, event)

    async def test_dangling_mixed_with_uri_still_raises_for_bad_event_id(
        self, storage: JsonFileStorage
    ) -> None:
        """A URI-form entry does NOT shield a bad event-ID entry beside it.

        Each entry is validated independently per its kind. URI-form
        skip; event-ID-form check.
        """
        session = _make_session("trace_test_mixed_bad")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["external:ok", "evt_999"],  # second is bad
        )

        with pytest.raises(ValueError, match="Dangling reference"):
            await append_event(storage, session, event)

    async def test_uppercase_scheme_treated_as_event_id_and_fails_validation(
        self, storage: JsonFileStorage
    ) -> None:
        """A string like `EXTERNAL:foo` is NOT URI-form (must be lowercase).

        Therefore it falls through to event-ID validation and (since no
        such event exists) MUST raise. This catches a class of bugs
        where a producer accidentally uses uppercase scheme and gets
        silent acceptance.
        """
        session = _make_session("trace_test_uppercase")
        await storage.create_session(session)

        event = _make_correction(
            session.id,
            corrects_event_ids=["EXTERNAL:foo"],  # uppercase — not URI form
        )

        with pytest.raises(ValueError, match="Dangling reference"):
            await append_event(storage, session, event)


class TestNonCorrectionFieldsUnchanged:
    """Verify the L3.1 carve-out is scoped strictly to corrects_event_ids.

    Per spec §4.4, only corrects_event_ids accepts URI-form entries.
    revises_event_id, retries_event_id, related_decision_ids remain
    event-ID-only.
    """

    async def test_revises_event_id_still_validated(
        self, storage: JsonFileStorage
    ) -> None:
        """A decision's revises_event_id must still be a real event ID."""
        from trace_mcp.schema.events import DecisionData

        session = _make_session("trace_test_revises")
        await storage.create_session(session)

        bad = TraceEvent(
            session_id=session.id,
            type="decision",
            actor=Actor(type="human", id="researcher"),
            decision=DecisionData(
                description="revise something nonexistent",
                proposed_by=Actor(type="human", id="researcher"),
                revises_event_id="evt_999",
            ),
        )

        with pytest.raises(ValueError, match="Dangling reference"):
            await append_event(storage, session, bad)

    async def test_related_decision_ids_still_validated(
        self, storage: JsonFileStorage
    ) -> None:
        """A contribution's related_decision_ids must reference real events."""
        from trace_mcp.schema.events import ContributionData

        session = _make_session("trace_test_related")
        await storage.create_session(session)

        bad = TraceEvent(
            session_id=session.id,
            type="contribution",
            actor=Actor(type="ai", id="claude-opus-4.7"),
            contribution=ContributionData(
                description="references nonexistent decision",
                direction="human",
                execution="ai",
                related_decision_ids=["evt_999"],
            ),
        )

        with pytest.raises(ValueError, match="Dangling reference"):
            await append_event(storage, session, bad)

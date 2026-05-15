"""E2E tests for v0.4.1 PROV-LD export split (L6.x, spec §6).

Verifies the new PROV mappings introduced in v0.4.1:
  - Correction with in-session event-ID target → prov:wasInvalidatedBy
  - Correction with URI-form target → qualified prov:wasInfluencedBy
    with prov:qualifiedInfluence pointing to a prov:Influence blank node
    bearing prov:atLocation
  - tool_call.parent_event_id → prov:wasInformedBy

These tests build real sessions in memory, export them via the actual
PROV-JSON exporter, and assert on the structured output. No mocks.

The old `wasRevisionOf` mapping for corrections is GONE — consumers
matching that predicate for corrections must update their queries.
`wasRevisionOf` continues to be emitted for decision revisions
(revises_event_id) and tool_call retries (retries_event_id) — those
ARE evolutionary refinements and the existing mapping is correct.
"""

from __future__ import annotations

import json

from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.schema.events import (
    AnnotationData,
    DecisionData,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor


def _make_session(session_id: str = "trace_test_prov") -> Session:
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="prov-ld-split-test",
            participants=[
                Actor(type="human", id="researcher"),
                Actor(type="ai", id="claude-opus-4.7"),
            ],
        ),
    )


def _export_bundle(session: Session) -> dict:
    """Export and return the inner bundle dict for easy inspection."""
    raw = export_prov_jsonld(session)
    doc = json.loads(raw)
    bundle = doc["bundle"][f"trace:{session.id}"]
    return bundle


class TestCorrectionInvalidatedBy:
    """Event-ID corrections → prov:wasInvalidatedBy (v0.4.1)."""

    def test_event_id_target_emits_wasInvalidatedBy(self) -> None:
        """A correction with an in-session event-ID target emits the
        invalidation relation, NOT the legacy wasRevisionOf."""
        session = _make_session()

        # Prior event to be corrected
        prior = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude-opus-4.7"),
            annotation=AnnotationData(category="observation", content="initial"),
        )
        session.events.append(prior)

        correction = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="that prior observation was wrong",
                corrects_event_ids=["evt_001"],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)
        assert "wasInvalidatedBy" in bundle
        inv = bundle["wasInvalidatedBy"]
        # Look for the entry referring to evt_001
        keys = [k for k in inv.keys() if "evt_001" in k]
        assert len(keys) == 1, f"expected 1 invalidation entry for evt_001, got {len(keys)}"
        entry = inv[keys[0]]
        assert entry["prov:entity"] == "trace:evt_001"
        assert entry["prov:activity"] == "trace:evt_002_annotation"

    def test_event_id_correction_NOT_in_wasRevisionOf(self) -> None:
        """v0.4.1 BREAKING for consumers: corrections no longer use
        wasRevisionOf. Migration callout in CHANGELOG."""
        session = _make_session()

        prior = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(category="observation", content="x"),
        )
        session.events.append(prior)

        correction = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="wrong",
                corrects_event_ids=["evt_001"],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)
        # No `corr_*` keys in wasRevisionOf (the legacy mapping)
        rev = bundle.get("wasRevisionOf", {})
        for k in rev.keys():
            assert not k.startswith("trace:corr_"), (
                f"legacy correction key in wasRevisionOf: {k}"
            )


class TestCorrectionInfluencedByUri:
    """URI-form corrections → qualified prov:wasInfluencedBy (v0.4.1)."""

    def test_uri_target_emits_wasInfluencedBy_with_qualifiedInfluence(self) -> None:
        """A correction anchored to an external URI emits the qualified-
        influence pattern: wasInfluencedBy relation + Influence blank
        node + atLocation carrying the URI."""
        session = _make_session()

        correction = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="subagent's pyright claim was false",
                corrects_event_ids=["external:https://example.com/transcript#L225"],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)

        # Top-level wasInfluencedBy section exists with one entry
        assert "wasInfluencedBy" in bundle
        wif = bundle["wasInfluencedBy"]
        keys = list(wif.keys())
        assert len(keys) == 1
        entry = wif[keys[0]]

        # The influenced entity is the annotation
        assert entry["prov:influenced"] == "trace:evt_001_annotation"

        # The qualifiedInfluence link points to a blank node ID
        qi_ref = entry["prov:qualifiedInfluence"]
        assert qi_ref.startswith("_:infl_")

        # The blank node exists in the influence section
        assert "influence" in bundle
        infl = bundle["influence"]
        assert qi_ref in infl
        infl_node = infl[qi_ref]

        # The blank node has the qualified-influence type AND the URI
        assert infl_node["prov:type"] == "prov:Influence"
        assert infl_node["prov:atLocation"] == "external:https://example.com/transcript#L225"

    def test_uri_target_NOT_in_wasInvalidatedBy(self) -> None:
        """URI-form refs go to wasInfluencedBy, NOT wasInvalidatedBy."""
        session = _make_session()

        correction = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="x",
                corrects_event_ids=["jsonl:/path#L1"],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)
        # wasInvalidatedBy should be empty (and cleaned out) for URI-only correction
        assert "wasInvalidatedBy" not in bundle

    def test_multiple_uris_get_distinct_qi_ids(self) -> None:
        """Each URI-form entry gets a deterministic, distinct blank-node ID."""
        session = _make_session()

        correction = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="multi-anchor",
                corrects_event_ids=[
                    "external:https://example.com/a",
                    "jsonl:/path#L1",
                ],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)
        wif = bundle["wasInfluencedBy"]
        assert len(wif) == 2

        # Each entry resolves to a distinct blank node in `influence`
        qi_ids = {wif[k]["prov:qualifiedInfluence"] for k in wif}
        assert len(qi_ids) == 2  # distinct
        infl = bundle["influence"]
        assert all(qi in infl for qi in qi_ids)

        # The two URIs both appear as atLocation values
        locations = {infl[qi]["prov:atLocation"] for qi in qi_ids}
        assert locations == {"external:https://example.com/a", "jsonl:/path#L1"}


class TestMixedEventIdAndUriCorrection:
    """A correction can have BOTH event-ID and URI-form anchors."""

    def test_mixed_emits_both_relations(self) -> None:
        session = _make_session()

        prior = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(category="observation", content="x"),
        )
        session.events.append(prior)

        correction = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="wrong",
                corrects_event_ids=["evt_001", "external:https://example.com/source"],
            ),
        )
        session.events.append(correction)

        bundle = _export_bundle(session)
        # Event-ID → wasInvalidatedBy
        assert "wasInvalidatedBy" in bundle
        assert len(bundle["wasInvalidatedBy"]) == 1
        # URI-form → wasInfluencedBy + influence
        assert "wasInfluencedBy" in bundle
        assert len(bundle["wasInfluencedBy"]) == 1
        assert "influence" in bundle


class TestDispatchParentWasInformedBy:
    """tool_call.parent_event_id → prov:wasInformedBy (v0.4.1)."""

    def test_parent_event_id_emits_wasInformedBy(self) -> None:
        """A subagent dispatch's parent_event_id link emits the dispatch-
        chain relation."""
        session = _make_session()

        # Controller decision that motivated the dispatch
        decision = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="decision",
            actor=Actor(type="human", id="researcher"),
            decision=DecisionData(
                description="start matcher iteration",
                proposed_by=Actor(type="human", id="researcher"),
                disposition="accepted",
                resolved_by=Actor(type="ai", id="claude-opus-4.7"),
            ),
        )
        session.events.append(decision)

        # Subagent dispatch with parent_event_id linking back to evt_001
        dispatch = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude-opus-4.7"),
            tool_call=ToolCallData(
                server="claude-code",
                name="Agent",
                input={"task": "implementer"},
                host="internal",
                parent_event_id="evt_001",
            ),
        )
        session.events.append(dispatch)

        bundle = _export_bundle(session)
        assert "wasInformedBy" in bundle
        wib = bundle["wasInformedBy"]
        # Look for entry where informed is the dispatch and informant is the parent
        match = [
            v for v in wib.values()
            if v.get("prov:informed") == "trace:evt_002"
            and v.get("prov:informant") == "trace:evt_001"
        ]
        assert len(match) == 1

    def test_no_parent_no_wasInformedBy_entry(self) -> None:
        """A regular tool_call (no parent_event_id) does NOT emit wasInformedBy."""
        session = _make_session()

        tc = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(
                server="some-mcp-server",
                name="search",
                input={"q": "x"},
                # no parent_event_id
            ),
        )
        session.events.append(tc)

        bundle = _export_bundle(session)
        # Either wasInformedBy section absent (cleaned out as empty) OR empty
        assert not bundle.get("wasInformedBy")


class TestUnchangedRelations:
    """Decision revisions + tool retries still use prov:wasRevisionOf."""

    def test_decision_revision_still_wasRevisionOf(self) -> None:
        """revises_event_id on decisions is genuinely evolutionary refinement —
        wasRevisionOf is the correct PROV mapping (unchanged in v0.4.1)."""
        session = _make_session()

        first = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="decision",
            actor=Actor(type="ai", id="claude"),
            decision=DecisionData(
                description="threshold 0.85",
                proposed_by=Actor(type="ai", id="claude"),
                disposition="revised",
                resolved_by=Actor(type="human", id="researcher"),
                revision_note="see evt_002",
            ),
        )
        session.events.append(first)

        revised = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="decision",
            actor=Actor(type="human", id="researcher"),
            decision=DecisionData(
                description="threshold 0.80",
                proposed_by=Actor(type="human", id="researcher"),
                disposition="accepted",
                resolved_by=Actor(type="ai", id="claude"),
                revises_event_id="evt_001",
            ),
        )
        session.events.append(revised)

        bundle = _export_bundle(session)
        rev = bundle.get("wasRevisionOf", {})
        match = [
            v for v in rev.values()
            if v.get("prov:generatedEntity") == "trace:evt_002"
            and v.get("prov:usedEntity") == "trace:evt_001"
        ]
        assert len(match) == 1

    def test_tool_call_retry_still_wasRevisionOf(self) -> None:
        """retries_event_id on tool calls is unchanged in v0.4.1."""
        session = _make_session()

        first = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(
                server="mcp",
                name="search",
                input={"q": "x"},
                status="error",
            ),
        )
        session.events.append(first)

        retry = TraceEvent(
            id="evt_002",
            session_id=session.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(
                server="mcp",
                name="search",
                input={"q": "x"},
                retries_event_id="evt_001",
            ),
        )
        session.events.append(retry)

        bundle = _export_bundle(session)
        rev = bundle.get("wasRevisionOf", {})
        match = [
            v for v in rev.values()
            if v.get("prov:generatedEntity") == "trace:evt_002"
            and v.get("prov:usedEntity") == "trace:evt_001"
        ]
        assert len(match) == 1


class TestRoundTrip:
    """Export round-trips through json.loads/dumps cleanly."""

    def test_export_is_valid_json(self) -> None:
        session = _make_session()

        correction = TraceEvent(
            id="evt_001",
            session_id=session.id,
            type="annotation",
            actor=Actor(type="ai", id="claude"),
            annotation=AnnotationData(
                category="correction",
                content="x",
                corrects_event_ids=["external:https://example.com/foo"],
            ),
        )
        session.events.append(correction)

        raw = export_prov_jsonld(session)
        parsed = json.loads(raw)
        assert "@context" in parsed
        assert "bundle" in parsed

"""E2E tests for the v0.4.1 PROV-LD export split (L6.x, spec §6).

Asserts the v0.4.1 PROV semantics against the **conformant JSON-LD**
output by parsing it with a real RDF parser (rdflib) and checking the
resulting PROV-O triples — not the old bespoke PROV-JSON dict shape
(which P5/A-R3-7 proved a conformant parser extracts nothing from).

Semantics verified:
  - Correction with in-session event-ID target → prov:wasInvalidatedBy
  - Correction with URI-form target → prov:qualifiedInfluence → a
    prov:Influence node bearing prov:atLocation (the URI)
  - tool_call.parent_event_id → prov:wasInformedBy
  - Decision revisions + tool retries still → prov:wasRevisionOf

rdflib is a dev/test dependency (pyproject [dev]); skipped if absent,
run for real under `uv run --with rdflib`.
"""

from __future__ import annotations

import json

import pytest

rdflib = pytest.importorskip("rdflib")
from rdflib import Graph, Literal, Namespace, URIRef  # noqa: E402
from rdflib.namespace import RDF  # noqa: E402

from trace_mcp.exporters.prov_jsonld import export_prov_jsonld  # noqa: E402
from trace_mcp.schema import Session, SessionMetadata  # noqa: E402
from trace_mcp.schema.events import (  # noqa: E402
    AnnotationData,
    DecisionData,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor  # noqa: E402

PROV = Namespace("http://www.w3.org/ns/prov#")
TRACE = Namespace("https://trace-protocol.org/ns/v0.3#")


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


def _graph(session: Session) -> Graph:
    """Export and parse into an RDF graph via a real JSON-LD parser."""
    raw = export_prov_jsonld(session)
    g = Graph()
    g.parse(data=raw, format="json-ld")
    return g


class TestCorrectionInvalidatedBy:
    """Event-ID corrections → prov:wasInvalidatedBy (v0.4.1)."""

    def test_event_id_target_emits_wasInvalidatedBy(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="ai", id="claude-opus-4.7"),
                annotation=AnnotationData(category="observation", content="initial"),
            )
        )
        session.events.append(
            TraceEvent(
                id="evt_002",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="human", id="researcher"),
                annotation=AnnotationData(
                    category="correction",
                    content="that was wrong",
                    corrects_event_ids=["evt_001"],
                ),
            )
        )
        g = _graph(session)
        assert (TRACE.evt_001, PROV.wasInvalidatedBy, TRACE.evt_002_annotation) in g

    def test_event_id_correction_NOT_wasRevisionOf(self) -> None:
        """v0.4.1 BREAKING: corrections no longer use wasRevisionOf."""
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(category="observation", content="x"),
            )
        )
        session.events.append(
            TraceEvent(
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
        )
        g = _graph(session)
        # No wasRevisionOf triple involving the correction or corrected event.
        assert (TRACE.evt_001, PROV.wasRevisionOf, None) not in g
        assert (TRACE.evt_002_annotation, PROV.wasRevisionOf, None) not in g


class TestCorrectionInfluencedByUri:
    """URI-form corrections → qualified prov:Influence (v0.4.1)."""

    def test_uri_target_emits_qualified_influence_with_atLocation(self) -> None:
        session = _make_session()
        uri = "external:https://example.com/transcript#L225"
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="human", id="researcher"),
                annotation=AnnotationData(
                    category="correction",
                    content="claim was false",
                    corrects_event_ids=[uri],
                ),
            )
        )
        g = _graph(session)
        qis = list(g.objects(TRACE.evt_001_annotation, PROV.qualifiedInfluence))
        assert len(qis) == 1
        infl = qis[0]
        assert (infl, RDF.type, PROV.Influence) in g
        assert (infl, PROV.atLocation, Literal(uri)) in g

    def test_uri_target_NOT_wasInvalidatedBy(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
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
        )
        g = _graph(session)
        assert (None, PROV.wasInvalidatedBy, None) not in g

    def test_multiple_uris_get_distinct_influence_nodes(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="human", id="researcher"),
                annotation=AnnotationData(
                    category="correction",
                    content="multi",
                    corrects_event_ids=["external:https://example.com/a", "jsonl:/path#L1"],
                ),
            )
        )
        g = _graph(session)
        qis = set(g.objects(TRACE.evt_001_annotation, PROV.qualifiedInfluence))
        assert len(qis) == 2
        locations = {str(loc) for qi in qis for loc in g.objects(qi, PROV.atLocation)}
        assert locations == {"external:https://example.com/a", "jsonl:/path#L1"}


class TestMixedEventIdAndUriCorrection:
    def test_mixed_emits_both_relations(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(category="observation", content="x"),
            )
        )
        session.events.append(
            TraceEvent(
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
        )
        g = _graph(session)
        assert (TRACE.evt_001, PROV.wasInvalidatedBy, TRACE.evt_002_annotation) in g
        assert len(list(g.objects(TRACE.evt_002_annotation, PROV.qualifiedInfluence))) == 1


class TestDispatchParentWasInformedBy:
    """tool_call.parent_event_id → prov:wasInformedBy (v0.4.1)."""

    def test_parent_event_id_emits_wasInformedBy(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="decision",
                actor=Actor(type="human", id="researcher"),
                decision=DecisionData(
                    description="start",
                    proposed_by=Actor(type="human", id="researcher"),
                    disposition="accepted",
                    resolved_by=Actor(type="ai", id="claude-opus-4.7"),
                ),
            )
        )
        session.events.append(
            TraceEvent(
                id="evt_002",
                session_id=session.id,
                type="tool_call",
                actor=Actor(type="ai", id="claude-opus-4.7"),
                tool_call=ToolCallData(
                    server="claude-code",
                    name="Agent",
                    input={"task": "x"},
                    host="internal",
                    parent_event_id="evt_001",
                ),
            )
        )
        g = _graph(session)
        assert (TRACE.evt_002, PROV.wasInformedBy, TRACE.evt_001) in g

    def test_no_parent_no_wasInformedBy(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="tool_call",
                actor=Actor(type="ai", id="claude"),
                tool_call=ToolCallData(server="mcp", name="search", input={"q": "x"}),
            )
        )
        g = _graph(session)
        assert (None, PROV.wasInformedBy, None) not in g


class TestUnchangedRelations:
    """Decision revisions + tool retries still → prov:wasRevisionOf."""

    def test_decision_revision_still_wasRevisionOf(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="decision",
                actor=Actor(type="ai", id="claude"),
                decision=DecisionData(
                    description="0.85",
                    proposed_by=Actor(type="ai", id="claude"),
                    disposition="revised",
                    resolved_by=Actor(type="human", id="researcher"),
                    revision_note="see evt_002",
                ),
            )
        )
        session.events.append(
            TraceEvent(
                id="evt_002",
                session_id=session.id,
                type="decision",
                actor=Actor(type="human", id="researcher"),
                decision=DecisionData(
                    description="0.80",
                    proposed_by=Actor(type="human", id="researcher"),
                    disposition="accepted",
                    resolved_by=Actor(type="ai", id="claude"),
                    revises_event_id="evt_001",
                ),
            )
        )
        g = _graph(session)
        assert (TRACE.evt_002, PROV.wasRevisionOf, TRACE.evt_001) in g

    def test_tool_call_retry_still_wasRevisionOf(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=session.id,
                type="tool_call",
                actor=Actor(type="ai", id="claude"),
                tool_call=ToolCallData(server="mcp", name="s", input={"q": "x"}, status="error"),
            )
        )
        session.events.append(
            TraceEvent(
                id="evt_002",
                session_id=session.id,
                type="tool_call",
                actor=Actor(type="ai", id="claude"),
                tool_call=ToolCallData(server="mcp", name="s", input={"q": "x"}, retries_event_id="evt_001"),
            )
        )
        g = _graph(session)
        assert (TRACE.evt_002, PROV.wasRevisionOf, TRACE.evt_001) in g


class TestConformance:
    """The export is valid, parseable, conformant JSON-LD."""

    def test_export_is_valid_jsonld_graph(self) -> None:
        session = _make_session()
        session.events.append(
            TraceEvent(
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
        )
        raw = export_prov_jsonld(session)
        parsed = json.loads(raw)
        assert "@context" in parsed and "@graph" in parsed
        g = _graph(session)
        assert len(g) > 0
        # PROV namespace actually present (real RDF, not strings).
        assert any(str(p).startswith(str(PROV)) for _, p, _ in g) or any(
            isinstance(o, URIRef) and str(o).startswith(str(PROV)) for _, _, o in g
        )

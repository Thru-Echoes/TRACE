"""P5 / Round-3 A-R3-7 (A4): PROV-LD export round-trips through a real
PROV-O / RDF parser.

The pre-existing PROV tests only do ``json.loads`` + key-presence — they
never proved the emitted document is *valid* JSON-LD/RDF. This test parses
the exporter output with rdflib (a real RDF/JSON-LD parser) and asserts
the graph is well-formed, non-empty, and carries the standard PROV
namespace — the parser-validation A4 said was missing.

rdflib is a dev/test dependency (pyproject [dev]); skipped if absent so
non-dev installs don't fail, but CI/dev runs it for real.
"""

from __future__ import annotations

import json

import pytest

rdflib = pytest.importorskip("rdflib")

from trace_mcp.exporters.prov_jsonld import export_prov_jsonld  # noqa: E402
from trace_mcp.schema import Session, SessionMetadata  # noqa: E402
from trace_mcp.schema.events import AnnotationData, ToolCallData, TraceEvent  # noqa: E402
from trace_mcp.schema.session import Actor  # noqa: E402

_PROV = "http://www.w3.org/ns/prov#"


def _session_with_v041_prov_shapes() -> Session:
    s = Session(
        id="trace_test_p5",
        metadata=SessionMetadata(
            project="p5-roundtrip",
            participants=[Actor(type="human", id="researcher"), Actor(type="ai", id="claude")],
        ),
    )
    # event-target correction → prov:wasInvalidatedBy
    s.events.append(
        TraceEvent(
            id="evt_001",
            session_id=s.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(server="x", name="t", input={}),
        )
    )
    s.events.append(
        TraceEvent(
            id="evt_002",
            session_id=s.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(category="correction", content="fix", corrects_event_ids=["evt_001"]),
        )
    )
    # URI-target correction → qualified prov:wasInfluencedBy + prov:atLocation
    s.events.append(
        TraceEvent(
            id="evt_003",
            session_id=s.id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="external claim wrong",
                corrects_event_ids=["external:https://example.com/t#L9"],
            ),
        )
    )
    # parent_event_id dispatch → prov:wasInformedBy
    s.events.append(
        TraceEvent(
            id="evt_004",
            session_id=s.id,
            type="tool_call",
            actor=Actor(type="ai", id="claude"),
            tool_call=ToolCallData(server="claude-code", name="Agent", input={}, parent_event_id="evt_001"),
        )
    )
    return s


def test_prov_export_is_valid_parseable_jsonld() -> None:
    """The exported document must parse cleanly via a real JSON-LD/RDF
    parser into a non-empty graph (catches malformed JSON-LD regressions)."""
    raw = export_prov_jsonld(_session_with_v041_prov_shapes())
    # Sanity: it is valid JSON first.
    json.loads(raw)
    g = rdflib.Graph()
    g.parse(data=raw, format="json-ld")  # raises on invalid JSON-LD
    assert len(g) > 0, "PROV-LD parsed to an empty graph"


def test_prov_namespace_present_in_graph() -> None:
    """Standard PROV-O namespace IRIs must appear in the parsed graph —
    proves the document carries real PROV vocabulary, not just strings."""
    raw = export_prov_jsonld(_session_with_v041_prov_shapes())
    g = rdflib.Graph()
    g.parse(data=raw, format="json-ld")
    iris = {str(p) for _, p, _ in g}
    iris |= {str(o) for _, _, o in g if isinstance(o, rdflib.URIRef)}
    assert any(i.startswith(_PROV) for i in iris), (
        f"no PROV-O namespace IRI in parsed graph; sample={sorted(iris)[:10]}"
    )

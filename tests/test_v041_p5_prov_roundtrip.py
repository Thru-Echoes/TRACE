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


_TRACE_NS = "https://trace-protocol.org/ns/v0.3#"


def _session_with_confidence() -> Session:
    from trace_mcp.schema import DecisionConfidence
    from trace_mcp.schema.events import DecisionData

    s = Session(
        id="trace_test_conf",
        metadata=SessionMetadata(project="confidence-roundtrip", participants=[Actor(type="ai", id="claude")]),
    )
    s.events.append(
        TraceEvent(
            id="evt_001",
            session_id="trace_test_conf",
            type="decision",
            actor=Actor(type="ai", id="claude"),
            decision=DecisionData(
                description="Keep v3 provisionally",
                proposed_by=Actor(type="ai", id="claude"),
                confidence=DecisionConfidence.model_validate(
                    {
                        "interval": {"lower": -30.0, "upper": 583.75, "level": 0.9},
                        "method": {"name": "percentile_bootstrap"},
                        "sample_size": 8,
                        "statistic": "mean_paired_delta",
                        "direction": "higher",
                        "estimate": 260.0,
                        "evidence": [
                            {"role": "candidate", "locator": "results/v3/visible_result.json", "sha256": "b" * 64}
                        ],
                        "evidence_digests": {"candidate": "sha256:" + "b" * 64},
                    }
                ),
            ),
        )
    )
    return s


def test_confidence_terms_survive_jsonld_parsing() -> None:
    """The confidence literals and the evidence entity are real triples, not just JSON keys."""
    raw = export_prov_jsonld(_session_with_confidence())
    g = rdflib.Graph()
    g.parse(data=raw, format="json-ld")

    lows = [o.toPython() for _, _, o in g.triples((None, rdflib.URIRef(_TRACE_NS + "confidenceLow"), None))]
    assert -30.0 in lows

    used = [o for _, _, o in g.triples((None, rdflib.URIRef(_PROV + "used"), None))]
    assert used
    digests = [str(o) for entity in used for _, _, o in g.triples((entity, rdflib.URIRef(_TRACE_NS + "sha256"), None))]
    assert "b" * 64 in digests

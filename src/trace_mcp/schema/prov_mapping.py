"""W3C PROV export mapping definitions.

Maps TRACE concepts to W3C PROV ontology terms for interoperable provenance export.
"""

# TRACE → PROV concept mapping
PROV_MAPPING = {
    "Session": "prov:Bundle",
    "TraceEvent": "prov:Activity",
    "Actor": "prov:Agent",
    "ToolCallData.input": "prov:Entity",  # prov:used
    "ToolCallData.output": "prov:Entity",  # prov:wasGeneratedBy
    "DecisionData": "prov:Activity",  # with trace: attributes
    "DecisionData.revision": "prov:wasRevisionOf",
    # v0.5.1: decision confidence. Scalar trace:confidence* literals on the decision Activity (the two
    # bounds are separate literals because a bare JSON-LD array is a set and may be reordered); each
    # evidence file is a prov:Entity the decision prov:used, which, as with tool inputs, records the
    # producer's assertion (TRACE verifies no digest). Entity ids are content hashes of the reference.
    # Rule-state extras (verdict, min_effect, holdout) are not projected: TRACE does not read them.
    "DecisionData.confidence": "trace:confidence* literals on the prov:Activity",
    "DecisionData.confidence.evidence[]": "prov:Entity (trace:kind=Evidence), prov:used by the decision Activity",
    "AnnotationData": "prov:Entity",  # prov:wasAttributedTo
    # v0.4.1: corrections split into two relations depending on target shape.
    # Event-ID target = repudiatory invalidation (the prior event is no longer
    # valid). URI-form target = influence from an externally-located artifact,
    # reified through a qualified Influence node bearing prov:atLocation.
    "AnnotationData.corrects_event_ids[evt_*]": "prov:wasInvalidatedBy",
    "AnnotationData.corrects_event_ids[<scheme>:*]": "prov:wasInfluencedBy",
    # v0.4.1: tool_call dispatch chain — controller event informed the dispatch.
    "ToolCallData.parent_event_id": "prov:wasInformedBy",
}

# Namespace URIs are identifiers, not resolvable URLs — this is standard
# W3C PROV practice.  The "trace:" prefix defines a namespace for TRACE-
# specific properties (trace:description, trace:disposition, etc.) within
# PROV JSON-LD documents.
PROV_CONTEXT = {
    "prov": "http://www.w3.org/ns/prov#",
    "trace": "https://trace-protocol.org/ns/v0.3#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

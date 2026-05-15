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

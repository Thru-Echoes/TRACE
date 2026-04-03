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

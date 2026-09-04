"""W3C PROV JSON-LD export for TRACE sessions.

Emits **standards-conformant JSON-LD** (a flat ``@graph`` of typed node
objects with ``prov:``/``trace:`` predicates) so the document round-trips
through any conformant JSON-LD/RDF processor (e.g. rdflib) into real
PROV-O triples.

History: v0.4.x originally emitted W3C *PROV-JSON* (a different
serialization) under a JSON-LD ``@context`` — a conformant JSON-LD
parser extracted **zero** triples from it; this was replaced with
this conformant node-object form (verified by an rdflib
round-trip test). Semantics are unchanged: the v0.4.1 correction split
(event-ID target → ``prov:wasInvalidatedBy``; URI-form target →
``prov:qualifiedInfluence`` → a ``prov:Influence`` node bearing
``prov:atLocation``) and ``parent_event_id`` → ``prov:wasInformedBy``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from trace_mcp import project_identity as pident
from trace_mcp.schema import Session
from trace_mcp.schema.prov_mapping import PROV_CONTEXT
from trace_mcp.tools.session_tools import is_uri_form_reference


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _dt(dt: Any) -> dict[str, str] | None:
    """xsd:dateTime typed literal node (or None)."""
    iso = _iso(dt)
    return {"@value": iso, "@type": "xsd:dateTime"} if iso is not None else None


def _project_entity(node: Any, project_key: str) -> None:
    """Reify the project as an Entity carrying its historical alias labels.

    Optional per spec §6: it is what lets a consumer holding an artifact stamped
    with a drifted display label discover that the label and the key name the
    same project, without needing the registry file itself. Silent no-op when no
    registry is available or the key is unenrolled — an export must never fail
    because identity metadata is missing.
    """
    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError:
        return
    if registry is None:
        return
    entry = registry.projects.get(project_key)
    if entry is None:
        return
    pn = node(f"trace:project_{project_key}", "prov:Entity")
    pn["trace:kind"] = "Project"
    pn["trace:projectKey"] = project_key
    pn["trace:project"] = entry.display_label
    if entry.aliases:
        pn["trace:aliasLabel"] = list(entry.aliases)


def _lit(value: Any) -> Any:
    """Coerce a property value to a JSON-LD-safe literal.

    Scalars pass through; dicts/lists are JSON-stringified so they remain
    valid JSON-LD string literals (a conformant parser would otherwise
    drop a bare nested object that has no @id/@value).
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, default=str, sort_keys=True)


def export_prov_jsonld(session: Session) -> str:
    """Export a TRACE session as standards-conformant W3C PROV JSON-LD."""
    nodes: dict[str, dict[str, Any]] = {}

    def node(node_id: str, node_type: str | None = None) -> dict[str, Any]:
        n = nodes.setdefault(node_id, {"@id": node_id})
        if node_type is not None:
            existing = n.get("@type")
            if existing is None:
                n["@type"] = node_type
            elif existing != node_type:
                n["@type"] = sorted({*([existing] if isinstance(existing, str) else existing), node_type})
        return n

    def rel(subject_id: str, predicate: str, object_id: str) -> None:
        node(subject_id).setdefault(predicate, []).append({"@id": object_id})

    # Agents (declared participants)
    for p in session.metadata.participants:
        ag = node(f"trace:{p.id}", "prov:Agent")
        ag["trace:actorType"] = p.type
        if p.role:
            ag["trace:role"] = p.role

    # Session as an activity
    sess_id = f"trace:session_{session.id}"
    sn = node(sess_id, "prov:Activity")
    sn["trace:kind"] = "Session"
    if _dt(session.created):
        sn["prov:startedAtTime"] = _dt(session.created)
    if _dt(session.ended):
        sn["prov:endedAtTime"] = _dt(session.ended)
    # `trace:project` keeps its display-label meaning FOREVER: the already-exported
    # corpus carries drifted labels under it, and re-meaning the predicate would
    # silently reinterpret those artifacts. `trace:projectKey` is the additive
    # sibling that carries identity. Resolution goes through the registry-aware
    # helper rather than bare canonicalization, so a session whose label is a
    # registered alias of a renamed project exports the project's real key
    # instead of a key derived from the old label.
    sn["trace:project"] = session.metadata.project
    project_key = pident.session_project_key(session.metadata)
    if project_key:
        sn["trace:projectKey"] = project_key
        _project_entity(node, project_key)
    sn["trace:status"] = session.status

    for evt in session.events:
        aid = f"trace:{evt.id}"
        actor_id = f"trace:{evt.actor.id}"
        ag = node(actor_id, "prov:Agent")
        ag.setdefault("trace:actorType", evt.actor.type)

        if evt.type == "tool_call" and evt.tool_call:
            tc = evt.tool_call
            a = node(aid, "prov:Activity")
            a["trace:kind"] = "ToolCall"
            if _dt(evt.timestamp):
                a["prov:startedAtTime"] = _dt(evt.timestamp)
            a["trace:server"] = tc.server
            a["trace:toolName"] = tc.name
            a["trace:status"] = tc.status
            if tc.duration_ms is not None:
                a["trace:durationMs"] = tc.duration_ms
            a["prov:wasAssociatedWith"] = {"@id": actor_id}
            if tc.retries_event_id:
                # Unchanged in v0.4.1: a retry is an evolutionary refinement
                # of the original call → prov:wasRevisionOf.
                rel(aid, "prov:wasRevisionOf", f"trace:{tc.retries_event_id}")
            # v0.4.1: parent_event_id → prov:wasInformedBy (dispatch chain).
            if tc.parent_event_id:
                rel(aid, "prov:wasInformedBy", f"trace:{tc.parent_event_id}")
            in_id = f"trace:{evt.id}_input"
            ie = node(in_id, "prov:Entity")
            ie["trace:kind"] = "ToolInput"
            ie["prov:value"] = _lit(tc.input)
            rel(aid, "prov:used", in_id)
            if tc.output is not None:
                out_id = f"trace:{evt.id}_output"
                oe = node(out_id, "prov:Entity")
                oe["trace:kind"] = "ToolOutput"
                oe["prov:value"] = _lit(tc.output)
                rel(out_id, "prov:wasGeneratedBy", aid)

        elif evt.type == "decision" and evt.decision:
            d = evt.decision
            a = node(aid, "prov:Activity")
            a["trace:kind"] = "Decision"
            if _dt(evt.timestamp):
                a["prov:startedAtTime"] = _dt(evt.timestamp)
            a["trace:description"] = _lit(d.description)
            a["trace:disposition"] = d.disposition
            a["prov:wasAssociatedWith"] = {"@id": actor_id}
            if d.rationale:
                a["trace:rationale"] = _lit(d.rationale)
            if d.warnings:
                a["trace:warnings"] = _lit(d.warnings)
            if d.revises_event_id:
                rel(aid, "prov:wasRevisionOf", f"trace:{d.revises_event_id}")
            if d.confidence is not None:
                c = d.confidence
                a["trace:confidenceStatistic"] = c.statistic
                a["trace:confidenceDirection"] = c.direction
                a["trace:confidenceEstimate"] = c.estimate
                a["trace:confidenceLow"] = c.interval.lower
                a["trace:confidenceHigh"] = c.interval.upper
                a["trace:confidenceLevel"] = c.interval.level
                a["trace:confidenceMethod"] = c.method.name
                a["trace:confidenceSampleSize"] = c.sample_size
                if c.method.algorithm:
                    a["trace:confidenceAlgorithm"] = c.method.algorithm
                if c.method.resamples is not None:
                    a["trace:confidenceResamples"] = c.method.resamples
                if c.method.seed is not None:
                    a["trace:confidenceSeed"] = c.method.seed
                if c.unit:
                    a["trace:confidenceUnit"] = c.unit
                if c.contract:
                    a["trace:confidenceContract"] = c.contract
                for ev in c.evidence:
                    # Entity identity is a content hash of the whole reference (role, locator, digest):
                    # two locators for the same bytes stay two entities, a reordered list keeps every
                    # id, and no raw string enters the identifier.
                    key = json.dumps(
                        {"locator": ev.locator, "role": ev.role, "sha256": ev.sha256},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    ev_id = f"{aid}_evidence_{hashlib.sha256(key.encode()).hexdigest()[:16]}"
                    ent = node(ev_id, "prov:Entity")
                    ent["trace:kind"] = "Evidence"
                    ent["trace:role"] = ev.role
                    ent["prov:atLocation"] = ev.locator
                    ent["trace:sha256"] = ev.sha256
                    rel(aid, "prov:used", ev_id)

        elif evt.type == "annotation" and evt.annotation:
            an = evt.annotation
            ent_id = f"trace:{evt.id}_annotation"
            e = node(ent_id, "prov:Entity")
            e["trace:kind"] = "Annotation"
            e["trace:category"] = an.category
            e["prov:value"] = _lit(an.content)
            rel(ent_id, "prov:wasAttributedTo", actor_id)
            # v0.4.1 correction split (spec §6).
            for idx, ref in enumerate(an.corrects_event_ids):
                if is_uri_form_reference(ref):
                    infl_id = f"_:infl_{evt.id}_{idx}"
                    inf = node(infl_id, "prov:Influence")
                    inf["prov:atLocation"] = ref
                    rel(ent_id, "prov:qualifiedInfluence", infl_id)
                    node(ent_id).setdefault("prov:wasInfluencedBy", []).append({"@id": infl_id})
                else:
                    # Repudiatory: the corrected in-session event was
                    # invalidated by this correction entity.
                    node(f"trace:{ref}", "prov:Entity")
                    rel(f"trace:{ref}", "prov:wasInvalidatedBy", ent_id)

        elif evt.type == "contribution" and evt.contribution:
            c = evt.contribution
            a = node(aid, "prov:Activity")
            a["trace:kind"] = "Contribution"
            if _dt(evt.timestamp):
                a["prov:startedAtTime"] = _dt(evt.timestamp)
            a["trace:description"] = _lit(c.description)
            a["trace:direction"] = c.direction
            a["trace:execution"] = c.execution
            a["prov:wasAssociatedWith"] = {"@id": actor_id}
            if c.artifact:
                a["trace:artifact"] = _lit(c.artifact)
            for dec_id in c.related_decision_ids:
                rel(aid, "prov:used", f"trace:{dec_id}")

        elif evt.type == "state_change" and evt.state_change:
            sc = evt.state_change
            a = node(aid, "prov:Activity")
            a["trace:kind"] = "StateChange"
            if _dt(evt.timestamp):
                a["prov:startedAtTime"] = _dt(evt.timestamp)
            a["trace:description"] = _lit(sc.description)
            a["prov:wasAssociatedWith"] = {"@id": actor_id}
            if sc.old_value is not None:
                a["trace:oldValue"] = _lit(sc.old_value)
            if sc.new_value is not None:
                a["trace:newValue"] = _lit(sc.new_value)

    doc = {"@context": PROV_CONTEXT, "@graph": list(nodes.values())}
    return json.dumps(doc, indent=2, default=str)

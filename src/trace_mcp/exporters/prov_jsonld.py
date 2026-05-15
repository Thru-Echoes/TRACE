"""W3C PROV JSON-LD export for TRACE sessions.

Maps TRACE concepts to W3C PROV ontology for interoperable provenance export.
Uses the PROV-JSON serialization format.
"""

from __future__ import annotations

import json
from typing import Any

from trace_mcp.schema import Session
from trace_mcp.schema.prov_mapping import PROV_CONTEXT
from trace_mcp.tools.session_tools import _is_uri_form_reference


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def export_prov_jsonld(session: Session) -> str:
    """Export a TRACE session as W3C PROV JSON-LD."""
    doc: dict[str, Any] = {
        "@context": PROV_CONTEXT,
        "bundle": {
            f"trace:{session.id}": _build_bundle(session),
        },
    }
    return json.dumps(doc, indent=2, default=str)


def _build_bundle(session: Session) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "agent": {},
        "activity": {},
        "entity": {},
        "wasGeneratedBy": {},
        "used": {},
        "wasAttributedTo": {},
        "wasRevisionOf": {},
        # v0.4.1: new PROV relations per spec §6.
        # `wasInvalidatedBy` — repudiatory corrections to in-session events.
        # `wasInfluencedBy` — corrections anchored to URI-form external refs.
        # `influence` — qualified-influence blank nodes for URI corrections,
        #   bearing `prov:atLocation` with the URI.
        # `wasInformedBy` — dispatch-chain relation (tool_call.parent_event_id).
        "wasInvalidatedBy": {},
        "wasInfluencedBy": {},
        "influence": {},
        "wasInformedBy": {},
    }

    # Register agents from participants
    for p in session.metadata.participants:
        agent_id = f"trace:{p.id}"
        bundle["agent"][agent_id] = {
            "prov:type": f"trace:{p.type}",
        }
        if p.role:
            bundle["agent"][agent_id]["trace:role"] = p.role

    # Session as top-level activity
    session_activity_id = f"trace:session_{session.id}"
    bundle["activity"][session_activity_id] = {
        "prov:type": "trace:Session",
        "prov:startTime": _iso(session.created),
        "prov:endTime": _iso(session.ended),
        "trace:project": session.metadata.project,
        "trace:status": session.status,
    }

    # Process events
    for evt in session.events:
        activity_id = f"trace:{evt.id}"
        actor_id = f"trace:{evt.actor.id}"

        # Ensure actor is registered
        if actor_id not in bundle["agent"]:
            bundle["agent"][actor_id] = {
                "prov:type": f"trace:{evt.actor.type}",
            }

        if evt.type == "tool_call" and evt.tool_call:
            tc = evt.tool_call
            bundle["activity"][activity_id] = {
                "prov:type": "trace:ToolCall",
                "prov:startTime": _iso(evt.timestamp),
                "trace:server": tc.server,
                "trace:toolName": tc.name,
                "trace:status": tc.status,
            }
            if tc.duration_ms is not None:
                bundle["activity"][activity_id]["trace:durationMs"] = tc.duration_ms
            if tc.retries_event_id:
                retried_id = f"trace:{tc.retries_event_id}"
                bundle["wasRevisionOf"][f"trace:retry_{evt.id}"] = {
                    "prov:generatedEntity": activity_id,
                    "prov:usedEntity": retried_id,
                }
            # v0.4.1: parent_event_id → prov:wasInformedBy. The dispatch activity
            # was informed by the controller-side event that issued it. Enables
            # consumers to walk the dispatch graph from a contribution back through
            # the subagent invocations that produced it.
            if tc.parent_event_id:
                parent_id = f"trace:{tc.parent_event_id}"
                bundle["wasInformedBy"][f"trace:wib_{evt.id}"] = {
                    "prov:informed": activity_id,
                    "prov:informant": parent_id,
                }

            # Input entity
            input_id = f"trace:{evt.id}_input"
            bundle["entity"][input_id] = {
                "prov:type": "trace:ToolInput",
                "prov:value": tc.input,
            }
            bundle["used"][f"trace:used_{evt.id}"] = {
                "prov:activity": activity_id,
                "prov:entity": input_id,
            }

            # Output entity
            if tc.output is not None:
                output_id = f"trace:{evt.id}_output"
                bundle["entity"][output_id] = {
                    "prov:type": "trace:ToolOutput",
                    "prov:value": tc.output,
                }
                bundle["wasGeneratedBy"][f"trace:gen_{evt.id}"] = {
                    "prov:entity": output_id,
                    "prov:activity": activity_id,
                }

        elif evt.type == "decision" and evt.decision:
            d = evt.decision
            bundle["activity"][activity_id] = {
                "prov:type": "trace:Decision",
                "prov:startTime": _iso(evt.timestamp),
                "trace:description": d.description,
                "trace:disposition": d.disposition,
            }
            if d.rationale:
                bundle["activity"][activity_id]["trace:rationale"] = d.rationale
            if d.warnings:
                bundle["activity"][activity_id]["trace:warnings"] = d.warnings

            # Revision link
            if d.revises_event_id:
                revised_id = f"trace:{d.revises_event_id}"
                bundle["wasRevisionOf"][f"trace:rev_{evt.id}"] = {
                    "prov:generatedEntity": activity_id,
                    "prov:usedEntity": revised_id,
                }

        elif evt.type == "annotation" and evt.annotation:
            a = evt.annotation
            entity_id = f"trace:{evt.id}_annotation"
            bundle["entity"][entity_id] = {
                "prov:type": "trace:Annotation",
                "trace:category": a.category,
                "prov:value": a.content,
            }
            bundle["wasAttributedTo"][f"trace:attr_{evt.id}"] = {
                "prov:entity": entity_id,
                "prov:agent": actor_id,
            }
            # v0.4.1: correction mapping is split per spec §6.
            # - Event-ID target (in-session) → prov:wasInvalidatedBy
            #   The corrected event is invalidated by this correction activity.
            #   This is REPUDIATORY semantics, distinct from `wasRevisionOf`
            #   which implies evolutionary refinement.
            # - URI-form target (out-of-session, per spec §3.7.1) → qualified
            #   prov:wasInfluencedBy. The correction was influenced by the
            #   externally-located artifact. Reified via prov:qualifiedInfluence
            #   pointing to a prov:Influence blank node bearing prov:atLocation
            #   with the URI.
            for idx, corrected_ref in enumerate(a.corrects_event_ids):
                if _is_uri_form_reference(corrected_ref):
                    # Qualified-influence pattern (PROV-O §4 qualified relations).
                    # Stable, deterministic blank-node ID: derived from event ID
                    # + index within the entry list (not from Python hash, which
                    # would be non-deterministic across runs).
                    qi_id = f"_:infl_{evt.id}_{idx}"
                    bundle["influence"][qi_id] = {
                        "prov:type": "prov:Influence",
                        "prov:atLocation": corrected_ref,
                    }
                    bundle["wasInfluencedBy"][f"trace:wif_{evt.id}_{idx}"] = {
                        "prov:influenced": entity_id,
                        "prov:qualifiedInfluence": qi_id,
                    }
                else:
                    # Event-ID target: invalidated-by.
                    bundle["wasInvalidatedBy"][f"trace:inv_{evt.id}_{corrected_ref}"] = {
                        "prov:entity": f"trace:{corrected_ref}",
                        "prov:activity": entity_id,
                    }

        elif evt.type == "contribution" and evt.contribution:
            c = evt.contribution
            bundle["activity"][activity_id] = {
                "prov:type": "trace:Contribution",
                "prov:startTime": _iso(evt.timestamp),
                "trace:description": c.description,
                "trace:direction": c.direction,
                "trace:execution": c.execution,
            }
            if c.artifact:
                bundle["activity"][activity_id]["trace:artifact"] = c.artifact
            # Link to related decisions
            for dec_id in c.related_decision_ids:
                rel_id = f"trace:{dec_id}"
                bundle["used"][f"trace:used_{evt.id}_{dec_id}"] = {
                    "prov:activity": activity_id,
                    "prov:entity": rel_id,
                }

        elif evt.type == "state_change" and evt.state_change:
            sc = evt.state_change
            bundle["activity"][activity_id] = {
                "prov:type": "trace:StateChange",
                "prov:startTime": _iso(evt.timestamp),
                "trace:description": sc.description,
            }
            if sc.old_value is not None:
                bundle["activity"][activity_id]["trace:oldValue"] = sc.old_value
            if sc.new_value is not None:
                bundle["activity"][activity_id]["trace:newValue"] = sc.new_value

    # Clean out empty sections
    bundle = {k: v for k, v in bundle.items() if v}

    return bundle

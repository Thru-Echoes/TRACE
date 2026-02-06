"""W3C PROV JSON-LD export for TRACE sessions.

Maps TRACE concepts to W3C PROV ontology for interoperable provenance export.
Uses the PROV-JSON serialization format.
"""

from __future__ import annotations

import json
from typing import Any

from trace_mcp.schema import Session
from trace_mcp.schema.prov_mapping import PROV_CONTEXT


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

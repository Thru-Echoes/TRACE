#!/usr/bin/env python3
"""Migrate TRACE v1.0 (monolithic trace.json) to TRACE v0.1 (per-session JSON files).

Usage:
    python scripts/migrate_v1_to_v01.py <path-to-old-trace.json> [--output-dir DIR]

Produces per-session TRACE v0.1 files in ~/.trace/sessions/ (or --output-dir).
Creates a backup of the original file at <path>.v1.bak.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path


def parse_timestamp(ts: str | None) -> str:
    """Normalize a timestamp string to ISO format."""
    if not ts:
        return datetime.now().isoformat()
    return ts


def _actor(source: str) -> dict:
    """Map old provenance source to a v0.1 Actor."""
    source_lower = (source or "").lower()
    if source_lower in ("ai", "ai_suggested"):
        return {"type": "ai", "id": "ai-assistant"}
    elif source_lower in ("human",):
        return {"type": "human", "id": "researcher"}
    else:
        return {"type": "human", "id": "collaborative"}


def _direction_actor(proposed_by: str) -> dict:
    """Map decision proposed_by to Actor."""
    lower = (proposed_by or "").lower()
    if lower in ("ai", "ai_suggested"):
        return {"type": "ai", "id": "ai-assistant"}
    elif lower in ("human",):
        return {"type": "human", "id": "researcher"}
    else:
        return {"type": "human", "id": "collaborative"}


def migrate_code_contribution(cc: dict, evt_num: int) -> dict:
    """Convert a v1.0 code_contribution to a v0.1 annotation event."""
    authorship = cc.get("authorship", {})
    metrics = cc.get("metrics", {})
    description = cc.get("description", "")

    # Build rich content from authorship data
    parts = [description]
    auth_lines = []
    for key in [
        "ai_authored_lines",
        "human_authored_lines",
        "human_improved_ai_lines",
        "collaborative_lines",
        "ai_authored_words",
        "human_authored_words",
    ]:
        val = authorship.get(key, 0)
        if val:
            auth_lines.append(f"  {key}: {val}")
    if auth_lines:
        parts.append("\nAuthorship:\n" + "\n".join(auth_lines))

    metric_lines = []
    for key in [
        "lines_added",
        "lines_removed",
        "lines_modified",
        "words_added",
        "words_removed",
        "files_changed_count",
    ]:
        val = metrics.get(key, 0)
        if val:
            metric_lines.append(f"  {key}: {val}")
    if metric_lines:
        parts.append("\nMetrics:\n" + "\n".join(metric_lines))

    file_path = cc.get("file_path", "unknown")
    parts.append(f"\nFile: {file_path}")

    git_meta = cc.get("git_metadata", {})
    if git_meta:
        commit = git_meta.get("commit_hash", "")
        msg = git_meta.get("commit_message", "")
        author = git_meta.get("author_name", "")
        if commit:
            parts.append(f"Git: {commit[:8]} by {author} — {msg}")

    content = "\n".join(parts)

    # Determine who did this
    ai_lines = authorship.get("ai_authored_lines", 0) or 0
    human_lines = authorship.get("human_authored_lines", 0) or 0
    if ai_lines > human_lines:
        actor = {"type": "ai", "id": "ai-assistant"}
    elif human_lines > 0:
        actor = {"type": "human", "id": "researcher"}
    else:
        actor = {"type": "ai", "id": "ai-assistant"}

    tags = ["code_contribution", f"v1_id:{cc.get('id', '')}", cc.get("contribution_type", "")]
    if git_meta:
        tags.append("git-synced")

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(cc.get("timestamp")),
        "session_id": "",  # filled by caller
        "type": "annotation",
        "actor": actor,
        "annotation": {
            "category": "observation",
            "content": content,
            "tags": [t for t in tags if t],
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 code_contribution {cc.get('id', '')}",
        },
    }


def migrate_idea(idea: dict, evt_num: int) -> dict:
    """Convert a v1.0 idea to a v0.1 annotation event."""
    origin = idea.get("origin", {})
    source = origin.get("source", "human")
    triggered = origin.get("triggered_by", "")

    content_parts = [idea.get("idea", "")]
    if triggered:
        content_parts.append(f"\nTriggered by: {triggered}")
    eval_status = idea.get("evaluation", {}).get("status", "pending")
    content_parts.append(f"Status: {eval_status}")
    idea_type = idea.get("idea_type", "")
    if idea_type:
        content_parts.append(f"Type: {idea_type}")

    tags = ["idea", f"v1_id:{idea.get('id', '')}"]
    if idea_type:
        tags.append(idea_type)

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(idea.get("timestamp")),
        "session_id": "",
        "type": "annotation",
        "actor": _actor(source),
        "annotation": {
            "category": "observation",
            "content": "\n".join(content_parts),
            "tags": tags,
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 idea {idea.get('id', '')}",
        },
    }


def migrate_learning(learning: dict, evt_num: int) -> dict:
    """Convert a v1.0 learning to a v0.1 annotation event."""
    prov = learning.get("provenance", {})
    source = prov.get("discovered_by", "human")

    content_parts = [learning.get("learning", "")]
    evidence = learning.get("evidence", "")
    if evidence:
        content_parts.append(f"\nEvidence: {evidence}")
    conf = learning.get("confidence", {})
    if conf:
        content_parts.append(f"Confidence: {conf.get('level', 'unknown')} ({conf.get('value', '?')})")

    tags = ["learning", f"v1_id:{learning.get('id', '')}"] + learning.get("tags", [])

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(learning.get("timestamp")),
        "session_id": "",
        "type": "annotation",
        "actor": _actor(source),
        "annotation": {
            "category": "learning",
            "content": "\n".join(content_parts),
            "tags": tags,
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 learning {learning.get('id', '')}",
        },
    }


def migrate_gotcha(gotcha: dict, evt_num: int) -> dict:
    """Convert a v1.0 gotcha to a v0.1 annotation event."""
    prov = gotcha.get("provenance", {})
    source = prov.get("discovered_by", "human")

    problem = gotcha.get("problem", "")
    solution = gotcha.get("solution", "")
    severity = gotcha.get("severity", "medium")
    content = f"PROBLEM: {problem}\n\nSOLUTION: {solution}\n\nSeverity: {severity}"

    tags = ["gotcha", f"v1_id:{gotcha.get('id', '')}", f"severity:{severity}"] + gotcha.get("tags", [])

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(gotcha.get("timestamp")),
        "session_id": "",
        "type": "annotation",
        "actor": _actor(source),
        "annotation": {
            "category": "gotcha",
            "content": content,
            "tags": tags,
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 gotcha {gotcha.get('id', '')}",
        },
    }


def migrate_decision(decision: dict, evt_num: int) -> dict:
    """Convert a v1.0 decision to a v0.1 decision event."""
    prov = decision.get("provenance", {})
    proposed_by = prov.get("proposed_by", "human")

    # Build rationale including alternatives
    rationale_parts = [decision.get("rationale", "")]
    alternatives = decision.get("alternatives", [])
    if alternatives:
        alt_strs = []
        for alt in alternatives:
            option = alt.get("option", "?")
            why_rejected = alt.get("why_rejected", "")
            alt_strs.append(f"  - {option}: {why_rejected}")
        rationale_parts.append("\nAlternatives considered:\n" + "\n".join(alt_strs))

    conf = decision.get("confidence", {})
    if conf:
        rationale_parts.append(f"\nConfidence: {conf.get('initial', '?')}")

    tags = [f"v1_id:{decision.get('id', '')}"] + decision.get("tags", [])

    actor = _direction_actor(proposed_by)

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(decision.get("timestamp")),
        "session_id": "",
        "type": "decision",
        "actor": actor,
        "decision": {
            "description": decision.get("decision", ""),
            "rationale": "\n".join(rationale_parts),
            "proposed_by": actor,
            "disposition": "accepted",
            "resolved_by": {"type": "human", "id": "researcher"},
            "tags": tags,
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 decision {decision.get('id', '')}",
        },
    }


def migrate_intervention(intervention: dict, evt_num: int) -> dict:
    """Convert a v1.0 intervention to a v0.1 annotation event."""
    ai_output = intervention.get("ai_output", {}).get("summary", "")
    human_action = intervention.get("human_action", {})
    action_desc = human_action.get("description", "")
    rationale = human_action.get("rationale", "")
    expertise = intervention.get("expertise_applied", [])
    significance = intervention.get("impact", {}).get("significance", "")

    content = textwrap.dedent(f"""\
        INTERVENTION ({intervention.get("intervention_type", "correction")})

        AI output: {ai_output}

        Human action: {action_desc}

        Rationale: {rationale}

        Expertise applied: {", ".join(expertise)}
        Significance: {significance}""")

    tags = [
        "intervention",
        f"v1_id:{intervention.get('id', '')}",
        intervention.get("intervention_type", ""),
        f"significance:{significance}",
    ]

    return {
        "id": f"evt_{evt_num:03d}",
        "timestamp": parse_timestamp(intervention.get("timestamp")),
        "session_id": "",
        "type": "annotation",
        "actor": {"type": "human", "id": "researcher"},
        "annotation": {
            "category": "observation",
            "content": content,
            "tags": [t for t in tags if t],
        },
        "context": {
            "reasoning_summary": f"Migrated from TRACE v1.0 intervention {intervention.get('id', '')}",
        },
    }


def build_session(
    old_session: dict | None,
    session_id: str,
    project: str,
    events: list[dict],
) -> dict:
    """Build a v0.1 Session dict from old session data + migrated events."""
    if old_session:
        created = parse_timestamp(old_session.get("started"))
        ended = parse_timestamp(old_session.get("ended"))
        status = "completed" if old_session.get("ended") else "active"
        purpose = old_session.get("purpose", "")
        reflection = old_session.get("reflection", {})
        summary_text = reflection.get("what_went_well", "") or ""
    else:
        created = datetime.now().isoformat()
        ended = datetime.now().isoformat()
        status = "completed"
        purpose = "Migration of orphan entries from TRACE v1.0"
        summary_text = "Entries not associated with any session in the original TRACE v1.0 data."

    # Sort events by timestamp
    events.sort(key=lambda e: e.get("timestamp", ""))

    # Re-number events sequentially
    for i, evt in enumerate(events, 1):
        evt["id"] = f"evt_{i:03d}"
        evt["session_id"] = session_id

    return {
        "context": "https://trace-protocol.org/v0.1",
        "trace_version": "0.1.0",
        "id": session_id,
        "created": created,
        "ended": ended if status == "completed" else None,
        "status": status,
        "metadata": {
            "project": project,
            "description": purpose,
            "participants": [
                {"type": "human", "id": "researcher"},
                {"type": "ai", "id": "claude-opus-4-5-20251101", "role": "assistant"},
            ],
            "tags": ["migrated-from-v1.0"],
            "custom": {"migrated_from": "TRACE-1.0", "original_session_id": session_id},
        },
        "summary": summary_text,
        "events": events,
    }


def migrate(input_path: Path, output_dir: Path) -> list[Path]:
    """Run the full migration. Returns list of created files."""
    with open(input_path) as f:
        data = json.load(f)

    project = data.get("metadata", {}).get("project", "Unknown Project")

    # Collect old sessions by ID
    old_sessions: dict[str, dict] = {}
    for s in data.get("sessions", []):
        old_sessions[s["id"]] = s

    # Bucket events by session_id (None → "ORPHAN")
    session_events: dict[str, list[dict]] = {sid: [] for sid in old_sessions}
    session_events["ORPHAN"] = []

    evt_counter = 0

    def assign(evt: dict, sid: str | None) -> None:
        nonlocal evt_counter
        evt_counter += 1
        bucket = sid if sid and sid in old_sessions else "ORPHAN"
        session_events[bucket].append(evt)

    # Migrate code contributions
    for cc in data.get("code_contributions", []):
        evt = migrate_code_contribution(cc, evt_counter)
        assign(evt, cc.get("session_id"))

    # Migrate ideas
    for idea in data.get("ideas", []):
        evt = migrate_idea(idea, evt_counter)
        assign(evt, idea.get("session_id"))

    # Migrate decisions
    for decision in data.get("decisions", []):
        evt = migrate_decision(decision, evt_counter)
        assign(evt, decision.get("session_id"))

    # Migrate learnings
    for learning in data.get("learnings", []):
        evt = migrate_learning(learning, evt_counter)
        assign(evt, learning.get("session_id"))

    # Migrate gotchas
    for gotcha in data.get("gotchas", []):
        evt = migrate_gotcha(gotcha, evt_counter)
        assign(evt, gotcha.get("session_id"))

    # Migrate interventions
    for intervention in data.get("interventions", []):
        evt = migrate_intervention(intervention, evt_counter)
        assign(evt, intervention.get("session_id"))

    # Build session files
    output_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []

    for sid, events in session_events.items():
        if sid == "ORPHAN":
            if not events:
                continue
            file_id = f"trace_v1_orphan_{project.lower().replace(' ', '_')[:30]}"
            session_dict = build_session(None, file_id, project, events)
        else:
            file_id = f"trace_v1_{sid}_{project.lower().replace(' ', '_')[:30]}"
            session_dict = build_session(old_sessions[sid], sid, project, events)

        out_path = output_dir / f"{file_id}.json"
        with open(out_path, "w") as f:
            json.dump(session_dict, f, indent=2, default=str)
            f.write("\n")
        created_files.append(out_path)

    return created_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate TRACE v1.0 → v0.1")
    parser.add_argument("input", help="Path to old trace.json")
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / ".trace" / "sessions"),
        help="Output directory for v0.1 session files (default: ~/.trace/sessions/)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating a backup of the original file",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    output_dir = Path(args.output_dir)

    # Backup
    if not args.no_backup:
        backup_path = input_path.with_suffix(".json.v1.bak")
        if not backup_path.exists():
            shutil.copy2(input_path, backup_path)
            print(f"Backed up: {input_path} → {backup_path}")
        else:
            print(f"Backup already exists: {backup_path}")

    # Migrate
    created = migrate(input_path, output_dir)

    print(f"\nMigration complete: {len(created)} session files created in {output_dir}/")
    for path in created:
        # Count events
        with open(path) as f:
            session = json.load(f)
        n_events = len(session.get("events", []))
        print(f"  {path.name}: {n_events} events")

    # Summary
    with open(input_path) as f:
        data = json.load(f)
    total_items = (
        len(data.get("code_contributions", []))
        + len(data.get("ideas", []))
        + len(data.get("decisions", []))
        + len(data.get("learnings", []))
        + len(data.get("gotchas", []))
        + len(data.get("interventions", []))
    )
    print(f"\nMigrated {total_items} total entries from TRACE v1.0")
    print(f"  Code contributions: {len(data.get('code_contributions', []))}")
    print(f"  Ideas: {len(data.get('ideas', []))}")
    print(f"  Decisions: {len(data.get('decisions', []))}")
    print(f"  Learnings: {len(data.get('learnings', []))}")
    print(f"  Gotchas: {len(data.get('gotchas', []))}")
    print(f"  Interventions: {len(data.get('interventions', []))}")


if __name__ == "__main__":
    main()

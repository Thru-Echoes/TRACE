"""Retroactively enrich v1 TRACE sessions with contribution events and suggestion_type.

These v1 sessions have explicit attribution tags in annotation text:
  [HUMAN-DIRECTED], [AI-SUGGESTED], [HUMAN-AUTHORED], [HUMAN-MANUAL-EDIT], [GIT-SYNC]
that map directly to contribution direction/execution fields.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

SESSIONS_DIR = Path.home() / ".trace" / "sessions"


def load(session_id: str) -> dict:
    path = SESSIONS_DIR / f"{session_id}.json"
    with open(path) as f:
        return json.load(f)


def save(session_id: str, data: dict) -> None:
    path = SESSIONS_DIR / f"{session_id}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {path.name}")


def next_evt_id(events: list[dict]) -> str:
    max_n = 0
    for e in events:
        eid = e.get("id", "")
        if eid.startswith("evt_"):
            try:
                n = int(eid.split("_")[1])
                max_n = max(max_n, n)
            except (IndexError, ValueError):
                pass
    return f"evt_{max_n + 1:03d}"


def make_contribution(session_id: str, events: list[dict], **kwargs) -> dict:
    eid = next_evt_id(events)
    # Use the original event's timestamp if provided
    ts = kwargs.get("timestamp", datetime.now(UTC).isoformat())
    return {
        "id": eid,
        "timestamp": ts,
        "session_id": session_id,
        "type": "contribution",
        "actor": {"type": kwargs.get("actor_type", "ai"), "id": kwargs.get("actor_id", "ai-assistant"), "role": None},
        "tool_call": None,
        "decision": None,
        "annotation": None,
        "state_change": None,
        "contribution": {
            "description": kwargs["description"],
            "artifact": kwargs.get("artifact"),
            "direction": kwargs["direction"],
            "execution": kwargs["execution"],
            "related_decision_ids": kwargs.get("related_decision_ids", []),
            "tags": kwargs.get("tags", []),
        },
        "context": {
            "conversation_turn": None,
            "parent_event_id": kwargs.get("source_event_id"),
            "reasoning_summary": "Derived from v1 code_contribution annotation",
            "related_event_ids": [kwargs["source_event_id"]] if kwargs.get("source_event_id") else [],
        },
        "verification": None,
    }


def extract_file(content: str) -> str | None:
    """Extract file path from annotation content."""
    m = re.search(r"File:\s*(.+?)(?:\n|$)", content)
    return m.group(1).strip() if m else None


def extract_description(content: str) -> str:
    """Extract the first line as description, stripping tags."""
    first_line = content.split("\n")[0]
    # Remove known tags
    for tag in [
        "[GIT-SYNC]",
        "[HUMAN-DIRECTED]",
        "[AI-SUGGESTED]",
        "[HUMAN-AUTHORED]",
        "[HUMAN-MANUAL-EDIT]",
        "INTERVENTION (correction)",
    ]:
        first_line = first_line.replace(tag, "")
    return first_line.strip().rstrip(":")


def determine_direction_execution(content: str, actor_type: str) -> tuple[str, str] | None:
    """Determine direction and execution from annotation text.
    Returns (direction, execution) or None if uncertain."""

    # Explicit tags are the strongest signals
    # Tags appear as [TAG] or [TAG: explanation] so match prefix
    if "[HUMAN-AUTHORED" in content or "[HUMAN-MANUAL-EDIT]" in content:
        return ("human", "human")

    if "[HUMAN-DIRECTED" in content:
        # Human had the idea, AI executed
        return ("human", "ai")

    if "[AI-SUGGESTED" in content:
        # AI had the idea, AI executed (human approved)
        return ("ai", "ai")

    # GIT-SYNC entries: check authorship in content
    if "[GIT-SYNC]" in content:
        if "human_authored_lines:" in content and "ai_authored_lines:" not in content:
            return ("human", "human")
        if "ai_authored_lines:" in content and "human_improved_ai_lines:" in content:
            return ("collaborative", "ai")
        if "ai_authored_lines:" in content:
            return ("ai", "ai") if actor_type == "ai" else None
        # No authorship data — can't determine
        return None

    return None


def strip_contributions(s: dict) -> int:
    """Remove previously added contribution events (makes re-runs idempotent)."""
    before = len(s["events"])
    s["events"] = [e for e in s["events"] if e["type"] != "contribution"]
    return before - len(s["events"])


def enrich_v1_orphan(s: dict) -> int:
    """Enrich the orphan v1 session."""
    removed = strip_contributions(s)
    if removed:
        print(f"  Stripped {removed} existing contribution events")

    contributions_added = 0
    st_added = 0

    # First pass: add suggestion_type to decisions and contribution field
    for evt in s["events"]:
        # Ensure schema fields
        if "contribution" not in evt:
            evt["contribution"] = None
        if evt.get("decision") and "suggestion_type" not in evt["decision"]:
            evt["decision"]["suggestion_type"] = None

        if evt["type"] == "decision" and evt.get("decision"):
            proposer_type = evt["decision"]["proposed_by"]["type"]
            proposer_id = evt["decision"]["proposed_by"]["id"]

            if proposer_id == "collaborative":
                evt["decision"]["suggestion_type"] = "collaborative"
                st_added += 1
            elif proposer_type == "human" and proposer_id == "researcher":
                # Human's own decision — no suggestion_type
                pass

    # Second pass: convert code_contribution annotations to contribution events
    new_events = []
    for evt in s["events"]:
        if evt["type"] != "annotation":
            continue
        tags = evt.get("annotation", {}).get("tags", [])
        if "code_contribution" not in tags:
            continue

        content = evt["annotation"]["content"]
        actor_type = evt["actor"]["type"]
        result = determine_direction_execution(content, actor_type)
        if result is None:
            continue

        direction, execution = result
        desc = extract_description(content)
        artifact = extract_file(content)

        new_events.append(
            make_contribution(
                s["id"],
                s["events"] + new_events,
                description=desc,
                artifact=artifact,
                direction=direction,
                execution=execution,
                tags=[t for t in tags if t not in ("code_contribution", "git-synced") and not t.startswith("v1_id:")],
                source_event_id=evt["id"],
                timestamp=evt["timestamp"],
                actor_type=evt["actor"]["type"],
                actor_id=evt["actor"]["id"],
            )
        )
        contributions_added += 1

    s["events"].extend(new_events)

    if st_added:
        print(f"  +{st_added} suggestion_type fields")
    return contributions_added


def enrich_v1_S003(s: dict) -> int:
    """Enrich the S003 v1 session."""
    removed = strip_contributions(s)
    if removed:
        print(f"  Stripped {removed} existing contribution events")

    contributions_added = 0

    # Ensure schema fields on all events
    for evt in s["events"]:
        if "contribution" not in evt:
            evt["contribution"] = None
        if evt.get("decision") and "suggestion_type" not in evt["decision"]:
            evt["decision"]["suggestion_type"] = None

    # Convert code_contribution annotations to contribution events
    new_events = []
    for evt in s["events"]:
        if evt["type"] != "annotation":
            continue
        tags = evt.get("annotation", {}).get("tags", [])
        if "code_contribution" not in tags:
            continue

        content = evt["annotation"]["content"]
        actor_type = evt["actor"]["type"]
        result = determine_direction_execution(content, actor_type)
        if result is None:
            continue

        direction, execution = result
        desc = extract_description(content)
        artifact = extract_file(content)

        new_events.append(
            make_contribution(
                s["id"],
                s["events"] + new_events,
                description=desc,
                artifact=artifact,
                direction=direction,
                execution=execution,
                tags=[t for t in tags if t not in ("code_contribution",) and not t.startswith("v1_id:")],
                source_event_id=evt["id"],
                timestamp=evt["timestamp"],
                actor_type=evt["actor"]["type"],
                actor_id=evt["actor"]["id"],
            )
        )
        contributions_added += 1

    s["events"].extend(new_events)
    return contributions_added


def main() -> None:
    print("TRACE v1 Retroactive Enrichment")
    print("=" * 60)

    total_contributions = 0

    # Orphan session
    sid = "trace_v1_orphan_when_algorithms_meet_artists"
    print(f"\n{sid}:")
    s = load(sid)
    n = enrich_v1_orphan(s)
    print(f"  +{n} contribution events")
    total_contributions += n
    save(sid, s)

    # S003 session
    sid = "trace_v1_S003_when_algorithms_meet_artists"
    print(f"\n{sid}:")
    s = load(sid)
    n = enrich_v1_S003(s)
    print(f"  +{n} contribution events")
    total_contributions += n
    save(sid, s)

    # Verify by checking what was added
    print(f"\n{'=' * 60}")
    print(f"Total contribution events added: {total_contributions}")

    # Print summary of what was derived
    print("\nContribution breakdown:")
    for sid_name in ["trace_v1_orphan_when_algorithms_meet_artists", "trace_v1_S003_when_algorithms_meet_artists"]:
        s = load(sid_name)
        contribs = [e for e in s["events"] if e["type"] == "contribution"]
        if contribs:
            print(f"\n  {sid_name}:")
            matrix: dict[str, int] = {}
            for c in contribs:
                key = f"{c['contribution']['direction']}→{c['contribution']['execution']}"
                matrix[key] = matrix.get(key, 0) + 1
            for k, v in sorted(matrix.items()):
                print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
